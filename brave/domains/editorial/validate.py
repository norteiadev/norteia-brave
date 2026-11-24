"""Web check of the perishable editorial facts of old posts (a web search + Gemini 2.5 Flash).

The resolver parks a matched claim as ``pending_validation`` when its column is perishable and
its post is over a year old (``schema.needs_validation``). ``validate_pending`` asks Gemini —
one grounded call per ENTITY, with the entity's name, place, the post date and every pending
fact — whether each fact still holds today, and moves the claim:

  confirmado     → ``matched`` (the resolver writes it; ``validation_verdict`` keeps it from
                   going back to pending)
  desatualizado  → ``outdated``   (never sent)
  sem_evidencia  → ``unverified`` (never sent)

Nothing is deleted: the verdict, the sources (what the model cited AND the pages the Google
Search grounding rests on) and ``validated_at`` are written on the claim.

Two searches (``editorial_validate_search``):

  parallel (default) — one Parallel ``/v1/search`` per entity ($0.001), the excerpts go in the
      prompt and Gemini answers WITHOUT a search tool, in a response schema. A ``confirmado``
      counts only when it cites a page the search returned. ~$0.006 per entity.
  google — Gemini with Grounding with Google Search ($0.035 per grounded prompt). Gemini 2.5
      cannot combine the search tool with a response schema, so the prompt asks for JSON in the
      text. A ``confirmado`` with no grounding page behind it is not trusted. ~$0.04 per entity.

Either way the model's own memory is not evidence: an unbacked ``confirmado`` is demoted to
``sem_evidencia``, and ``parse_answer`` validates the JSON with Pydantic.

The LLM client is injected (``generate_grounded``, from ``brave.clients.factory``): it books the
cost — tokens plus the grounding fee — in ``llm_generations`` and the daily cost guard.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import structlog
from pydantic import BaseModel, BeforeValidator, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from brave.clients.parallel import ParallelSearch
from brave.core.models import EditorialClaim, EditorialDocument, Municipio, RioRecord
from brave.shared.exceptions import CostGuardError, ProviderBalanceError

logger = structlog.get_logger(__name__)

LANE = "editorial_validate"
PENDING = "pending_validation"
FAILED = "validation_failed"
MAX_SOURCES = 3
MAX_NOTE = 300

_NEVER = datetime(1970, 1, 1, tzinfo=UTC)  # "never tried" sorts before every real attempt
_STATUS = {"confirmado": "matched", "desatualizado": "outdated", "sem_evidencia": "unverified"}

_VERDICTS = """\
Para cada fato, dê um veredito:
- confirmado: uma fonte atual confirma o essencial do fato;
- desatualizado: uma fonte atual CONTRADIZ o fato de um jeito que muda a visita: diz que deixou \
de valer, que o acesso ou a estrutura é outra, que agora exige algo que o fato não diz \
(agendamento, ingresso pago) ou que o lugar fechou;
- sem_evidencia: a busca não trouxe fonte confiável sobre o fato. Sem evidência, não chute: \
nem confirme nem negue.
Desatualizado exige contradição. NÃO é contradição: uma fonte que traz detalhes a mais, que \
descreve o lugar com outras palavras, que não menciona parte do fato, ou uma diferença pequena \
de número (distância ou tempo até uns 20%, como 82 km em vez de 84 km). Se a fonte confirma o \
essencial e não contradiz o resto, é confirmado; se não fala do essencial, é sem_evidencia.
O conhecimento que você já tem não é evidência: só vale o que a busca trouxe. Fonte antiga \
(mais velha que a data do post) não confirma nada.
"""

_JSON_CONTRACT = """\
Responda SOMENTE com JSON, sem texto antes ou depois e sem cerca de código:
{"facts": [{"id": "<id recebido>", "verdict": "confirmado|desatualizado|sem_evidencia", \
"sources": ["https://..."], "note": "..."}]}
Um item por fato, com o mesmo id recebido. `sources`: até 3 URLs das páginas que sustentam o \
veredito (lista vazia em sem_evidencia). `note`: até 200 caracteres, em PT-BR, o que a fonte \
diz, com suas palavras.
"""

VALIDATE_SYSTEM = (
    "Você confere, na web, fatos de um guia de viagem que podem estar velhos. Use a busca do "
    "Google: procure fontes atuais e confiáveis (site oficial, prefeitura, órgão de turismo, "
    "reportagem recente) sobre o lugar. " + _VERDICTS + "\n" + _JSON_CONTRACT
)

VALIDATE_SYSTEM_SEARCH = (
    "Você confere fatos de um guia de viagem que podem estar velhos. Você recebe trechos de "
    "páginas da web encontrados por uma busca sobre o lugar, cada um com a URL e, quando houver, "
    "a data. Julgue SOMENTE pelos trechos: prefira fontes atuais e confiáveis (site oficial, "
    "prefeitura, órgão de turismo, reportagem recente); um trecho que fala de outro lugar não "
    "serve. "
    + _VERDICTS.replace("a busca não trouxe", "os trechos não trazem")
    + "\n`sources`: até 3 URLs, copiadas exatamente dos trechos que sustentam o veredito (lista "
    "vazia em sem_evidencia). `note`: até 200 caracteres, em PT-BR, o que o trecho diz, com "
    "suas palavras. Um item por fato, com o mesmo id recebido."
)

# Gemini responseSchema (OpenAPI subset) of ValidationAnswer: the reply is this JSON, no prose.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "facts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "verdict": {
                        "type": "STRING",
                        "enum": ["confirmado", "desatualizado", "sem_evidencia"],
                    },
                    "sources": {"type": "ARRAY", "items": {"type": "STRING"}, "maxItems": 3},
                    "note": {"type": "STRING"},
                },
                "required": ["id", "verdict", "sources", "note"],
                "propertyOrdering": ["id", "verdict", "sources", "note"],
            },
        }
    },
    "required": ["facts"],
}

# What the search looks for, per perishable column (the objective + the second query).
_TOPICS: dict[str, str] = {
    "how_to_get_there": "como chegar, acesso, estrada",
    "local_infrastructure": "estrutura, estacionamento, banheiro, restaurante",
    "tips": "dicas, horário de funcionamento, ingresso",
    "safety_alerts": "segurança, alerta, interdição",
    "accessibility": "acessibilidade",
    "connectivity_level": "sinal de celular, internet",
    "structure_level": "estrutura turística, hospedagem",
    "estimated_spending_range": "preços, custo",
    "flow_level": "movimento, lotação, fluxo de turistas",
}
MAX_EXCERPT_CHARS = 1500  # per result: ten results stay a few thousand tokens


# -- the answer -------------------------------------------------------------------------------


def _urls(v: Any) -> Any:
    if not isinstance(v, list):
        return v
    return [u for u in v if isinstance(u, str) and re.match(r"https?://\S+$", u.strip())][
        :MAX_SOURCES
    ]


def _clip(v: Any) -> Any:
    return v[:MAX_NOTE] if isinstance(v, str) else v


class FactVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    verdict: Literal["confirmado", "desatualizado", "sem_evidencia"]
    sources: Annotated[list[str], BeforeValidator(_urls)] = []
    note: Annotated[str, BeforeValidator(_clip)] = ""


class ValidationAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facts: list[FactVerdict]


def parse_answer(text: str) -> list[FactVerdict]:
    """The verdicts in the model's text (a code fence or a line of chatter around the JSON is
    tolerated). ``ValueError`` when it is not the contract."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    return ValidationAnswer.model_validate(json.loads(text[start : end + 1])).facts


def build_prompt(
    *,
    kind: str,
    name: str,
    post_name: str | None,
    municipio: str | None,
    uf: str,
    today: date,
    facts: list[tuple[str, str, date | None, Any]],
) -> str:
    """The user message: ``facts`` is ``(claim id, column, post date, value)``."""
    where = f"{municipio}/{uf}" if municipio else uf
    lines = [
        f"Hoje: {today.isoformat()}",
        f"Tipo: {kind}",
        f"Nome: {name}" + (f' (o post o chama de "{post_name}")' if post_name else ""),
        f"Local: {where}",
        "",
        "Fatos a conferir:",
    ]
    for fact_id, column, published, value in facts:
        text = "; ".join(map(str, value)) if isinstance(value, list) else str(value)
        lines.append(
            f"- id={fact_id} | coluna={column} | post={published.isoformat() if published else '?'}"
            f" | fato: {text}"
        )
    return "\n".join(lines)


def search_request(
    *, name: str, municipio: str | None, uf: str, columns: list[str]
) -> tuple[list[str], str]:
    """``(queries, objective)`` of the one Parallel search of an entity."""
    where = f"{municipio} {uf}" if municipio else uf
    topics = ", ".join(dict.fromkeys(_TOPICS.get(c, c) for c in columns))
    objective = (
        f"Informação atual e confiável sobre {name} ({where.replace(' ', '/')}), Brasil: {topics}."
    )
    return [f"{name} {where}", f"{name} {where} {topics}"], objective


def render_results(results: list[dict[str, Any]]) -> str:
    """The search results as prompt context: URL, date, title, then the excerpts."""
    blocks = []
    for r in results:
        head = f"URL: {r.get('url') or ''}"
        if r.get("publish_date"):
            head += f" | data: {r['publish_date']}"
        if r.get("title"):
            head += f" | título: {r['title']}"
        text = "\n".join(r.get("excerpts") or [])[:MAX_EXCERPT_CHARS]
        blocks.append(f"{head}\n{text}")
    return "\n\n".join(blocks)


def _host(u: str) -> str:
    return (urlsplit(u.strip()).hostname or "").removeprefix("www.")


def _url_key(u: str) -> str:
    return (
        u.strip().rstrip("/").removeprefix("https://").removeprefix("http://").removeprefix("www.")
    )


@dataclass
class _Answer:
    """One entity's verdicts plus how to tell a backed ``confirmado`` from the model's memory."""

    verdicts: list[FactVerdict]
    backed: Callable[[FactVerdict], bool]
    sources: dict[str, Any]  # stored on every claim next to the fact's own ``cited`` URLs
    unbacked_note: str


async def _ask_google(llm: Any, model: str, prompt: str) -> _Answer:
    answer = await llm.generate_grounded(
        [{"role": "user", "content": prompt}], model, system=VALIDATE_SYSTEM
    )
    grounded = bool(answer.sources)
    return _Answer(
        verdicts=parse_answer(answer.text),
        backed=lambda _f: grounded,
        sources={"grounding": list(answer.sources), "queries": list(answer.queries)},
        unbacked_note="sem páginas da busca do Google.",
    )


async def _ask_search(
    llm: Any, search: Any, model: str, prompt: str, *, name: str, municipio: str | None,
    uf: str, facts: list[tuple[str, str]], source_url: str,
) -> _Answer:  # fmt: skip
    """``facts`` is ``(claim id, column)``. Pages of the post's own site (``source_url``) are
    dropped: the guide cannot confirm itself."""
    columns = [col for _, col in facts]
    queries, objective = search_request(name=name, municipio=municipio, uf=uf, columns=columns)
    found: ParallelSearch = await search.search(queries, objective)
    own = _host(source_url)
    results = [r for r in found.results if r.get("url") and _host(str(r["url"])) != own]
    urls = [str(r["url"]) for r in results]
    keys = {_url_key(u) for u in urls}
    sources = {"search": urls, "queries": queries, "search_id": found.search_id}
    if not results:  # nothing to judge by: no paid generation for a sure sem_evidencia
        empty = "a busca não trouxe páginas."
        verdicts = [FactVerdict(id=i, verdict="sem_evidencia", note=empty) for i, _ in facts]
        return _Answer(verdicts, lambda _f: False, sources, empty)
    context = f"{prompt}\n\nTrechos da busca:\n\n{render_results(results)}"
    text = await llm.generate(
        [{"role": "user", "content": context}], model, system=VALIDATE_SYSTEM_SEARCH,
        response_schema=ANSWER_SCHEMA,
    )  # fmt: skip
    return _Answer(
        verdicts=parse_answer(text),
        backed=lambda f: any(_url_key(u) in keys for u in f.sources),
        sources=sources,
        unbacked_note="nenhuma fonte citada veio da busca.",
    )


# -- the run ----------------------------------------------------------------------------------


@dataclass
class ValidateResult:
    entities: int = 0  # entities answered (one paid call each)
    confirmed: int = 0
    outdated: int = 0
    unverified: int = 0
    failed: int = 0  # entities whose call or answer failed: one attempt spent on each claim
    unanswered: int = 0  # facts the model skipped: one attempt spent, they stay pending
    exhausted: int = 0  # claims that reached the attempt limit (validation_failed)
    ufs: set[str] = field(default_factory=set)  # UFs whose claims changed (to resolve)


def _pending(uf: str | None) -> tuple[Any, ...]:
    where: list[Any] = [EditorialClaim.status == PENDING, EditorialClaim.canonical_key.isnot(None)]
    if uf:
        where.append(EditorialDocument.uf == uf.upper())
    return tuple(where)


def pending_entities(session: Session, uf: str | None = None) -> list[tuple[str, str]]:
    """``(entity_kind, canonical_key)`` of every entity with a claim waiting for the check: the
    unit of one paid call. The least tried first, then the one whose last attempt is oldest (a
    never-tried entity before all), then a stable order — an entity that keeps failing cannot
    hold the whole batch."""
    attempts = func.max(EditorialClaim.validation_attempts)
    last = func.min(func.coalesce(EditorialClaim.last_attempt_at, _NEVER))
    rows = session.execute(
        select(EditorialClaim.entity_kind, EditorialClaim.canonical_key)
        .join(EditorialDocument, EditorialDocument.url == EditorialClaim.document_url)
        .where(*_pending(uf))
        .group_by(EditorialClaim.entity_kind, EditorialClaim.canonical_key)
        .order_by(attempts, last, EditorialClaim.entity_kind, EditorialClaim.canonical_key)
    ).all()
    return [(k, c) for k, c in rows]


def reopen_failed(session: Session, uf: str | None = None) -> int:
    """Put the ``validation_failed`` claims of ``uf`` (every UF when None) back in line for the
    check: status ``pending_validation``, attempts back to 0 (the last error stays). Returns how
    many. The explicit way out of the attempt limit (``brave editorial-retry``)."""
    where: list[Any] = [EditorialClaim.status == FAILED]
    if uf:
        where.append(EditorialDocument.uf == uf.upper())
    claims = session.scalars(
        select(EditorialClaim)
        .join(EditorialDocument, EditorialDocument.url == EditorialClaim.document_url)
        .where(*where)
    ).all()
    for c in claims:
        c.status, c.validation_attempts = PENDING, 0
    session.commit()
    logger.info("editorial_validate_reopened", uf=uf, claims=len(claims))
    return len(claims)


def reopen_validated(session: Session, uf: str) -> int:
    """Put every judged claim of ``uf`` back in line for a new check (a new search or prompt):
    status ``pending_validation``, attempts back to 0. The verdict, note, sources and date it had
    are moved to ``validation_sources["previous"]`` (oldest first) — nothing judged is lost.
    Returns how many. ``brave editorial-retry <UF> --revalidate``."""
    claims = session.scalars(
        select(EditorialClaim)
        .join(EditorialDocument, EditorialDocument.url == EditorialClaim.document_url)
        .where(
            EditorialDocument.uf == uf.upper(),
            EditorialClaim.superseded_at.is_(None),
            (EditorialClaim.validation_verdict.isnot(None)) | (EditorialClaim.status == FAILED),
        )
    ).all()
    for c in claims:
        old = dict(c.validation_sources or {})
        history = list(old.pop("previous", []))
        history.append(
            {
                "verdict": c.validation_verdict,
                "status": c.status,
                "note": c.validation_note,
                "sources": old,
                "validated_at": c.validated_at.isoformat() if c.validated_at else None,
                "last_error": c.last_validation_error,
            }
        )
        c.validation_sources = {"previous": history}
        c.validation_verdict = c.validation_note = c.validated_at = None
        c.status, c.validation_attempts = PENDING, 0
    session.commit()
    logger.info("editorial_validate_reopened_all", uf=uf, claims=len(claims))
    return len(claims)


def _spend_attempt(
    claims: list[EditorialClaim], error: str, *, max_attempts: int, now: datetime
) -> int:
    """One failed attempt on each of ``claims``; returns how many hit the limit."""
    exhausted = 0
    for c in claims:
        c.validation_attempts += 1
        c.last_validation_error, c.last_attempt_at = error[:500], now
        if c.validation_attempts >= max_attempts:
            c.status = FAILED
            exhausted += 1
    return exhausted


def _place(session: Session, kind: str, key: str, claim: EditorialClaim) -> tuple[str, str | None]:
    """The entity's name and município as Brave knows them (the post's wording as a fallback)."""
    if kind == "destino":
        nome = session.scalar(select(Municipio.nome).where(Municipio.ibge_code == key))
        return nome or claim.entity_name, nome or claim.municipio_text
    normalized = session.scalar(select(RioRecord.normalized).where(RioRecord.canonical_key == key))
    normalized = normalized if isinstance(normalized, dict) else {}
    name = normalized.get("name") or normalized.get("nome") or claim.entity_name
    municipio = normalized.get("municipio") or normalized.get("municipio_nome")
    return name, municipio or claim.municipio_text


async def validate_pending(
    session: Session,
    llm: Any,
    *,
    model: str,
    uf: str | None = None,
    limit: int,
    max_attempts: int,
    today: date | None = None,
    search: Any = None,
) -> ValidateResult:
    """Check up to ``limit`` entities. Each is committed as soon as it is answered (a paid call
    is never lost to a later failure). One failing entity is counted and skipped; the budget
    wall (``CostGuardError``) and a provider balance wall stop the run and propagate (not an
    attempt). Every other failure spends one attempt on the entity's claims; at ``max_attempts``
    a claim becomes ``validation_failed`` and is never paid for again by itself.

    ``search`` (a Parallel-shaped client) picks the Parallel path; None keeps Google grounding.
    With nothing found, every fact is ``sem_evidencia`` without a paid generation."""
    today = today or date.today()
    result = ValidateResult()
    for kind, key in pending_entities(session, uf)[:limit]:
        # SKIP LOCKED: a second runner never pays for the entity this one holds.
        rows = session.execute(
            select(EditorialClaim, EditorialDocument.uf)
            .join(EditorialDocument, EditorialDocument.url == EditorialClaim.document_url)
            .where(
                *_pending(uf),
                EditorialClaim.entity_kind == kind,
                EditorialClaim.canonical_key == key,
            )
            .order_by(EditorialClaim.created_at, EditorialClaim.id)
            .with_for_update(of=EditorialClaim, skip_locked=True)
        ).all()
        if not rows:
            continue
        claims = [c for c, _ in rows]
        name, municipio = _place(session, kind, key, claims[0])
        post_name = claims[0].entity_name if claims[0].entity_name != name else None
        prompt = build_prompt(
            kind=kind, name=name, post_name=post_name, municipio=municipio, uf=rows[0][1],
            today=today,
            facts=[(str(c.id), c.field, c.published_time, c.value) for c in claims],
        )  # fmt: skip
        try:
            if search is None:
                answer = await _ask_google(llm, model, prompt)
            else:
                answer = await _ask_search(
                    llm, search, model, prompt, name=name, municipio=municipio, uf=rows[0][1],
                    facts=[(str(c.id), c.field) for c in claims], source_url=claims[0].document_url,
                )  # fmt: skip
        except (CostGuardError, ProviderBalanceError):
            session.commit()  # the generation rows of the calls already paid for
            raise
        except Exception as exc:  # noqa: BLE001 — one entity must not stop the others
            # a billed call that came back unusable still has its cost row: commit, not rollback
            result.exhausted += _spend_attempt(
                claims, f"{type(exc).__name__}: {exc}", max_attempts=max_attempts,
                now=datetime.now(UTC),
            )  # fmt: skip
            session.commit()
            result.failed += 1
            logger.warning("editorial_validate_failed", kind=kind, key=key, error=str(exc)[:300])
            continue

        by_id = {v.id: v for v in answer.verdicts}
        now = datetime.now(UTC)
        result.entities += 1
        for c in claims:
            fact = by_id.get(str(c.id))
            if fact is None:
                result.unanswered += 1
                result.exhausted += _spend_attempt(
                    [c], "the model skipped this fact", max_attempts=max_attempts, now=now
                )
                continue
            verdict, note = fact.verdict, fact.note
            if verdict == "confirmado" and not answer.backed(fact):
                verdict, note = "sem_evidencia", f"{answer.unbacked_note} {note}".strip()[:MAX_NOTE]
            c.validation_verdict, c.validation_note, c.validated_at = verdict, note, now
            previous = (c.validation_sources or {}).get("previous")
            c.validation_sources = {
                "cited": fact.sources,
                **answer.sources,
                **({"previous": previous} if previous else {}),
            }
            c.status = _STATUS[verdict]
            if verdict == "confirmado":
                result.confirmed += 1
            elif verdict == "desatualizado":
                result.outdated += 1
            else:
                result.unverified += 1
            result.ufs.add(rows[0][1])
        session.commit()
    logger.info(
        "editorial_validated", uf=uf, entities=result.entities, confirmed=result.confirmed,
        outdated=result.outdated, unverified=result.unverified, failed=result.failed,
        unanswered=result.unanswered,
    )  # fmt: skip
    return result

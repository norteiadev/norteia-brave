"""Web check of the perishable editorial facts of old posts (Gemini 2.5 Flash + Google Search).

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

Gemini 2.5 cannot combine the search tool with a response schema, so the prompt asks for JSON in
the text and ``parse_answer`` validates it with Pydantic. A ``confirmado`` with no grounding page
behind it is demoted to ``sem_evidencia``: the model's own memory is not evidence.

The LLM client is injected (``generate_grounded``, from ``brave.clients.factory``): it books the
cost — tokens plus the grounding fee — in ``llm_generations`` and the daily cost guard.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

import structlog
from pydantic import BaseModel, BeforeValidator, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.core.models import EditorialClaim, EditorialDocument, Municipio, RioRecord
from brave.shared.exceptions import CostGuardError, ProviderBalanceError

logger = structlog.get_logger(__name__)

LANE = "editorial_validate"
PENDING = "pending_validation"
MAX_SOURCES = 3
MAX_NOTE = 300

_STATUS = {"confirmado": "matched", "desatualizado": "outdated", "sem_evidencia": "unverified"}

VALIDATE_SYSTEM = """\
Você confere, na web, fatos de um guia de viagem que podem estar velhos. Use a busca do \
Google: procure fontes atuais e confiáveis (site oficial, prefeitura, órgão de turismo, \
reportagem recente) sobre o lugar. Para cada fato, dê um veredito:
- confirmado: uma fonte atual confirma o fato como está escrito;
- desatualizado: uma fonte atual mostra que o fato mudou, deixou de valer ou o lugar fechou;
- sem_evidencia: a busca não trouxe fonte confiável sobre o fato. Sem evidência, não chute: \
nem confirme nem negue.
O conhecimento que você já tem não é evidência: só vale o que a busca trouxe. Fonte antiga \
(mais velha que a data do post) não confirma nada.

Responda SOMENTE com JSON, sem texto antes ou depois e sem cerca de código:
{"facts": [{"id": "<id recebido>", "verdict": "confirmado|desatualizado|sem_evidencia", \
"sources": ["https://..."], "note": "..."}]}
Um item por fato, com o mesmo id recebido. `sources`: até 3 URLs das páginas que sustentam o \
veredito (lista vazia em sem_evidencia). `note`: até 200 caracteres, em PT-BR, o que a fonte \
diz, com suas palavras.
"""


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


# -- the run ----------------------------------------------------------------------------------


@dataclass
class ValidateResult:
    entities: int = 0  # entities answered (one paid call each)
    confirmed: int = 0
    outdated: int = 0
    unverified: int = 0
    failed: int = 0  # entities whose call or answer failed: claims stay pending
    unanswered: int = 0  # facts the model skipped: they stay pending
    ufs: set[str] = field(default_factory=set)  # UFs whose claims changed (to resolve)


def _pending(uf: str | None) -> tuple[Any, ...]:
    where: list[Any] = [EditorialClaim.status == PENDING, EditorialClaim.canonical_key.isnot(None)]
    if uf:
        where.append(EditorialDocument.uf == uf.upper())
    return tuple(where)


def pending_entities(session: Session, uf: str | None = None) -> list[tuple[str, str]]:
    """``(entity_kind, canonical_key)`` of every entity with a claim waiting for the check,
    in a stable order: the unit of one paid call."""
    rows = session.execute(
        select(EditorialClaim.entity_kind, EditorialClaim.canonical_key)
        .join(EditorialDocument, EditorialDocument.url == EditorialClaim.document_url)
        .where(*_pending(uf))
        .distinct()
        .order_by(EditorialClaim.entity_kind, EditorialClaim.canonical_key)
    ).all()
    return [(k, c) for k, c in rows]


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
    today: date | None = None,
) -> ValidateResult:
    """Check up to ``limit`` entities. Each is committed as soon as it is answered (a paid call
    is never lost to a later failure). One failing entity is counted and skipped; the budget
    wall (``CostGuardError``) and a provider balance wall stop the run and propagate."""
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
            answer = await llm.generate_grounded(
                [{"role": "user", "content": prompt}], model, system=VALIDATE_SYSTEM
            )
            verdicts = parse_answer(answer.text)
        except (CostGuardError, ProviderBalanceError):
            session.commit()  # the generation rows of the calls already paid for
            raise
        except Exception as exc:  # noqa: BLE001 — one entity must not stop the others
            session.commit()  # a billed call that came back unusable still has its cost row
            result.failed += 1
            logger.warning("editorial_validate_failed", kind=kind, key=key, error=str(exc)[:300])
            continue

        by_id = {v.id: v for v in verdicts}
        now = datetime.now(UTC)
        result.entities += 1
        for c in claims:
            fact = by_id.get(str(c.id))
            if fact is None:
                result.unanswered += 1
                continue
            verdict, note = fact.verdict, fact.note
            if verdict == "confirmado" and not answer.sources:
                verdict, note = (
                    "sem_evidencia",
                    f"sem páginas da busca do Google. {note}"[:MAX_NOTE],
                )
            c.validation_verdict, c.validation_note, c.validated_at = verdict, note, now
            c.validation_sources = {
                "cited": fact.sources,
                "grounding": list(answer.sources),
                "queries": list(answer.queries),
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

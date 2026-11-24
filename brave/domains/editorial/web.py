"""Editorial source "web": the pushed attraction columns written from public pages.

For each Mar attraction of a UF: one Parallel ``/v1/search`` (the excerpts of public pages —
city hall, state tourism, environmental agency, press), then Gemini writes ``how_to_get_there``,
``tips``, ``local_infrastructure``, ``curiosities`` and ``accessibility`` from those excerpts
ONLY, in a response schema, citing the pages it used. Deterministic filters then drop a weak
field (and only that field): a source the search did not return, filler ("não há informação",
"recomenda-se confirmar"), accessibility without a concrete fact, a too-short text, a copy of
an excerpt; ``tips`` loses prices and opening hours (``schema.strip_volatile_tips``).

Storage reuses the editorial lane, nothing new in Mar:
  - one ``editorial_documents`` row per attraction, ``web://parallel/<canonical_key>``, source
    ``web_parallel``, ``published_time`` = the search date; each run is a new
    ``editorial_document_versions`` row holding the WHOLE Parallel answer (a rewrite with another
    model never pays the search again);
  - one claim per column, already pointing at the attraction (``match_method = "direct"``: the
    resolver does not re-match it), status ``matched``; a dropped column is a ``held_web`` claim
    with the reason in ``evidence``. A new run supersedes the claims of the run before. Nothing is
    deleted.

The resolver then writes ``normalized["editorial"]`` and the Mar outbox pushes it, as for any
editorial claim.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from brave.core.models import (
    EditorialClaim,
    EditorialDocument,
    EditorialDocumentVersion,
    MarRecord,
    RioRecord,
)
from brave.domains.editorial.schema import is_copied, stability_of, strip_volatile_tips
from brave.domains.editorial.validate import _host, _url_key, render_results
from brave.shared.exceptions import CostGuardError, ProviderBalanceError

logger = structlog.get_logger(__name__)

SOURCE = "web_parallel"
LANE = "editorial_web"
URL_PREFIX = "web://parallel/"
FIELDS: tuple[str, ...] = (
    "how_to_get_there",
    "tips",
    "local_infrastructure",
    "curiosities",
    "accessibility",
)
MAX_SOURCES = 3
MIN_CHARS = 40

# Results from these hosts never reach the prompt: the blog that blocks crawlers (not used as a
# source), and listings / reviews / social pages (low signal, user content).
EXCLUDED_HOSTS: tuple[str, ...] = (
    "viagensecaminhos.com",
    "google.com",
    "google.com.br",
    "goo.gl",
    "tripadvisor",
    "booking.com",
    "facebook.com",
    "instagram.com",
)

LABEL: dict[str, str] = {
    "how_to_get_there": (
        "como chegar: rodovia ou rua de acesso, ponto de referência, distância de uma cidade, "
        "transporte público. NÃO vale: só dizer em que cidade/bairro fica."
    ),
    "tips": (
        "dicas acionáveis para o visitante: o que levar, melhor horário ou época, cuidados, regras "
        "da visita (agendamento, proibições). NÃO vale: descrição do lugar, elogio, história, "
        "preço, horário de funcionamento."
    ),
    "local_infrastructure": (
        "estrutura para o visitante no próprio local: estacionamento, banheiro, "
        "quiosque/restaurante, trilha sinalizada, centro de visitantes, guarda-vidas. NÃO vale: "
        "estrutura da cidade, da paróquia ou da administração."
    ),
    "curiosities": (
        "curiosidades: história, origem do nome, fatos marcantes ou pouco conhecidos do lugar. "
        "NÃO vale: descrição genérica da paisagem."
    ),
    "accessibility": (
        "acessibilidade para pessoa com deficiência ou mobilidade reduzida: rampa, piso tátil, "
        "banheiro adaptado, cadeira anfíbia, trilha acessível — só quando um trecho fala disso "
        "explicitamente."
    ),
}

WRITE_SYSTEM = (
    "Você escreve campos de um guia de atrativos turísticos do Brasil a partir de trechos de "
    "páginas da web (cada um com URL e, quando houver, data). Use SOMENTE fatos concretos que os "
    "trechos dizem; o que você já sabe não conta. Prefira fontes oficiais e recentes (prefeitura, "
    "governo do estado, órgão ambiental, reportagem). Escreva em PT-BR, em terceira pessoa, frases "
    "curtas e objetivas, até 500 caracteres por campo. REESCREVA com suas palavras: os trechos são "
    "de terceiros, então nunca repita 8 ou mais palavras seguidas de um trecho — mude a ordem, "
    "resuma, troque as palavras; nomes próprios e endereços podem ficar como estão. "
    'Campo sem fato concreto nos trechos fica VAZIO (value "" e sources []). Nunca escreva que '
    "falta informação, nunca recomende confirmar ou consultar, nunca preencha com descrição "
    "genérica: é melhor um campo vazio do que um campo fraco. Em sources, copie exatamente as URLs "
    "dos trechos usados (até 3). Não invente números."
)

_FIELD_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "value": {"type": "STRING"},
        "sources": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["value", "sources"],
}
WRITE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {f: _FIELD_SCHEMA for f in FIELDS},
    "required": list(FIELDS),
}

_FILLER = re.compile(
    r"n[ãa]o (h[áa]|foram|foi|existem|s[ãa]o|traz|trazem|especifica|menciona)[^.]{0,40}"
    r"(informa|men[çc][ãa]o|detalhe|dado)"
    r"|recomenda(-se| se|do)? (confirmar|verificar|consultar)"
    r"|[ée] (recomendado|importante) (confirmar|verificar)"
    r"|consulte|verifique (antes|com)|sem informa[çc][õo]es|n[ãa]o (foi|[ée]) poss[íi]vel",
    re.I,
)
_A11Y = re.compile(
    r"rampa|piso t[áa]til|cadeira|cadeirante|adaptad|acess[íi]ve|pcd|defici[êe]ncia"
    r"|mobilidade reduzida|libras|braile",
    re.I,
)


# -- pure: the request, the filters -----------------------------------------------------------


def doc_url(canonical_key: str) -> str:
    return f"{URL_PREFIX}{canonical_key}"


def search_request(*, name: str, municipio: str | None, uf: str) -> tuple[list[str], str]:
    """``(queries, objective)`` of the one Parallel search of an attraction."""
    where = f"{municipio} {uf}" if municipio else uf
    queries = [f"{name} {where}", f"{name} {where} como chegar estrutura dicas história"]
    objective = (
        f"Informação atual e confiável sobre o atrativo turístico {name} "
        f"({where.replace(' ', '/')}), Brasil: como chegar, infraestrutura, dicas de visita, "
        "curiosidades e história, acessibilidade."
    )
    return queries, objective


def usable_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The results that may reach the prompt (an URL, not an excluded host)."""
    out = []
    for r in results:
        url = str(r.get("url") or "")
        host = _host(url) if url else ""
        if host and not any(x in host for x in EXCLUDED_HOSTS):
            out.append(r)
    return out


def drop_reason(column: str, value: str, sources: list[str], excerpts: str) -> str | None:
    """Why a written value must not be used, or None to keep it."""
    if not sources:
        return "sem fonte da busca"
    if len(value) < MIN_CHARS:
        return "curto"
    if _FILLER.search(value):
        return "enchimento"
    if column == "accessibility" and not _A11Y.search(value):
        return "sem fato de acessibilidade"
    if is_copied(value, excerpts):
        return "cópia do trecho"
    return None


def build_prompt(name: str, municipio: str | None, uf: str, results: list[dict[str, Any]]) -> str:
    where = f"{municipio}/{uf}" if municipio else uf
    fields = "\n".join(f"- {f}: {LABEL[f]}" for f in FIELDS)
    return (
        f"Atrativo: {name}\nLocal: {where}\n\nCampos:\n{fields}\n\n"
        f"Trechos:\n\n{render_results(results)}"
    )


@dataclass(frozen=True)
class WebField:
    column: str
    value: str
    sources: list[str]
    dropped: str | None  # why it is held (``held_web``), None when it goes on as ``matched``


def read_answer(text: str, results: list[dict[str, Any]]) -> list[WebField]:
    """The written columns, each filtered. ``ValueError`` when the JSON is not the contract."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the answer is not a JSON object")
    keys = {_url_key(str(r["url"])) for r in results}
    excerpts = "\n".join("\n".join(r.get("excerpts") or []) for r in results)
    out: list[WebField] = []
    for column in FIELDS:
        item = data.get(column) if isinstance(data.get(column), dict) else {}
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        if column == "tips":
            value = strip_volatile_tips(value).strip()
            if not value:
                continue
        cited = [str(u) for u in item.get("sources") or [] if isinstance(u, str)]
        sources = [u for u in cited if _url_key(u) in keys][:MAX_SOURCES]
        out.append(WebField(column, value, sources, drop_reason(column, value, sources, excerpts)))
    return out


# -- what to fill ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    canonical_key: str
    uf: str
    name: str
    municipio: str | None


def pending_attractions(
    session: Session, uf: str, *, refresh_days: int, limit: int, now: datetime | None = None
) -> list[Target]:
    """Active Mar attractions of ``uf`` with no web document, or one older than
    ``refresh_days``: never-filled first, then the oldest fill."""
    now = now or datetime.now(UTC)
    n = RioRecord.normalized
    fetched = (
        select(EditorialDocument.fetched_at)
        .where(EditorialDocument.url == func.concat(URL_PREFIX, RioRecord.canonical_key))
        .correlate(RioRecord)
        .scalar_subquery()
    )
    rows = session.execute(
        select(
            RioRecord.canonical_key,
            RioRecord.uf,
            func.coalesce(n["name"].as_string(), n["nome"].as_string()),
            func.coalesce(n["municipio"].as_string(), n["municipio_nome"].as_string()),
            fetched,
        )
        .join(MarRecord, MarRecord.rio_id == RioRecord.id)
        .where(
            MarRecord.superseded_by_id.is_(None),
            MarRecord.entity_type == "attraction",
            RioRecord.uf == uf.upper(),
            RioRecord.canonical_key.isnot(None),
        )
        .order_by(fetched.asc().nulls_first(), RioRecord.canonical_key)
    ).all()
    cutoff = now - timedelta(days=refresh_days)
    out: list[Target] = []
    seen: set[str] = set()
    for key, ruf, name, municipio, last in rows:
        if not name or key in seen or (last is not None and last > cutoff):
            continue
        seen.add(key)
        out.append(Target(key, ruf, name, municipio))
        if len(out) >= limit:
            break
    return out


# -- the run -----------------------------------------------------------------------------------


@dataclass
class WebFillResult:
    attractions: int = 0  # searched (one paid search each)
    written: int = 0  # columns kept (matched claims)
    held: int = 0  # columns dropped by a filter (held_web claims)
    empty: int = 0  # attractions whose search brought no usable page (no generation paid)
    failed: int = 0  # attractions whose search or answer failed (nothing stored)
    ufs: set[str] = field(default_factory=set)


def _store(
    session: Session,
    t: Target,
    *,
    raw: dict[str, Any],
    body: str,
    today: date,
    fields: list[WebField],
) -> None:
    """The document (+ a new version) and its claims; the previous run's claims are superseded."""
    url = doc_url(t.canonical_key)
    blob = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(blob.encode()).hexdigest()
    now = datetime.now(UTC)
    doc = session.get(EditorialDocument, url)
    if doc is None:
        doc = EditorialDocument(
            url=url, source=SOURCE, uf=t.uf, published_time=today, content_hash=digest,
            body_text=body, kml_points=[], fetched_at=now, extracted_at=now,
        )  # fmt: skip
        session.add(doc)
        session.flush()
    version = EditorialDocumentVersion(
        id=uuid.uuid4(), document_url=url, content_hash=digest, html=blob, kml_raw={},
        body_text=body, published_time=today, fetched_at=now,
    )  # fmt: skip
    session.add(version)
    session.flush()
    doc.content_hash, doc.body_text, doc.published_time = digest, body, today
    doc.fetched_at, doc.extracted_at, doc.error, doc.uf = now, now, None, t.uf
    doc.current_version_id = version.id
    session.execute(
        update(EditorialClaim)
        .where(EditorialClaim.document_url == url, EditorialClaim.status != "superseded")
        .values(
            superseded_from_status=EditorialClaim.status,
            status="superseded",
            superseded_at=now,
        )
    )
    for f in fields:
        session.add(
            EditorialClaim(
                id=uuid.uuid4(),
                document_url=url,
                version_id=version.id,
                source=SOURCE,
                entity_kind="atrativo",
                entity_name=t.name,
                municipio_text=t.municipio,
                canonical_key=t.canonical_key,
                match_method="direct",
                field=f.column,
                value=f.value,
                stability=stability_of(f.column),
                evidence=(
                    " ".join(f.sources)
                    if f.dropped is None
                    else f"descartado: {f.dropped} | {' '.join(f.sources)}".strip(" |")
                ),
                published_time=today,
                status="held_web" if f.dropped else "matched",
            )  # fmt: skip
        )


async def fill(
    session: Session,
    llm: Any,
    search: Any,
    *,
    model: str,
    targets: list[Target],
    today: date | None = None,
) -> WebFillResult:
    """Search + write + store each target, committed one by one (a paid call is never lost to a
    later failure). The budget wall and a provider balance wall stop the run and propagate; any
    other failure skips that attraction (nothing stored: it is picked again next run)."""
    today = today or date.today()
    result = WebFillResult()
    for t in targets:
        queries, objective = search_request(name=t.name, municipio=t.municipio, uf=t.uf)
        try:
            found = await search.search(queries, objective)
            results = usable_results(found.results)
            raw = {"search_id": found.search_id, "queries": queries, "objective": objective,
                   "results": found.results}  # fmt: skip
            body = render_results(results)
            fields: list[WebField] = []
            if results:
                text = await llm.generate(
                    [{"role": "user", "content": build_prompt(t.name, t.municipio, t.uf, results)}],
                    model, system=WRITE_SYSTEM, response_schema=WRITE_SCHEMA,
                )  # fmt: skip
                fields = read_answer(text, results)
            else:
                result.empty += 1
        except (CostGuardError, ProviderBalanceError):
            session.commit()  # the cost rows of the calls already paid for
            raise
        except Exception as exc:  # noqa: BLE001 — one attraction must not stop the others
            session.commit()
            result.failed += 1
            logger.warning("editorial_web_failed", key=t.canonical_key, error=str(exc)[:300])
            continue
        _store(session, t, raw=raw, body=body, today=today, fields=fields)
        session.commit()
        result.attractions += 1
        result.written += sum(1 for f in fields if f.dropped is None)
        result.held += sum(1 for f in fields if f.dropped is not None)
        result.ufs.add(t.uf)
    logger.info(
        "editorial_web_filled", attractions=result.attractions, written=result.written,
        held=result.held, empty=result.empty, failed=result.failed,
    )  # fmt: skip
    return result

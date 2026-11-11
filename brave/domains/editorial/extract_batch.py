"""LLM extraction of the editorial columns over the Message Batches API (50% off tokens).

Same lifecycle and the same money rules as ``brave.domains.places.copy_batch`` (a domain may
not import its sibling, so the small helpers are restated here):

  - state lives in COLUMNS of ``editorial_documents`` (``batch_id`` / ``batch_submitted_at``
    / ``extracted_at`` / ``error``), never in a JSONB stamp or a Redis set;
  - RESERVE budget, then CLAIM the posts (placeholder, committed), then ``batches.create``,
    then stamp the real id: a redelivered task selects nothing and bills nothing;
  - collect applies each result at most once (the stamp is the applied-marker, cleared in
    the same transaction as the writes) and settles the reservation against the real bill;
  - a reaper frees claims whose batch is genuinely gone.

One request per post; the answer is a forced tool call validated by ``schema.parse_extraction``
(entity by entity: one bad entity does not cost the post). Claims of a re-extracted post are
replaced wholesale.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from anthropic import APIConnectionError, APIStatusError, APITimeoutError, NotFoundError
from sqlalchemy import Select, delete, func, select

from brave.clients.llm import (
    _CACHE_READ_MULTIPLIER,
    _CACHE_WRITE_MULTIPLIER,
    _usage_int,
    anthropic_prices,
    anthropic_thinking,
)
from brave.core.models import EditorialClaim, EditorialDocument, LLMGeneration
from brave.domains.editorial.schema import Extraction, claim_drafts, parse_extraction
from brave.observability.cost_guard import reconcile_spend, record_spend

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from brave.config.settings import LLMConfig

logger = structlog.get_logger(__name__)

LANE = "editorial_extract_batch"
TOOL_NAME = "registrar_extracao"

# batches.create commits spend irrevocably: same placeholders as copy_batch.
CLAIM_BATCH_ID = "claim_pending_create"
AMBIGUOUS_BATCH_ID = "claim_ambiguous_create"
_BATCH_REAP_AFTER = timedelta(hours=25)

_BATCH_TOKEN_DISCOUNT = 0.5
# Batch-rate USD reserved per post. Measured shape (ES spike): a post body is ~3k tokens;
# with the system prompt + tool schema ~6k in and up to ~4k out ≈ $0.026 at batch rates on
# claude-sonnet-5-5 ($2/$10 per MTok). Rounded UP on purpose: under-reserving is the
# dangerous direction (it lets a batch commit past the daily budget).
EST_USD_PER_POST = 0.06
# 50 posts reserve $3.00, well inside a default-budget day.
EXTRACT_BATCH_SIZE = 50
_MAX_TOKENS = 16000

EXTRACT_SYSTEM = """\
Você lê posts de um blog de viagem (texto limpo) e extrai fatos para as colunas editoriais \
da norteia-api. O autor visitou os lugares; o blog é a fonte. Não use web, não invente, não \
infira: só o que o texto do post afirma. Responda SEMPRE chamando a ferramenta \
`registrar_extracao`, com uma entidade por lugar citado.

## Entidades
- kind `destino` = o município do post (nome do município em `name` e em `municipio`).
- kind `atrativo` = ponto turístico concreto (praia, pedra, gruta, igreja, parque, trilha, \
mirante, museu). Bairro ou região não é atrativo. `municipio` = onde fica, ou null se o post \
não deixar claro (atrativo de divisa: só o que o texto diz).
- Posts-lista ("10 pedras", "18 cidades"): extraia cada item.
- Entidade sem nenhuma coluna: liste mesmo assim com `fields` vazio.

## Colunas (omita a coluna se o post não traz o fato)
destino: best_time_to_visit, seasonality (LISTA de textos curtos), how_to_get_there, \
recommended_duration (até 100 caracteres), tips, safety_alerts, estimated_spending_range \
(até 100), predominant_audience_profile (até 200), pace_profile (só `lento`, `equilibrado` \
ou `intenso`), connectivity_level (até 50).
atrativo: free_entry (true/false), price, opening_hours, how_to_get_there, tips, \
local_infrastructure, curiosities, accessibility, safety_alerts, estimated_capacity, \
conservation_level.

Cada coluna é `{"value": ..., "evidence": ...}`.

## Regras
1. `value` nunca é cópia: reescreva o fato com suas palavras, em PT-BR, conciso. A cópia só \
vai em `evidence`: trecho literal curto do post (no máximo 20 palavras), apenas para auditoria.
2. Ignore comentários de leitores, blocos de afiliados (Booking, RentCars, Civitatis, seguro, \
chip), listas de hotéis e pousadas, links "leia também".
3. `curiosities` = fato histórico ou natural verificável (datas, tombamento, origem do nome, \
altitude, recorde). Opinião ("lindo", "imperdível") não é curiosidade.
4. `local_infrastructure` = estrutura física citada (banheiro, estacionamento, quiosque, \
restaurante, centro de visitantes). "Pouca infraestrutura" também conta.
5. `tips` = dicas de experiência. Não ponha preço em R$, horário de funcionamento nem limite de \
pessoas em `tips`: isso vai em price / opening_hours / estimated_capacity.
6. O post pode ter mais de uma versão de um número (ex.: extensão): use o do corpo do texto.
"""


def _strip(node: Any) -> Any:
    """The JSON schema without the keys the tool schema does not need (titles, OpenAPI
    ``discriminator``): they only cost input tokens."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in {"title", "discriminator"}}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def extraction_tool() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": "Registra as entidades (destinos e atrativos) e as colunas extraídas do post.",
        "input_schema": _strip(Extraction.model_json_schema()),
    }


def custom_id(url: str) -> str:
    """Anthropic wants ^[a-zA-Z0-9_-]{1,64}$ — a URL does not fit, its sha1 does."""
    return hashlib.sha1(url.encode()).hexdigest()  # noqa: S324 — an id, not a secret


def build_request(doc: EditorialDocument, model: str) -> dict[str, Any]:
    user = f"URL: {doc.url}\npublished_time: {doc.published_time or '?'}\n\n{doc.body_text}"
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "system": EXTRACT_SYSTEM,
        "messages": [{"role": "user", "content": user}],
        "tools": [extraction_tool()],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
    }
    thinking = anthropic_thinking(model)
    if thinking is not None:
        params["thinking"] = thinking
    return {"custom_id": custom_id(doc.url), "params": params}


def pending_filter(uf: str | None = None) -> tuple[Any, ...]:
    """Posts that want extraction and have none in flight."""
    where: list[Any] = [
        EditorialDocument.extracted_at.is_(None),
        EditorialDocument.error.is_(None),
        EditorialDocument.batch_id.is_(None),
        EditorialDocument.body_text != "",
    ]
    if uf:
        where.append(EditorialDocument.uf == uf.upper())
    return tuple(where)


def pending_posts(session: Session, uf: str | None = None) -> int:
    return (
        session.scalar(
            select(func.count()).select_from(EditorialDocument).where(*pending_filter(uf))
        )
        or 0
    )


def _candidates(limit: int, uf: str | None) -> Select[Any]:
    return (
        select(EditorialDocument)
        .where(*pending_filter(uf))
        .order_by(EditorialDocument.fetched_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


def _request_was_never_billed(exc: BaseException) -> bool:
    """True only when ``batches.create`` PROVABLY never reached the model (see copy_batch)."""
    if isinstance(exc, APITimeoutError):
        return False
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code < 500
    return False


def submit_batch(
    session: Session,
    client: Any,
    *,
    redis_client: Any,
    llm_config: LLMConfig,
    model: str,
    uf: str | None = None,
    limit: int = EXTRACT_BATCH_SIZE,
) -> str | None:
    """Submit one extraction batch; returns its id, or None when nothing was sent.

    Order (see copy_batch.submit_batch for the why): reserve a batch's worth of budget and
    size the batch off what the counter returns → hand back the unused part → SELECT FOR
    UPDATE SKIP LOCKED → write the claim and COMMIT → ``batches.create`` → stamp the id.
    """
    now = datetime.now(UTC)
    reserved = limit * EST_USD_PER_POST
    booked_by_others = record_spend(redis_client, reserved) - reserved
    remaining = max(0.0, llm_config.usd_daily_budget - booked_by_others)
    sized = min(limit, int(remaining // EST_USD_PER_POST))

    docs = list(session.scalars(_candidates(sized, uf))) if sized > 0 else []
    requests = [build_request(d, model) for d in docs]
    used = len(requests) * EST_USD_PER_POST
    if used < reserved:
        reconcile_spend(redis_client, reserved - used, 0.0, reserved_at=now)
    if sized <= 0:
        logger.warning("editorial_submit_budget_exhausted", remaining_usd=round(remaining, 4))
        return None
    if not docs:
        session.rollback()
        return None

    for d in docs:
        d.batch_id, d.batch_submitted_at = CLAIM_BATCH_ID, now
    session.commit()

    try:
        batch = client.messages.batches.create(requests=requests)
    except Exception as exc:
        if _request_was_never_billed(exc):
            for d in docs:
                d.batch_id = d.batch_submitted_at = None
            session.commit()
            reconcile_spend(redis_client, used, 0.0, reserved_at=now)
            logger.warning("editorial_submit_refunded", requests=len(requests), error=str(exc))
        else:
            # Anthropic may hold a real, billing batch: neither refund nor free the posts.
            for d in docs:
                d.batch_id = AMBIGUOUS_BATCH_ID
            session.commit()
            logger.error(
                "editorial_submit_ambiguous_left_claimed",
                requests=len(requests),
                submitted_at=now.isoformat(),
                action="list Anthropic batches near submitted_at; collect or cancel by hand, "
                "then clear editorial_documents.batch_id",
                error=str(exc),
            )
        raise

    for d in docs:
        d.batch_id = batch.id
    session.commit()
    logger.info("editorial_batch_submitted", batch_id=batch.id, requests=len(requests))
    return batch.id


def inflight_batch_ids(session: Session) -> list[str]:
    rows = session.scalars(
        select(EditorialDocument.batch_id).where(EditorialDocument.batch_id.isnot(None)).distinct()
    ).all()
    return [b for b in rows if b and b not in (CLAIM_BATCH_ID, AMBIGUOUS_BATCH_ID)]


def _batch_is_gone(client: Any, batch_id: str) -> bool:
    """True only when Anthropic says 404; any other failure reads as "still there"."""
    try:
        client.messages.batches.retrieve(batch_id)
    except NotFoundError:
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("editorial_reap_probe_failed", batch_id=batch_id, error=str(exc))
    return False


def reap_stale_claims(session: Session, client: Any, *, now: datetime | None = None) -> int:
    """Free the posts of claims whose batch is genuinely gone. Returns how many.

    A claim placeholder (crash between claim and create) is freed by time alone; a real id
    only once ``retrieve`` 404s (and never with ``client=None``); the ambiguous marker never:
    freeing it would re-pay for work Anthropic may already have done.
    """
    cutoff = (now or datetime.now(UTC)) - _BATCH_REAP_AFTER
    stale = session.scalars(
        select(EditorialDocument).where(
            EditorialDocument.batch_id.isnot(None), EditorialDocument.batch_submitted_at < cutoff
        )
    ).all()
    probed: dict[str, bool] = {}
    freed = 0
    for d in stale:
        bid = d.batch_id or ""
        if bid == AMBIGUOUS_BATCH_ID:
            logger.error("editorial_ambiguous_claim_needs_operator", url=d.url)
            continue
        if bid != CLAIM_BATCH_ID:
            if client is None:
                continue
            if bid not in probed:
                probed[bid] = _batch_is_gone(client, bid)
            if not probed[bid]:
                continue
        d.batch_id = d.batch_submitted_at = None
        freed += 1
    if freed:
        session.commit()
        logger.warning("editorial_reaped_stale_claims", freed=freed)
    return freed


def _record_generation(session: Session, message: Any, model: str) -> float:
    """Price one batched answer at BATCH rates, persist the llm_generations row, return USD."""
    usage = getattr(message, "usage", None)
    prompt = _usage_int(usage, "input_tokens")
    completion = _usage_int(usage, "output_tokens")
    cache_read = _usage_int(usage, "cache_read_input_tokens")
    cache_write = _usage_int(usage, "cache_creation_input_tokens")
    price_in, price_out = anthropic_prices(model)
    usd = (
        (
            prompt * price_in
            + cache_write * price_in * _CACHE_WRITE_MULTIPLIER
            + cache_read * price_in * _CACHE_READ_MULTIPLIER
            + completion * price_out
        )
        * _BATCH_TOKEN_DISCOUNT
    ) / 1_000_000
    session.add(
        LLMGeneration(
            id=uuid.uuid4(),
            lane=LANE,
            model_slug=model,
            resolved_provider=model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            usd_cost=usd,
        )
    )
    return usd


def _tool_input(message: Any) -> Any:
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", "") == TOOL_NAME:
            return block.input
    return None


def apply_result(
    session: Session,
    doc: EditorialDocument,
    result: Any,
    *,
    batch_id: str,
    model: str,
    redis_client: Any = None,
) -> bool:
    """Apply one batch result line to ``doc``. Returns True when it wrote claims.

    IDEMPOTENT: the writes happen only while the post still carries ``batch_id``, and the
    stamp is cleared in the same transaction. Commits before touching Redis (the settle is
    not transactional). ``errored`` burns the post (``error`` set, never retried by itself —
    a paid retry loop is the wrong default); ``expired``/``canceled`` are unbilled and free
    the post for the next submit.
    """
    if doc.batch_id != batch_id:
        return False
    reserved_at = doc.batch_submitted_at
    rtype = getattr(result, "type", "errored")
    usd = 0.0
    extracted = False

    if rtype == "succeeded":
        message = result.message
        usd = _record_generation(session, message, model)
        entities, errors = [], []
        if getattr(message, "stop_reason", None) == "max_tokens":
            doc.error = "max_tokens: the extraction was cut off"
        elif (raw := _tool_input(message)) is None:
            doc.error = "the model did not call the extraction tool"
        else:
            entities, errors = parse_extraction(raw)
            if errors and not entities:
                doc.error = "; ".join(errors)[:1000]
            else:
                if errors:
                    logger.warning("editorial_entities_dropped", url=doc.url, errors=errors)
                session.execute(
                    delete(EditorialClaim).where(EditorialClaim.document_url == doc.url)
                )
                for entity in entities:
                    for draft in claim_drafts(entity):
                        session.add(
                            EditorialClaim(
                                id=uuid.uuid4(),
                                document_url=doc.url,
                                source=doc.source,
                                entity_kind=entity.kind,
                                entity_name=entity.name,
                                municipio_text=entity.municipio,
                                field=draft.field,
                                value=draft.value,
                                stability=draft.stability,
                                evidence=draft.evidence,
                                published_time=doc.published_time,
                                status="held_volatile"
                                if draft.stability == "volatil"
                                else "unmatched",
                            )
                        )
                doc.extracted_at, doc.error, extracted = datetime.now(UTC), None, True
    elif rtype == "errored":
        doc.error = "the batch request errored"
        logger.warning("editorial_result_errored", url=doc.url)
    else:  # expired / canceled: not billed, eligible again
        # ponytail: unbounded re-submit of a post that keeps expiring; bound it with a counter
        # column the day it shows up (an expiry costs nothing, only latency).
        logger.info("editorial_result_unbilled", url=doc.url, result_type=rtype)

    doc.batch_id = doc.batch_submitted_at = None
    session.commit()
    if redis_client is not None:
        reconcile_spend(redis_client, EST_USD_PER_POST, usd, reserved_at=reserved_at)
    return extracted


def collect_batches(
    session: Session,
    client: Any,
    *,
    redis_client: Any = None,
    model: str,
    now: datetime | None = None,
) -> set[str]:
    """Reap stale claims, then apply every ENDED batch. Returns the UFs that got claims
    (the ones to resolve). One un-retrievable batch never stops the others."""
    reap_stale_claims(session, client, now=now)
    ufs: set[str] = set()
    for batch_id in inflight_batch_ids(session):
        try:
            batch = client.messages.batches.retrieve(batch_id)
            if getattr(batch, "processing_status", None) != "ended":
                continue
            urls = {
                custom_id(u): u
                for u in session.scalars(
                    select(EditorialDocument.url).where(EditorialDocument.batch_id == batch_id)
                )
            }
            for entry in client.messages.batches.results(batch_id):
                url = urls.get(entry.custom_id)
                if url is None:  # not ours, or already applied
                    continue
                # FOR UPDATE + populate_existing: two overlapping collects must not both see
                # the stamp (duplicate cost rows, a double refund).
                doc = session.get(
                    EditorialDocument, url, with_for_update=True, populate_existing=True
                )
                if doc is None:
                    continue
                if apply_result(
                    session, doc, entry.result, batch_id=batch_id, model=model,
                    redis_client=redis_client,
                ):  # fmt: skip
                    ufs.add(doc.uf)
                session.commit()
            logger.info("editorial_batch_collected", batch_id=batch_id)
        except Exception as exc:  # noqa: BLE001 — one bad batch must not stop the others
            session.rollback()
            logger.warning("editorial_collect_failed", batch_id=batch_id, error=str(exc))
    return ufs

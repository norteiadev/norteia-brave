"""Deferred (Message Batches API) variant of the TourismCopywriter description step.

Same prompt, same tool, same guards as :mod:`brave.lanes.atrativos.copywriter` — only the
transport differs: requests are submitted to Anthropic's Message Batches API (50% off input
and output tokens, results within 24h) instead of being awaited inline in the sweep. The
web_search fee is NOT discounted ("priced the same as those in regular Messages API
requests"), so the realized saving on a description is ~35%, not 50%.

Shape of the change (the reason this file is small): PlacesEnrichmentAgent already has a
working no-copywriter path. With ``description_enabled=False`` the whole description block is
skipped and everything else (Places writes, audit, route_by_score, record_event) runs
unchanged. Batch mode is therefore "flip that flag off at the two construction sites in
brave/tasks/pipeline.py, and perform the three deferred writes here later":

  - ``descricao_editorial``  ← the generated prose (em-dash guard applied);
  - ``completude_value``     ← lifted to the with-description degrau;
  - ``descricao_attempts``   ← incremented ONLY when Anthropic actually billed us.

Everything the copywriter owns (system prompt, web_search tool dict, context builder,
dash-strip) is imported, never duplicated — the inline and batched paths must stay
byte-identical or the two spends are not comparable.

KNOWN GAP — a late description does NOT reach norteia-api on its own.
``collect_batches`` ends exactly where the inline path ends: ``route_by_score`` and nothing
more. It does NOT push. In this project the DLQ/steward gate is the canonical gate, and the
only push dispatchers are the steward ``/approve`` route and the WhatsApp finalize gate;
auto-pushing here would publish an attraction to norteia-api with zero human validation —
including one that the newly-written description just lifted over the score threshold.
The consequence the operator must know: a record ALREADY ACTIVE in Mar whose description
lands afterwards keeps ``description: null`` in norteia-api until something re-pushes it
(a steward action, or a future deliberate re-push job). This is a known gap, not a bug to be
papered over with an automatic push.

State lives in COLUMNS, not JSONB: ``rio_records.descricao_batch_id`` +
``descricao_batch_submitted_at`` (migration 0012). Two reasons: a JSONB stamp shares the fate
of every whole-column write in the codebase (PlacesEnrichmentAgent used to erase one wholesale
— now fixed there, but the next such writer would not know), and the collector's worklist is a
``SELECT DISTINCT`` over this column. The DB is the single source of truth for both "do not
resubmit" and "still to collect";
there is no Redis worklist (one existed, had no TTL and no reaper, and the repo's own
reset-brave-db skill flushes ``brave:*`` — which stranded every stamped record forever).

D-18 boundary: no imports from brave.lanes.destinos or brave.tasks.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog
from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    NotFoundError,
)
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm.attributes import flag_modified

# Pricing + the usage-counter reader come from the inline client so the batched row and the
# inline row can never drift apart (the batch rates are literally the standard rates x 0.5).
from brave.clients.llm import (
    _CACHE_READ_MULTIPLIER,
    _CACHE_WRITE_MULTIPLIER,
    _SONNET_4_5_INPUT_USD_PER_MTOK,
    _SONNET_4_5_OUTPUT_USD_PER_MTOK,
    _WEB_SEARCH_USD_PER_REQUEST,
    _usage_int,
)
from brave.core.models import LLMGeneration, RioRecord
from brave.core.rio.routing import route_by_score
from brave.lanes.atrativos.copywriter import (
    COPYWRITER_SYSTEM,
    WEB_SEARCH_TOOL,
    _build_context,
    _strip_dashes,
)
from brave.lanes.atrativos.places_enrichment import (
    _COMPLETUDE_WITH_DESCRIPTION,
    _MAX_DESCRIPTION_ATTEMPTS,
)
from brave.observability.cost_guard import reconcile_spend, record_spend

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from brave.config.settings import LLMConfig, ScoreConfig

logger = structlog.get_logger(__name__)

# Placeholder written to descricao_batch_id BEFORE batches.create is called, so the records
# are un-selectable while the money is being committed. Real Anthropic ids are "msgbatch_*",
# so this can never collide with one, and inflight_batch_ids() filters it out (retrieving it
# would 404). A crash between the claim and the create leaves records holding it: the reaper
# frees them (see reap_stale_claims) — recoverable, never permanently stuck.
CLAIM_BATCH_ID = "claim_pending_create"

# Distinct from CLAIM_BATCH_ID on purpose: it marks rows whose create failed AMBIGUOUSLY, i.e.
# Anthropic may hold a real batch that is really billing. Time alone must NOT free these — the
# plain placeholder is safe to reap because a crash before create means no batch exists, but
# reaping this one resubmits (and re-pays for) work that may already be paid, with the paid
# results unreachable. Only an operator who has checked Anthropic can clear it by hand.
AMBIGUOUS_BATCH_ID = "claim_ambiguous_create"

# Batch tokens are billed at 50% of the standard Messages API rates. The web_search fee is
# NOT discounted, so it is added at full price below.
_BATCH_TOKEN_DISCOUNT: float = 0.5

# Max requests per batch. batches.create COMMITS the spend irrevocably, so the default must
# fit inside a DEFAULT-budget day on its own (130 x $0.075 = $9.75 < LLMConfig.usd_daily_budget
# of $10.0) — otherwise turning the flag on with shipped config would block every batch.
# The real per-tick size is min(this, what the budget reservation returns) — see submit_batch.
DESCRIPTION_BATCH_SIZE: int = 130

# Estimated batch-rate cost of one description. Used to size the batch AND as the per-record
# budget RESERVATION at submit; the real cost is recorded per result and reconciled then.
#
# Derived from 5 live inline calls, which came in at $0.094-$0.116 all-in on the STANDARD API,
# each spending exactly 2 web searches:
#   searches   2 x $0.01 = $0.02, and the web_search fee is NOT discounted in batch;
#   tokens     $0.094-$0.116 - $0.02 = $0.074-$0.096 standard → x0.5 = $0.037-$0.048 in batch;
#   all-in     $0.057-$0.068 per description at batch rates.
# 0.075 rounds the worst case UP on purpose. Under-reserving is the dangerous direction — it
# is what lets a batch commit past the daily budget — while the unused remainder costs nothing:
# submit hands it back in the same tick and collect settles the rest against the real bill.
_EST_USD_PER_DESCRIPTION: float = 0.075

# Anthropic expires a batch that has not finished within 24h. Anything still stamped past this
# margin is unrecoverable by definition — free it so the record becomes eligible again.
_BATCH_REAP_AFTER = timedelta(hours=25)

# Expiries do NOT burn a descricao_attempt (Anthropic does not bill them), which would
# otherwise make an expired record immediately and forever re-eligible. web_search is
# throttled inside batches, so a big batch can expire over and over — bound it separately.
_MAX_BATCH_EXPIRIES: int = 2
_EXPIRIES_KEY = "descricao_batch_expiries"

# Anthropic requires ^[a-zA-Z0-9_-]{1,64}$ for custom_id. A RioRecord UUID string fits.
_CUSTOM_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

_LANE = "atrativo_copywriter_batch"


def _request_was_never_billed(exc: BaseException) -> bool:
    """True only when ``batches.create`` PROVABLY never reached the model.

    Asymmetric on purpose, and the whole subtlety of the failure path. A clean client-side
    refusal (no connection at all, or a 4xx the API rejected the request with) created no
    batch: the reservation is dead money and the claim freezes N records until the reaper, so
    both are undone at once. A timeout or a 5xx is AMBIGUOUS — the request may have been
    received and the batch may exist and be billing — so nothing is undone there.

    APITimeoutError subclasses APIConnectionError and MUST be tested first: a request that
    timed out was sent. Anything unrecognised reads as ambiguous (never refund on a guess).
    """
    if isinstance(exc, APITimeoutError):
        return False
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code < 500
    return False


def build_request(rio: RioRecord, model: str = "claude-sonnet-4-5") -> dict[str, Any]:
    """Build one Message Batches request line for an atrativo.

    ``params`` must stay byte-identical to what ``RealLLMClient.generate`` sends for the
    inline path (model / max_tokens=2048 / system / messages / tools), otherwise the two
    paths are not the same experiment. Batch-forbidden params (stream, and the routing
    hints the Messages API accepts) are simply never set.

    Grounding: a deferred request is built from the DB, hours after the Places call, so the
    live ``details`` payload the inline copywriter grounds on is gone — only ``address``
    survives in normalized. web_search therefore carries more of the load here.
    """
    normalized = rio.normalized or {}
    return {
        "custom_id": str(rio.id),
        "params": {
            "model": model,
            "max_tokens": 2048,
            "system": COPYWRITER_SYSTEM,
            "messages": [
                {
                    "role": "user",
                    "content": _build_context(
                        normalized.get("name") or "",
                        normalized.get("municipio") or "",
                        rio.uf or normalized.get("uf") or "",
                        {"formatted_address": normalized.get("address") or ""},
                    ),
                }
            ],
            "tools": [WEB_SEARCH_TOOL],
        },
    }


def candidates_select(limit: int = DESCRIPTION_BATCH_SIZE) -> Select:
    """The eligibility query: atrativos that still want a description and have none in flight.

    Mirrors PlacesEnrichmentAgent's ``wants_description`` predicate (routing + attempt
    budget included), plus the ``descricao_batch_id IS NULL`` guard — the column, not a
    JSONB key, is what makes resubmission (and therefore double spend) impossible.

    FOR UPDATE SKIP LOCKED: two submit ticks running concurrently must not both select the
    same rows. The loser skips them and builds a smaller batch instead of billing a
    duplicate.
    """
    attempts = RioRecord.normalized["descricao_attempts"].as_integer()
    expiries = RioRecord.normalized[_EXPIRIES_KEY].as_integer()
    return (
        select(RioRecord)
        .where(
            RioRecord.entity_type == "attraction",
            RioRecord.routing != "descarte",
            RioRecord.descricao_batch_id.is_(None),
            RioRecord.normalized["name"].as_string().isnot(None),
            RioRecord.normalized["descricao_editorial"].as_string().is_(None),
            # absent counter reads as SQL NULL, and NULL < 3 is NULL (row silently dropped)
            or_(attempts.is_(None), func.coalesce(attempts, 0) < _MAX_DESCRIPTION_ATTEMPTS),
            or_(expiries.is_(None), func.coalesce(expiries, 0) < _MAX_BATCH_EXPIRIES),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


def inflight_batch_ids(session: Session) -> list[str]:
    """Every real Anthropic batch id still stamped on a record — the collector's worklist.

    Derived from the DB on every tick (no Redis set to lose, no TTL to forget). The claim
    placeholder is filtered out: it is not an Anthropic id and retrieving it would 404.
    """
    rows = (
        session.execute(
            select(RioRecord.descricao_batch_id)
            .where(RioRecord.descricao_batch_id.isnot(None))
            .distinct()
        )
        .scalars()
        .all()
    )
    return [b for b in rows if b and b not in (CLAIM_BATCH_ID, AMBIGUOUS_BATCH_ID)]


def _batch_is_gone(client: Any, batch_id: str) -> bool:
    """True only when Anthropic says the batch does not exist (404).

    Anything else — a network blip, a 5xx, an expired key — reads as "still there". The batch
    may hold PAID results, and freeing its last row deletes the only pointer to it:
    inflight_batch_ids derives the whole worklist from the stamps, so an orphaned id is
    invisible forever while Anthropic keeps its results for 29 days.
    """
    try:
        client.messages.batches.retrieve(batch_id)
    except NotFoundError:
        return True
    except Exception as exc:  # noqa: BLE001 — an unreadable batch is not a missing batch
        logger.warning("copy_batch_reap_probe_failed", batch_id=batch_id, error=str(exc))
    return False


def reap_stale_claims(session: Session, client: Any, *, now: datetime | None = None) -> int:
    """Clear stale batch stamps whose batch is genuinely gone. Returns the number freed.

    The recovery path for everything that can strand a record: a crash between the claim and
    batches.create (the placeholder has no batch to probe — time alone frees it), a batch that
    vanished (rotated key, deleted batch), and anything the collector never managed to apply.
    Without it a stamped record is excluded from the eligibility query forever and silently
    stops getting a description.

    TIME IS NOT ENOUGH on its own: a real batch whose results merely failed to retrieve for
    the ~45 min before the cutoff would have its PAID results discarded, its reservations left
    unsettled, and its rows resubmitted and billed again. So a stale stamp carrying a real id
    is only freed once ``retrieve`` 404s; while it still resolves it stays stamped and collect
    keeps retrying it.
    """
    cutoff = (now or datetime.now(UTC)) - _BATCH_REAP_AFTER
    # ponytail: SELECT + attribute loop, not a bulk UPDATE — a reap is at most a batch's
    # worth of rows (<=150) once every 15 min. Switch to update() if that ever stops holding.
    stale = (
        session.execute(
            select(RioRecord).where(
                RioRecord.descricao_batch_id.isnot(None),
                RioRecord.descricao_batch_submitted_at < cutoff,
            )
        )
        .scalars()
        .all()
    )
    probed: dict[str, bool] = {}  # rows share batch ids — probe each id once
    freed = 0
    for rio in stale:
        batch_id = rio.descricao_batch_id or ""
        if batch_id == AMBIGUOUS_BATCH_ID:
            # Never freed by time: Anthropic may hold a real, billing batch for these rows.
            # Freeing them would re-pay for work already paid for. Operator clears by hand.
            logger.error("copy_batch_ambiguous_claim_needs_operator", rio_id=str(rio.id))
            continue
        if batch_id and batch_id != CLAIM_BATCH_ID:
            if batch_id not in probed:
                probed[batch_id] = _batch_is_gone(client, batch_id)
            if not probed[batch_id]:
                logger.warning("copy_batch_stale_claim_kept_batch_alive", batch_id=batch_id)
                continue
        rio.descricao_batch_id = None
        rio.descricao_batch_submitted_at = None
        freed += 1
    if freed:
        session.commit()
        logger.warning("copy_batch_reaped_stale_claims", freed=freed)
    return freed


def _record_generation(session: Session, message: Any, model: str) -> float:
    """Price a batched response at BATCH rates and persist the llm_generations row.

    Tokens are 50% off; the $10/1,000 web_search fee is not (docs: "priced the same as
    those in regular Messages API requests"). Returns the USD cost — the caller settles it
    against the submit-time reservation with a single reconcile_spend.

    The cache counters are priced for the same reason RealLLMClient prices them: a cache hit
    MOVES tokens out of input_tokens, so a path that ignores them under-reports its bill the
    moment anyone puts cache_control on COPYWRITER_SYSTEM — which is the obvious next step
    here, the system prompt being identical across every request in a batch. Read defensively:
    Anthropic omits both counters entirely while caching is off.
    """
    usage = getattr(message, "usage", None)
    prompt_tokens = _usage_int(usage, "input_tokens")
    completion_tokens = _usage_int(usage, "output_tokens")
    cache_read_tokens = _usage_int(usage, "cache_read_input_tokens")
    cache_write_tokens = _usage_int(usage, "cache_creation_input_tokens")
    searches = _usage_int(getattr(usage, "server_tool_use", None), "web_search_requests")
    usd_cost = (
        (
            prompt_tokens * _SONNET_4_5_INPUT_USD_PER_MTOK
            + cache_write_tokens * _SONNET_4_5_INPUT_USD_PER_MTOK * _CACHE_WRITE_MULTIPLIER
            + cache_read_tokens * _SONNET_4_5_INPUT_USD_PER_MTOK * _CACHE_READ_MULTIPLIER
            + completion_tokens * _SONNET_4_5_OUTPUT_USD_PER_MTOK
        )
        * _BATCH_TOKEN_DISCOUNT
    ) / 1_000_000 + searches * _WEB_SEARCH_USD_PER_REQUEST

    session.add(
        LLMGeneration(
            id=uuid.uuid4(),
            lane=_LANE,
            model_slug=model,
            resolved_provider=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usd_cost=usd_cost,
        )
    )
    return usd_cost


def apply_result(
    session: Session,
    rio: RioRecord,
    result: Any,
    config: ScoreConfig,
    *,
    batch_id: str,
    model: str = "claude-sonnet-4-5",
    redis_client: Any = None,
) -> bool:
    """Perform the three deferred writes for one batch result line. Returns True if applied.

    ``result`` is the ``result`` member of a MessageBatchIndividualResponse:
    succeeded / errored / canceled / expired.

    IDEMPOTENT. The stamp IS the applied-marker: the writes only happen while the record
    still carries ``batch_id``, and the stamp is cleared in the SAME transaction as them. A
    crash mid-stream therefore re-reads already-applied entries as no-ops instead of writing
    a second LLMGeneration row, recording phantom spend (which would trip the guard for
    everyone), and burning a second descricao_attempt. COMMITS that transaction itself: the
    Redis settle below is not transactional, so it may only fire once the writes it settles
    are durable.

    Attempt accounting follows the BILL, not the outcome: a succeeded-but-empty response and
    an errored one both consumed the request, so they burn an attempt; expired and canceled
    are not billed by Anthropic and must not (three expiries would otherwise permanently
    exclude a record from descriptions) — they burn a bounded _EXPIRIES_KEY counter instead.

    Ends at route_by_score, exactly like the inline path. It does NOT push — see the module
    docstring for the gap that leaves.
    """
    if rio.descricao_batch_id != batch_id:
        # Already applied, or reaped and possibly resubmitted. Either way: not ours to write.
        logger.debug("copy_batch_result_already_applied", rio_id=str(rio.id), batch_id=batch_id)
        return False

    normalized = dict(rio.normalized or {})
    attempts = int(normalized.get("descricao_attempts") or 0)
    rtype = getattr(result, "type", "errored")
    prose: str | None = None
    usd_actual = 0.0

    if rtype == "succeeded":
        message = result.message
        # A response came back, so it was billed — record it even if unusable.
        usd_actual = _record_generation(session, message, model)
        if getattr(message, "stop_reason", None) == "pause_turn":
            # The docs do not say whether a batched server-tool request can pause, nor how
            # to resume one in-batch. Treat it as a failed attempt and MEASURE the rate
            # before designing anything smarter.
            logger.warning("copy_batch_pause_turn", rio_id=str(rio.id))
        else:
            prose = _strip_dashes(
                "".join(
                    getattr(block, "text", "")
                    for block in getattr(message, "content", [])
                    if getattr(block, "type", "") == "text"
                )
            )
        if prose:
            normalized["descricao_editorial"] = prose
            normalized["completude_value"] = max(
                float(normalized.get("completude_value", 0.0)),
                _COMPLETUDE_WITH_DESCRIPTION,
            )
        else:
            normalized["descricao_attempts"] = attempts + 1
    elif rtype == "errored":
        normalized["descricao_attempts"] = attempts + 1
        logger.warning("copy_batch_result_errored", rio_id=str(rio.id))
    else:  # expired / canceled — not billed by Anthropic, so not an attempt
        expiries = int(normalized.get(_EXPIRIES_KEY) or 0) + 1
        normalized[_EXPIRIES_KEY] = expiries
        if expiries >= _MAX_BATCH_EXPIRIES:
            # Silent otherwise: an unbilled outcome leaves the record eligible, so it would
            # be resubmitted and expire again every hour, forever.
            logger.warning(
                "copy_batch_expiries_exhausted",
                rio_id=str(rio.id),
                expiries=expiries,
                max_expiries=_MAX_BATCH_EXPIRIES,
                result_type=rtype,
            )
        else:
            logger.info("copy_batch_result_unbilled", rio_id=str(rio.id), result_type=rtype)

    # descricao_batch_submitted_at IS the day the reservation was booked on — reconcile needs
    # it to refuse a refund against a day it was not reserved on (submit and collect straddle
    # a UTC midnight whenever a batch takes more than a few hours). Read it before the stamp
    # is cleared below.
    reserved_at = rio.descricao_batch_submitted_at

    rio.normalized = normalized
    flag_modified(rio, "normalized")
    # Same transaction as the writes above — this is what makes a re-read a no-op.
    rio.descricao_batch_id = None
    rio.descricao_batch_submitted_at = None
    session.flush()

    if prose:
        # completude moved → a borderline record can cross mar↔dlq (same re-score point the
        # inline path runs at the end of PlacesEnrichmentAgent.run). No push follows.
        route_by_score(session, rio, config)
        session.flush()

    # COMMIT BEFORE TOUCHING REDIS. The Redis settle is not part of this transaction and
    # cannot be rolled back with it: if the commit raises (deadlock, dropped connection,
    # statement timeout) the stamp is restored, the next 15-minute tick re-retrieves this same
    # entry, the guard above passes again — and a settle that had already fired would release
    # a second time a reservation that was only ever made once. On an unbilled entry that is a
    # full _EST_USD_PER_DESCRIPTION of phantom budget per record, handed to every lane sharing
    # the daily counter. Settling after the commit can only ever UNDER-release (a crash here
    # leaves the reservation booked until its UTC-day key expires), which is the safe side.
    session.commit()

    # Settle THIS request's submit-time reservation against its real bill — exactly once per
    # submitted request, unbilled outcomes included (actual 0.0 releases the whole
    # reservation). The stamp guard above is what stops a re-read settling it twice.
    if redis_client is not None:
        reconcile_spend(
            redis_client,
            _EST_USD_PER_DESCRIPTION,
            usd_actual,
            reserved_at=reserved_at,
        )
    return True


def submit_batch(
    session: Session,
    client: Any,
    *,
    redis_client: Any,
    llm_config: LLMConfig,
    model: str = "claude-sonnet-4-5",
    limit: int = DESCRIPTION_BATCH_SIZE,
) -> str | None:
    """Submit one description batch. Returns the batch id, or None when nothing was sent.

    RESERVE, THEN CLAIM, THEN SPEND. This task is acks_late + reject_on_worker_lost, so the
    broker is guaranteed to redeliver it if the worker is killed — and batches.create commits
    real money that cannot be undone. So the order is:

      1. reserve a whole batch's worth of budget and size the batch off what the counter
         RETURNS — the INCRBYFLOAT is the atomic claim on the budget (see below);
      2. release the part of that reservation this tick will not use;
      3. SELECT ... FOR UPDATE SKIP LOCKED the candidates;
      4. write the claim placeholder + submitted_at and COMMIT — the rows are now
         un-selectable, so a redelivery after this point selects nothing and bills nothing;
      5. batches.create, then stamp the real id.

    (1) before (3) because SKIP LOCKED keeps two concurrent submits on disjoint ROWS, not on
    a disjoint BUDGET: with read-then-reserve, a redelivery running beside the original both
    read remaining=$10 and both reserve $9.90. Reserving first serializes them — the second
    one's counter read already contains the first one's claim, so it sizes down to what is
    actually left.

    A crash between (4) and (5) leaves records holding the claim: the reaper frees them
    (25h), which costs latency, never a second charge. Their reservation is then never
    settled by collect, but the daily counter is a UTC-day key that expires within 24h — so
    it always rolls over before the reaper frees the rows.
    """
    now = datetime.now(UTC)
    # Down-size, never block: a binary "would this batch overshoot?" check raises on the very
    # first tick whenever limit * _EST > budget, which silently stops descriptions entirely
    # (the flag also disables the inline path). record_spend returns the counter AFTER our
    # increment, so subtracting our own reservation yields what everyone else had booked at
    # the instant we claimed — the only read that a concurrent submit cannot race.
    reserved = limit * _EST_USD_PER_DESCRIPTION
    booked_by_others = record_spend(redis_client, reserved) - reserved
    remaining = max(0.0, llm_config.usd_daily_budget - booked_by_others)
    sized = min(limit, int(remaining // _EST_USD_PER_DESCRIPTION))

    rows = list(session.execute(candidates_select(sized)).scalars().all()) if sized > 0 else []
    requests = [build_request(rio, model=model) for rio in rows]
    # Hand back everything we over-reserved. What stays booked is exactly what collect will
    # settle, one _EST_USD_PER_DESCRIPTION per request, in apply_result.
    used = len(requests) * _EST_USD_PER_DESCRIPTION
    if used < reserved:
        reconcile_spend(redis_client, reserved - used, 0.0, reserved_at=now)
    if sized <= 0:
        logger.warning(
            "copy_batch_submit_budget_exhausted",
            remaining_usd=round(remaining, 4),
            est_usd_per_description=_EST_USD_PER_DESCRIPTION,
        )
        return None
    if not rows:
        return None

    for rio in rows:
        rio.descricao_batch_id = CLAIM_BATCH_ID
        rio.descricao_batch_submitted_at = now
    session.commit()

    try:
        batch = client.messages.batches.create(requests=requests)
    except Exception as exc:
        # Without this, a 529 (or a time_limit kill) leaves $9.90 of a $10 day reserved for
        # nothing and 150 rows frozen until the 25h reaper: the next tick sizes to 1, the one
        # after to 0, and the lane is dead until the UTC day key expires.
        if _request_was_never_billed(exc):
            for rio in rows:
                rio.descricao_batch_id = None
                rio.descricao_batch_submitted_at = None
            session.commit()
            reconcile_spend(redis_client, used, 0.0, reserved_at=now)
            logger.warning(
                "copy_batch_submit_refunded",
                requests=len(requests),
                refunded_usd=round(used, 4),
                error=str(exc),
            )
        else:
            # AMBIGUOUS: the request may have arrived and the batch may exist and be billing.
            # Refunding would hand out budget Anthropic charged; clearing the claims would
            # resubmit (and re-bill) every one of these requests on the next tick. Re-stamp to
            # AMBIGUOUS_BATCH_ID so the 25h reaper does NOT free them either — the plain
            # placeholder is reaped on time alone, which here would re-pay for a live batch.
            for rio in rows:
                rio.descricao_batch_id = AMBIGUOUS_BATCH_ID
            session.commit()
            logger.error(
                "copy_batch_submit_ambiguous_left_claimed",
                requests=len(requests),
                reserved_usd=round(used, 4),
                submitted_at=now.isoformat(),
                action="list Anthropic batches created near submitted_at; cancel or collect by "
                "hand, then clear descricao_batch_id on the affected rio_records",
                error=str(exc),
            )
        raise

    for rio in rows:
        rio.descricao_batch_id = batch.id
    session.commit()

    logger.info("copy_batch_submitted", batch_id=batch.id, requests=len(requests))
    return batch.id


def collect_batches(
    session: Session,
    client: Any,
    config: ScoreConfig,
    *,
    redis_client: Any = None,
    model: str = "claude-sonnet-4-5",
    now: datetime | None = None,
) -> int:
    """Reap stale claims, then apply every ENDED batch. Returns entries applied.

    Per-batch isolation: one un-retrievable id (NotFound past Anthropic's 29-day result
    retention, a rotated key) must not abort collection for every other batch. Progress is
    persisted per entry as it goes, never accumulated in memory to be returned at the end.

    No push is dispatched from here — see the module docstring.
    """
    reap_stale_claims(session, client, now=now)

    applied = 0
    for batch_id in inflight_batch_ids(session):
        this_batch = 0
        try:
            batch = client.messages.batches.retrieve(batch_id)
            if getattr(batch, "processing_status", None) != "ended":
                continue
            for entry in client.messages.batches.results(batch_id):
                try:
                    # FOR UPDATE: the stamp is the applied-marker, so two overlapping collect
                    # runs reading it unlocked both see batch_id and both apply — duplicate
                    # LLMGeneration rows, a double refund, two of the three attempts burned.
                    # The lock serializes them; the loser re-reads a cleared stamp and no-ops.
                    # populate_existing: without it the lock is worthless here. get() returns
                    # an instance already in the identity map WITHOUT re-reading it, so the
                    # loser would take the row lock and then evaluate the stamp it loaded
                    # before the winner cleared it. reap_stale_claims runs first on every tick
                    # and loads RioRecord objects, committing (and so expiring them) only when
                    # it actually frees something — so unexpired instances are the norm here,
                    # not a corner case.
                    rio = session.get(
                        RioRecord,
                        uuid.UUID(entry.custom_id),
                        with_for_update=True,
                        populate_existing=True,
                    )
                except ValueError:  # not one of ours (custom_id is always a RioRecord UUID)
                    continue
                if rio is None:
                    continue
                if apply_result(
                    session,
                    rio,
                    entry.result,
                    config,
                    batch_id=batch_id,
                    model=model,
                    redis_client=redis_client,
                ):
                    this_batch += 1
                # apply_result commits its own writes (it must, to settle Redis only against a
                # durable write), so this is a no-op on the applied path. It is NOT redundant
                # on the no-op path: that one wrote nothing and never committed, and the
                # SELECT ... FOR UPDATE above still holds a row lock per entry. Without this,
                # re-collecting an already-applied batch accumulates one lock per result for
                # the whole run.
                session.commit()
            applied += this_batch
            logger.info("copy_batch_collected", batch_id=batch_id, applied=this_batch)
        except Exception as exc:  # noqa: BLE001 — one bad batch must not stop the others
            session.rollback()
            logger.warning("copy_batch_collect_failed", batch_id=batch_id, error=str(exc))
    return applied


if __name__ == "__main__":  # pragma: no cover — ponytail runnable check
    # No network, no DB: the custom_id contract and the batch-forbidden-param check.
    class _Rio:
        id = uuid.uuid4()
        uf = "BA"
        normalized = {"name": "Igreja Matriz", "municipio": "Porto Seguro"}

    req = build_request(_Rio())  # type: ignore[arg-type]
    assert _CUSTOM_ID_RE.match(req["custom_id"]), req["custom_id"]
    assert not {"stream", "speed", "store", "cache_hint", "context_hint"} & set(req["params"])
    assert req["params"]["tools"] == [WEB_SEARCH_TOOL]
    # The shipped default must fit a default-budget day, or the flag silently does nothing.
    assert DESCRIPTION_BATCH_SIZE * _EST_USD_PER_DESCRIPTION < 10.0
    print("copy_batch self-check ok")

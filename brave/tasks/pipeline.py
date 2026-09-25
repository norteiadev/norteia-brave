"""Celery pipeline tasks (D-05, D-06, CORE-10).

Core tasks:
  process_nascente      — ingest NascenteRecord through Rio pipeline
  reprocess_record_task — re-score an existing RioRecord
  publish_mar           — Publicação: send an active Mar row to norteia-api
                          (brave.core.mar.publication.publish; never promotes)
  repush_pending_mar    — beat: re-enqueue publish_mar for pending Mar rows

Idempotency: Every task is a no-op on re-run (D-03, D-15).
Failure policy: brave.tasks.failure_policy.task_failure_policy — retry, then
PoisonQuarantine (NOT the review DLQ, see PITFALLS §7, T-02-02); a provider billing
wall pauses the motor instead.
"""

import asyncio
import functools
import os
import uuid
from datetime import UTC
from typing import Any, NamedTuple

import structlog
from celery import shared_task
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from brave.clients.factory import clients_for
from brave.config.runtime import load_effective_config, overlay_redis
from brave.config.settings import AppConfig
from brave.core.mar.publication import publish, republish_pending
from brave.core.models import RioRecord
from brave.core.nascente.service import get_nascente
from brave.core.rio.routing import process_nascente_record, reprocess_record
from brave.shared.exceptions import (  # noqa: F401 (PermanentError/TransientError re-export)
    PermanentError,
    ProviderBalanceError,
    TransientError,
)
from brave.tasks.failure_policy import task_failure_policy

logger = structlog.get_logger(__name__)

# Redis key that sweep_tripadvisor sets when a session error halts the sweep.
# Operator must re-inject a fresh session via POST /api/v1/tripadvisor/session
# and then re-trigger the sweep. Cleared when a new session is successfully injected.
_TA_NEEDS_BOOTSTRAP_KEY = "brave:ta:needs_bootstrap"

# Consecutive ta_keepalive session failures (260917-tkd). The beat pings the same
# GraphQL transport the sweep uses, so a single failure is far more likely to be a
# blip than a dead session — only a streak marks needs_bootstrap, and the beat never
# touches the engine either way.
_TA_KEEPALIVE_FAILURES_KEY = "brave:ta:keepalive_failures"
_KEEPALIVE_FAILURES_BEFORE_BOOTSTRAP = 3


def _bump_keepalive_failures(redis: Any, ttl: int) -> int:
    """Increment the consecutive-failure counter and return it (0 when Redis is down).

    The counter carries the session TTL so failures from an old session never add up
    with today's. Best-effort, like _mark_needs_bootstrap: the beat must not raise.
    """
    try:
        falhas = int(redis.incr(_TA_KEEPALIVE_FAILURES_KEY))
        redis.expire(_TA_KEEPALIVE_FAILURES_KEY, max(ttl, 1))
        return falhas
    except Exception:  # noqa: BLE001
        logger.warning("ta_keepalive_counter_failed")
        return 0


def _mark_needs_bootstrap() -> None:
    """Set the needs_bootstrap Redis marker after a session fail-fast.

    Best-effort: if Redis is unreachable, we log a warning but do NOT raise.
    The session error is already logged before this is called; the marker is
    purely for dashboard visibility (EngineControl session-health pill).

    T-12-04-01: Only the key name is written (no cookie fragments, no exc str).
    """
    try:
        import redis as _redis_lib  # noqa: PLC0415

        _rc = _redis_lib.from_url(
            os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        )
        _rc.set(_TA_NEEDS_BOOTSTRAP_KEY, "1")
    except Exception:  # noqa: BLE001
        logger.warning("mark_needs_bootstrap_failed")  # best-effort; never mask session error


def _extract_contact_phone(rio: "RioRecord") -> str:
    """Return the canonical E.164 contact phone for an atrativo, or "" if absent.

    CR-03: ContactFinderAgent stores the phone at
    normalized["contacts"]["phone_e164"]. There is no top-level "contact_phone"
    key. Reading the wrong key produced "" in production, which keyed every LGPD
    consent/opt-out/suppression row and the inbound-routing lookup on the empty
    string. This is the single canonical accessor for the outreach phone.
    """
    contacts = (rio.normalized or {}).get("contacts") or {}
    return contacts.get("phone_e164") or ""


def _log_conversation_messages(
    session: Session,
    rio_id: str,
    contact_phone: str,
    final_state: Any,
    inbound_text: str | None = None,
) -> None:
    """Append-only sync of a LangGraph final state into conversation_message (R2 Option B).

    Writes a ConversationMessage row for every message boundary that is NOT yet logged
    for this rio_id, so no message is dropped across the outreach (outbound asks) and
    resume (inbound reply + follow-up) write-points.

    Correctness (CR-02): the desired ordered transcript is reconstructed so the owner's
    inbound reply is persisted in its correct chronological position — BEFORE any
    follow-up outbound the graph produced in response to it — never appended after the
    follow-up. If the graph already carries the inbound as a user turn we keep its
    position; otherwise we splice it in right before the trailing outbound follow-up(s).

    Idempotency (CR-03): append is keyed on IDENTITY, not on a row count. Each turn is
    assigned a deterministic 0-based `turn_seq` (its chronological index in the thread)
    and inserted only when no row already exists for (rio_id, turn_seq) — also guarded
    by an existence check on (rio_id, direction, role, content). The UNIQUE
    (rio_id, turn_seq) constraint backstops a concurrent racer. A retry/replay is a true
    no-op regardless of any drift between persisted-row count and the graph's `messages`
    length (a shorter replay no longer silently drops, a re-emitted turn no longer
    duplicates).

    LGPD (R3, T-04-24): phone is masked at write time via mask_phone — the raw E.164
    number is NEVER persisted in conversation_message.

    Appends on the CALLER'S session (the same one that commits at the task's single
    session.commit()) — it never opens or commits a separate session, and tolerates an
    empty/None final state (the graph produced nothing → no rows). The LangGraph
    AsyncPostgresSaver checkpoint persistence is untouched (additive — no change to
    scoring/routing/push).
    """
    from brave.core.models import ConversationMessage, mask_phone

    phone_masked = mask_phone(contact_phone)
    rio_uuid = uuid.UUID(rio_id)

    final_extraction = (
        final_state.get("extraction") if isinstance(final_state, dict) else None
    )

    # 1) Pull the graph's ordered turns as (direction, role, content) tuples.
    graph_turns: list[dict[str, Any]] = []
    if isinstance(final_state, dict):
        for turn in final_state.get("messages") or []:
            if isinstance(turn, dict):
                role = turn.get("role") or "assistant"
                graph_turns.append(
                    {
                        "role": role,
                        "direction": "inbound" if role == "user" else "outbound",
                        "content": turn.get("content") or "",
                    }
                )

    # 2) Ensure the inbound reply is represented in its CORRECT chronological position
    #    (CR-02). If the graph already appended it as a user turn, keep it. Otherwise
    #    splice it in right before the LAST outbound turn (the follow-up Norteia
    #    produced in response to the reply), so the owner's reply precedes that
    #    response — never after it. If there is no trailing outbound (the graph ended
    #    on the reply), append the inbound at the end.
    if inbound_text:
        has_inbound = any(
            t["direction"] == "inbound" and t["content"] == inbound_text
            for t in graph_turns
        )
        if not has_inbound:
            last_outbound = next(
                (
                    i
                    for i in range(len(graph_turns) - 1, -1, -1)
                    if graph_turns[i]["direction"] == "outbound"
                ),
                None,
            )
            insert_at = last_outbound if last_outbound is not None else len(graph_turns)
            graph_turns.insert(
                insert_at,
                {"role": "user", "direction": "inbound", "content": inbound_text},
            )

    if not graph_turns:
        return

    # 3) Assign a deterministic turn_seq (chronological index) and insert by IDENTITY
    #    (CR-03). turn_seq is the 0-based position in the reconstructed thread, so a
    #    replay maps each turn to the same seq. We skip a turn that already exists by
    #    (rio_id, turn_seq) OR by (rio_id, direction, role, content) — making both a
    #    count-drift replay and a duplicate-content re-emit a no-op.
    existing_seqs = set(
        session.scalars(
            select(ConversationMessage.turn_seq).where(
                ConversationMessage.rio_id == rio_uuid
            )
        ).all()
    )

    last_outbound_idx = max(
        (i for i, t in enumerate(graph_turns) if t["direction"] == "outbound"),
        default=-1,
    )

    for seq, turn in enumerate(graph_turns):
        if seq in existing_seqs:
            continue
        # Identity guard: a row with this exact (direction, role, content) already
        # persisted for this rio is treated as the same turn (idempotent replay).
        already = session.scalar(
            select(func.count(ConversationMessage.id)).where(
                ConversationMessage.rio_id == rio_uuid,
                ConversationMessage.direction == turn["direction"],
                ConversationMessage.role == turn["role"],
                ConversationMessage.content == turn["content"],
            )
        )
        if already:
            continue
        # Attach the extraction snapshot to the most recent OUTBOUND turn so the
        # transcript carries the structured result alongside that message boundary.
        extracted = (
            final_extraction
            if (seq == last_outbound_idx and turn["direction"] == "outbound")
            else None
        )
        session.add(
            ConversationMessage(
                rio_id=rio_uuid,
                turn_seq=seq,
                phone_masked=phone_masked,
                direction=turn["direction"],
                role=turn["role"],
                content=turn["content"],
                extracted=extracted,
            )
        )


# ---------------------------------------------------------------------------
# Exceptions for error classification
# ---------------------------------------------------------------------------
#
# TransientError / PermanentError now live in the central hierarchy
# (brave/shared/exceptions.py) and are imported at the top of this module. They
# remain module-level names here so every existing raise/except in this file —
# and any importer — keeps working unchanged.


# ---------------------------------------------------------------------------
# Session factory (lazy — resolved at task call time, not import time)
# ---------------------------------------------------------------------------


_ENGINES: dict[str, tuple[Any, Any]] = {}  # db_url -> (engine, sessionmaker)


def _get_session() -> tuple[Session, Any]:
    """Create a synchronous SQLAlchemy session from environment config.

    The engine + sessionmaker are a per-process singleton keyed by db_url, built on the
    FIRST call inside a task — i.e. after the prefork worker forked, never at import, so
    no pooled connection is shared across processes. Returns (session, engine); the
    caller closes the session (returning the connection to the pool) and must NOT
    dispose the shared engine.
    """
    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        raise PermanentError("BRAVE_DB_URL not set — cannot create DB session")
    if db_url not in _ENGINES:
        engine = create_engine(db_url, echo=False, pool_pre_ping=True)
        _ENGINES[db_url] = (engine, sessionmaker(bind=engine))
    engine, factory = _ENGINES[db_url]
    return factory(), engine


def _load_config(session: Session) -> AppConfig:
    """Effective config for a task, its overlay rows served from the Redis cache when present.

    The cache is dropped whenever a config_settings write commits; a Redis outage
    degrades to the plain DB read inside load_effective_config.
    """
    return load_effective_config(session, overlay_redis())


def _dispatch_chain(task: Any, rio_id: str) -> bool:
    """Enqueue the next chain task; a broker failure is logged, never run inline.

    Returns False when the enqueue failed. The record keeps its current sub_state;
    brave.redispatch_stalled_chain recovers the Places chain states (discovered /
    contacts_found / signals_gathered). An inline .run() here used to end, with the broker
    down, in the CALLER's quarantine ("retry failed: Reject") — and in discover it cut the
    fan-out short.
    """
    try:
        task.delay(rio_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chain_dispatch_failed", task=task.name, rio_id=rio_id, error_type=type(exc).__name__
        )
        return False
    return True


def _beat_health(task: str, exc: BaseException | None = None) -> None:
    """Record (``exc``) or clear (None) a beat task's last error for the Painel.

    Best-effort (brave.core.beat_health): Redis being down must not change the task's own
    outcome.
    """
    import redis as _redis_lib  # noqa: PLC0415

    from brave.core import beat_health  # noqa: PLC0415

    try:
        rc = _redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))
        if exc is None:
            beat_health.clear_error(rc, task)
        else:
            beat_health.record_error(rc, task, exc)
    except Exception:  # noqa: BLE001
        logger.warning("beat_health_write_failed", task=task)


async def _using(clients: Any, coro: Any) -> Any:
    """Await ``coro`` inside ``clients``: persistent HTTP connections held for the whole
    event loop, everything the bag built closed when it ends (brave.clients.factory)."""
    async with clients:
        return await coro


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.process_nascente",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def process_nascente(self, nascente_id: str) -> None:
    """Process a NascenteRecord through the Rio pipeline.

    Idempotent: If a RioRecord already exists for this nascente_id, returns
    immediately without re-processing.

    Error handling:
      TransientError → retry (up to max_retries=3)
      PermanentError or unhandled after max_retries → quarantine_poison

    Args:
        nascente_id: UUID string of the NascenteRecord to process.
    """
    session, engine = _get_session()
    try:
        with task_failure_policy(
            self,
            session,
            "brave.process_nascente",
            nascente_id=uuid.UUID(nascente_id) if nascente_id else None,
        ):
            nascente_uuid = uuid.UUID(nascente_id)
            config = _load_config(session).score

            nascente = get_nascente(session, nascente_uuid)
            if nascente is None:
                raise PermanentError(f"NascenteRecord {nascente_id} not found")

            # Idempotency check: RioRecord with matching canonical_key
            canonical_key = nascente.source_ref
            existing = session.scalar(
                select(RioRecord).where(RioRecord.canonical_key == canonical_key)
            )
            if existing is not None:
                return  # Already processed — idempotent no-op

            process_nascente_record(session, nascente, config)
            session.commit()

    finally:
        session.close()


def _norteia_api_down() -> bool:
    """True only when norteia-api is confirmed down (cached ping, see mar/sync.py).

    Skipping the POST then costs nothing: no Celery retries against a dead host, and
    the row keeps pushed_at NULL for brave.repush_pending_mar to pick up.
    """
    import redis as _redis_lib  # noqa: PLC0415

    from brave.core.mar.sync import norteia_api_up  # noqa: PLC0415

    rc = _redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))
    return norteia_api_up(rc) is False


@shared_task(name="brave.repush_pending_mar", time_limit=300)
def repush_pending_mar() -> int:
    """Beat (15 min): re-dispatch the push for Mar rows norteia-api never accepted.

    No-op while externals are off (the Null client never stamps pushed_at, so every
    row would look pending) and while norteia-api is down.
    """
    if not AppConfig().run_real_externals or _norteia_api_down():
        return 0
    session, _ = _get_session()
    try:
        dispatched = republish_pending(session, publish_mar.delay)
    except Exception as exc:
        _beat_health("brave.repush_pending_mar", exc)
        raise
    finally:
        session.close()
    _beat_health("brave.repush_pending_mar")
    if dispatched:
        logger.info("repush_pending_mar_dispatched", count=dispatched)
    return dispatched


@shared_task(
    bind=True,
    max_retries=3,
    name="brave.publish_mar",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def publish_mar(self, rio_id: str) -> str | None:
    """Publicação: send the active Mar row of ``rio_id`` to norteia-api.

    Never promotes (that is ``brave.core.mar.publication.promote``). The endpoint is
    picked by entity_type; an unchanged payload skips the POST; a down API leaves the
    row pending for brave.repush_pending_mar. HTTP errors retry 3x, then the task ends
    FAILURE; the row keeps pushed_at NULL, so the 15-min repush tries it again.
    """
    session, _ = _get_session()
    try:
        with task_failure_policy(self, session, "brave.publish_mar", quarantine=False):
            return publish(
                session, uuid.UUID(rio_id), clients_for(AppConfig()).norteia_api
            ).status
    finally:
        session.close()


@shared_task(
    bind=True,
    name="brave.reprocess_record",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def reprocess_record_task(self, rio_id: str) -> None:
    """Re-score an existing RioRecord (reset → re-route).

    Idempotent: re-running with the same config produces the same result.

    Args:
        rio_id: UUID string of the RioRecord to reprocess.
    """
    session, engine = _get_session()
    try:
        with task_failure_policy(self, session, "brave.reprocess_record", quarantine=False):
            config = _load_config(session).score
            reprocess_record(session, uuid.UUID(rio_id), config)
            session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Phase 3 — Atrativos lane FSM tasks (D-01/D-02)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.discover_atrativo",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=600,  # Places API can be slow — 10 min limit
)
def discover_atrativo_task(
    self, uf: str, depth: str | None = None, *, run_id: str | None = None
) -> None:
    """Fan-out attraction discovery for one UF (sub_state → discovered).

    Sweeps Google Places for attractions in the given UF, resolves parent
    destinos from Mar, extracts via DeepSeek/instructor, and writes to Nascente.

    Depth gate (plan 10-02): discovery + Rio always run for atrativos at the
    rio depths, but the WhatsApp-gate FSM chain (find_contacts → gate) is only
    kicked when depth == NASCENTE_RIO_MAR. Under NASCENTE_RIO the chain is NOT
    kicked (neither find_contacts_task.delay nor its inline .run fallback fires).
    depth arrives ONLY as this arg — never read from Redis here. depth=None
    (legacy/direct call) defaults to NASCENTE_RIO_MAR (full chain).

    Idempotency: store_raw is idempotent by content_hash (D-03).
    Error handling: transient → retry; permanent → quarantine_poison.
    Client selection: clients_for — real clients only when run_real_externals=True (D-18).

    Args:
        uf: Two-letter Brazilian state code (e.g. "BA", "RJ").
        depth: Pipeline depth (nascente_rio | nascente_rio_mar). None → full.
    """
    from brave.core import engine as collection_engine
    from brave.domains.places.discovery_agent import DiscoveryAgent

    effective_depth = depth or collection_engine.NASCENTE_RIO_MAR

    session, engine = _get_session()
    try:
        with task_failure_policy(
            self,
            session,
            "brave.discover_atrativo",
            payload={"uf": uf},
            pause_action="sweep",
        ):
            effective = _load_config(session)
            config = effective.score

            from brave.clients.places import load_municipio_name_ibge_lookup

            # Places API has no IBGE field — the real client gets the name→IBGE lookup from
            # the municipios reference table so attractions get a resolved municipio_ibge
            # (required for parent-destino linkage via ensure_destino).
            clients = clients_for(
                effective, ibge_lookup=lambda: load_municipio_name_ibge_lookup(session)
            )
            places_client = clients.places
            llm_client = clients.llm("atrativos", session=session)

            # Load the IBGE DTB distrito reference once — threads into the discovery agent
            # for admin_area_level_3 → distrito name-match enrichment, mirroring how the
            # municipios reference is loaded and passed in the TA lane. Reads the seeded
            # distritos reference table (was a static CSV before §3).
            from brave.shared.ibge_distritos import load_distritos
            distritos = load_distritos(session)

            agent = DiscoveryAgent(
                places_client=places_client,
                llm_client=llm_client,
                session=session,
                config=config,
                distritos=distritos,
            )

            asyncio.run(_using(clients, agent.produce(uf)))
            session.commit()

            # ORCH-02 / D-03: fan out the FSM chain. DiscoveryAgent.produce returns None,
            # so chaining is keyed on sub_state queries (self-healing across restarts) —
            # never on a producer return value. Query every attraction this sweep landed at
            # sub_state='discovered' and dispatch find_contacts_task per row. A failed
            # .delay is logged and skipped: the record stays 'discovered' and
            # brave.redispatch_stalled_chain picks it up — one bad dispatch never stops the
            # fan-out. Replay-safe: a duplicate dispatch hits the contact_finder inline
            # precondition guard and no-ops (D-04, finding #2).
            discovered_ids = session.scalars(
                select(RioRecord.id).where(
                    RioRecord.entity_type == "attraction",
                    RioRecord.uf == uf,
                    RioRecord.sub_state == "discovered",
                )
            ).all()
            # Depth gate (plan 10-02): only NASCENTE_RIO_MAR kicks the WhatsApp-gate
            # FSM chain. Under NASCENTE_RIO discovery/Rio still ran above, but the
            # ENTIRE fan-out below is suppressed so the chain never advances toward the gate.
            if effective_depth != collection_engine.NASCENTE_RIO:
                for rio_id in discovered_ids:
                    _dispatch_chain(find_contacts_task, str(rio_id))

    finally:
        # Producer-completes lifecycle: engine_sweep_run claimed this producer before
        # .delay; the LAST producer completes the run (single outermost finally).
        _producer_done(run_id)
        session.close()


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.sweep_tripadvisor",
    acks_late=True,
    reject_on_worker_lost=True,
    # 1h: Places enrichment runs INLINE per record (place_details + TA review fetch), so a
    # whole-UF sweep serializes far more work than the old 600s. The copywriter no longer
    # runs here (brave.describe_uf owns descriptions). Per-record commit inside produce()
    # keeps progress durable if this limit is ever hit.
    time_limit=3600,
    soft_time_limit=3540,
)
def sweep_tripadvisor(
    self,
    uf: str,
    depth: str | None = None,
    *,
    bulk_national: bool = False,
    start_page: int = 1,
    max_pages: int | None = None,
    geo_id: int = 294280,
    max_per_uf: int | None = None,
    run_id: str | None = None,
) -> None:
    """TripAdvisor sweep for one UF — atrativos only, parent destinos from authoritative Rio records (Mtur/IBGE) (oa3).

    Mirrors sweep_uf but uses the TripAdvisor ingest lane instead of the Mtur seed.
    Produces TripAdvisor attraction records only. Parent destino RioRecords must already
    exist in Rio (run Mtur seed sweep first). TA-destinos (TripAdvisorDestinosIngest) is
    not wired here — no destinos QID has been captured; deferred until QID is discovered.

    Depth gate: depth=NASCENTE → run_rio=False (Nascente + reliability score only, no Rio validation).
    depth=None (legacy/direct call) defaults to the full pipeline path.

    Client selection: clients_for — NullTripAdvisorClient unless AppConfig().run_real_externals
    (RUN_REAL_EXTERNALS=True, opt-in only).

    Idempotency: store_raw dedups by (source, source_ref, content_hash).

    Bulk national branch (Phase 15, TA-12): when bulk_national=True the task takes a
    DISTINCT path that paginates the all-Brazil AttractionsFusion listing (geoId 294280)
    via TripAdvisorAtrativosIngest.produce_paginated — NO destinos producer, NO
    destino_rio_map (parent-less bulk ingest). It reads the resume offset from
    sweep_progress so a re-run continues from the page after the last completed offset
    (NOT page 1), seeds the live progress hash, commits per-page (inside produce_paginated),
    marks the run done when the pages run out (``stopped`` on a pause/off/stop or a provider
    billing wall), and on a mid-run 403/429 SessionExpiredError reuses
    the SHARED fail-fast block plus a GUARDED sweep_progress.stop_needs_bootstrap. The
    slice (small max_pages) and the full 334-page run share this ONE page-range-parameterized
    code path. The per-UF (bulk_national=False) path is left byte-for-byte unchanged.

    Args:
        uf:            Two-letter Brazilian state code (e.g. "BA", "RJ").
        depth:         Pipeline depth (nascente|nascente_rio|nascente_rio_mar|None).
        bulk_national: When True, run the national bulk pagination branch (geoId 294280)
                       instead of the per-UF atrativos path.
        start_page:    1-based page to start a FRESH bulk run at (offset = (start_page-1)*30).
                       Ignored when a prior run recorded progress (resume takes precedence).
        max_pages:     Cap on pages to fetch this bulk run (slice-first). None → full 334.
        geo_id:        TripAdvisor integer geoId for the bulk run (294280 = all Brazil).
    """
    from brave.core import engine as collection_engine
    from brave.domains.tripadvisor import sweep_progress
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest
    from brave.domains.tripadvisor.client import SessionExpiredError, SessionMissingError
    from brave.domains.tripadvisor.ibge import load_ibge_municipios

    run_rio = depth != collection_engine.NASCENTE

    import redis as _redis_lib  # noqa: PLC0415

    session, engine = _get_session()
    # One engine Redis client for every path (lifecycle, pause, R1, producer halt) —
    # built before the try so each except can use it whichever branch raised.
    engine_rc = _redis_lib.from_url(
        os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
    )
    # rc is the live progress hash client — bulk_national only. The per-UF path reaches
    # the SHARED fail-fast except with rc still None; the guarded `if rc is not None`
    # keeps it from touching the bulk panel (T-15-07-04).
    rc = None
    # The standalone bulk run claims itself (it is not dispatched by engine_sweep_run);
    # None = not counted against any run, so its finally must not call producer_done.
    bulk_run_id = None
    try:
        with task_failure_policy(
            self,
            session,
            "brave.sweep_tripadvisor",
            payload={"uf": uf},
            pause_action="sweep",
            passthrough=(SessionMissingError, SessionExpiredError),
        ):
            effective = _load_config(session)
            config = effective.score

            # T1 (pfr-01): ta_config must be defined before the branch so it is always
            # in scope for the per-UF TripAdvisorAtrativosIngest constructor. Without
            # this, ta_config is only defined inside the run_real_externals block and
            # the offline path would raise NameError; passing None keeps the
            # fetch_attraction_geo guard (ta_config is not None) dormant offline.
            ta_config = None
            if effective.run_real_externals:
                from brave.config.settings import TripAdvisorConfig

                ta_config = TripAdvisorConfig()
            from brave.clients.places import load_municipio_name_ibge_lookup

            clients = clients_for(
                effective, ibge_lookup=lambda: load_municipio_name_ibge_lookup(session)
            )
            ta_client = clients.tripadvisor
            geocoder = clients.geocoder

            # Load IBGE records — used by both destinos + atrativos. Reads the seeded
            # municipios reference table (was a static CSV before §3).
            ibge_records = load_ibge_municipios(session)

            if bulk_national:
                # ---- Bulk national branch (Phase 15, TA-12) -----------------------
                # DISTINCT path: paginate geoId 294280 via produce_paginated. No destinos
                # producer / destino_rio_map (parent-less bulk ingest). Per-page commits
                # happen inside produce_paginated; this branch only seeds/finishes progress
                # and reuses the SHARED fail-fast except below on a mid-run 403/429.
                rc = engine_rc
                # Standalone bulk runs are dispatched directly (scripts/ta_bulk_sweep.py), not
                # via engine_sweep_run, so the task claims itself — per execution, paired with
                # the producer_done in the finally (a Celery retry re-claims on its re-run).
                bulk_run_id = collection_engine.claim_producer(rc, run_id)

                # Resume: when a prior run recorded progress, continue from the page AFTER
                # the last completed offset (offset//30 + 2). Otherwise start a fresh run at
                # the operator-supplied start_page (default page 1 / offset 0).
                _progress = sweep_progress.get_progress(rc)
                if _progress["pages_done"] > 0:
                    _resume_offset = sweep_progress.get_resume_offset(rc)
                    _effective_start_page = (_resume_offset // 30) + 2
                else:
                    _effective_start_page = start_page
                    _resume_offset = (start_page - 1) * 30

                sweep_progress.start(
                    rc,
                    pages_total=334,
                    resume_from_offset=_resume_offset,
                )

                bulk_ingest = TripAdvisorAtrativosIngest(
                    ta_client=ta_client,
                    session=session,
                    config=config,
                    ibge_records=ibge_records,
                    destino_rio_map=None,
                    geocoder=geocoder,
                )
                try:
                    halted = asyncio.run(
                        _using(
                            clients,
                            bulk_ingest.produce_paginated(
                                geo_id,
                                _effective_start_page,
                                max_pages or 334,
                                rc,
                                run_rio=run_rio,
                                run_id=bulk_run_id,
                            ),
                        )
                    )
                except ProviderBalanceError:
                    sweep_progress.stop(rc)  # the failure policy then pauses the motor
                    raise
                # A pause/off/stop is not "done": the pages did not run out.
                if halted:
                    sweep_progress.stop(rc)
                else:
                    sweep_progress.mark_done(rc)
                # Terminal commit (produce_paginated already commits per page).
                session.commit()
                return

            # Build destino_rio_map: keyed by municipio_id (IBGE code) → (rio_id, source_ref)
            # Query ALL destination RioRecords in this UF — Mtur/IBGE origin=100 are the
            # authoritative source (oa3: TA does not produce destinos; QID not captured).
            # Operator must run a destinos/default sweep (Mtur seed) before a TA atrativos
            # sweep, or atrativos will quarantine with parent_destino_absent per record.
            import asyncio as _asyncio

            from sqlalchemy import select as _select

            from brave.core.models import NascenteRecord as _NascenteRecord
            from brave.core.models import RioRecord as _RioRecord
            session.flush()
            destino_rows = session.execute(
                _select(_RioRecord.id, _NascenteRecord.source_ref, _RioRecord.municipio_id)
                .join(_NascenteRecord, _RioRecord.nascente_id == _NascenteRecord.id)
                .where(
                    _NascenteRecord.entity_type == "destination",
                    _RioRecord.uf == uf,
                )
            ).all()
            # Map ibge_code → (rio_id, source_ref)
            destino_rio_map: dict = {
                row.municipio_id: (row.id, row.source_ref)
                for row in destino_rows
                if row.municipio_id
            }

            # Build the INLINE Places enrichment agent (distrito + hours/contact/price +
            # liveness — never the description, see below), run per-record inside produce()
            # after Rio routing — like the other completude steps. Constructed once per sweep
            # behind run_real_externals + the operator flags; the Null clients keep the TA floor + advance the record offline
            # (ZERO external spend). Replaces the old post-produce enrich_description/_places
            # dispatch, which the 600s time_limit could kill before it ran.
            # Build resiliently: a client-construction failure (e.g. a missing key) disables
            # inline enrichment for this sweep and logs — it must never crash the ingest.
            places_agent = None
            _distritos: list = []
            try:
                from brave.domains.places.places_enrichment import PlacesEnrichmentAgent
                from brave.shared.ibge_distritos import load_distritos

                _distritos = load_distritos(session)
                if effective.places_enrichment_enabled:
                    _places_client = clients.places
                else:
                    from brave.clients.null_places import NullPlacesClient
                    _places_client = NullPlacesClient()

                # The sweep NEVER writes descriptions, whatever the flags: description_enabled
                # =False makes PlacesEnrichmentAgent skip the whole description block cleanly.
                # Descriptions come later, per UF, from brave.describe_uf (engine action
                # "describe") or the batch lane (submit/collect_description_batch).
                from brave.clients.null_llm import NullLLMClient

                places_agent = PlacesEnrichmentAgent(
                    places_client=_places_client,
                    session=session,
                    config=config,
                    llm_client=NullLLMClient(),
                    distritos=_distritos,
                    voice_model_slug=effective.atrativo_voice_model_slug,
                    description_enabled=False,
                    enable_web_search=effective.run_real_externals,
                    max_distance_km=effective.places_match_max_distance_km,
                )
            except Exception:  # noqa: BLE001 — enrichment build must not crash the sweep
                logger.warning("inline_enrichment_build_failed", uf=uf)
                places_agent = None

            # Run atrativos producer using destino_rio_map.
            # ta_config=ta_config wires the TripAdvisorConfig instance so the
            # fetch_attraction_geo ftx geo-linkage guard activates under real externals.
            atrativos_ingest = TripAdvisorAtrativosIngest(
                ta_client=ta_client,
                session=session,
                config=config,
                ibge_records=ibge_records,
                destino_rio_map=destino_rio_map,
                geocoder=geocoder,
                ta_config=ta_config,
                places_agent=places_agent,
                distritos=_distritos,
            )
            # Per-UF path enriches review recency (fetch_recent_review per card) so
            # atualidade lifts the reliability score. The bulk_national branch above leaves
            # enrichment OFF (no per-card review calls at 10k scale).
            # redis=engine_rc lets the per-UF producer honor a mid-run Motor Pausado/
            # Desligado (engine.should_halt_producer) — otherwise the fanned-out producer
            # keeps paginating + inserting atrativos/synthesized destinos after a pause.
            ingested_rio_ids = _asyncio.run(
                _using(
                    clients,
                    atrativos_ingest.produce(
                        uf,
                        run_rio=run_rio,
                        enrich_reviews=True,
                        redis=engine_rc,
                        max_per_uf=max_per_uf,
                    ),
                )
            )

            session.commit()

            # Enrichment now runs INLINE inside produce() (per record, via places_agent) — no
            # post-produce dispatch. This survives a task-kill (per-record commit) where the old
            # dispatch loop was never reached when the 600s time_limit fired mid-produce.

    except (SessionMissingError, SessionExpiredError) as exc:
        # Operator error: session not injected (Missing) or expired at DataDome (Expired).
        # Do NOT retry — retries would silently ingest 0 records each time.
        # Do NOT quarantine — this is not a pipeline bug.
        # Set the needs_bootstrap marker so EngineControl shows the operator signal.
        session.rollback()
        _mark_needs_bootstrap()
        # Bulk branch only: flip the live progress panel to its terminal
        # stopped_needs_bootstrap state. GUARDED — the per-UF path reaches this
        # same except with rc still None, so an unguarded call would raise
        # UnboundLocalError (T-15-07-04). No retry, no quarantine (unchanged).
        if rc is not None:
            sweep_progress.stop_needs_bootstrap(rc)
        # R1: token expired → engine OFF — operator must inject a valid session before re-starting
        # R1 is a HARD off (operator must re-inject a session before restarting).
        # set_mode(DESLIGADO) subsumes set_enabled(False) + idle + inflight=0 AND
        # resets the operator mode — without it the engine lands at enabled=0 while
        # mode stays LIGADO, which makes the topbar "Ligar" button a no-op (stuck UI).
        # Redis-only (no session) keeps this fail-fast path from ever raising.
        collection_engine.set_mode(engine_rc, collection_engine.DESLIGADO)
        logger.warning(
            "sweep_tripadvisor_session_fail_fast",
            uf=uf,
            # T-12-04-01: log only the exception class name, never exc str
            # (exc str may contain cookie fragments from error context)
            error_type=type(exc).__name__,
        )
        return  # No retry, no quarantine — operator must re-inject session

    finally:
        # Producer-completes lifecycle: engine_sweep_run claimed the per-UF producer before
        # .delay (Retry-guarded: only the terminal run counts). The bulk branch claimed
        # itself this execution, so it always pays that claim back.
        if not bulk_national:
            _producer_done(run_id)
        elif bulk_run_id is not None:
            _producer_done(bulk_run_id, retrying_counts=True)
        session.close()


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.find_contacts",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def find_contacts_task(self, rio_id: str) -> None:
    """Advance one RioRecord from discovered → contacts_found (ContactFinderAgent).

    Idempotency guard: ContactFinderAgent.run() short-circuits if sub_state != "discovered".
    Client selection: clients_for — real clients only when run_real_externals=True (D-18).

    Args:
        rio_id: UUID string of the RioRecord to advance.
    """
    from brave.domains.places.contact_finder_agent import ContactFinderAgent

    session, engine = _get_session()
    try:
        with task_failure_policy(self, session, "brave.find_contacts", payload={"rio_id": rio_id}):
            rio_uuid = uuid.UUID(rio_id)
            # FOR UPDATE: ContactFinderAgent merges its writes onto the row's current
            # `normalized` under the same lock; taking it here holds it for the whole task so a
            # concurrent writer (copy_batch collect) cannot commit between our read and our write.
            rio = session.get(RioRecord, rio_uuid, with_for_update=True)
            if rio is None:
                raise PermanentError(f"RioRecord {rio_id} not found")

            # Idempotency: ContactFinderAgent.run() handles sub_state guard internally
            clients = clients_for(_load_config(session))
            agent = ContactFinderAgent(
                places_client=clients.places,
                session=session,
            )

            asyncio.run(_using(clients, agent.run(rio)))
            session.commit()

            # ORCH-02 / D-03: continue the chain only if this record actually advanced to
            # contacts_found (the ContactFinder inline guard short-circuits a duplicate/stale
            # dispatch — in which case we must NOT enqueue). Re-read sub_state after commit and
            # dispatch gather_signals_task (_dispatch_chain). Keyed on sub_state, not a return
            # value (D-03); replay-safe via the signal_agent guard (D-04).
            session.refresh(rio)
            if rio.sub_state == "contacts_found":
                _dispatch_chain(gather_signals_task, str(rio_id))

    finally:
        session.close()


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.gather_signals",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def gather_signals_task(self, rio_id: str) -> None:
    """Advance one RioRecord from contacts_found → signals_gathered → score (SignalAgent).

    After SignalAgent.run():
      - CLOSED_* places → routing=descarte, sub_state=None
      - Open places → reliability scored; borderline → sub_state=aguardando_consulta_whatsapp

    Idempotency guard: SignalAgent.run() short-circuits if sub_state != "contacts_found".

    Args:
        rio_id: UUID string of the RioRecord to advance.
    """
    from brave.domains.places.signal_agent import SignalAgent

    session, engine = _get_session()
    try:
        with task_failure_policy(self, session, "brave.gather_signals", payload={"rio_id": rio_id}):
            rio_uuid = uuid.UUID(rio_id)
            # FOR UPDATE: same reason as find_contacts_task — SignalAgent merges under this lock.
            rio = session.get(RioRecord, rio_uuid, with_for_update=True)
            if rio is None:
                raise PermanentError(f"RioRecord {rio_id} not found")

            effective = _load_config(session)
            config = effective.score

            clients = clients_for(effective)
            agent = SignalAgent(
                places_client=clients.places,
                session=session,
                config=config,
            )

            asyncio.run(_using(clients, agent.run(rio)))
            session.commit()

            # ORCH-02 / D-03: continue the chain only if this record actually advanced to
            # signals_gathered (a CLOSED / no-recent-reviews record is terminal DLQ with
            # sub_state=None, and must NOT be enriched). Re-read after commit and dispatch
            # enrich_places_task (the single enrichment agent — description + distrito + hours +
            # liveness). Keyed on sub_state; replay-safe via the Places agent's own guard.
            session.refresh(rio)
            if rio.sub_state == "signals_gathered":
                _dispatch_chain(enrich_places_task, str(rio_id))

    finally:
        session.close()


def _description_on(effective: AppConfig) -> bool:
    """The inline copywriter gate: real externals + description flag ON + batch lane OFF.

    description_enabled is gated on run_real_externals so an offline/CI run never writes
    the Null canned string; under batch mode the description is produced later by
    submit/collect_description_batch (50% off tokens) instead.
    """
    return bool(
        effective.run_real_externals
        and effective.description_enrichment_enabled
        and not effective.atrativo_description_batch_enabled
    )


class _EnrichCtx(NamedTuple):
    """The per-record-invariant inputs of _enrich_one, built once per chunk/task."""

    effective: AppConfig
    distritos: Any
    ibge_lookup: Any  # cached loader: the ~16k-row map loads once, and only for real Places


def _enrich_ctx(session: Session) -> _EnrichCtx:
    """Load config + the IBGE distritos table once, not once per atrativo.

    Only DB/Redis-backed state lives here. The async HTTP clients are built per
    asyncio.run (clients_for): a pooled connection must not outlive its loop.
    """
    from brave.clients.places import load_municipio_name_ibge_lookup
    from brave.shared.ibge_distritos import load_distritos

    return _EnrichCtx(
        _load_config(session),
        load_distritos(session),
        functools.cache(functools.partial(load_municipio_name_ibge_lookup, session)),
    )


def _enrich_agent(
    session: Session,
    ctx: _EnrichCtx,
    clients: Any,
    rio_id: str | None = None,
    llm_session: Any = None,
    *,
    describe: bool = False,
) -> Any:
    """Build the PlacesEnrichmentAgent off ``clients`` (the adapters) + the operator flags.

    Shared by enrich_places_task and brave.describe_uf so the per-UF description producer
    walks exactly the same path. ``llm_session`` is where the copywriter writes its
    llm_generations rows (default: ``session``); describe_uf passes a _RowBuffer so its
    gathered coroutines never touch the Session.
    """
    from brave.clients.null_llm import NullLLMClient
    from brave.clients.null_places import NullPlacesClient
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent

    effective = ctx.effective

    # The operator-toggleable places_enrichment_enabled flag (config_settings overlay,
    # /painel) gates the Places sub-step. When off, the Null client keeps the TA floor and
    # the agent still advances sub_state + re-scores — ZERO Google Places spend.
    if effective.places_enrichment_enabled:
        places_client = clients.places
    else:
        logger.info("places_enrichment_disabled", rio_id=rio_id)
        places_client = NullPlacesClient()

    # Copywriter (description sub-step) only under _description_on. ``describe`` is the hard
    # rule on top of the flags: ONLY brave.describe_uf (the Painel's "describe" action)
    # passes it. A sweep / enrich_places_task / repair script never writes a description,
    # whatever the overlay says.
    desc_on = describe and _description_on(effective)
    if desc_on:
        copy_llm = clients.llm(
            "atrativo_copywriter", session=session if llm_session is None else llm_session
        )
        copy_search = clients.search()
    else:
        copy_llm = NullLLMClient()
        copy_search = None

    return PlacesEnrichmentAgent(
        places_client=places_client,
        session=session,
        config=effective.score,
        llm_client=copy_llm,
        distritos=ctx.distritos,
        voice_model_slug=effective.atrativo_voice_model_slug,
        description_enabled=desc_on,
        enable_web_search=effective.run_real_externals,
        max_distance_km=effective.places_match_max_distance_km,
        search_client=copy_search,
        cascade_model=effective.atrativo_cascade_model,
    )


def _enrich_one(session: Session, rio: RioRecord, ctx: _EnrichCtx | None = None) -> None:
    """Run PlacesEnrichmentAgent on one RioRecord (no commit — the caller owns it)."""
    if ctx is None:
        ctx = _enrich_ctx(session)
    clients = clients_for(ctx.effective, ibge_lookup=ctx.ibge_lookup)
    agent = _enrich_agent(session, ctx, clients, str(rio.id))
    asyncio.run(_using(clients, agent.run(rio)))


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.enrich_places",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def enrich_places_task(self, rio_id: str) -> None:
    """Enrich one atrativo (PlacesEnrichmentAgent): description + distrito + hours/contact/
    price + review liveness, off one Google place_details call.

    Standalone entry for the Places-FSM discovery lane (gather_signals → here) AND manual
    DLQ re-enrich / 90-day refresh backfill. The TA sweep runs the SAME agent INLINE inside
    produce() — this task is the non-inline path. Graceful degradation: no confident match /
    no details keeps the TA floor and the record still advances + re-scores. CLOSED_* on a
    confident match → descarte.

    Idempotency guard: PlacesEnrichmentAgent.run() short-circuits unless
    sub_state in (None, "signals_gathered"). Client selection: real Places/LLM clients (via
    clients_for) only when run_real_externals=True AND the respective flag (D-18); else Null
    (ZERO spend).

    Args:
        rio_id: UUID string of the RioRecord to enrich.
    """
    session, engine = _get_session()
    try:
        with task_failure_policy(self, session, "brave.enrich_places", payload={"rio_id": rio_id}):
            rio_uuid = uuid.UUID(rio_id)
            # FOR UPDATE, like find_contacts / gather_signals: a duplicate (the sweeper
            # re-dispatching while the original is still queued) blocks here, then reads
            # google_enriched and skips the paid Places sub-step.
            rio = session.get(RioRecord, rio_uuid, with_for_update=True)
            if rio is None:
                raise PermanentError(f"RioRecord {rio_id} not found")

            _enrich_one(session, rio)
            session.commit()

    finally:
        session.close()


# Places chain: the task that moves a record OUT of each sub_state. Only `discovered` has
# another way back in (the daily discover re-query); a record left at contacts_found or
# signals_gathered by a failed .delay or an exhausted task waits here for the sweeper.
_CHAIN_NEXT_TASK: dict[str, str] = {
    "discovered": "find_contacts_task",
    "contacts_found": "gather_signals_task",
    "signals_gathered": "enrich_places_task",
}
_STALLED_AFTER_MINUTES = 30  # younger records may still have their task queued
_STALLED_BATCH = 50
_QUARANTINE_COOLDOWN_DAYS = 7  # a quarantined record is not paid for again before this


@shared_task(name="brave.redispatch_stalled_chain", time_limit=300)
def redispatch_stalled_chain() -> int:
    """Beat (15 min): _redispatch_stalled_chain, its failures kept for the Painel."""
    try:
        count = _redispatch_stalled_chain()
    except Exception as exc:
        _beat_health("brave.redispatch_stalled_chain", exc)
        raise
    _beat_health("brave.redispatch_stalled_chain")
    return count


def _redispatch_stalled_chain() -> int:
    """Re-dispatch the next chain task for atrativos stuck mid-chain.

    Picks up to 50 attractions at discovered / contacts_found / signals_gathered whose
    sub_state has not moved for 30 min (NULL = unknown = old enough), oldest first, and
    .delay()s the task that advances each one. The chain tasks are idempotent (sub_state
    guard under FOR UPDATE), so a duplicate costs no Places call.

    Skips the whole round unless the motor is LIGADO, run_real_externals is on and the
    ``default`` lane is enabled — every one of these tasks calls Google Places (paid).
    Depth gate, mirrored from discover_atrativo_task: under a NASCENTE_RIO run the chain is
    never kicked, so ``discovered`` is left alone.

    A re-dispatched row gets sub_state_changed_at = now, so the column reads "last chain
    activity" and a stuck record goes to the back of the line instead of holding one of the
    50 slots forever. A record quarantined in the last 7 days (its task exhausted its
    retries, or failed permanently) is skipped: each attempt would pay for up to four Places
    calls every 30 min. After the cooldown it gets one more try per quarantine — a transient
    Places outage does not strand a record forever.
    """
    from datetime import datetime, timedelta  # noqa: PLC0415

    import redis as _redis_lib  # noqa: PLC0415
    from sqlalchemy import String, and_, cast, exists, or_, update  # noqa: PLC0415

    from brave.config.runtime import enabled_sources  # noqa: PLC0415
    from brave.core import engine as collection_engine  # noqa: PLC0415
    from brave.core.models import PoisonQuarantine  # noqa: PLC0415

    rc = _redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))
    session, _ = _get_session()
    try:
        effective = _load_config(session)
        if (
            not effective.run_real_externals
            or "default" not in enabled_sources(effective)
            or collection_engine.get_mode(rc, session=session) != collection_engine.LIGADO
        ):
            return 0
        states = list(_CHAIN_NEXT_TASK)
        if collection_engine.get_depth(rc) == collection_engine.NASCENTE_RIO:
            states.remove("discovered")
        now = datetime.now(UTC)
        rows = session.execute(
            select(RioRecord.id, RioRecord.sub_state)
            .where(
                RioRecord.entity_type == "attraction",
                RioRecord.sub_state.in_(states),
                # signals_gathered is also where a FINISHED record rests: enrich_places never
                # moves sub_state, it only marks google_enriched. So only a record enrich never
                # finished is stalled there (and SignalAgent clears sub_state on a dlq routing).
                or_(
                    RioRecord.sub_state != "signals_gathered",
                    and_(
                        RioRecord.routing != "dlq",
                        RioRecord.normalized["google_enriched"].as_string().is_(None),
                    ),
                ),
                or_(
                    RioRecord.sub_state_changed_at.is_(None),
                    RioRecord.sub_state_changed_at
                    < now - timedelta(minutes=_STALLED_AFTER_MINUTES),
                ),
                ~exists().where(
                    PoisonQuarantine.payload["rio_id"].as_string()
                    == cast(RioRecord.id, String),
                    PoisonQuarantine.quarantined_at
                    > now - timedelta(days=_QUARANTINE_COOLDOWN_DAYS),
                ),
            )
            .order_by(RioRecord.sub_state_changed_at.asc().nulls_first())
            .limit(_STALLED_BATCH)
        ).all()
        if not rows:
            return 0
        session.execute(
            update(RioRecord)
            .where(RioRecord.id.in_([r.id for r in rows]))
            .values(sub_state_changed_at=now)
        )
        session.commit()
    finally:
        session.close()

    counts: dict[str, int] = {}
    for rio_id, sub_state in rows:
        globals()[_CHAIN_NEXT_TASK[sub_state]].delay(str(rio_id))
        counts[sub_state] = counts.get(sub_state, 0) + 1
    logger.info("stalled_chain_redispatched", total=len(rows), **counts)
    return len(rows)


# Records per describe_uf run. Each one can take up to enrich_places' 300s budget, so a
# chunk fits the hour time_limit; a full chunk self-chains the next one by id cursor.
_DESCRIBE_CHUNK = 25
# Copywriter calls (search + LLM) in flight at once inside one chunk. Network I/O only:
# every Session access stays serial, outside the gathered coroutines.
_DESCRIBE_CONCURRENCY = 5


class _RowBuffer:
    """Session stand-in for the gathered copywriter calls: holds their llm_generations
    rows until the serial write phase adds them to the real Session."""

    def __init__(self) -> None:
        self.rows: list[Any] = []

    def add(self, row: Any) -> None:
        self.rows.append(row)

    def flush(self) -> None:
        pass


async def _describe_chunk(
    session: Session,
    agent: Any,
    clients: Any,
    rows: _RowBuffer,
    jobs: list[tuple[uuid.UUID, tuple[str, str, str, str]]],
    stop: Any,
    uf: str,
) -> bool:
    """One describe_uf chunk in ONE event loop. True when the soft time limit cut it short.

    Phase 1 gathers the copywriter I/O, _DESCRIBE_CONCURRENCY at a time, and never touches
    the Session. ``stop(rio_id)`` (engine halt + cost guard) runs per record, right before
    its I/O. Phase 2 hands each result to agent.run and commits, one record at a time — a
    failure on either side rolls back that record only. A ProviderBalanceError stops new
    fetches and phase 2, but is raised only after what was fetched is written and the
    spend rows are committed.
    """
    from celery.exceptions import SoftTimeLimitExceeded  # noqa: PLC0415

    sem = asyncio.Semaphore(_DESCRIBE_CONCURRENCY)
    fetched: dict[uuid.UUID, Any] = {}
    balance: ProviderBalanceError | None = None

    async def _fetch(rio_id: uuid.UUID, args: tuple[str, str, str]) -> None:
        nonlocal balance
        async with sem:
            if balance is not None or stop(rio_id):
                return
            try:
                # details={}: every record here is google_enriched (no Places context).
                fetched[rio_id] = await agent.write_description(*args[:3], {}, args[3])
            except SoftTimeLimitExceeded:
                raise
            except ProviderBalanceError as exc:
                # No further fetch starts; the ones in flight finish (and are paid for),
                # so their results and spend rows are written below.
                balance = exc
                return
            except Exception as exc:  # noqa: BLE001 — kept per record, raised in phase 2
                fetched[rio_id] = exc

    cut = False
    async with clients:
        try:
            # _fetch keeps every per-record failure in ``fetched`` and a billing wall in
            # ``balance`` (raised once the spend rows are committed); only the soft time
            # limit escapes, and the results already in ``fetched`` are still written below.
            await asyncio.gather(*(_fetch(rio_id, args) for rio_id, args in jobs))
        except SoftTimeLimitExceeded:
            cut = True
            logger.warning("describe_uf_soft_time_limit", uf=uf, fetched=len(fetched))

        for rio_id, _args in jobs:
            result = fetched.get(rio_id)
            if result is None:  # stopped or cut before its I/O finished
                continue
            try:
                if isinstance(result, Exception):
                    raise result
                rio = session.get(RioRecord, rio_id)  # fresh: a batch may have claimed it
                if rio is not None:
                    await agent.run(rio, description=result)
                session.commit()
            except SoftTimeLimitExceeded:
                # ~60s before the hard kill, which would skip the finally and leak the
                # inflight token: drop this record and hand the rest of the UF on.
                session.rollback()
                cut = True
                logger.warning("describe_uf_soft_time_limit", uf=uf, rio_id=str(rio_id))
                break
            except ProviderBalanceError as exc:
                # Not a per-record failure: stop here, still commit the spend below.
                session.rollback()
                balance = exc
                break
            except Exception:  # noqa: BLE001 — one bad record must not abort the chunk
                session.rollback()
                logger.warning("describe_uf_record_failed", uf=uf, rio_id=str(rio_id), exc_info=True)

        # The spend happened whatever became of each record, so these rows go in their own
        # commit instead of riding (and rolling back with) a record's transaction.
        try:
            session.add_all(rows.rows)
            session.commit()
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.warning("describe_uf_llm_generations_failed", uf=uf, exc_info=True)
    if balance is not None:
        raise balance
    return cut


@shared_task(
    name="brave.describe_uf",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=3600,
    soft_time_limit=3540,
)
def describe_uf(
    uf: str,
    max_n: int | None = None,
    after_id: str | None = None,
    *,
    run_id: str | None = None,
) -> None:
    """Write descricao_editorial for one UF's atrativos (engine action "describe").

    The TA sweep never writes descriptions; this producer backfills them afterwards,
    per UF, through the SAME agent as enrich_places_task (_enrich_agent). Only google_enriched
    records are selected, so the agent skips the paid Places sub-step and only runs the
    copywriter. Selection shares copy_batch's eligibility predicate (no FOR UPDATE — the
    agent's own descricao_batch_id guard covers a batch that claims the row meanwhile).

    Chunked + keyset cursor: up to _DESCRIBE_CHUNK ids > after_id, ordered by id. A full
    chunk (or one cut short by the soft time limit) with no halt and max_n budget left
    re-dispatches itself from the last id consumed and hands its inflight token over (no
    decrement); a Stop/pause or a tripped cost guard halts. Any other exit is terminal and runs
    _producer_done exactly once. The cursor always advances, so a record whose
    description keeps failing cannot loop the chain. A per-record failure is logged and
    rolled back — it never aborts the chunk.

    The chunk runs in one event loop (_describe_chunk): copywriter I/O concurrent, bounded
    by _DESCRIBE_CONCURRENCY; Session reads before it and writes after it, serial.
    """
    import redis as _redis_lib  # noqa: PLC0415
    from celery.exceptions import SoftTimeLimitExceeded  # noqa: PLC0415

    from brave.core import engine as collection_engine
    from brave.domains.places.copy_batch import description_candidates_filter
    from brave.domains.places.copywriter import local_hint
    from brave.observability.cost_guard import pre_dispatch_check
    from brave.shared.exceptions import CostGuardError

    rc = _redis_lib.from_url(
        os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
    )
    session, engine = _get_session()
    chained = False
    try:
        effective = _load_config(session)
        if not _description_on(effective):
            logger.warning("describe_uf_description_disabled", uf=uf)
            return
        ctx = _enrich_ctx(session)
        clients = clients_for(ctx.effective, ibge_lookup=ctx.ibge_lookup)
        # The cascade search client's build guard: fail the UF once here instead of
        # walking the whole backlog failing every record before the agent.
        reason = clients.check_search()
        if reason is not None:
            logger.warning("describe_uf_cascade_misconfigured", uf=uf, reason=reason)
            return

        limit = _DESCRIBE_CHUNK if max_n is None else min(_DESCRIBE_CHUNK, max_n)
        stmt = select(RioRecord.id).where(
            RioRecord.uf == uf,
            *description_candidates_filter(),
            # Only records whose PAID Places sub-step already ran: the agent then skips
            # Places and only writes the description. A Places-FSM record still before
            # signals_gathered is enrich_places_task's, not ours (no double Details SKU).
            RioRecord.normalized["google_enriched"].as_boolean().is_(True),
        )
        if after_id:
            stmt = stmt.where(RioRecord.id > uuid.UUID(after_id))
        ids = list(session.scalars(stmt.order_by(RioRecord.id).limit(limit)).all())
        halted = cut = False

        def _stop(rio_id: uuid.UUID) -> bool:
            nonlocal halted
            if halted:
                return True
            if collection_engine.should_halt_producer(rc):
                halted = True
                logger.info("describe_uf_halted", uf=uf, at_rio_id=str(rio_id))
                return True
            try:
                pre_dispatch_check(rc, effective.llm)
            except CostGuardError:
                # The agent swallows a tripped budget as "no attempt" and would still
                # re-score + audit every remaining record, chunk after chunk, writing
                # nothing. The budget resets at midnight; the chain ends here.
                halted = True
                collection_engine.pause_with_reason(rc, "daily_budget", action="describe")
                logger.warning("describe_uf_cost_guard", uf=uf, at_rio_id=str(rio_id))
            return halted

        if ids:
            rows = _RowBuffer()
            agent = _enrich_agent(session, ctx, clients, llm_session=rows, describe=True)
            # Everything a coroutine needs is read here, as plain values, before the gather.
            jobs = []
            for rio_id in ids:
                rio = session.get(RioRecord, rio_id)
                if rio is not None and agent.wants_description(rio):
                    norm = rio.normalized or {}
                    jobs.append((
                        rio_id,
                        (
                            norm.get("name") or "",
                            norm.get("municipio") or "",
                            rio.uf or norm.get("uf") or "",
                            local_hint(norm),  # distrito/bairro → sharper search query
                        ),
                    ))
            # End the read transaction: no connection sits idle-in-transaction through the
            # gather, and phase 2's session.get reloads every record (expired), not a copy
            # as old as the gather.
            session.rollback()
            try:
                cut = asyncio.run(_describe_chunk(session, agent, clients, rows, jobs, _stop, uf))
            except ProviderBalanceError as exc:
                # A paid provider reported a billing wall mid-chunk — pause the motor with a
                # reason and halt the chunk/chain. _describe_chunk already wrote what was
                # fetched and committed the spend rows. No retry, no self-chain (chained
                # stays False, so the finally still runs _producer_done exactly once).
                session.rollback()
                collection_engine.pause_with_reason(rc, "provider_balance", exc.provider, action="describe")
                logger.warning("describe_uf_provider_balance", uf=uf, provider=exc.provider)
                return
            except SoftTimeLimitExceeded:
                # The signal handler raises wherever the main thread is — almost always the
                # idle event loop (select), not a coroutine — so it escapes asyncio.run past
                # every handler in _describe_chunk. Still hand the UF on, and keep the spend
                # rows of the calls that finished. Their prose is lost (no attempt burned).
                cut = True
                logger.warning("describe_uf_soft_time_limit", uf=uf, in_event_loop=True)
                try:
                    session.rollback()
                    session.add_all(rows.rows)
                    session.commit()
                except Exception:  # noqa: BLE001
                    session.rollback()
                    logger.warning("describe_uf_llm_generations_failed", uf=uf, exc_info=True)

        # ponytail: the cursor moves past the whole chunk even when cut — records the soft
        # limit dropped burn no attempt and the next describe run picks them up. Resume from
        # the first unfetched id if the limit ever trips in practice (25 records, 5 at a
        # time, is minutes against a 59-minute limit).
        remaining = None if max_n is None else max_n - len(ids)
        if (
            not halted
            and (cut or len(ids) == _DESCRIBE_CHUNK)
            and (remaining is None or remaining > 0)
        ):
            describe_uf.delay(uf, remaining, after_id=str(ids[-1]), run_id=run_id)
            chained = True
    finally:
        # Only the terminal run of the chain decrements: a self-chained successor carries
        # the inflight token engine_sweep_run counted for this UF.
        if not chained:
            _producer_done(run_id)
        session.close()


# ---------------------------------------------------------------------------
# Batched atrativo descriptions (Message Batches API, 50% off tokens).
#
# When atrativo_description_batch_enabled is on, _enrich_one runs with description_enabled
# =False (the TA sweep always does) — the copywriter never fires inline. These
# two beat-driven tasks own the description instead: submit hourly, collect every 15 min.
# All the logic lives in brave/domains/places/copy_batch.py; these are transport.
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="brave.submit_description_batch",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def submit_description_batch_task(self) -> None:
    """Submit one Message Batch of atrativo descriptions (beat: hourly).

    Selection, the budget sizing, the descricao_batch_id claim and the spend reservation all
    live in copy_batch.submit_batch — which claims BEFORE it spends, so the acks_late
    redelivery this task is guaranteed on a worker kill selects nothing and bills nothing.
    Failures are logged and swallowed: beat re-fires in an hour, and a retry storm on a task
    that COMMITS spend is the wrong shape.
    """
    import redis as _redis_lib  # noqa: PLC0415

    from brave.domains.places.copy_batch import submit_batch  # noqa: PLC0415

    session, engine = _get_session()
    try:
        effective = _load_config(session)
        if not (
            effective.run_real_externals
            and effective.description_enrichment_enabled
            and effective.atrativo_description_batch_enabled
        ):
            return
        submit_batch(
            session,
            clients_for(effective).batch,
            model=effective.atrativo_voice_model_slug,
            redis_client=_redis_lib.from_url(
                os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
            ),
            llm_config=effective.llm,
        )
    except Exception as exc:  # noqa: BLE001 — beat retries on the next tick
        session.rollback()
        logger.warning("copy_batch_submit_failed", error=str(exc))
    finally:
        session.close()


@shared_task(
    bind=True,
    name="brave.collect_description_batches",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=600,
)
def collect_description_batches_task(self) -> None:
    """Apply every ENDED description batch, and reap stale claims (beat: every 15 min).

    NOT gated on atrativo_description_batch_enabled: turning batch mode off must still land
    the batches already in flight, and must still reap — otherwise those records keep a
    descricao_batch_id forever and never become eligible again.

    run_real_externals=False is the MASTER kill switch, so nothing may call Anthropic here.
    The reaper still runs, with a None client: freeing a CLAIM_BATCH_ID placeholder needs no
    probe (time alone frees it), so a crash between claim and create is still recovered. A
    stamp carrying a REAL batch id is NOT freed while externals are off — the probe that
    proves the batch is gone is an API call, and freeing on a guess resubmits and re-bills
    work Anthropic may still be holding. Those records stay stamped until externals return.

    NO PUSH is dispatched from here. collect_batches ends at route_by_score, exactly like the
    inline copywriter path; the DLQ/steward gate stays the only way into norteia-api. See the
    copy_batch module docstring for the gap that leaves (a record already ACTIVE in Mar keeps
    description:null until something re-pushes it).
    """
    import redis as _redis_lib  # noqa: PLC0415

    from brave.domains.places.copy_batch import collect_batches, reap_stale_claims  # noqa: PLC0415

    session, engine = _get_session()
    try:
        effective = _load_config(session)
        if not effective.run_real_externals:
            reap_stale_claims(session, None)
        else:
            collect_batches(
                session,
                clients_for(effective).batch,
                effective.score,
                redis_client=_redis_lib.from_url(
                    os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
                ),
                model=effective.atrativo_voice_model_slug,
            )
        _beat_health("brave.collect_description_batches")
    except Exception as exc:  # noqa: BLE001 — beat retries on the next tick
        session.rollback()
        logger.warning("copy_batch_collect_failed", error=str(exc))
        _beat_health("brave.collect_description_batches", exc)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Phase 3 — Atrativos WhatsApp conversation tasks (D-08/D-10, 03-04).
#
# These tasks replace the stubs added in 03-02. The gate router
# (/approve, inbound webhook) dispatch sites in atrativos_gate.py keep working
# unchanged — same task names ("brave.outreach", "brave.resume_conversation").
#
# An owner-confirmed atrativo is promoted in finalize_node and published by
# brave.publish_mar (push_confirmed_fn=publish_mar.delay).
# outreach_task:        asyncio.run(_run()) + LangGraph WhatsAppAgent (D-08).
# resume_conversation_task: asyncio.run(_run()) + LangGraph graph resume (D-08).
#
# NullWhatsAppClient (brave/clients/null_whatsapp.py) is used in production when
# run_real_externals=False. Test fakes are NEVER imported in production tasks
# (T-03-04-07). FakeLLMClient/FakeWhatsApp are test-only (tests/fakes/).
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.outreach",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=900,  # multi-turn conversation can span hours; 15 min limit
)
def outreach_task(self, rio_id: str) -> None:
    """Send WhatsApp outreach for an approved atrativo (gate must have approved, D-06).

    Full LangGraph WhatsAppAgent implementation (replaces stub from 03-02, D-08).
    Same task name ("brave.outreach") so gate router dispatch sites keep working.

    Flow:
      1. Create AsyncPostgresSaver from BRAVE_DB_URL (strip +psycopg prefix).
      2. await saver.setup() — creates checkpoints + checkpoint_blobs tables.
      3. Select WhatsApp client (TwilioWhatsAppClient if run_real_externals,
         else NullWhatsAppClient; test fakes never imported here, T-03-04-07).
      4. Select LLM client.
      5. build_graph(wa_client, llm_client, session, redis, rio, config, settings,
                    checkpointer=saver).
      6. thread_id = f"atrativo:{rio_id}" — keyed by UUID, never phone. Pitfall 2.
      7. await graph.ainvoke(initial_state, config={"configurable": {"thread_id": ...}}).

    asyncio.run(_run()) pattern (Pitfall 5 — sync Celery worker
    cannot directly await; each task invocation creates and tears down its own event loop).

    Error handling: full try/except/finally pattern matching existing tasks.

    Args:
        rio_id: UUID string of the RioRecord to outreach.
    """
    from brave.shared.whatsapp.agent import build_graph

    session, engine = _get_session()
    try:
        with task_failure_policy(self, session, "brave.outreach", payload={"rio_id": rio_id}):
            rio_uuid = uuid.UUID(rio_id)
            # CR-04: lock the row (SELECT ... FOR UPDATE) so the idempotency guard and
            # the send are serialized — two concurrent dispatches for the same rio_id
            # cannot both pass the guard and double-send. The second waits on the lock,
            # re-reads the advanced/changed state, and no-ops.
            rio = session.get(RioRecord, rio_uuid, with_for_update=True)
            if rio is None:
                raise PermanentError(f"RioRecord {rio_id} not found")

            # Idempotency: only send if sub_state is whatsapp_in_progress
            if rio.sub_state != "whatsapp_in_progress":
                return  # Already advanced past this step — idempotent no-op

            effective = _load_config(session)
            config = effective.score

            # WhatsApp + LLM adapters (production: Twilio/Real or Null; never Fake, T-03-04-07)
            clients = clients_for(effective)
            wa_client = clients.whatsapp
            llm_client = clients.llm("atrativos", session=session)
            redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
            import redis as redis_lib
            redis_client = redis_lib.from_url(redis_url)

            settings = effective.whatsapp

            async def _run() -> Any:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                db_url = os.environ.get("BRAVE_DB_URL", "")
                # Strip SQLAlchemy driver prefix — langgraph-checkpoint-postgres
                # expects plain postgresql:// (not postgresql+psycopg://)
                pg_dsn = db_url.replace("postgresql+psycopg://", "postgresql://")

                saver = await AsyncPostgresSaver.from_conn_string(pg_dsn)
                await saver.setup()  # creates checkpoints + checkpoint_blobs tables

                graph = build_graph(
                    wa_client=wa_client,
                    llm_client=llm_client,
                    session=session,
                    redis_client=redis_client,
                    rio=rio,
                    config=config,
                    settings=settings,
                    push_confirmed_fn=publish_mar.delay,
                    checkpointer=saver,
                )

                thread_id = f"atrativo:{rio_id}"
                # Extract contact phone from the canonical ContactFinder location
                # (CR-03): normalized["contacts"]["phone_e164"].
                contact_phone = _extract_contact_phone(rio)
                if not contact_phone:
                    # No reachable owner — route to DLQ instead of dispatching an
                    # empty send / writing a consent row keyed on "".
                    rio.routing = "dlq"
                    rio.dlq_reason = "no_contact_phone"
                    rio.sub_state = None
                    logger.warning(
                        "outreach_no_contact_phone",
                        rio_id=rio_id,
                    )
                    return
                outreach_template = settings.approved_templates[0] if settings.approved_templates else "norteia_v1"

                initial_state = {
                    "rio_id": rio_id,
                    "contact_phone": contact_phone,
                    "messages": [],
                    "extraction": None,
                    "opted_out": False,
                    "window_open": True,
                    "last_inbound_at": None,
                    "turns": 0,
                    "max_turns": 3,
                    "outreach_template": outreach_template,
                    "message_text": "",
                }

                final_state = await graph.ainvoke(
                    initial_state,
                    config={"configurable": {"thread_id": thread_id}},
                )
                return final_state, contact_phone

            run_result = asyncio.run(_using(clients, _run()))
            # R2 Option B (DASH-05): append the produced OUTBOUND ask message(s) read from
            # the graph's FINAL state to the append-only conversation_message log, on this
            # task's OWN session, BEFORE the single commit below (alongside the saver — the
            # AsyncPostgresSaver persistence is untouched). Tolerant of the no-contact-phone
            # early return (run_result is None → nothing appended).
            if run_result is not None:
                final_state, used_phone = run_result
                _log_conversation_messages(
                    session=session,
                    rio_id=rio_id,
                    contact_phone=used_phone,
                    final_state=final_state,
                )
            session.commit()

    finally:
        session.close()


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.resume_conversation",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def resume_conversation_task(self, rio_id: str, reply_text: str) -> None:
    """Resume LangGraph conversation on inbound reply (n8n thin transport, D-08).

    Full LangGraph graph resume implementation (replaces stub from 03-02).
    Same task name ("brave.resume_conversation") so inbound webhook dispatch keeps working.

    Flow:
      1. Create AsyncPostgresSaver from BRAVE_DB_URL.
      2. Build graph with same checkpointer pattern as outreach_task.
      3. thread_id = f"atrativo:{rio_id}" — same key as outreach_task.
      4. Idempotency: if rio.sub_state != "whatsapp_in_progress" → return.
      5. Update state with message_text (inbound reply) and resume graph.
         The graph loads from checkpoint → recv_reply_node → extract/followup/finalize.

    asyncio.run(_run()) pattern: same as outreach_task (Pitfall 5).

    Args:
        rio_id:     UUID string of the RioRecord whose conversation to resume.
        reply_text: Raw inbound message body from the owner (from n8n/Twilio webhook).
    """
    from brave.shared.whatsapp.agent import build_graph

    session, engine = _get_session()
    try:
        with task_failure_policy(
            self,
            session,
            "brave.resume_conversation",
            payload={"rio_id": rio_id},
        ):
            rio_uuid = uuid.UUID(rio_id)
            # CR-04: lock the row so two concurrent inbound webhooks for the same
            # rio_id (owner double-tap / Twilio re-delivery) cannot both pass the
            # guard, resume the same checkpoint, and double-send a follow-up. The
            # second waits on the lock, re-reads the state, and no-ops if the
            # conversation already advanced past whatsapp_in_progress.
            rio = session.get(RioRecord, rio_uuid, with_for_update=True)
            if rio is None:
                raise PermanentError(f"RioRecord {rio_id} not found")

            # Idempotency: only resume if conversation is still active
            if rio.sub_state != "whatsapp_in_progress":
                return  # Conversation already completed or never started — no-op

            effective = _load_config(session)
            config = effective.score

            # WhatsApp + LLM adapters (production: Twilio/Real or Null; never Fake, T-03-04-07)
            clients = clients_for(effective)
            wa_client = clients.whatsapp
            llm_client = clients.llm("atrativos", session=session)
            redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
            import redis as redis_lib
            redis_client = redis_lib.from_url(redis_url)

            settings = effective.whatsapp

            # Canonical contact phone for masking the conversation_message rows (R3).
            contact_phone = _extract_contact_phone(rio)

            async def _run() -> Any:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

                db_url = os.environ.get("BRAVE_DB_URL", "")
                pg_dsn = db_url.replace("postgresql+psycopg://", "postgresql://")

                saver = await AsyncPostgresSaver.from_conn_string(pg_dsn)
                await saver.setup()

                graph = build_graph(
                    wa_client=wa_client,
                    llm_client=llm_client,
                    session=session,
                    redis_client=redis_client,
                    rio=rio,
                    config=config,
                    settings=settings,
                    push_confirmed_fn=publish_mar.delay,
                    checkpointer=saver,
                )

                thread_id = f"atrativo:{rio_id}"

                # Resume from checkpoint: pass reply_text as message_text state update.
                # LangGraph loads from AsyncPostgresSaver checkpoint → runs from recv_reply_node.
                # The message_text field is read by recv_reply_node from state.
                resume_state = {
                    "message_text": reply_text,
                }

                final_state = await graph.ainvoke(
                    resume_state,
                    config={"configurable": {"thread_id": thread_id}},
                )
                return final_state

            final_state = asyncio.run(_using(clients, _run()))
            # R2 Option B (DASH-05): append BOTH the INBOUND reply_text AND any follow-up
            # OUTBOUND message + extraction snapshot read from the graph's FINAL state to the
            # append-only conversation_message log, on this task's OWN session, BEFORE the
            # single commit below (alongside the saver — AsyncPostgresSaver is untouched).
            _log_conversation_messages(
                session=session,
                rio_id=rio_id,
                contact_phone=contact_phone,
                final_state=final_state,
                inbound_text=reply_text,
            )
            session.commit()

    finally:
        session.close()


# ---------------------------------------------------------------------------
# Phase F — LLM WhatsApp-number discovery (manual DLQ→WhatsApp batch, no-celular branch)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.discover_whatsapp_number",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def discover_whatsapp_number_task(self, rio_id: str) -> None:
    """Discover a WhatsApp number for a gated atrativo, then outreach or bounce to DLQ.

    Dispatched by the manual DLQ→WhatsApp batch endpoint (dlq.py) for the no-celular
    branch: an eligible atrativo was moved to sub_state="aguardando_consulta_whatsapp"
    but carries NO normalized["contact"]["whatsapp_candidate"]. This task asks the LLM
    for a plausible number.

    Offline (run_real_externals=False, the default / CI): NullLLMClient returns no
    number → the record routes straight back to DLQ (aguardando_consulta_whatsapp → None)
    with dlq_reason="no_contact_found". Deterministic, no network, keyless.

    Real (run_real_externals=True, opt-in): on a found celular the task populates
    normalized["contacts"]["phone_e164"] (raw, for consent/outreach) AND
    normalized["contact"]["whatsapp_candidate"] (MASKED, for the board), advances
    aguardando_consulta_whatsapp → whatsapp_in_progress, and dispatches outreach_task
    via the dispatch-then-inline-fallback idiom (same as the batch endpoint).

    Idempotency (D-01): only proceeds while sub_state == "aguardando_consulta_whatsapp".
    A replay after the record already advanced (or bounced) is a no-op. CR-04: the row
    is held with SELECT ... FOR UPDATE for the whole task so a concurrent resume cannot
    interleave.

    Args:
        rio_id: UUID string of the RioRecord (an atrativo parked at the WhatsApp gate).
    """
    from sqlalchemy.orm.attributes import flag_modified

    from brave.core.atrativos.state_machine import advance_sub_state
    from brave.core.models import whatsapp_candidate_from_phone
    from brave.domains.places.contact_finder_agent import _normalize_phone_e164
    from brave.domains.places.number_discovery import discover_number

    session, engine = _get_session()
    try:
        with task_failure_policy(
            self,
            session,
            "brave.discover_whatsapp_number",
            payload={"rio_id": rio_id},
        ):
            rio_uuid = uuid.UUID(rio_id)
            # CR-04: hold the row lock for the whole task so a concurrent inbound/resume
            # cannot interleave with the discovery → advance/bounce write.
            rio = session.get(RioRecord, rio_uuid, with_for_update=True)
            if rio is None:
                raise PermanentError(f"RioRecord {rio_id} not found")

            # Idempotency (D-01): only run while parked at the gate awaiting a number.
            if rio.sub_state != "aguardando_consulta_whatsapp":
                return

            # LLM adapter (D-18): Null offline (no number), Real opt-in.
            clients = clients_for(_load_config(session))
            normalized = rio.normalized or {}
            raw_phone = asyncio.run(
                _using(
                    clients,
                    discover_number(
                        clients.llm("atrativos", session=session),
                        name=normalized.get("name") or "",
                        uf=rio.uf,
                        address=normalized.get("address"),
                    ),
                )
            )

            # Only a MOBILE (celular) number is a plausible WhatsApp — whatsapp_candidate_from_phone
            # returns the MASKED celular or None (landline / no number). The raw E.164 is kept
            # separately for the consent/outreach path.
            masked_candidate = whatsapp_candidate_from_phone(raw_phone)

            if raw_phone and masked_candidate is not None:
                phone_e164 = _normalize_phone_e164(raw_phone)
                new_normalized = dict(normalized)
                contacts = dict(new_normalized.get("contacts") or {})
                contacts["phone_e164"] = phone_e164
                new_normalized["contacts"] = contacts
                # Store the WhatsApp candidate ALREADY MASKED (LGPD R3) — never the raw celular.
                new_normalized["contact"] = {"whatsapp_candidate": masked_candidate}
                rio.normalized = new_normalized
                flag_modified(rio, "normalized")

                # Found → approve for outreach (aguardando → whatsapp_in_progress).
                advance_sub_state(
                    session,
                    rio,
                    "aguardando_consulta_whatsapp",
                    "whatsapp_in_progress",
                    actor="number_discovery",
                    validate=True,
                    lock=False,
                )
                session.commit()

                # No inline .run — its failure would quarantine THIS task's record. The
                # sweeper does not cover the WhatsApp states (it must never send on its own),
                # so a failed enqueue bounces the record back to the DLQ, where the operator
                # sees it and can move it to the gate again, instead of stranding it at
                # whatsapp_in_progress, invisible to the gate queue.
                if not _dispatch_chain(outreach_task, rio_id):
                    advance_sub_state(
                        session,
                        rio,
                        "whatsapp_in_progress",
                        None,
                        actor="number_discovery",
                        validate=True,
                        lock=False,
                    )
                    session.commit()
                    logger.error("outreach_dispatch_failed_back_to_dlq", rio_id=rio_id)
                    return

                logger.info("whatsapp_number_found", rio_id=rio_id)
                return

            # Not found → back to DLQ (aguardando_consulta_whatsapp → None) with a distinct reason.
            advance_sub_state(
                session,
                rio,
                "aguardando_consulta_whatsapp",
                None,
                actor="number_discovery",
                validate=True,
                lock=False,
            )
            rio.routing = "dlq"
            rio.dlq_reason = "no_contact_found"
            session.commit()

            logger.info("whatsapp_number_not_found", rio_id=rio_id)

    finally:
        session.close()


# ---------------------------------------------------------------------------
# Collection engine — operator-controlled start/stop sweep orchestrator
# ---------------------------------------------------------------------------

# Maps a domain's SweepDispatch.task_name (stable public celery name) to the
# producer task's ATTRIBUTE in THIS module. The dispatch loop resolves the producer
# via ``globals()[attr]`` (not the celery registry) so a test that monkeypatches
# ``pipeline.discover_atrativo_task`` / ``pipeline.sweep_tripadvisor``
# still intercepts the ``.delay`` — the registry indirection stays transparent.
_PRODUCER_ATTR_BY_TASK_NAME: dict[str, str] = {
    "brave.discover_atrativo": "discover_atrativo_task",
    "brave.sweep_tripadvisor": "sweep_tripadvisor",
}


@shared_task(
    bind=True,
    name="brave.engine_sweep_run",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=3600,  # paces across up to 27 UFs; only dispatches (does not await)
)
def engine_sweep_run(
    self,
    ufs: list[str] | None = None,
    lane: str = "both",
    depth: str | None = None,
    source: str = "default",
    run_id: str | None = None,
    max_per_uf: int | None = None,
    action: str = "sweep",
) -> dict:
    """Operator-started full sweep orchestrator (engine ON).

    Fans out the per-source producer tasks per UF — for the ``default`` (Google
    Places) lane that is discover_atrativo_task (atrativos, which auto-chains and
    STOPS at the WhatsApp gate); for ``tripadvisor`` it is sweep_tripadvisor. The
    domain owns its lane→producer routing (``sweep_plan``). Between UFs it re-reads
    the Redis engine state and breaks the loop the moment Stop is requested:
    already-dispatched UF tasks finish on the workers (graceful drain), no further
    UFs are fanned out, and the engine returns to idle.

    Depth gate (plan 10-02): depth is read ONCE at the authenticated /start edge
    (plan 10-01) and passed in as an arg — never re-read from Redis in this loop,
    so a stale/mutated Redis depth mid-run cannot escalate spend (T-10-04). It is
    threaded down to each producer as its own depth arg:
      - NASCENTE: the ``default`` lane has NO free producer (the Mtur destino seed is
        retired; Places always costs) → nothing is dispatched for it under NASCENTE.
      - NASCENTE_RIO / NASCENTE_RIO_MAR: honor lane as today. The difference is
        downstream: nascente_rio runs producers + Rio but does not kick the
        atrativos WhatsApp-gate chain; nascente_rio_mar kicks it.
    depth=None (legacy/direct call) defaults to NASCENTE_RIO_MAR. The sweep adds
    NO automated promote_to_mar / Mar push under any depth — Mar push stays on
    the unchanged human DLQ gate + WhatsApp finalize path (ENG-05).

    Never auto-validates, never reaches the WhatsApp send path — it only kicks the
    same producer/chain tasks the beat and /sweep endpoint already use.

    action="describe" swaps the per-UF producer for describe_uf (descriptions for the
    UF's already-swept atrativos, capped by max_per_uf); depth/source are unused then.
    The state/mode gates and the inflight lifecycle are identical.
    """
    import time as _time

    import redis as redis_lib

    from brave.core import engine as collection_engine
    from brave.domains import get_domain
    from brave.tasks.beat_schedule import UF_LIST

    # Resolve the domain ONCE per run — single-source-per-run (brave:engine:source is
    # read once at /start and threaded in as ``source``). The domain owns its
    # lane→producer routing; this loop never names a source.
    domain = get_domain(source) if action == "sweep" else None

    redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
    rc = redis_lib.from_url(redis_url)
    targets = ufs or list(UF_LIST)
    per_uf_delay = float(os.environ.get("BRAVE_ENGINE_UF_DELAY_SECONDS", "3"))

    # depth read once at /start (10-01) and passed in; default full for legacy callers.
    effective_depth = depth or collection_engine.NASCENTE_RIO_MAR
    nascente_only = effective_depth == collection_engine.NASCENTE

    dispatched = 0
    try:
        for uf in targets:
            if collection_engine.get_state(rc) != collection_engine.RUNNING:
                logger.info("engine_stop_drain", at_uf=uf, dispatched=dispatched)
                break
            # Motor Pausado (phase C): the operator mode is orthogonal to runtime
            # state — PAUSADO/DESLIGADO break the loop (no new UFs, no auto-push)
            # while the graceful-drain contract above stays intact. Read per-UF so a
            # mid-run pause takes effect on the next iteration; the finally block then
            # marks idle + finalizes the run as parcial.
            if collection_engine.get_mode(rc) != collection_engine.LIGADO:
                logger.info("engine_mode_pause_drain", at_uf=uf, dispatched=dispatched)
                break
            # Registry-driven dispatch: the domain returns which producer task(s) to
            # fan out for this UF+depth+lane (the former ``if source == ...`` ladder now
            # lives in each domain's ``sweep_plan``). Each producer still ``.delay()``s
            # onto the single 'celery' queue; behavior is byte-identical per source.
            if action == "describe":
                producers = [(describe_uf, (uf, max_per_uf), {})]
            else:
                producers = [
                    (
                        globals()[_PRODUCER_ATTR_BY_TASK_NAME[_spec.task_name]],
                        _spec.args,
                        _spec.kwargs,
                    )
                    for _spec in domain.sweep_plan(
                        uf,
                        depth=effective_depth,
                        lane=lane,
                        nascente_only=nascente_only,
                        max_per_uf=max_per_uf,
                    )
                ]
            stale = False
            for _producer, _args, _kwargs in producers:
                # Producer-completes lifecycle: claim this producer BEFORE dispatch so the
                # run stays RUNNING/syncing until its terminal finally calls producer_done
                # with the same run_id. None = this orchestrator's run is no longer the
                # current one (a newer start replaced it) — stop dispatching for it.
                claimed = collection_engine.claim_producer(rc, run_id)
                if claimed is None:
                    stale = True
                    break
                _producer.delay(*_args, **_kwargs, run_id=claimed)
            if stale:
                logger.info("engine_stale_run_drain", at_uf=uf, dispatched=dispatched)
                break
            collection_engine.progress(rc, run_id, uf=uf)
            dispatched += 1
            logger.info(
                "engine_uf_dispatched",
                uf=uf,
                dispatched=dispatched,
                lane=lane,
                depth=effective_depth,
            )
            if per_uf_delay > 0:
                _time.sleep(per_uf_delay)
    finally:
        # The dispatch loop is done but the fanned-out producers keep running (live
        # kanban), so the motor is NOT turned off here unless nothing is in flight — the
        # fast paths where the loop dispatched nothing (paused/stopped before the first
        # UF) or every producer already finished. Otherwise the LAST producer's
        # producer_done completes the run.
        if _lifecycle(collection_engine.dispatch_finished, rc, run_id):
            logger.info("engine_run_complete", dispatched=dispatched, depth=effective_depth)
        else:
            logger.info(
                "engine_dispatch_complete_producers_inflight",
                dispatched=dispatched,
                depth=effective_depth,
            )

    return {"dispatched": dispatched, "lane": lane, "depth": effective_depth, "source": source}


def _lifecycle(verb: Any, rc: Any, run_id: str | None) -> bool:
    """Call an engine lifecycle verb (producer_done / dispatch_finished) with a fresh DB
    session for the runs_history finalize. BEST-EFFORT: a Redis/DB hiccup must never
    break the task's own result or error handling — it is logged and reads as False.
    Without a DB the Redis lifecycle still runs (session None skips only the row write).
    """
    try:
        session, _ = _get_session()
    except Exception as exc:  # noqa: BLE001
        logger.warning("engine_lifecycle_no_db", error=str(exc))
        session = None
    try:
        return verb(rc, session, run_id)
    except Exception as exc:  # noqa: BLE001 — best-effort; never break the task
        logger.warning("engine_lifecycle_failed", verb=verb.__name__, error=str(exc))
        return False
    finally:
        if session is not None:
            session.close()


def _producer_done(run_id: str | None, *, retrying_counts: bool = False) -> None:
    """Producer-completes lifecycle — called ONCE in a producer's outermost finally.

    A Celery Retry unwinding through the finally is NOT a terminal outcome: self.retry()
    raises Retry, Celery re-queues, and the RE-RUN hits the finally again. The claim
    fires ONCE per logical dispatch (orchestrator), so producer_done must fire once per
    TERMINAL outcome — skip it while a Retry is in flight. ``retrying_counts`` is for a
    producer that claims itself per execution (the standalone bulk branch): every
    execution pays its own claim back.
    """
    import sys  # noqa: PLC0415

    import redis as _r  # noqa: PLC0415
    from celery.exceptions import Retry  # noqa: PLC0415

    from brave.core import engine as collection_engine  # noqa: PLC0415

    if not retrying_counts and isinstance(sys.exc_info()[1], Retry):
        return
    try:
        rc = _r.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))
    except Exception:  # noqa: BLE001 — best-effort; never break the producer
        return
    _lifecycle(collection_engine.producer_done, rc, run_id)


@shared_task(
    bind=False,
    max_retries=0,
    name="brave.ta_keepalive",
    ignore_result=True,
)
def ta_keepalive() -> None:
    """Keep-alive beat: refresh DataDome cookies when session is live (260629-p2v).

    Fires on a periodic interval (BRAVE_TA_KEEPALIVE_INTERVAL_SECONDS, default 600s).
    Issues ONE light AttractionsFusion GraphQL page (the SAME transport the sweep uses)
    to re-mint datadome + __vt. Cookie write-back happens inside the fetch
    (session.persist_rotated_cookies); a successful ping also slides the session TTL
    explicitly, so a 200 that carries no Set-Cookie still keeps the session alive.

    Skips silently when:
      - run_real_externals is False (offline / CI)
      - No session in Redis (brave:ta:session TTL <= 0)

    On 403/SessionExpiredError/SessionMissingError (260917-tkd):
      Counts CONSECUTIVE failures in brave:ta:keepalive_failures and marks
      needs_bootstrap only from the third one on. It NEVER touches the engine.
      Deciding a session is dead belongs to the sweep, which fails fast and turns the
      motor off (R1) — a health beat must not be what kills a healthy run. The old
      behaviour (HTML transport + set_mode(DESLIGADO)) cut the 2026-09-15 pilot at 59
      atrativos: DataDome 403s the HTML page while the GraphQL sweep keeps getting 200.

    On any other exception: logs error_type at WARNING and returns. Never raises.

    T-p2v-02: Never logs cookie values or str(exc) — error_type and ttl_before only.
    """
    app_config = AppConfig()
    if not app_config.run_real_externals:
        logger.debug("ta_keepalive_skipped_offline")
        _beat_health("brave.ta_keepalive")  # a skip is not a failure: clear a stale one
        return

    import redis as _redis_lib  # noqa: PLC0415

    _redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
    rc = _redis_lib.from_url(_redis_url)

    from brave.domains.tripadvisor.client import BRAVE_TA_SESSION_KEY  # noqa: PLC0415

    ttl = rc.ttl(BRAVE_TA_SESSION_KEY)
    if ttl <= 0:
        logger.debug("ta_keepalive_skipped_no_session")
        _beat_health("brave.ta_keepalive")
        return

    from brave.config.settings import TripAdvisorConfig  # noqa: PLC0415
    from brave.domains.tripadvisor.client import (  # noqa: PLC0415
        SessionExpiredError,
        SessionMissingError,
        TripAdvisorClient,
    )

    ta_config = TripAdvisorConfig()
    ta_client = TripAdvisorClient(config=ta_config, redis=rc)

    try:
        import asyncio as _asyncio  # noqa: PLC0415

        async def _ping() -> None:
            # ONE AttractionsFusion GraphQL page (all-Brazil geoId 294280, page 1) to
            # re-mint datadome — the same transport, host and query the sweep uses, so
            # the beat can never call a session dead while the sweep is collecting.
            # The fetch calls persist_rotated_cookies internally.
            async for _offset, _cards in ta_client.fetch_attractions_paginated_gql(
                geo_id=294280, start_page=1, max_pages=1
            ):
                break  # one page is enough; write-back happened inside

        _asyncio.run(_ping())
        # Slide the TTL even when the response carried no Set-Cookie: the ping proved
        # the session works, and persist_rotated_cookies only writes when cookies rotate.
        rc.expire(BRAVE_TA_SESSION_KEY, ta_config.session_ttl)
        rc.delete(_TA_KEEPALIVE_FAILURES_KEY)
        _beat_health("brave.ta_keepalive")
        logger.info("ta_keepalive_ok", ttl_before=ttl)

    except (SessionExpiredError, SessionMissingError) as exc:
        # NEVER touches the engine (260917-tkd). Consecutive failures only; the marker
        # is operator visibility, and the sweep still owns the R1 hard-off.
        falhas = _bump_keepalive_failures(rc, ta_config.session_ttl)
        if falhas >= _KEEPALIVE_FAILURES_BEFORE_BOOTSTRAP:
            _mark_needs_bootstrap()
        # A dead session shows on the TA session pill, not as a failing beat.
        _beat_health("brave.ta_keepalive")
        logger.warning(
            "ta_keepalive_session_expired",
            error_type=type(exc).__name__,
            falhas_consecutivas=falhas,
            marcou_bootstrap=falhas >= _KEEPALIVE_FAILURES_BEFORE_BOOTSTRAP,
            # T-p2v-02: never log str(exc) — may contain cookie fragments
        )

    except Exception as exc:  # noqa: BLE001
        # Unknown error (DNS, proxy, asyncio loop conflict) — log and return.
        # The beat scheduler MUST NOT crash; the next interval fires normally.
        logger.warning(
            "ta_keepalive_error",
            error_type=type(exc).__name__,
        )
        _beat_health("brave.ta_keepalive", exc)


# ---------------------------------------------------------------------------
# RecordEvent retention — nightly prune (Log tab, Decisão C)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="brave.prune_record_events",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def prune_record_events_task(self, retention_days: int = 90) -> int:
    """Nightly prune of aged RecordEvent rows (retention Decisão C).

    Deletes RecordEvent rows with ``status IN ('ok', 'skip')`` and
    ``created_at < now() - retention_days``. Rows with ``status='fail'``
    (quarantine / incident records) are PRESERVED indefinitely — they are the
    record of the failure and must never age out.

    Single-queue model: dispatched onto the default 'celery' queue (no
    options.queue on the beat entry). Idempotent: re-running only deletes rows
    that still exceed the retention window, so a replay is a no-op once the
    aged 'ok'/'skip' rows are gone.

    Args:
        retention_days: Age threshold in days (default 90). Rows older than
                        now() - retention_days AND status IN ('ok','skip') go.

    Returns:
        The number of rows deleted.
    """
    from datetime import datetime, timedelta

    from sqlalchemy import delete

    from brave.core.models import RecordEvent

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    session, engine = _get_session()
    try:
        result = session.execute(
            delete(RecordEvent).where(
                RecordEvent.status.in_(("ok", "skip")),
                RecordEvent.created_at < cutoff,
            )
        )
        session.commit()
        deleted = result.rowcount or 0
        _beat_health("brave.prune_record_events")
        logger.info(
            "prune_record_events_ok",
            deleted=deleted,
            retention_days=retention_days,
        )
        return deleted
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        # Retention prune is best-effort maintenance — a failure must not crash
        # the beat scheduler; the next nightly run retries the same window.
        logger.warning(
            "prune_record_events_error",
            error_type=type(exc).__name__,
        )
        _beat_health("brave.prune_record_events", exc)
        return 0
    finally:
        session.close()


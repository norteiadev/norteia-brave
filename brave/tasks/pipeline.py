"""Celery pipeline tasks (D-05, D-06, CORE-10).

Four tasks:
  process_nascente      — ingest NascenteRecord through Rio pipeline
  push_mar              — push scored RioRecord to Mar layer + norteia-api
  reprocess_record_task — re-score an existing RioRecord
  push_destination_task — Phase 2 destino-specific push (D-09). Always calls
                          push_destination — not entity-agnostic.

Idempotency: Every task is a no-op on re-run (D-03, D-15).
Poison quarantine: After max_retries failures, the task goes to PoisonQuarantine,
                   NOT to the review DLQ (see PITFALLS §7, T-02-02).

Error classification:
  TransientError (network flap, DB timeout) → self.retry with backoff
  PermanentError (malformed payload, schema violation) → quarantine_poison
  Any exception after max_retries → quarantine_poison

push_mar provenance flattening (D-15, D-16):
  Mar push payload uses the flat per-criterion shape required by the Pact contract:
    {"origem": float, "completude": float, "corroboracao": float,
     "atualidade": float, "validacao_humana": float}
  The promote_to_mar service writes provenance as:
    {"score_breakdown": {...flat...}, "score_version": ..., "nascente_id": ..., "rio_id": ...}
  push_mar flattens score_breakdown to top-level provenance keys for the API push.
"""

import asyncio
import os
import uuid
from typing import Any

import structlog
from celery import shared_task
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from brave.clients.norteia_api import NorteiaApiClient
from brave.config.settings import AppConfig, ScoreConfig
from brave.core.models import RioRecord
from brave.core.nascente.service import get_nascente
from brave.core.rio.routing import process_nascente_record, reprocess_record

logger = structlog.get_logger(__name__)

# Redis key that sweep_tripadvisor sets when a session error halts the sweep.
# Operator must re-inject a fresh session via POST /api/v1/tripadvisor/session
# and then re-trigger the sweep. Cleared when a new session is successfully injected.
_TA_NEEDS_BOOTSTRAP_KEY = "brave:ta:needs_bootstrap"


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


class TransientError(Exception):
    """Transient failure — retry with backoff (network, DB timeout, etc.)."""


class PermanentError(Exception):
    """Permanent failure — quarantine, do not retry (malformed payload, etc.)."""


# ---------------------------------------------------------------------------
# Session factory (lazy — resolved at task call time, not import time)
# ---------------------------------------------------------------------------


def _get_session() -> tuple[Session, Any]:
    """Create a synchronous SQLAlchemy session from environment config.

    Returns (session, engine) pair; caller must close both.
    """
    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        raise PermanentError("BRAVE_DB_URL not set — cannot create DB session")
    engine = create_engine(db_url, echo=False)
    SessionFactory = sessionmaker(bind=engine)
    return SessionFactory(), engine


# ---------------------------------------------------------------------------
# Poison quarantine helper (re-exported from brave.core.quarantine — D-18)
# ---------------------------------------------------------------------------

# quarantine_poison is defined in brave/core/quarantine.py so that lane code
# (e.g. DesmembramentoAgent in brave/lanes/destinos/) can import it from core
# without depending on the tasks layer.  This re-export keeps existing callers
# working without any change.
from brave.core.quarantine import quarantine_poison  # noqa: F401 (re-export)

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
        nascente_uuid = uuid.UUID(nascente_id)
        config = ScoreConfig()

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

    except PermanentError as exc:
        session.rollback()
        # Re-open session for quarantine write
        q_session, q_engine = _get_session()
        try:
            quarantine_poison(
                session=q_session,
                nascente_id=uuid.UUID(nascente_id) if nascente_id else None,
                task_name="brave.process_nascente",
                error=str(exc),
            )
            q_session.commit()
        finally:
            q_session.close()
            q_engine.dispose()

    except Exception as exc:
        session.rollback()
        try:
            # Retry transient errors
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            # After max_retries, quarantine
            q_session, q_engine = _get_session()
            try:
                quarantine_poison(
                    session=q_session,
                    nascente_id=uuid.UUID(nascente_id) if nascente_id else None,
                    task_name="brave.process_nascente",
                    error=str(exc),
                )
                q_session.commit()
            finally:
                q_session.close()
                q_engine.dispose()

    finally:
        session.close()
        engine.dispose()


def _build_push_payload(mar_record: Any, rio_record: RioRecord) -> dict[str, Any]:
    """Build the flat-provenance Mar push payload from a MarRecord + RioRecord.

    The Pact contract shape (D-16) requires flat per-criterion provenance keys:
        {"origem": float, "completude": float, "corroboracao": float,
         "atualidade": float, "validacao_humana": float}

    promote_to_mar stores provenance as:
        {"score_breakdown": {...flat criteria...}, "score_version": str,
         "nascente_id": str, "rio_id": str}

    This function flattens score_breakdown to top-level provenance keys.

    Args:
        mar_record: MarRecord returned by promote_to_mar.
        rio_record: Source RioRecord (for entity_type, source, source_ref).

    Returns:
        Dict matching the Pact contract Mar push shape.
    """
    provenance_raw = mar_record.provenance or {}
    score_breakdown = provenance_raw.get("score_breakdown", {})
    score_version = provenance_raw.get("score_version", mar_record.score_version or "v1.0")

    # Flat per-criterion provenance (the Pact contract shape, D-16)
    flat_provenance = {
        "origem": float(score_breakdown.get("origem", 0.0)),
        "completude": float(score_breakdown.get("completude", 0.0)),
        "corroboracao": float(score_breakdown.get("corroboracao", 0.0)),
        "atualidade": float(score_breakdown.get("atualidade", 0.0)),
        "validacao_humana": float(score_breakdown.get("validacao_humana", 0.0)),
    }

    # Extract source and source_ref from source_ref (format "source:UF:id")
    # source_ref = "mtur:BA:123" → source = "mtur"
    source_ref = mar_record.source_ref or ""
    source_parts = source_ref.split(":", 1)
    source = source_parts[0] if source_parts else "unknown"

    canonical = mar_record.canonical or {}

    return {
        "source": source,
        "source_ref": source_ref,
        "entity_type": mar_record.entity_type,
        "canonical": canonical,
        "reliability_score": float(mar_record.reliability_score),
        "score_version": score_version,
        "provenance": flat_provenance,
    }


@shared_task(
    bind=True,
    max_retries=3,
    name="brave.push_mar",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def push_mar(self, rio_id: str) -> None:
    """Push a scored RioRecord to the Mar layer and norteia-api (D-15, D-16, CORE-05).

    Pipeline:
      1. Load RioRecord; if routing != 'mar', no-op (idempotent).
      2. Call promote_to_mar to create/update MarRecord (idempotent by source_ref).
      3. Build flat-provenance push payload (Pact contract shape).
      4. POST to norteia-api via NorteiaApiClient (Bearer auth, tenacity retry).

    Idempotency:
      - promote_to_mar is idempotent by source_ref (D-15).
      - norteia-api is an idempotent upsert by source_ref — double push is safe.
      - If routing != 'mar', returns immediately.

    Error handling:
      TransientError (5xx from norteia-api) → tenacity retries in NorteiaApiClient.
      PermanentError → log and return (no quarantine for push errors in Phase 1).
      After max_retries → Celery retry; after max → pass (Phase 3 adds retry DLQ).

    Args:
        rio_id: UUID string of the RioRecord to push.
    """
    from brave.clients.null_norteia_api import NullNorteiaApiClient
    from brave.core.mar.service import promote_to_mar

    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        rio = session.get(RioRecord, rio_uuid)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        # Idempotency: only process mar-routed records
        if rio.routing != "mar":
            return  # Not ready for Mar — idempotent no-op

        # Step 1: Promote to Mar layer (idempotent by source_ref, D-15)
        mar = promote_to_mar(session, rio)
        session.commit()

        # Step 2: Determine which client to use
        app_config = AppConfig()
        if app_config.run_real_externals:
            norteia_api_url = os.environ.get("BRAVE_NORTEIA_API_URL", "")
            norteia_service_token = os.environ.get("BRAVE_NORTEIA_API_SERVICE_TOKEN", "")
            api_client = NorteiaApiClient(
                base_url=norteia_api_url,
                service_token=norteia_service_token,
            )
        else:
            api_client = NullNorteiaApiClient()

        # Step 3: Build flat-provenance payload (Pact contract shape, D-16)
        payload = _build_push_payload(mar, rio)

        # Step 4: Push to norteia-api
        async def _push() -> dict[str, Any]:
            if isinstance(api_client, NorteiaApiClient):
                async with api_client as client:
                    if rio.entity_type == "destination":
                        return await client.push_destination(payload)
                    else:
                        return await client.push_attraction(payload)
            else:
                # FakeNorteiaApiClient — no context manager needed
                if rio.entity_type == "destination":
                    return await api_client.push_destination(payload)
                else:
                    return await api_client.push_attraction(payload)

        asyncio.run(_push())

    except PermanentError as exc:
        session.rollback()
        # WR-02: a permanently-failed Mar push must not vanish silently.
        logger.error("push_mar_permanent_failure", rio_id=rio_id, error=str(exc))

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            # WR-02: surface permanently-failed pushes (DLQ deferred) — at minimum log.
            logger.error(
                "push_mar_max_retries_exceeded",
                rio_id=rio_id,
                error=str(exc),
            )

    finally:
        session.close()
        engine.dispose()


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
        config = ScoreConfig()
        reprocess_record(session, uuid.UUID(rio_id), config)
        session.commit()
    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            # WR-02: surface permanently-failed reprocess (no silent drop).
            logger.error(
                "reprocess_record_max_retries_exceeded",
                rio_id=rio_id,
                error=str(exc),
            )
    finally:
        session.close()
        engine.dispose()


@shared_task(
    bind=True,
    max_retries=3,
    name="brave.push_destination",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def push_destination_task(self, rio_id: str) -> None:
    """Promote a validated DLQ destino to Mar and push to norteia-api (D-09).

    Phase 2 destino-specific push. Always calls push_destination — not
    entity-agnostic. Called by the DLQ validate endpoint after routing
    transitions to "mar".

    Pipeline:
      1. Load RioRecord; if routing != 'mar', no-op (idempotent).
      2. Call promote_to_mar to create/update MarRecord (idempotent by source_ref).
      3. Build flat-provenance push payload (Pact contract shape).
      4. POST to norteia-api via push_destination (never push_attraction).

    Idempotency:
      - promote_to_mar is idempotent by source_ref (D-15).
      - norteia-api is an idempotent upsert by source_ref — double push is safe.
      - If routing != 'mar', returns immediately.

    Error handling:
      PermanentError → log and return (no quarantine for push errors).
      After max_retries → Celery retry; after max → pass (Phase 3 adds retry DLQ).

    Args:
        rio_id: UUID string of the RioRecord to push.
    """
    from brave.clients.null_norteia_api import NullNorteiaApiClient
    from brave.core.mar.service import promote_to_mar

    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        rio = session.get(RioRecord, rio_uuid)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        # Idempotency: only process mar-routed records
        if rio.routing != "mar":
            return  # Not ready for Mar — idempotent no-op

        # Step 1: Promote to Mar layer (idempotent by source_ref, D-15)
        mar = promote_to_mar(session, rio)
        session.commit()

        # Step 2: Determine which client to use
        app_config = AppConfig()
        if app_config.run_real_externals:
            norteia_api_url = os.environ.get("BRAVE_NORTEIA_API_URL", "")
            norteia_service_token = os.environ.get("BRAVE_NORTEIA_API_SERVICE_TOKEN", "")
            api_client = NorteiaApiClient(
                base_url=norteia_api_url,
                service_token=norteia_service_token,
            )
        else:
            api_client = NullNorteiaApiClient()

        # Step 3: Build flat-provenance payload (Pact contract shape, D-16)
        payload = _build_push_payload(mar, rio)

        # Step 4: Push to norteia-api — always push_destination (D-09)
        async def _push() -> dict[str, Any]:
            if isinstance(api_client, NorteiaApiClient):
                async with api_client as client:
                    return await client.push_destination(payload)
            else:
                return await api_client.push_destination(payload)

        asyncio.run(_push())

    except PermanentError as exc:
        session.rollback()
        # WR-02: a permanently-failed destino push must not vanish silently.
        logger.error(
            "push_destination_permanent_failure", rio_id=rio_id, error=str(exc)
        )

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            # WR-02: surface permanently-failed pushes (DLQ deferred) — at minimum log.
            logger.error(
                "push_destination_max_retries_exceeded",
                rio_id=rio_id,
                error=str(exc),
            )

    finally:
        session.close()
        engine.dispose()


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
def discover_atrativo_task(self, uf: str, depth: str | None = None) -> None:
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
    Client selection: real clients only when run_real_externals=True (D-18).

    Args:
        uf: Two-letter Brazilian state code (e.g. "BA", "RJ").
        depth: Pipeline depth (nascente_rio | nascente_rio_mar). None → full.
    """
    from brave.core import engine as collection_engine
    from brave.core.quarantine import quarantine_poison as _quarantine
    from brave.lanes.atrativos.discovery_agent import DiscoveryAgent

    effective_depth = depth or collection_engine.NASCENTE_RIO_MAR

    session, engine = _get_session()
    try:
        app_config = AppConfig()
        config = ScoreConfig()

        # Select Places client based on run_real_externals flag
        if app_config.run_real_externals:
            places_api_key = os.environ.get("BRAVE_PLACES_API_KEY", "")
            from brave.clients.places import RealPlacesClient
            places_client = RealPlacesClient(api_key=places_api_key)
        else:
            from brave.clients.null_places import NullPlacesClient
            places_client = NullPlacesClient()

        # Select LLM client based on run_real_externals flag
        redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        import redis as redis_lib
        redis_client = redis_lib.from_url(redis_url)
        if app_config.run_real_externals:
            from brave.clients.llm import RealLLMClient
            llm_client = RealLLMClient(config=app_config.llm, redis_client=redis_client, session=session, lane="atrativos")
        else:
            from brave.clients.null_llm import NullLLMClient
            llm_client = NullLLMClient()

        agent = DiscoveryAgent(
            places_client=places_client,
            llm_client=llm_client,
            session=session,
            config=config,
        )

        asyncio.run(agent.produce(uf))
        session.commit()

        # ORCH-02 / D-03: fan out the FSM chain. DiscoveryAgent.produce returns None,
        # so chaining is keyed on sub_state queries (self-healing across restarts) —
        # never on a producer return value. Query every attraction this sweep landed at
        # sub_state='discovered' and dispatch find_contacts_task per row. Dispatch-then-
        # inline-fallback (swallow-all, from dlq.py): an operator/test with no broker still
        # advances the chain synchronously. Replay-safe: a duplicate dispatch hits the
        # contact_finder inline precondition guard and no-ops (D-04, finding #2).
        # Materialize the IDs up front (as strings) BEFORE dispatching. The inline
        # fallback (.run) opens/commits a session that can expire/detach live ORM rows;
        # holding ORM objects across a dispatch would raise DetachedInstanceError on the
        # next loop iteration. Selecting the scalar id column avoids that entirely.
        discovered_ids = session.scalars(
            select(RioRecord.id).where(
                RioRecord.entity_type == "attraction",
                RioRecord.uf == uf,
                RioRecord.sub_state == "discovered",
            )
        ).all()
        # Depth gate (plan 10-02): only NASCENTE_RIO_MAR kicks the WhatsApp-gate
        # FSM chain. Under NASCENTE_RIO discovery/Rio still ran above, but the
        # ENTIRE fan-out below — both the .delay dispatch AND the .run inline
        # fallback — is suppressed so the chain never advances toward the gate.
        if effective_depth != collection_engine.NASCENTE_RIO:
            for rio_id in discovered_ids:
                try:
                    find_contacts_task.delay(str(rio_id))
                except Exception:
                    find_contacts_task.run(str(rio_id))

    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            _quarantine(
                session=q_session,
                nascente_id=None,
                task_name="brave.discover_atrativo",
                error=str(exc),
                payload={"uf": uf},
            )
            q_session.commit()
        finally:
            q_session.close()
            q_engine.dispose()

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            q_session, q_engine = _get_session()
            try:
                _quarantine(
                    session=q_session,
                    nascente_id=None,
                    task_name="brave.discover_atrativo",
                    error=str(exc),
                    payload={"uf": uf},
                )
                q_session.commit()
            finally:
                q_session.close()
                q_engine.dispose()

    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Phase 5 — Destinos recurring sweep (ORCH-01, D-01/D-02)
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.sweep_uf",  # MUST be exactly this — beat_schedule.py expects it (D-01)
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=600,  # producers fan out per-município; allow headroom
)
def sweep_uf(self, uf: str, depth: str | None = None) -> None:
    """Recurring Destinos sweep for one UF (ORCH-01, D-01/D-02).

    Composes the two destino producers:
      1. MturSeedIngest.produce(uf)      — idempotent seed re-ingest (origem=100).
      2. DesmembramentoAgent.produce(uf) — recurring LLM sub-destino discovery (origem=40).

    Depth gate (plan 10-02): depth arrives as an explicit task arg (never read
    from Redis here — the orchestrator owns the read). Under depth=NASCENTE the
    Mtur seed runs Nascente-only (run_rio=False) and the LLM Desmembramento is
    skipped entirely — zero external cost. At the rio depths both run as today.
    depth=None (legacy/direct call) defaults to the full path.

    Producer-only (D-02): both producers call store_raw + process_nascente_record
    internally, so records land in DLQ/Mar/descarte by §7.6 automatically. This task
    adds NO scoring/validation branch — promotion to Mar stays behind §7.6 + the human
    DLQ steward gate. NotebookLM is NOT run here (manual report ingest only, Deferred).

    Idempotency: store_raw dedups by (source, source_ref, content_hash) (D-01), so a
    replayed sweep for the same UF is a no-op.
    Error handling: a missing Mtur CSV (FileNotFoundError) or other PermanentError is
    quarantined (PoisonQuarantine), not lost; transient errors retry then quarantine.
    Client selection: real LLM only when run_real_externals=True (D-06/D-18).

    Args:
        uf: Two-letter Brazilian state code (e.g. "BA", "RJ").
    """
    from brave.clients.mtur import MturClient
    from brave.core import engine as collection_engine
    from brave.core.quarantine import quarantine_poison as _quarantine
    from brave.lanes.destinos.desmembramento import DesmembramentoAgent
    from brave.lanes.destinos.mtur import MturSeedIngest

    # Depth derivation — nascente is the free Nascente-only path: no Rio, no LLM.
    run_rio = depth != collection_engine.NASCENTE
    run_desmembramento = depth != collection_engine.NASCENTE

    session, engine = _get_session()
    try:
        config = ScoreConfig()
        app_config = AppConfig()

        try:
            # Mtur seed re-ingest (idempotent — store_raw dedups by content_hash).
            # run_rio=False under nascente: Nascente + §7.6 score only, no Rio.
            seed = MturSeedIngest(MturClient(), session, config)
            asyncio.run(seed.produce(uf, run_rio=run_rio))

            # Desmembramento — the real recurring LLM discovery (origem=40 firewall).
            # Skipped entirely under nascente (it is paid LLM, not a free source).
            # LLM client selection mirrors discover_atrativo_task (real vs fake).
            if run_desmembramento:
                redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
                import redis as redis_lib
                redis_client = redis_lib.from_url(redis_url)
                if app_config.run_real_externals:
                    from brave.clients.llm import RealLLMClient
                    llm_client = RealLLMClient(config=app_config.llm, redis_client=redis_client, session=session, lane="destinos")
                else:
                    from brave.clients.null_llm import NullLLMClient
                    llm_client = NullLLMClient()
                # Ctor arg order: llm FIRST, then mtur (desmembramento.py:128).
                desm = DesmembramentoAgent(llm_client, MturClient(), session, config)
                asyncio.run(desm.produce(uf))
        except FileNotFoundError as exc:
            # A missing Mtur seed CSV is permanent — quarantine, never retry (T-05-03).
            raise PermanentError(f"Mtur seed CSV missing for sweep {uf}: {exc}") from exc

        session.commit()

    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            _quarantine(
                session=q_session,
                nascente_id=None,
                task_name="brave.sweep_uf",
                error=str(exc),
                payload={"uf": uf},
            )
            q_session.commit()
        finally:
            q_session.close()
            q_engine.dispose()

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            q_session, q_engine = _get_session()
            try:
                _quarantine(
                    session=q_session,
                    nascente_id=None,
                    task_name="brave.sweep_uf",
                    error=str(exc),
                    payload={"uf": uf},
                )
                q_session.commit()
            finally:
                q_session.close()
                q_engine.dispose()

    finally:
        session.close()
        engine.dispose()


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.sweep_tripadvisor",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=600,
)
def sweep_tripadvisor(self, uf: str, depth: str | None = None) -> None:
    """TripAdvisor sweep for one UF — destinos then atrativos (plan 11-03, TA-05/TA-06).

    Mirrors sweep_uf but uses the TripAdvisor ingest lane instead of Mtur/Desmembramento.
    Produces TripAdvisor destination + attraction records via the GraphQL scraper lane.

    Depth gate: depth=NASCENTE → run_rio=False (Nascente + §7.6 score only, no Rio validation).
    depth=None (legacy/direct call) defaults to the full pipeline path.

    Client selection: NullTripAdvisorClient unless AppConfig().run_real_externals
    (RUN_REAL_EXTERNALS=True, opt-in only).

    Idempotency: store_raw dedups by (source, source_ref, content_hash).

    Args:
        uf:    Two-letter Brazilian state code (e.g. "BA", "RJ").
        depth: Pipeline depth (nascente|nascente_rio|nascente_rio_mar|None).
    """
    from brave.core import engine as collection_engine
    from brave.core.quarantine import quarantine_poison as _quarantine
    from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest
    from brave.lanes.tripadvisor.client import SessionExpiredError, SessionMissingError
    from brave.lanes.tripadvisor.destinos import TripAdvisorDestinosIngest
    from brave.lanes.tripadvisor.ibge import load_ibge_csv

    run_rio = depth != collection_engine.NASCENTE

    session, engine = _get_session()
    try:
        config = ScoreConfig()
        app_config = AppConfig()

        if app_config.run_real_externals:
            import redis as _redis_lib
            from brave.lanes.tripadvisor.client import TripAdvisorClient
            from brave.config.settings import TripAdvisorConfig
            _ta_redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
            ta_config = TripAdvisorConfig()
            ta_client = TripAdvisorClient(
                config=ta_config,
                redis=_redis_lib.from_url(_ta_redis_url),
            )
        else:
            from brave.clients.null_tripadvisor import NullTripAdvisorClient
            ta_client = NullTripAdvisorClient()

        if app_config.run_real_externals:
            from brave.clients.nominatim import NominatimGeocoderClient
            geocoder = NominatimGeocoderClient(
                config=app_config.nominatim,
                redis=_redis_lib.from_url(_ta_redis_url),
            )
        else:
            from brave.clients.null_nominatim import NullGeocoderClient
            geocoder = NullGeocoderClient()

        # Load IBGE records (static CSV) — used by both destinos + atrativos
        import os as _os
        _project_root = _os.path.dirname(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        )
        ibge_csv_path = _os.path.join(_project_root, "data", "ibge", "ibge_municipios.csv")
        ibge_records = load_ibge_csv(ibge_csv_path)

        # Step 1: Run destinos producer — builds the destino_rio_map for atrativos
        destinos_ingest = TripAdvisorDestinosIngest(
            ta_client=ta_client,
            session=session,
            config=config,
            ibge_records=ibge_records,
        )
        import asyncio as _asyncio
        _asyncio.run(destinos_ingest.produce(uf, run_rio=run_rio))

        # Build destino_rio_map: keyed by ibge_code → (rio_id, source_ref)
        # Query RioRecord for TA destination records in this UF (after produce + flush)
        from sqlalchemy import select as _select
        from brave.core.models import RioRecord as _RioRecord, NascenteRecord as _NascenteRecord
        session.flush()
        destino_rows = session.execute(
            _select(_RioRecord.id, _NascenteRecord.source_ref, _RioRecord.municipio_id)
            .join(_NascenteRecord, _RioRecord.nascente_id == _NascenteRecord.id)
            .where(
                _NascenteRecord.source == "tripadvisor",
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

        # Step 2: Run atrativos producer using destino_rio_map
        atrativos_ingest = TripAdvisorAtrativosIngest(
            ta_client=ta_client,
            session=session,
            config=config,
            ibge_records=ibge_records,
            destino_rio_map=destino_rio_map,
            geocoder=geocoder,
        )
        _asyncio.run(atrativos_ingest.produce(uf, run_rio=run_rio))

        session.commit()

    except (SessionMissingError, SessionExpiredError) as exc:
        # Operator error: session not injected (Missing) or expired at DataDome (Expired).
        # Do NOT retry — retries would silently ingest 0 records each time.
        # Do NOT quarantine — this is not a pipeline bug.
        # Set the needs_bootstrap marker so EngineControl shows the operator signal.
        session.rollback()
        _mark_needs_bootstrap()
        logger.warning(
            "sweep_tripadvisor_session_fail_fast",
            uf=uf,
            # T-12-04-01: log only the exception class name, never exc str
            # (exc str may contain cookie fragments from error context)
            error_type=type(exc).__name__,
        )
        return  # No retry, no quarantine — operator must re-inject session

    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            _quarantine(
                session=q_session,
                nascente_id=None,
                task_name="brave.sweep_tripadvisor",
                error=str(exc),
                payload={"uf": uf},
            )
            q_session.commit()
        finally:
            q_session.close()
            q_engine.dispose()

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            q_session, q_engine = _get_session()
            try:
                _quarantine(
                    session=q_session,
                    nascente_id=None,
                    task_name="brave.sweep_tripadvisor",
                    error=str(exc),
                    payload={"uf": uf},
                )
                q_session.commit()
            finally:
                q_session.close()
                q_engine.dispose()

    finally:
        session.close()
        engine.dispose()


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
    Client selection: real clients only when run_real_externals=True (D-18).

    Args:
        rio_id: UUID string of the RioRecord to advance.
    """
    from brave.core.quarantine import quarantine_poison as _quarantine
    from brave.lanes.atrativos.contact_finder_agent import ContactFinderAgent

    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        rio = session.get(RioRecord, rio_uuid)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        # Idempotency: ContactFinderAgent.run() handles sub_state guard internally
        app_config = AppConfig()

        if app_config.run_real_externals:
            places_api_key = os.environ.get("BRAVE_PLACES_API_KEY", "")
            from brave.clients.places import RealPlacesClient
            places_client = RealPlacesClient(api_key=places_api_key)
        else:
            from brave.clients.null_places import NullPlacesClient
            places_client = NullPlacesClient()

        agent = ContactFinderAgent(
            places_client=places_client,
            session=session,
        )

        asyncio.run(agent.run(rio))
        session.commit()

        # ORCH-02 / D-03: continue the chain only if this record actually advanced to
        # contacts_found (the ContactFinder inline guard short-circuits a duplicate/stale
        # dispatch — in which case we must NOT enqueue). Re-read sub_state after commit and
        # dispatch gather_signals_task with the same dispatch-then-inline-fallback. Keyed on
        # sub_state, not a return value (D-03); replay-safe via the signal_agent guard (D-04).
        session.refresh(rio)
        if rio.sub_state == "contacts_found":
            try:
                gather_signals_task.delay(str(rio_id))
            except Exception:
                gather_signals_task.run(str(rio_id))

    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            _quarantine(
                session=q_session,
                nascente_id=None,
                task_name="brave.find_contacts",
                error=str(exc),
                payload={"rio_id": rio_id},
            )
            q_session.commit()
        finally:
            q_session.close()
            q_engine.dispose()

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            q_session, q_engine = _get_session()
            try:
                _quarantine(
                    session=q_session,
                    nascente_id=None,
                    task_name="brave.find_contacts",
                    error=str(exc),
                    payload={"rio_id": rio_id},
                )
                q_session.commit()
            finally:
                q_session.close()
                q_engine.dispose()

    finally:
        session.close()
        engine.dispose()


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
      - Open places → §7.6 scored; borderline → sub_state=aguardando_consulta_whatsapp

    Idempotency guard: SignalAgent.run() short-circuits if sub_state != "contacts_found".

    Args:
        rio_id: UUID string of the RioRecord to advance.
    """
    from brave.core.quarantine import quarantine_poison as _quarantine
    from brave.lanes.atrativos.signal_agent import SignalAgent

    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        rio = session.get(RioRecord, rio_uuid)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        app_config = AppConfig()
        config = ScoreConfig()

        if app_config.run_real_externals:
            places_api_key = os.environ.get("BRAVE_PLACES_API_KEY", "")
            apify_api_key = os.environ.get("BRAVE_APIFY_API_KEY", "")
            from brave.clients.apify import RealApifyClient
            from brave.clients.places import RealPlacesClient
            places_client = RealPlacesClient(api_key=places_api_key)
            apify_client = RealApifyClient(api_key=apify_api_key)
        else:
            from brave.clients.null_apify import NullApifyClient
            from brave.clients.null_places import NullPlacesClient
            places_client = NullPlacesClient()
            apify_client = NullApifyClient()

        agent = SignalAgent(
            places_client=places_client,
            apify_client=apify_client,
            session=session,
            config=config,
        )

        asyncio.run(agent.run(rio))
        session.commit()

    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            _quarantine(
                session=q_session,
                nascente_id=None,
                task_name="brave.gather_signals",
                error=str(exc),
                payload={"rio_id": rio_id},
            )
            q_session.commit()
        finally:
            q_session.close()
            q_engine.dispose()

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            q_session, q_engine = _get_session()
            try:
                _quarantine(
                    session=q_session,
                    nascente_id=None,
                    task_name="brave.gather_signals",
                    error=str(exc),
                    payload={"rio_id": rio_id},
                )
                q_session.commit()
            finally:
                q_session.close()
                q_engine.dispose()

    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Phase 3 — Atrativos WhatsApp conversation + push tasks (D-08/D-10, 03-04).
#
# These tasks replace the stubs added in 03-02. The gate router
# (/approve, inbound webhook) dispatch sites in atrativos_gate.py keep working
# unchanged — same task names ("brave.outreach", "brave.resume_conversation").
#
# push_attraction_task: mirrors push_destination_task exactly (D-10).
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
    name="brave.push_attraction",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def push_attraction_task(self, rio_id: str) -> None:
    """Promote a validated atrativo to Mar and push to norteia-api (D-10).

    Mirror of push_destination_task — always calls push_attraction, never
    push_destination. Called by finalize_node in the WhatsAppAgent after
    owner-validation confirms existe=sim / funcionando=sim.

    Pipeline:
      1. Load RioRecord; if routing != 'mar', no-op (idempotent).
      2. Call promote_to_mar to create/update MarRecord (idempotent by source_ref).
      3. Build flat-provenance push payload (Pact contract shape).
      4. POST to norteia-api via push_attraction (never push_destination — D-10).

    Idempotency:
      - promote_to_mar is idempotent by source_ref (D-15).
      - norteia-api is an idempotent upsert by source_ref — double push is safe.
      - If routing != 'mar', returns immediately.

    Error handling:
      PermanentError → log and return (no quarantine for push errors).
      After max_retries → Celery retry; after max → pass.

    Args:
        rio_id: UUID string of the RioRecord to push.
    """
    from brave.clients.null_norteia_api import NullNorteiaApiClient
    from brave.core.mar.service import promote_to_mar

    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        rio = session.get(RioRecord, rio_uuid)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        # Idempotency: only process mar-routed records
        if rio.routing != "mar":
            return  # Not ready for Mar — idempotent no-op

        # Step 1: Promote to Mar layer (idempotent by source_ref, D-15)
        mar = promote_to_mar(session, rio)
        session.commit()

        # Step 2: Determine which API client to use
        app_config = AppConfig()
        if app_config.run_real_externals:
            norteia_api_url = os.environ.get("BRAVE_NORTEIA_API_URL", "")
            norteia_service_token = os.environ.get("BRAVE_NORTEIA_API_SERVICE_TOKEN", "")
            api_client = NorteiaApiClient(
                base_url=norteia_api_url,
                service_token=norteia_service_token,
            )
        else:
            api_client = NullNorteiaApiClient()

        # Step 3: Build flat-provenance payload (Pact contract shape, D-16)
        payload = _build_push_payload(mar, rio)

        # Step 4: Push to norteia-api — always push_attraction (D-10)
        async def _push() -> dict[str, Any]:
            if isinstance(api_client, NorteiaApiClient):
                async with api_client as client:
                    return await client.push_attraction(payload)
            else:
                return await api_client.push_attraction(payload)

        asyncio.run(_push())

    except PermanentError as exc:
        session.rollback()
        # WR-02: a permanently-failed attraction push must not vanish silently.
        logger.error(
            "push_attraction_permanent_failure", rio_id=rio_id, error=str(exc)
        )

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            # WR-02: surface permanently-failed pushes (DLQ deferred) — at minimum log.
            logger.error(
                "push_attraction_max_retries_exceeded",
                rio_id=rio_id,
                error=str(exc),
            )

    finally:
        session.close()
        engine.dispose()


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

    asyncio.run(_run()) pattern: same as push_mar (Pitfall 5 — sync Celery worker
    cannot directly await; each task invocation creates and tears down its own event loop).

    Error handling: full try/except/finally pattern matching existing tasks.

    Args:
        rio_id: UUID string of the RioRecord to outreach.
    """
    from brave.clients.null_whatsapp import NullWhatsAppClient
    from brave.lanes.atrativos.whatsapp_agent import build_graph

    session, engine = _get_session()
    try:
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

        app_config = AppConfig()
        config = ScoreConfig()

        # Select WhatsApp client (production: Twilio or Null; never Fake, T-03-04-07)
        if app_config.run_real_externals:
            from brave.clients.whatsapp import TwilioWhatsAppClient
            wa_config = app_config.whatsapp
            wa_client = TwilioWhatsAppClient(
                account_sid=wa_config.twilio_account_sid,
                auth_token=wa_config.twilio_auth_token,
                from_number=wa_config.from_number,
                messaging_service_sid=wa_config.messaging_service_sid or None,
            )
        else:
            wa_client = NullWhatsAppClient()

        # Select LLM client
        redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        import redis as redis_lib
        redis_client = redis_lib.from_url(redis_url)
        if app_config.run_real_externals:
            from brave.clients.llm import RealLLMClient
            llm_client = RealLLMClient(config=app_config.llm, redis_client=redis_client, session=session, lane="atrativos")
        else:
            from brave.clients.null_llm import NullLLMClient
            llm_client = NullLLMClient()

        settings = app_config.whatsapp

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

        run_result = asyncio.run(_run())
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

    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            quarantine_poison(
                session=q_session,
                nascente_id=None,
                task_name="brave.outreach",
                error=str(exc),
                payload={"rio_id": rio_id},
            )
            q_session.commit()
        finally:
            q_session.close()
            q_engine.dispose()

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            q_session, q_engine = _get_session()
            try:
                quarantine_poison(
                    session=q_session,
                    nascente_id=None,
                    task_name="brave.outreach",
                    error=str(exc),
                    payload={"rio_id": rio_id},
                )
                q_session.commit()
            finally:
                q_session.close()
                q_engine.dispose()

    finally:
        session.close()
        engine.dispose()


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
    from brave.clients.null_whatsapp import NullWhatsAppClient
    from brave.lanes.atrativos.whatsapp_agent import build_graph

    session, engine = _get_session()
    try:
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

        app_config = AppConfig()
        config = ScoreConfig()

        # Select WhatsApp client (production: Twilio or Null; never Fake, T-03-04-07)
        if app_config.run_real_externals:
            from brave.clients.whatsapp import TwilioWhatsAppClient
            wa_config = app_config.whatsapp
            wa_client = TwilioWhatsAppClient(
                account_sid=wa_config.twilio_account_sid,
                auth_token=wa_config.twilio_auth_token,
                from_number=wa_config.from_number,
                messaging_service_sid=wa_config.messaging_service_sid or None,
            )
        else:
            wa_client = NullWhatsAppClient()

        # Select LLM client
        redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        import redis as redis_lib
        redis_client = redis_lib.from_url(redis_url)
        if app_config.run_real_externals:
            from brave.clients.llm import RealLLMClient
            llm_client = RealLLMClient(config=app_config.llm, redis_client=redis_client, session=session, lane="atrativos")
        else:
            from brave.clients.null_llm import NullLLMClient
            llm_client = NullLLMClient()

        settings = app_config.whatsapp

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

        final_state = asyncio.run(_run())
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

    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            quarantine_poison(
                session=q_session,
                nascente_id=None,
                task_name="brave.resume_conversation",
                error=str(exc),
                payload={"rio_id": rio_id},
            )
            q_session.commit()
        finally:
            q_session.close()
            q_engine.dispose()

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            q_session, q_engine = _get_session()
            try:
                quarantine_poison(
                    session=q_session,
                    nascente_id=None,
                    task_name="brave.resume_conversation",
                    error=str(exc),
                    payload={"rio_id": rio_id},
                )
                q_session.commit()
            finally:
                q_session.close()
                q_engine.dispose()

    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Collection engine — operator-controlled start/stop sweep orchestrator
# ---------------------------------------------------------------------------


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
) -> dict:
    """Operator-started full sweep orchestrator (engine ON).

    Fans out the existing producer tasks per UF — sweep_uf (destinos) and
    discover_atrativo_task (atrativos, which auto-chains and STOPS at the WhatsApp
    gate). Between UFs it re-reads the Redis engine state and breaks the loop the
    moment Stop is requested: already-dispatched UF tasks finish on the workers
    (graceful drain), no further UFs are fanned out, and the engine returns to idle.

    Depth gate (plan 10-02): depth is read ONCE at the authenticated /start edge
    (plan 10-01) and passed in as an arg — never re-read from Redis in this loop,
    so a stale/mutated Redis depth mid-run cannot escalate spend (T-10-04). It is
    threaded down to sweep_uf / discover_atrativo_task as their own depth arg:
      - NASCENTE: dispatch ONLY sweep_uf (Mtur-only, run_rio=False); atrativos are
        NEVER dispatched regardless of lane (they have no free source); no LLM.
      - NASCENTE_RIO / NASCENTE_RIO_MAR: honor lane as today. The difference is
        downstream: nascente_rio runs producers + Rio but does not kick the
        atrativos WhatsApp-gate chain; nascente_rio_mar kicks it.
    depth=None (legacy/direct call) defaults to NASCENTE_RIO_MAR. The sweep adds
    NO automated promote_to_mar / Mar push under any depth — Mar push stays on
    the unchanged human DLQ gate + WhatsApp finalize path (ENG-05).

    Never auto-validates, never reaches the WhatsApp send path — it only kicks the
    same producer/chain tasks the beat and /sweep endpoint already use.
    """
    import time as _time

    import redis as redis_lib

    from brave.core import engine as collection_engine
    from brave.tasks.beat_schedule import UF_LIST

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
            if source == "tripadvisor":
                # TripAdvisor lane — single task covers both destinos + atrativos
                # (sweep_tripadvisor runs TripAdvisorDestinosIngest then TripAdvisorAtrativosIngest).
                # Depth gate still applies: nascente-only is run_rio=False inside sweep_tripadvisor.
                sweep_tripadvisor.delay(uf, depth=effective_depth)
            elif nascente_only:
                # Free path: Mtur-only seed regardless of lane (atrativos = Places,
                # no free source). Threaded depth makes sweep_uf skip Rio + LLM.
                sweep_uf.delay(uf, depth=effective_depth)
            else:
                if lane in ("destinos", "both"):
                    sweep_uf.delay(uf, depth=effective_depth)
                if lane in ("atrativos", "both"):
                    discover_atrativo_task.delay(uf, depth=effective_depth)
            collection_engine.mark_uf_dispatched(rc, uf)
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
        collection_engine.mark_idle(rc)
        logger.info("engine_run_complete", dispatched=dispatched, depth=effective_depth)

    return {"dispatched": dispatched, "lane": lane, "depth": effective_depth, "source": source}

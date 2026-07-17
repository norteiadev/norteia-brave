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
from brave.config.runtime import load_effective_config
from brave.config.settings import AppConfig
from brave.core.models import RioRecord
from brave.core.nascente.service import get_nascente
from brave.core.rio.routing import process_nascente_record, reprocess_record
from brave.shared.exceptions import PermanentError, TransientError  # noqa: F401 (re-export)

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
#
# TransientError / PermanentError now live in the central hierarchy
# (brave/shared/exceptions.py) and are imported at the top of this module. They
# remain module-level names here so every existing raise/except in this file —
# and any importer — keeps working unchanged.


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
# (e.g. producers under brave/lanes/) can import it from core
# without depending on the tasks layer.  This re-export keeps existing callers
# working without any change.
from datetime import UTC

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
        config = load_effective_config(session).score

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
    """Build the flat-provenance Mar push payload (D-16 Pact contract shape).

    Thin shim: the logic moved to
    ``brave.core.mar.service.build_push_payload`` (returns a typed
    MarPushPayload). This returns ``.model_dump()`` so the dict is byte-identical
    to before and every call site stays unchanged.

    Args:
        mar_record: MarRecord returned by promote_to_mar.
        rio_record: Source RioRecord (kept for signature compatibility).

    Returns:
        Dict matching the Pact contract Mar push shape.
    """
    from brave.core.mar.service import build_push_payload

    return build_push_payload(mar_record, rio_record).model_dump()


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
        # Phase F: the attraction recency backstop may route to DLQ instead of
        # promoting (returns None). Commit the DLQ routing and no-op the push.
        if mar is None:
            session.commit()
            return
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
        config = load_effective_config(session).score
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
        # Phase F: the attraction recency backstop may route to DLQ instead of
        # promoting (returns None). Commit the DLQ routing and no-op the push.
        if mar is None:
            session.commit()
            return
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
        config = load_effective_config(session).score

        # Select Places client based on run_real_externals flag
        if app_config.run_real_externals:
            places_api_key = os.environ.get("BRAVE_PLACES_API_KEY", "")
            from brave.clients.places import (
                RealPlacesClient,
                load_municipio_name_ibge_lookup,
            )
            # Places API has no IBGE field — wire the name→IBGE lookup from the
            # municipios reference table so attractions get a resolved municipio_ibge
            # (required for parent-destino linkage via ensure_destino).
            places_client = RealPlacesClient(
                api_key=places_api_key,
                ibge_lookup=load_municipio_name_ibge_lookup(session),
            )
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
        # Producer-completes lifecycle: dispatched by engine_sweep_run
        # (incr_inflight before .delay); decrement so the LAST producer completes the
        # run. Best-effort, never breaks the task (single outermost finally).
        _producer_finally_lifecycle()
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
) -> None:
    """TripAdvisor sweep for one UF — atrativos only, parent destinos from authoritative Rio records (Mtur/IBGE) (oa3).

    Mirrors sweep_uf but uses the TripAdvisor ingest lane instead of the Mtur seed.
    Produces TripAdvisor attraction records only. Parent destino RioRecords must already
    exist in Rio (run Mtur seed sweep first). TA-destinos (TripAdvisorDestinosIngest) is
    not wired here — no destinos QID has been captured; deferred until QID is discovered.

    Depth gate: depth=NASCENTE → run_rio=False (Nascente + reliability score only, no Rio validation).
    depth=None (legacy/direct call) defaults to the full pipeline path.

    Client selection: NullTripAdvisorClient unless AppConfig().run_real_externals
    (RUN_REAL_EXTERNALS=True, opt-in only).

    Idempotency: store_raw dedups by (source, source_ref, content_hash).

    Bulk national branch (Phase 15, TA-12): when bulk_national=True the task takes a
    DISTINCT path that paginates the all-Brazil AttractionsFusion listing (geoId 294280)
    via TripAdvisorAtrativosIngest.produce_paginated — NO destinos producer, NO
    destino_rio_map (parent-less bulk ingest). It reads the resume offset from
    sweep_progress so a re-run continues from the page after the last completed offset
    (NOT page 1), seeds the live progress hash, commits per-page (inside produce_paginated),
    marks the run done on completion, and on a mid-run 403/429 SessionExpiredError reuses
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
    from brave.core.quarantine import quarantine_poison as _quarantine
    from brave.lanes.tripadvisor import sweep_progress
    from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest
    from brave.lanes.tripadvisor.client import SessionExpiredError, SessionMissingError
    from brave.lanes.tripadvisor.ibge import load_ibge_municipios

    run_rio = depth != collection_engine.NASCENTE

    session, engine = _get_session()
    # rc is the sync Redis client for the live progress hash. It MUST be initialized
    # before the try so the SHARED fail-fast except can reference it safely: the per-UF
    # path (which can ALSO raise SessionExpiredError) reaches that except with rc still
    # None, and the guarded `if rc is not None` keeps it from raising UnboundLocalError
    # (T-15-07-04). Only the bulk_national branch assigns rc.
    rc = None
    try:
        config = load_effective_config(session).score
        app_config = AppConfig()

        # T1 (pfr-01): ta_config must be defined before the branch so it is always
        # in scope for the per-UF TripAdvisorAtrativosIngest constructor. Without
        # this, ta_config is only defined inside the run_real_externals block and
        # the offline path would raise NameError; passing None keeps the
        # fetch_attraction_geo guard (ta_config is not None) dormant offline.
        ta_config = None
        if app_config.run_real_externals:
            import redis as _redis_lib

            from brave.config.settings import TripAdvisorConfig
            from brave.lanes.tripadvisor.client import TripAdvisorClient
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

        # Load IBGE records — used by both destinos + atrativos. Reads the seeded
        # municipios reference table (was a static CSV before §3).
        ibge_records = load_ibge_municipios(session)

        if bulk_national:
            # ---- Bulk national branch (Phase 15, TA-12) -----------------------
            # DISTINCT path: paginate geoId 294280 via produce_paginated. No destinos
            # producer / destino_rio_map (parent-less bulk ingest). Per-page commits
            # happen inside produce_paginated; this branch only seeds/finishes progress
            # and reuses the SHARED fail-fast except below on a mid-run 403/429.
            import redis as _redis_lib  # noqa: PLC0415

            rc = _redis_lib.from_url(
                os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
            )

            # Standalone bulk runs are dispatched directly (scripts/ta_bulk_sweep.py),
            # NOT via engine_sweep_run/start_run, so reset the producer-lifecycle keys to
            # a clean baseline. A stale positive inflight (or a claimed last_run_ended)
            # from a prior orchestrator run would otherwise make this run's maybe_complete
            # return False and the badge would never flip to "synced".
            rc.set(collection_engine._INFLIGHT_KEY, "0")
            rc.delete(collection_engine._DISPATCH_DONE_KEY)
            rc.delete(collection_engine._LAST_RUN_ENDED_KEY)

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
            asyncio.run(
                bulk_ingest.produce_paginated(
                    geo_id,
                    _effective_start_page,
                    max_pages or 334,
                    rc,
                    run_rio=run_rio,
                )
            )
            sweep_progress.mark_done(rc)
            # Terminal commit (produce_paginated already commits per page).
            session.commit()
            # Standalone completion: the bulk run is dispatched directly (scripts/
            # ta_bulk_sweep.py), NOT via engine_sweep_run, so it never went through the
            # incr_inflight/dispatch_done lifecycle. Latch dispatch_done and complete the
            # run inline so the badge flips DESLIGADO + "synced" (race-safe GETSET claim;
            # the shared outermost finally then no-ops on the already-claimed marker).
            _bulk_final_state = collection_engine.get_state(rc)
            collection_engine.set_dispatch_done(rc, True)
            if collection_engine.maybe_complete(rc):
                _bulk_run_id = (
                    collection_engine._decode(rc.get(collection_engine._RUN_ID_KEY))
                    or None
                )
                if _bulk_run_id:
                    _bulk_dispatched = sweep_progress.get_progress(rc).get("pages_done", 0)
                    _finalize_run_history(
                        _bulk_run_id, _bulk_dispatched, _bulk_final_state
                    )
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
        )
        # Per-UF path enriches review recency (fetch_recent_review per card) so
        # atualidade lifts the reliability score. The bulk_national branch above leaves
        # enrichment OFF (no per-card review calls at 10k scale).
        # redis=_prod_rc lets the per-UF producer honor a mid-run Motor Pausado/
        # Desligado (engine.should_halt_producer) — otherwise the fanned-out producer
        # keeps paginating + inserting atrativos/synthesized destinos after a pause.
        import redis as _prod_redis_lib  # noqa: PLC0415
        _prod_rc = _prod_redis_lib.from_url(
            os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        )
        ingested_rio_ids = _asyncio.run(
            atrativos_ingest.produce(
                uf,
                run_rio=run_rio,
                enrich_reviews=True,
                redis=_prod_rc,
                max_per_uf=max_per_uf,
            )
        )

        session.commit()

        # Description enrichment for TA atrativos. The TA lane never enters the Places
        # FSM chain (find_contacts → gather_signals → enrich_description), so this is
        # its ONLY path to descricao_editorial — dispatched at every Rio depth
        # (run_rio: nascente_rio AND nascente_rio_mar), NOT gated to _mar. It is
        # description-ONLY: no contact-finder/signal/WhatsApp steps (TA carries no
        # Google place_id). The agent is idempotent (its own sub_state guard) and the
        # actual MD-scrape + LLM spend stays behind run_real_externals +
        # description_enrichment_enabled (checked inside enrich_description_task).
        if run_rio and ingested_rio_ids:
            for _rio_id in ingested_rio_ids:
                try:
                    enrich_description_task.delay(_rio_id)
                except Exception:  # broker down → inline fallback (mirror Places lane)
                    enrich_description_task.run(_rio_id)

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
        import redis as _r1_redis  # noqa: PLC0415
        _r1_rc = rc if rc is not None else _r1_redis.from_url(
            os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        )
        # R1 is a HARD off (operator must re-inject a session before restarting).
        # set_mode(DESLIGADO) subsumes set_enabled(False) + mark_idle + inflight=0 AND
        # resets the operator mode — without it the engine lands at enabled=0 while
        # mode stays LIGADO, which makes the topbar "Ligar" button a no-op (stuck UI).
        # Redis-only (no session) keeps this fail-fast path from ever raising.
        collection_engine.set_mode(_r1_rc, collection_engine.DESLIGADO)
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
        # Producer-completes lifecycle: the per-UF TA producer is dispatched by
        # engine_sweep_run (incr_inflight before .delay). Decrement here so the LAST
        # producer completes the run. Best-effort, never breaks the task. The bulk_national
        # branch is dispatched standalone (not via engine_sweep_run) so it never
        # incremented — decr clamps at 0 and its own inline maybe_complete already
        # completed the run (idempotent: the GETSET claim makes this a no-op).
        _producer_finally_lifecycle()
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
      - Open places → reliability scored; borderline → sub_state=aguardando_consulta_whatsapp

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
        config = load_effective_config(session).score

        if app_config.run_real_externals:
            places_api_key = os.environ.get("BRAVE_PLACES_API_KEY", "")
            from brave.clients.places import RealPlacesClient
            places_client = RealPlacesClient(api_key=places_api_key)
        else:
            from brave.clients.null_places import NullPlacesClient
            places_client = NullPlacesClient()

        agent = SignalAgent(
            places_client=places_client,
            session=session,
            config=config,
        )

        asyncio.run(agent.run(rio))
        session.commit()

        # ORCH-02 / D-03: continue the chain only if this record actually advanced to
        # signals_gathered (a CLOSED / no-recent-reviews record is terminal DLQ with
        # sub_state=None, and must NOT be enriched). Re-read after commit and dispatch
        # enrich_description_task with the same dispatch-then-inline-fallback. Keyed on
        # sub_state (D-03); replay-safe via the description agent's own guard (D-04).
        session.refresh(rio)
        if rio.sub_state == "signals_gathered":
            try:
                enrich_description_task.delay(str(rio_id))
            except Exception:
                enrich_description_task.run(str(rio_id))

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


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.enrich_description",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def enrich_description_task(self, rio_id: str) -> None:
    """Advance one RioRecord signals_gathered → description_enriched (DescriptionEnrichmentAgent).

    Fuzzy-matches the atrativo to its Guia Melhores Destinos editorial page, scrapes
    the description, rewrites it in the Norteia voice, persists descricao_editorial,
    and re-scores. Graceful degradation: no MD page / no text / rewrite failure keeps
    the floor (posicionamento) and the record still advances.

    Idempotency guard: DescriptionEnrichmentAgent.run() short-circuits if
    sub_state != "signals_gathered". Client selection: real clients only when
    run_real_externals=True (D-18).

    Args:
        rio_id: UUID string of the RioRecord to enrich.
    """
    from brave.core.quarantine import quarantine_poison as _quarantine
    from brave.lanes.atrativos.description import DescriptionEnrichmentAgent

    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        rio = session.get(RioRecord, rio_uuid)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        app_config = AppConfig()
        effective = load_effective_config(session)
        config = effective.score
        md_config = app_config.melhores_destinos

        redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        import redis as redis_lib
        redis_client = redis_lib.from_url(redis_url)

        # Real clients require BOTH run_real_externals AND the operator-toggleable
        # description_enrichment_enabled flag (config_settings overlay, /painel). When the
        # flag is off, the Null clients keep the floor and the agent still advances
        # sub_state + re-scores — a real local sweep runs with ZERO LLM spend on descriptions.
        if app_config.run_real_externals and effective.description_enrichment_enabled:
            from brave.clients.melhores_destinos import RealMelhoresDestinosClient
            md_client = RealMelhoresDestinosClient(config=md_config, redis=redis_client)
            from brave.clients.llm import RealLLMClient
            llm_client = RealLLMClient(
                config=app_config.llm,
                redis_client=redis_client,
                session=session,
                lane="melhores_destinos",
            )
        else:
            if app_config.run_real_externals and not effective.description_enrichment_enabled:
                logger.info("description_enrichment_disabled", rio_id=rio_id)
            from brave.clients.null_melhores_destinos import NullMelhoresDestinosClient
            md_client = NullMelhoresDestinosClient()
            from brave.clients.null_llm import NullLLMClient
            llm_client = NullLLMClient()

        # Load the IBGE DTB distrito reference once — threads into the enrichment agent
        # for the MD breadcrumb <Place> → distrito name-match, scoped to the atrativo's
        # parent município. Mirrors the discovery lane's distrito load. Reads the seeded
        # distritos reference table (was a static CSV before §3).
        from brave.shared.ibge_distritos import load_distritos
        distritos = load_distritos(session)

        agent = DescriptionEnrichmentAgent(
            md_client=md_client,
            llm_client=llm_client,
            session=session,
            config=config,
            md_config=md_config,
            distritos=distritos,
        )

        asyncio.run(agent.run(rio))
        session.commit()

        # Chain the Google Places enrichment step (TA lane only): opening hours + review
        # liveness. Dispatched for EVERY TA-sourced record after description — NOT gated on
        # sub_state: a TA atrativo scores ~55 < 80 and dlq-bounces sub_state to None here,
        # so a sub_state gate would skip Places entirely (the agent keys idempotency on its
        # own google_enriched marker). The Places-FSM lane is skipped by the agent's
        # cross-lane guard (it already carries Google signals from SignalAgent).
        if (rio.canonical_key or "").startswith("tripadvisor:"):
            try:
                enrich_places_task.delay(rio_id)
            except Exception:  # broker down → inline fallback (mirror the TA description dispatch)
                enrich_places_task.run(rio_id)

    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            _quarantine(
                session=q_session,
                nascente_id=None,
                task_name="brave.enrich_description",
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
                    task_name="brave.enrich_description",
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
    name="brave.enrich_places",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def enrich_places_task(self, rio_id: str) -> None:
    """Advance one TA RioRecord description_enriched → places_enriched (PlacesEnrichmentAgent).

    Resolves the atrativo to a Google place_id (Text Search + name/proximity match),
    fetches Place Details, and persists Google opening hours (``weekday_text``) + review
    liveness (``atualidade`` boost via max, ``most_recent_review_at``, ``place_id_cache``).
    Graceful degradation: no confident match / no details keeps the TA floor and the
    record still advances + re-scores. CLOSED_* on a confident match → descarte.

    Idempotency guard: PlacesEnrichmentAgent.run() short-circuits if
    sub_state != "description_enriched" (and no-ops a Places-FSM record). Client
    selection: real Places client only when run_real_externals=True AND
    places_enrichment_enabled (D-18); else NullPlacesClient (ZERO Google spend).

    Args:
        rio_id: UUID string of the RioRecord to enrich.
    """
    from brave.core.quarantine import quarantine_poison as _quarantine
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent

    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        rio = session.get(RioRecord, rio_uuid)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        app_config = AppConfig()
        effective = load_effective_config(session)
        config = effective.score

        # Real Places client requires BOTH run_real_externals AND the operator-toggleable
        # places_enrichment_enabled flag (config_settings overlay, /painel). When off, the
        # Null client keeps the TA floor and the agent still advances sub_state + re-scores
        # — a real local sweep runs with ZERO Google Places spend on enrichment.
        if app_config.run_real_externals and effective.places_enrichment_enabled:
            places_api_key = os.environ.get("BRAVE_PLACES_API_KEY", "")
            from brave.clients.places import (
                RealPlacesClient,
                load_municipio_name_ibge_lookup,
            )
            places_client = RealPlacesClient(
                api_key=places_api_key,
                ibge_lookup=load_municipio_name_ibge_lookup(session),
            )
        else:
            if app_config.run_real_externals and not effective.places_enrichment_enabled:
                logger.info("places_enrichment_disabled", rio_id=rio_id)
            from brave.clients.null_places import NullPlacesClient
            places_client = NullPlacesClient()

        agent = PlacesEnrichmentAgent(
            places_client=places_client,
            session=session,
            config=config,
            max_distance_km=app_config.places_match_max_distance_km,
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
                task_name="brave.enrich_places",
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
                    task_name="brave.enrich_places",
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
        # Phase F: the attraction recency backstop may route to DLQ instead of
        # promoting (returns None). Commit the DLQ routing and no-op the push.
        if mar is None:
            session.commit()
            return
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
    from brave.shared.whatsapp.agent import build_graph

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
        config = load_effective_config(session).score

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
                push_confirmed_fn=push_attraction_task.delay,
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
    from brave.shared.whatsapp.agent import build_graph

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
        config = load_effective_config(session).score

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
                push_confirmed_fn=push_attraction_task.delay,
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
    from brave.core.quarantine import quarantine_poison as _quarantine
    from brave.lanes.atrativos.contact_finder_agent import _normalize_phone_e164
    from brave.lanes.atrativos.number_discovery import discover_number

    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        # CR-04: hold the row lock for the whole task so a concurrent inbound/resume
        # cannot interleave with the discovery → advance/bounce write.
        rio = session.get(RioRecord, rio_uuid, with_for_update=True)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        # Idempotency (D-01): only run while parked at the gate awaiting a number.
        if rio.sub_state != "aguardando_consulta_whatsapp":
            return

        app_config = AppConfig()

        # LLM client selection (D-18): Null offline (no number), Real opt-in.
        if app_config.run_real_externals:
            redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
            import redis as redis_lib
            redis_client = redis_lib.from_url(redis_url)
            from brave.clients.llm import RealLLMClient
            llm_client = RealLLMClient(
                config=app_config.llm,
                redis_client=redis_client,
                session=session,
                lane="atrativos",
            )
        else:
            from brave.clients.null_llm import NullLLMClient
            llm_client = NullLLMClient()

        normalized = rio.normalized or {}
        raw_phone = asyncio.run(
            discover_number(
                llm_client,
                name=normalized.get("name") or "",
                uf=rio.uf,
                address=normalized.get("address"),
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

            # Dispatch-then-inline-fallback (same idiom the batch endpoint uses): the
            # per-task commit above released the row lock, so an inline outreach_task.run
            # can re-acquire it offline; .delay is the normal broker path.
            try:
                outreach_task.delay(rio_id)
            except Exception:
                outreach_task.run(rio_id)

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

    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            _quarantine(
                session=q_session,
                nascente_id=None,
                task_name="brave.discover_whatsapp_number",
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
                    task_name="brave.discover_whatsapp_number",
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
    """
    import time as _time

    import redis as redis_lib

    from brave.core import engine as collection_engine
    from brave.domains import get_domain
    from brave.tasks.beat_schedule import UF_LIST

    # Resolve the domain ONCE per run — single-source-per-run (brave:engine:source is
    # read once at /start and threaded in as ``source``). The domain owns its
    # lane→producer routing; this loop never names a source.
    domain = get_domain(source)

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
            for _spec in domain.sweep_plan(
                uf,
                depth=effective_depth,
                lane=lane,
                nascente_only=nascente_only,
                max_per_uf=max_per_uf,
            ):
                _producer = globals()[_PRODUCER_ATTR_BY_TASK_NAME[_spec.task_name]]
                # Producer-completes lifecycle: count this producer BEFORE dispatch so
                # the run stays RUNNING/syncing until its finally decrements. The
                # matching decrement lives in each producer's OUTERMOST finally.
                collection_engine.incr_inflight(rc)
                _producer.delay(*_spec.args, **_spec.kwargs)
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
        # Read the engine state BEFORE any completion to detect a mid-run Stop (the same
        # signal the loop reads at the top): STOPPING ⇒ the run drained early ⇒ parcial.
        # NB the state may still be RUNNING/STOPPING here — the dispatch loop is done but
        # the fanned-out producers keep running (live kanban), so the motor is NOT turned
        # off here anymore. It flips off only when the LAST producer's finally drains
        # inflight to 0 (or immediately below when nothing/everything is already done).
        final_state = collection_engine.get_state(rc)
        # Latch dispatch_done, then attempt completion. maybe_complete only fires when
        # dispatch is done AND no producer is in flight — i.e. the fast paths where the
        # loop dispatched nothing (paused/stopped before the first UF) or every producer
        # already finished. In the common case producers are still in flight → this
        # returns False and the LAST producer completes the run. Redis-only + race-safe
        # (single-winner GETSET claim); D-18: the run_history finalize stays HERE.
        collection_engine.set_dispatch_done(rc, True)
        if collection_engine.maybe_complete(rc):
            logger.info("engine_run_complete", dispatched=dispatched, depth=effective_depth)
            # Finalize the durable runs_history row (UI-PAINEL-2 Varreduras trail).
            # BEST-EFFORT: a runs-history write failure must NEVER abort the sweep
            # (T-17.1-02-02). Skipped when the start never persisted a row (run_id None).
            if run_id:
                _finalize_run_history(run_id, dispatched, final_state)
        else:
            logger.info(
                "engine_dispatch_complete_producers_inflight",
                dispatched=dispatched,
                inflight=collection_engine.get_inflight(rc),
                depth=effective_depth,
            )

    return {"dispatched": dispatched, "lane": lane, "depth": effective_depth, "source": source}


def _finalize_run_history(run_id: str, dispatched: int, final_state: str) -> None:
    """Best-effort finalize of a runs_history row at sweep completion.

    UPDATE the row keyed by run_id: ended_at=now(), ufs_dispatched, and status
    ("parcial" if a Stop drained the run early, else "concluido"). Any failure —
    DB unavailable, session error, missing row — is swallowed and logged; this
    function NEVER raises into the sweep's finally block (T-17.1-02-02).
    """
    from datetime import datetime

    from brave.core import engine as collection_engine
    from brave.core.models import RunHistory

    try:
        session, db_engine = _get_session()
        try:
            run = session.get(RunHistory, uuid.UUID(run_id))
            if run is not None:
                run.ended_at = datetime.now(UTC)
                run.ufs_dispatched = dispatched
                run.status = (
                    "parcial"
                    if final_state == collection_engine.STOPPING
                    else "concluido"
                )
                session.commit()
        finally:
            session.close()
            db_engine.dispose()
    except Exception as exc:  # best-effort — never abort the sweep
        logger.warning(
            "engine_run_history_finalize_failed", run_id=run_id, error=str(exc)
        )


def _producer_finally_lifecycle() -> None:
    """Producer-completes lifecycle decrement — called ONCE in a producer's outermost finally.

    The orchestrator (engine_sweep_run) only DISPATCHES the producer tasks; they run for
    minutes afterward. To keep the engine "syncing" until real work stops, each dispatched
    producer decrements the shared in-flight counter here and, when it is the LAST to
    finish (dispatch already latched done + counter drained to 0), completes the run:
    race-safe motor-off via engine.maybe_complete (single-winner GETSET claim) plus a
    best-effort runs_history finalize.

    BEST-EFFORT + idempotent: wrapped so a Redis/DB hiccup can NEVER break the producer's
    own result or error handling. Placed in the SINGLE outermost finally (not per-except)
    so exactly one decrement runs per dispatch regardless of the retry/quarantine branch.
    D-18 stays intact — engine.maybe_complete is redis-only; only this pipeline layer
    touches runs_history.
    """
    # A Celery Retry unwinding through the producer's finally is NOT a terminal
    # completion — self.retry() raises Retry, Celery re-queues, and the RE-RUN hits
    # this finally again. incr_inflight fires ONCE per logical dispatch (orchestrator),
    # so the decrement must fire ONCE per TERMINAL outcome — not per execution. Skip
    # the decrement while a Retry is in flight; the eventual terminal run (success,
    # PermanentError→quarantine, MaxRetriesExceeded→quarantine, or fail-fast return)
    # decrements exactly once. Without this, N retries decrement N+1 times → counter
    # drains early → premature "synced" while the retried producer is still running.
    import sys  # noqa: PLC0415

    try:
        from celery.exceptions import Retry  # noqa: PLC0415

        if isinstance(sys.exc_info()[1], Retry):
            return
    except Exception:  # noqa: BLE001 — never let the guard itself break the producer
        pass

    try:
        import redis as _r  # noqa: PLC0415

        from brave.core import engine as _ce  # noqa: PLC0415

        _rc = _r.from_url(
            os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
        )
        # Snapshot BEFORE completion — maybe_complete flips state to IDLE, and the
        # runs_history status hinges on a mid-run Stop (STOPPING → parcial).
        _final_state = _ce.get_state(_rc)
        _dispatched = int(_ce._decode(_rc.get(_ce._UFS_DONE_KEY)) or 0)
        _ce.decr_inflight(_rc)
        if _ce.maybe_complete(_rc):
            _run_id = _ce._decode(_rc.get(_ce._RUN_ID_KEY)) or None
            if _run_id:
                _finalize_run_history(_run_id, _dispatched, _final_state)
    except Exception:  # noqa: BLE001 — best-effort; never break the producer
        pass


@shared_task(
    bind=False,
    max_retries=0,
    name="brave.ta_keepalive",
    ignore_result=True,
)
def ta_keepalive() -> None:
    """Keep-alive beat: refresh DataDome cookies when session is live (260629-p2v).

    Fires on a periodic interval (BRAVE_TA_KEEPALIVE_INTERVAL_SECONDS, default 600s).
    Issues ONE light HTML GET via fetch_attractions_paginated(max_pages=1) to re-mint
    datadome + __vt. Cookie write-back is handled inside fetch_attractions_paginated
    (session.persist_rotated_cookies), sliding the session TTL automatically.

    Skips silently when:
      - run_real_externals is False (offline / CI)
      - No session in Redis (brave:ta:session TTL <= 0)

    On 403/SessionExpiredError/SessionMissingError:
      Same fallback as sweep_tripadvisor: sets needs_bootstrap + engine OFF.
      Does NOT crash the beat (exception is caught and logged).

    On any other exception: logs error_type at WARNING and returns. Never raises.

    T-p2v-02: Never logs cookie values or str(exc) — error_type and ttl_before only.
    """
    app_config = AppConfig()
    if not app_config.run_real_externals:
        logger.debug("ta_keepalive_skipped_offline")
        return

    import redis as _redis_lib  # noqa: PLC0415

    _redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
    rc = _redis_lib.from_url(_redis_url)

    from brave.lanes.tripadvisor.client import BRAVE_TA_SESSION_KEY  # noqa: PLC0415

    ttl = rc.ttl(BRAVE_TA_SESSION_KEY)
    if ttl <= 0:
        logger.debug("ta_keepalive_skipped_no_session")
        return

    from brave.config.settings import TripAdvisorConfig  # noqa: PLC0415
    from brave.core import engine as collection_engine  # noqa: PLC0415
    from brave.lanes.tripadvisor.client import (  # noqa: PLC0415
        SessionExpiredError,
        SessionMissingError,
        TripAdvisorClient,
    )

    ta_config = TripAdvisorConfig()
    ta_client = TripAdvisorClient(config=ta_config, redis=rc)

    try:
        import asyncio as _asyncio  # noqa: PLC0415

        async def _ping() -> None:
            # ONE HTML GET (all-Brazil geoId 294280, page 1) to re-mint datadome.
            # geo_id=294280 is the all-Brazil national listing (same as bulk sweep).
            # fetch_attractions_paginated calls persist_rotated_cookies internally.
            async for _offset, _cards in ta_client.fetch_attractions_paginated(
                geo_id=294280, start_page=1, max_pages=1
            ):
                pass  # write-back + TTL slide happened inside; result not needed

        _asyncio.run(_ping())
        logger.info("ta_keepalive_ok", ttl_before=ttl)

    except (SessionExpiredError, SessionMissingError) as exc:
        # Same fallback as sweep_tripadvisor: needs_bootstrap + engine OFF.
        # set_mode(DESLIGADO) subsumes set_enabled(False) + mark_idle + inflight=0 and
        # resets the operator mode so mode/enabled never desync into a stuck "Ligar".
        _mark_needs_bootstrap()
        collection_engine.set_mode(rc, collection_engine.DESLIGADO)
        logger.warning(
            "ta_keepalive_session_expired",
            error_type=type(exc).__name__,
            # T-p2v-02: never log str(exc) — may contain cookie fragments
        )

    except Exception as exc:  # noqa: BLE001
        # Unknown error (DNS, proxy, asyncio loop conflict) — log and return.
        # The beat scheduler MUST NOT crash; the next interval fires normally.
        logger.warning(
            "ta_keepalive_error",
            error_type=type(exc).__name__,
        )


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
        return 0
    finally:
        session.close()
        engine.dispose()


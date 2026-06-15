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

from celery import shared_task
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session

from brave.clients.norteia_api import NorteiaApiClient
from brave.core.models import RioRecord
from brave.core.nascente.service import get_nascente
from brave.core.rio.routing import process_nascente_record, reprocess_record
from brave.config.settings import AppConfig, DBConfig, ScoreConfig


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
    from brave.core.mar.service import promote_to_mar
    from brave.clients.null_norteia_api import NullNorteiaApiClient

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

    except PermanentError:
        session.rollback()
        # No quarantine for push_mar permanent errors — log and return
        pass

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            pass  # Phase 3 adds DLQ for permanently failed pushes

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
            pass
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
    from brave.core.mar.service import promote_to_mar
    from brave.clients.null_norteia_api import NullNorteiaApiClient

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

    except PermanentError:
        session.rollback()
        # No quarantine for push_destination_task permanent errors — log and return
        pass

    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            pass  # Phase 3 adds DLQ for permanently failed pushes

    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# Phase 3: Atrativos lane tasks (D-01, D-06, D-08)
# Stubs — full implementation in 03-04
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    name="brave.outreach",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=900,
)
def outreach_task(self, rio_id: str) -> None:  # type: ignore[override]
    """Send WhatsApp outreach for an approved atrativo (gate must have approved, D-06).

    Full implementation in 03-04 (WhatsAppAgent LangGraph conversation).
    This stub exists so the gate /approve endpoint can dispatch without ImportError.

    Flow (03-04 implementation):
      1. compliance gate (D-11) — gate.send_path_gate()
      2. LangGraph WhatsAppAgent — Sonnet PT-BR opening + DeepSeek extraction
      3. send_template via TwilioWhatsAppClient (behind WhatsAppClientProtocol)
    """
    # TODO (03-04): implement full outreach flow
    pass


@shared_task(
    bind=True,
    max_retries=3,
    name="brave.resume_conversation",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def resume_conversation_task(self, rio_id: str, reply_text: str) -> None:  # type: ignore[override]
    """Resume LangGraph conversation on inbound reply (n8n thin transport, D-08).

    Full implementation in 03-04.
    This stub exists so the inbound webhook endpoint can dispatch without ImportError.

    Flow (03-04 implementation):
      1. Load LangGraph graph with AsyncPostgresSaver checkpointer
      2. Resume from thread_id = f"atrativo:{rio_id}"
      3. Process reply_text in recv_reply node
      4. Extract answers (DeepSeek/instructor) or ask follow-up (Sonnet)
    """
    # TODO (03-04): implement full conversation resumption
    pass

"""Integration — RecordEvent timeline emission for the TripAdvisor atrativos lane.

Exercises the instrumented ``_ingest_one`` against a REAL Postgres test DB (the
same docker-compose DB the other integration tests use) with fake/absent external
clients, and asserts the append-only ``record_events`` timeline the drawer "Log"
tab reads:

  1. A resolvable attraction ingests end-to-end and emits the ordered chain
       tripadvisor_synced → município_resolved → parent_destino_linked → validated
       → ingested → scored → routed
     (ingested comes from store_raw; scored/routed from process_nascente_record —
     both behind their idempotency early-returns).

  2. A non-resolvable attraction (ibge_unmatched) emits EXACTLY ONE terminal
     quarantined/fail event, and the two failure endpoints surface it:
       GET /api/v1/failures/cards        → a card keyed by source_ref
       GET /api/v1/failures/cards/log    → the quarantined step (ibge_unmatched)

No real externals used — RUN_REAL_EXTERNALS must be absent / 0. The ta_client is
a MagicMock (never awaited on these paths: no enrich, no geocoder, ta_config=None).
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.models import RecordEvent
from brave.lanes.tripadvisor.ibge import IbgeMunicipio

# Ensure the integration DB URL is present before model/app import (mirrors the
# sibling integration modules; the shared db_session fixture reads BRAVE_DB_URL).
os.environ.setdefault(
    "BRAVE_DB_URL",
    "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
)

# Bearer token pinned for the failure-endpoint TestClient. DashboardConfig re-reads
# os.environ on every request, so we also re-pin it inside the test just before the
# call (other test modules may overwrite it when the suite runs in one process).
_BEARER_TOKEN = "test-log-events-bearer-token"
os.environ.setdefault("BRAVE_DASHBOARD_BEARER_TOKEN", _BEARER_TOKEN)
os.environ.setdefault("BRAVE_STEWARD_SECRET", "test-log-events-steward-secret")

_BEARER_HEADERS = {"Authorization": f"Bearer {_BEARER_TOKEN}"}

# Uberlândia — the attraction name matches this record exactly so resolve_municipio
# (fuzzy name match) resolves without any lat/lng or geocoder.
_IBGE_UBERLANDIA = IbgeMunicipio("3170107", "Uberlândia", "MG", -18.9186, -48.2772)


def _make_config() -> ScoreConfig:
    return ScoreConfig(
        weight_origem=30.0,
        weight_completude=20.0,
        weight_corroboracao=20.0,
        weight_atualidade=15.0,
        weight_validacao_humana=15.0,
        threshold_mar=85.0,
        score_version="v1.1",
    )


def _card(location_id: int, name: str) -> dict:
    """A minimal normalized AttractionsFusion listing card (no lat/lng)."""
    return {
        "locationId": location_id,
        "name": name,
        "review_count": 120,
        "rating": 4.3,
        "category": "Waterfalls",
    }


def _stages_for(db_session: Session, source_ref: str) -> list[str]:
    """Stage names for a source_ref, oldest→newest (same query the Log tab uses)."""
    rows = db_session.scalars(
        select(RecordEvent)
        .where(RecordEvent.source_ref == source_ref)
        .order_by(RecordEvent.created_at.asc())
    ).all()
    return [r.stage for r in rows]


# ---------------------------------------------------------------------------
# 1. Full ordered chain for a resolvable attraction
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_ingest_one_emits_ordered_pipeline_chain(db_session: Session) -> None:
    """A resolvable atrativo emits the ordered Brave pipeline chain in record_events.

    Drives _ingest_one with run_rio=True and an EMPTY destino_rio_map so the parent
    destino is created on demand (its own store_raw/routing events land under the
    destino source_ref, not the atrativo's — the query is source_ref-scoped).

    Required subsequence (in the order the drawer renders it):
      tripadvisor_synced → municipio_resolved → parent_destino_linked → validated
      → ingested → scored → routed
    """
    from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    location_id = 900_000 + uuid.uuid4().int % 90_000
    source_ref = f"tripadvisor:attraction:{location_id}"

    ingest = TripAdvisorAtrativosIngest(
        ta_client=MagicMock(),  # never awaited on this path (no enrich/geo)
        session=db_session,
        config=_make_config(),
        ibge_records=[_IBGE_UBERLANDIA],
        destino_rio_map={},  # forces _ensure_destino (destino-first)
    )
    await ingest._ingest_one("MG", _card(location_id, "Uberlândia"), run_rio=True)
    db_session.flush()

    stages = _stages_for(db_session, source_ref)

    # Every required stage is present exactly once for this atrativo.
    required = [
        "tripadvisor_synced",
        "municipio_resolved",
        "parent_destino_linked",
        "validated",
        "ingested",
        "scored",
        "routed",
    ]
    for stage in required:
        assert stages.count(stage) == 1, (
            f"stage {stage!r} must be emitted exactly once; got stages={stages}"
        )

    # ingested/scored/routed prove the DB-stage instrumentation fired (store_raw +
    # process_nascente_record), not just the pre-DB lane stages.
    assert {"ingested", "scored", "routed"}.issubset(set(stages))

    # Ordered subsequence: filtering the timeline to the required stages must yield
    # them in pipeline order (this is exactly what the Log tab renders).
    ordered = [s for s in stages if s in set(required)]
    assert ordered == required, (
        f"record_events must be ordered {required}; got {ordered} (full: {stages})"
    )

    # The terminal routed event must NOT be a failure for a normally-routed record.
    routed = db_session.scalars(
        select(RecordEvent).where(
            RecordEvent.source_ref == source_ref,
            RecordEvent.stage == "routed",
        )
    ).all()
    assert len(routed) == 1 and routed[0].status in ("ok", "fail")
    # No terminal quarantine on the success path.
    assert "quarantined" not in stages


# ---------------------------------------------------------------------------
# 2. Terminal quarantine (ibge_unmatched) + failure endpoints
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_ibge_unmatched_emits_single_terminal_and_surfaces_in_failure_endpoints(
    db_session: Session,
) -> None:
    """A non-resolvable atrativo → exactly one quarantined/fail event, surfaced by both endpoints.

    The card name matches no IBGE município and there is no geocoder / ta_config, so
    _ingest_one quarantines as ibge_unmatched and returns before store_raw. The
    timeline must carry exactly ONE terminal fail event (stage=quarantined), and:
      - GET /api/v1/failures/cards        returns a card for this source_ref, and
      - GET /api/v1/failures/cards/log    returns the quarantined step (ibge_unmatched).
    """
    from brave.lanes.tripadvisor.atrativos import TripAdvisorAtrativosIngest

    location_id = 700_000 + uuid.uuid4().int % 90_000
    source_ref = f"tripadvisor:attraction:{location_id}"
    unresolvable_name = f"Praia Do Bosque Inexistente {uuid.uuid4().hex[:6]}"

    ingest = TripAdvisorAtrativosIngest(
        ta_client=MagicMock(),
        session=db_session,
        config=_make_config(),
        # Only São Paulo present → the ES-ish name cannot resolve; no geocoder/ta_config.
        ibge_records=[IbgeMunicipio("3550308", "São Paulo", "SP", -23.5505, -46.6333)],
        destino_rio_map={},
    )
    await ingest._ingest_one("ES", _card(location_id, unresolvable_name), run_rio=True)
    # Commit so the failure endpoints (which use a separate get_db session) can read it.
    db_session.commit()

    # --- exactly one terminal fail event -----------------------------------
    fail_events = db_session.scalars(
        select(RecordEvent).where(
            RecordEvent.source_ref == source_ref,
            RecordEvent.status == "fail",
        )
    ).all()
    assert len(fail_events) == 1, (
        f"exactly one terminal fail event expected; got {[(e.stage, e.status) for e in fail_events]}"
    )
    terminal = fail_events[0]
    assert terminal.stage == "quarantined"
    assert "ibge_unmatched" in (terminal.message or "")
    assert (terminal.data or {}).get("reason") == "ibge_unmatched"
    # No Nascente/Rio stage events for a record that quarantined pre-store_raw.
    stages = _stages_for(db_session, source_ref)
    assert "ingested" not in stages and "routed" not in stages

    # --- failure endpoints -------------------------------------------------
    from brave.api.main import app  # noqa: PLC0415

    os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = _BEARER_TOKEN
    client = TestClient(app, raise_server_exceptions=False)

    # GET /failures/cards → a card keyed by our source_ref, carrying real identity.
    r_cards = client.get("/api/v1/failures/cards", headers=_BEARER_HEADERS)
    assert r_cards.status_code == 200, f"{r_cards.status_code}: {r_cards.text}"
    cards = r_cards.json()
    card = next((c for c in cards if c["source_ref"] == source_ref), None)
    assert card is not None, (
        f"failure card for {source_ref} not found among {len(cards)} cards"
    )
    assert card["entity_type"] == "attraction"
    assert card["uf"] == "ES"
    assert card["name"] == unresolvable_name  # REAL name, not the opaque task_name
    assert card["last_stage"] == "quarantined"
    assert "ibge_unmatched" in (card["error"] or "")

    # GET /failures/cards/log?source_ref= → the quarantined step for a rio-less card.
    r_log = client.get(
        "/api/v1/failures/cards/log",
        params={"source_ref": source_ref},
        headers=_BEARER_HEADERS,
    )
    assert r_log.status_code == 200, f"{r_log.status_code}: {r_log.text}"
    body = r_log.json()
    events = body["events"]
    quarantined_steps = [
        e for e in events if e["stage"] == "quarantined" and e["status"] == "fail"
    ]
    assert len(quarantined_steps) == 1, (
        f"log must carry exactly one quarantined step; got {events}"
    )
    assert "ibge_unmatched" in (quarantined_steps[0]["message"] or "")
    assert body["identity"]["name"] == unresolvable_name
    assert body["identity"]["uf"] == "ES"

"""Integration tests for the Phase F manual DLQ→WhatsApp batch endpoint + discovery task.

Endpoint under test:
  POST /api/v1/dlq/whatsapp-batch  — move eligible DLQ atrativos into the WhatsApp column

Covers (per the Phase F spec):
  - eligible (NO horário + NO preço) moves + dispatches the correct branch
  - with-celular candidate  → whatsapp_in_progress + outreach_task dispatch
  - no-celular candidate     → aguardando_consulta_whatsapp + discover_whatsapp_number_task
  - ineligible (has horário / has preço) → 422, record untouched
  - atomic: a mixed batch with one ineligible id moves NOTHING (422)
  - auth: 401 without steward/bearer; auth-before-lock (401 not 423 when LIGADO)
  - edit-lock: 423 when the engine is LIGADO (Motor Pausado, Phase C)
  - phone masked in the atrativo projection after the move (LGPD R3)
  - owner-confirmation: validacao_humana=100 → re-score ≥80 → Mar (validate_and_promote_rio)
  - discovery task: offline (Null LLM) no number → back to DLQ (no_contact_found)
  - discovery task: found number → whatsapp_in_progress + populates contact + outreach

All tests are @pytest.mark.integration (require docker-compose postgres). The edit-lock
mode is driven through a fakeredis override of get_redis so these tests never pollute the
shared real Redis. run_real_externals defaults to False → the discovery task uses the Null
LLM (no number) unless a test injects a fake.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from brave.core.models import MarRecord, NascenteRecord, RioRecord

os.environ.setdefault(
    "BRAVE_DB_URL",
    "postgresql+psycopg://brave:brave@localhost:5432/norteia_brave",
)

# Force (not setdefault): the test secrets MUST win over any ambient/.env value.
STEWARD_SECRET = "test-dlq-whatsapp-steward-secret"
os.environ["BRAVE_STEWARD_SECRET"] = STEWARD_SECRET
DASHBOARD_BEARER = "test-dlq-whatsapp-dashboard-bearer"
os.environ["BRAVE_DASHBOARD_BEARER_TOKEN"] = DASHBOARD_BEARER
BEARER_HEADERS = {"Authorization": f"Bearer {DASHBOARD_BEARER}"}

_MASKED_CELULAR = "+5573*****01"
_RAW_CELULAR = "+5573999990001"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _force_env(monkeypatch):
    """Re-assert this module's secrets per test (win over cross-file env) + force offline.

    monkeypatch.setenv auto-reverts after each test. Deleting RUN_REAL_EXTERNALS keeps
    AppConfig().run_real_externals=False so the discovery task uses the Null LLM (no
    number) unless a test explicitly injects a fake.
    """
    monkeypatch.setenv("BRAVE_STEWARD_SECRET", STEWARD_SECRET)
    monkeypatch.setenv("BRAVE_DASHBOARD_BEARER_TOKEN", DASHBOARD_BEARER)
    monkeypatch.delenv("RUN_REAL_EXTERNALS", raising=False)


@pytest.fixture(autouse=True)
def _cleanup_committed_rows(db_engine):
    """Delete rows this test COMMITS so they do not leak into the shared integration DB.

    The batch endpoint commits records into aguardando_consulta_whatsapp/whatsapp_in_progress;
    the module's db_session fixture only rollback()s, so committed rows would accumulate
    across runs and (past the /gate endpoint's max limit) break unrelated queue tests.
    Snapshot rio_record ids before the test and delete any new ones (plus their nascente
    parents and audit/conversation children) afterward — cleaning ONLY what this test created.
    """
    from sqlalchemy import text

    with db_engine.connect() as conn:
        before = {r[0] for r in conn.execute(text("SELECT id FROM rio_records"))}
    yield
    with db_engine.begin() as conn:
        after = {r[0] for r in conn.execute(text("SELECT id FROM rio_records"))}
        new_ids = list(after - before)
        if not new_ids:
            return
        conn.execute(
            text("DELETE FROM conversation_message WHERE rio_id = ANY(:i)"), {"i": new_ids}
        )
        conn.execute(
            text("DELETE FROM consent_log WHERE rio_id = ANY(:i)"), {"i": new_ids}
        )
        conn.execute(
            text("DELETE FROM audit_log WHERE record_id = ANY(:i)"), {"i": new_ids}
        )
        nasc = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT nascente_id FROM rio_records "
                    "WHERE id = ANY(:i) AND nascente_id IS NOT NULL"
                ),
                {"i": new_ids},
            )
        ]
        conn.execute(text("DELETE FROM rio_records WHERE id = ANY(:i)"), {"i": new_ids})
        if nasc:
            conn.execute(text("DELETE FROM nascente_records WHERE id = ANY(:i)"), {"i": nasc})


def _client_with_mode(mode: str | None) -> TestClient:
    """Build a TestClient whose get_redis is a fresh fakeredis pinned to `mode`.

    mode=None → fakeredis with no engine:mode key → LIGADO default (editing LOCKED).
    Overriding get_redis isolates the edit-lock state from the shared real Redis and
    only affects require_editing_unlocked (the batch endpoint uses no other Redis).
    """
    from brave.api import deps
    from brave.api.main import app
    from brave.core import engine as collection_engine

    fake = fakeredis.FakeRedis()
    if mode is not None:
        collection_engine.set_mode(fake, mode)
    app.dependency_overrides[deps.get_redis] = lambda: fake
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def unlocked_client():
    """Editing UNLOCKED (mode PAUSADO) so the batch move passes require_editing_unlocked."""
    from brave.api import deps
    from brave.api.main import app
    from brave.core import engine as collection_engine

    client = _client_with_mode(collection_engine.PAUSADO)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(deps.get_redis, None)


@pytest.fixture
def locked_client():
    """Editing LOCKED (mode LIGADO — the default) so the batch move returns 423."""
    from brave.api import deps
    from brave.api.main import app

    client = _client_with_mode(None)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(deps.get_redis, None)


@pytest.fixture
def dispatch_spy(monkeypatch):
    """Capture outreach + discovery Celery dispatches (no broker, no inline .run)."""
    from brave.tasks import pipeline

    calls: dict[str, list] = {"outreach": [], "discovery": []}
    monkeypatch.setattr(
        pipeline.outreach_task, "delay", lambda *a, **k: calls["outreach"].append(a)
    )
    monkeypatch.setattr(
        pipeline.discover_whatsapp_number_task,
        "delay",
        lambda *a, **k: calls["discovery"].append(a),
    )
    return calls


def _make_dlq_atrativo(
    db_session: Session,
    *,
    normalized: dict | None = None,
    uf: str = "BA",
    routing: str = "dlq",
    sub_state: str | None = None,
) -> RioRecord:
    """Insert an attraction RioRecord (default: routing='dlq', sub_state=None)."""
    src = f"places:{uf}:{uuid.uuid4().hex}"
    nascente = NascenteRecord(
        id=uuid.uuid4(),
        source="places_discovery",
        source_ref=src,
        entity_type="attraction",
        uf=uf,
        payload={"name": "Atrativo Teste", "place_id": src},
        content_hash=f"hash:{src}",
        version=1,
    )
    db_session.add(nascente)
    db_session.flush()

    norm: dict = {"name": "Atrativo Teste"}
    if normalized:
        norm.update(normalized)

    rio = RioRecord(
        id=uuid.uuid4(),
        nascente_id=nascente.id,
        entity_type="attraction",
        uf=uf,
        routing=routing,
        sub_state=sub_state,
        dlq_reason="no_recent_reviews",
        normalized=norm,
        canonical_key=src,
    )
    db_session.add(rio)
    db_session.flush()
    return rio


# ---------------------------------------------------------------------------
# Batch endpoint — eligible moves + branch dispatch
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_batch_candidate_moves_to_whatsapp_and_dispatches_outreach(
    unlocked_client, db_session, dispatch_spy
) -> None:
    """A DLQ atrativo WITH a captured celular → whatsapp_in_progress + outreach dispatch."""
    rio = _make_dlq_atrativo(
        db_session, normalized={"contact": {"whatsapp_candidate": _MASKED_CELULAR}}
    )
    db_session.commit()

    r = unlocked_client.post(
        "/api/v1/dlq/whatsapp-batch",
        json={"rio_ids": [str(rio.id)]},
        headers=BEARER_HEADERS,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["moved"] == 1
    assert body["outreach"] == 1
    assert body["discovery"] == 0

    db_session.refresh(rio)
    assert rio.sub_state == "whatsapp_in_progress"
    assert rio.routing == "in_progress"  # off the DLQ column

    assert dispatch_spy["outreach"] == [(str(rio.id),)]
    assert dispatch_spy["discovery"] == []


@pytest.mark.integration
def test_batch_no_candidate_dispatches_number_discovery(
    unlocked_client, db_session, dispatch_spy
) -> None:
    """A DLQ atrativo WITHOUT a celular → aguardando + discover_whatsapp_number_task."""
    rio = _make_dlq_atrativo(db_session)  # no normalized["contact"]
    db_session.commit()

    r = unlocked_client.post(
        "/api/v1/dlq/whatsapp-batch",
        json={"rio_ids": [str(rio.id)]},
        headers=BEARER_HEADERS,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["moved"] == 1
    assert body["discovery"] == 1
    assert body["outreach"] == 0

    db_session.refresh(rio)
    assert rio.sub_state == "aguardando_consulta_whatsapp"
    assert rio.routing == "in_progress"

    assert dispatch_spy["discovery"] == [(str(rio.id),)]
    assert dispatch_spy["outreach"] == []


# ---------------------------------------------------------------------------
# Batch endpoint — server-side eligibility (422)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_batch_ineligible_with_horario_returns_422(
    unlocked_client, db_session, dispatch_spy
) -> None:
    """A record that already has horário (Places weekday_text) → 422, untouched."""
    rio = _make_dlq_atrativo(
        db_session, normalized={"weekday_text": ["Monday: 9:00 AM – 5:00 PM"]}
    )
    db_session.commit()

    r = unlocked_client.post(
        "/api/v1/dlq/whatsapp-batch",
        json={"rio_ids": [str(rio.id)]},
        headers=BEARER_HEADERS,
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["ineligible"] == [
        {"rio_id": str(rio.id), "reason": "has_horario_or_preco"}
    ]

    db_session.refresh(rio)
    assert rio.sub_state is None
    assert rio.routing == "dlq"
    assert dispatch_spy["outreach"] == []
    assert dispatch_spy["discovery"] == []


@pytest.mark.integration
def test_batch_ineligible_with_preco_returns_422(
    unlocked_client, db_session, dispatch_spy
) -> None:
    """A record that already has preço (owner_valor) → 422, untouched."""
    rio = _make_dlq_atrativo(db_session, normalized={"owner_valor": "R$ 25"})
    db_session.commit()

    r = unlocked_client.post(
        "/api/v1/dlq/whatsapp-batch",
        json={"rio_ids": [str(rio.id)]},
        headers=BEARER_HEADERS,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["ineligible"][0]["reason"] == "has_horario_or_preco"

    db_session.refresh(rio)
    assert rio.sub_state is None
    assert rio.routing == "dlq"


@pytest.mark.integration
def test_batch_is_atomic_one_ineligible_moves_nothing(
    unlocked_client, db_session, dispatch_spy
) -> None:
    """A mixed batch (one eligible + one ineligible) is atomic — NOTHING is moved."""
    good = _make_dlq_atrativo(
        db_session, normalized={"contact": {"whatsapp_candidate": _MASKED_CELULAR}}
    )
    bad = _make_dlq_atrativo(db_session, normalized={"owner_valor": "R$ 25"})
    db_session.commit()

    r = unlocked_client.post(
        "/api/v1/dlq/whatsapp-batch",
        json={"rio_ids": [str(good.id), str(bad.id)]},
        headers=BEARER_HEADERS,
    )
    assert r.status_code == 422, r.text

    db_session.refresh(good)
    db_session.refresh(bad)
    # The eligible record was NOT moved — the whole batch was rejected.
    assert good.sub_state is None and good.routing == "dlq"
    assert bad.sub_state is None and bad.routing == "dlq"
    assert dispatch_spy["outreach"] == []
    assert dispatch_spy["discovery"] == []


@pytest.mark.integration
def test_batch_already_in_whatsapp_returns_422(
    unlocked_client, db_session, dispatch_spy
) -> None:
    """A record already parked at the gate (sub_state set) is not re-movable → 422."""
    rio = _make_dlq_atrativo(
        db_session, routing="dlq", sub_state="aguardando_consulta_whatsapp"
    )
    db_session.commit()

    r = unlocked_client.post(
        "/api/v1/dlq/whatsapp-batch",
        json={"rio_ids": [str(rio.id)]},
        headers=BEARER_HEADERS,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["ineligible"][0]["reason"] == "already_in_whatsapp"


# ---------------------------------------------------------------------------
# Batch endpoint — auth + edit-lock
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_batch_requires_auth(unlocked_client, db_session) -> None:
    """No steward secret / bearer → 401 (before any move)."""
    rio = _make_dlq_atrativo(db_session)
    db_session.commit()

    r = unlocked_client.post(
        "/api/v1/dlq/whatsapp-batch", json={"rio_ids": [str(rio.id)]}
    )
    assert r.status_code == 401, r.text


@pytest.mark.integration
def test_batch_edit_lock_returns_423_when_ligado(
    locked_client, db_session, dispatch_spy
) -> None:
    """Engine LIGADO → 423 Locked (Motor Pausado edit-lock), record untouched."""
    rio = _make_dlq_atrativo(
        db_session, normalized={"contact": {"whatsapp_candidate": _MASKED_CELULAR}}
    )
    db_session.commit()

    r = locked_client.post(
        "/api/v1/dlq/whatsapp-batch",
        json={"rio_ids": [str(rio.id)]},
        headers=BEARER_HEADERS,
    )
    assert r.status_code == 423, r.text

    db_session.refresh(rio)
    assert rio.sub_state is None and rio.routing == "dlq"
    assert dispatch_spy["outreach"] == []
    assert dispatch_spy["discovery"] == []


@pytest.mark.integration
def test_batch_auth_runs_before_lock(locked_client, db_session) -> None:
    """LIGADO + no auth → 401 (auth-before-lock), never a 423 that leaks lock state."""
    r = locked_client.post(
        "/api/v1/dlq/whatsapp-batch", json={"rio_ids": [str(uuid.uuid4())]}
    )
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# LGPD — phone masked in the projection after the move
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_batch_move_masks_phone_in_projection(
    unlocked_client, db_session, dispatch_spy
) -> None:
    """After the move, GET /atrativos/{id} surfaces only masked numbers (no raw celular)."""
    rio = _make_dlq_atrativo(
        db_session,
        normalized={
            "contact": {"whatsapp_candidate": _MASKED_CELULAR},
            "contacts": {"phone_e164": _RAW_CELULAR, "website": "https://x.example"},
        },
    )
    db_session.commit()

    moved = unlocked_client.post(
        "/api/v1/dlq/whatsapp-batch",
        json={"rio_ids": [str(rio.id)]},
        headers=BEARER_HEADERS,
    )
    assert moved.status_code == 202, moved.text

    detail = unlocked_client.get(
        f"/api/v1/atrativos/{rio.id}", headers=BEARER_HEADERS
    )
    assert detail.status_code == 200, detail.text
    norm = detail.json()["normalized"]

    # Masked WhatsApp candidate surfaced; raw celular never present anywhere.
    assert norm["contact"]["whatsapp_candidate"] == _MASKED_CELULAR
    assert norm["contacts"]["phone_masked"] == _MASKED_CELULAR
    assert "phone_e164" not in norm["contacts"]
    assert "999990001" not in json.dumps(norm)


# ---------------------------------------------------------------------------
# Owner-confirmation still promotes to Mar (validate_and_promote_rio mechanics)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_owner_confirmation_validacao_100_reaches_mar(db_session) -> None:
    """Owner-confirm mechanic: validacao_humana=100 → re-score crosses ≥80 → Mar.

    Preserves the validate_and_promote_rio path the WhatsApp finalize node relies on.
    Base score (val=0): 30+20+0+15 = 65 → DLQ; with val=100 → 80 → Mar. A recent
    most_recent_review_at is set so the Phase F attraction recency backstop in
    promote_to_mar (missing/>90d review → DLQ) passes for this owner-validated record.
    """
    from brave.config.settings import ScoreConfig
    from brave.core.dlq.service import validate_and_promote_rio
    from brave.core.rio.routing import reprocess_record

    recent = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    rio = _make_dlq_atrativo(
        db_session,
        normalized={
            "name": "Atrativo Owner",
            "lat": -12.0,
            "lon": -38.0,
            "origem_value": 100.0,
            "completude_value": 100.0,
            "corroboracao_value": 0.0,
            "atualidade_value": 100.0,
            "validacao_humana_value": 0.0,
            "most_recent_review_at": recent,  # within 90d → recency backstop passes
        },
    )
    db_session.flush()

    # Base re-score without owner validation → DLQ (65 < 80).
    reprocess_record(db_session, rio.id, ScoreConfig())
    db_session.flush()
    db_session.refresh(rio)
    assert rio.routing == "dlq"
    assert float(rio.score) < 80.0

    # Owner confirmation injects validacao_humana=100 → re-score ≥80 → Mar + MarRecord.
    # Pass an explicit default ScoreConfig so the test is deterministic regardless of any
    # DB config overlay (threshold_mar tuning) load_effective_config would otherwise apply.
    mar = validate_and_promote_rio(db_session, rio, ScoreConfig())
    db_session.refresh(rio)
    assert rio.routing == "mar"
    assert float(rio.score) >= 80.0
    assert mar is not None
    assert isinstance(mar, MarRecord)


# ---------------------------------------------------------------------------
# discover_whatsapp_number_task — offline not-found + injected found
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_discovery_task_no_number_routes_back_to_dlq(db_session) -> None:
    """Offline (Null LLM) → no number → back to DLQ with dlq_reason='no_contact_found'."""
    from brave.tasks.pipeline import discover_whatsapp_number_task

    rio = _make_dlq_atrativo(
        db_session, routing="in_progress", sub_state="aguardando_consulta_whatsapp"
    )
    db_session.commit()  # the task opens its own session — must see a committed row

    discover_whatsapp_number_task.run(str(rio.id))

    db_session.refresh(rio)
    assert rio.sub_state is None
    assert rio.routing == "dlq"
    assert rio.dlq_reason == "no_contact_found"


@pytest.mark.integration
def test_discovery_task_found_advances_and_dispatches_outreach(
    db_session, monkeypatch
) -> None:
    """Found number (fake LLM) → whatsapp_in_progress, populates contact, dispatches outreach."""
    from brave.lanes.atrativos.schemas import WhatsAppNumberDiscovery
    from brave.tasks import pipeline
    from tests.fakes.fake_llm import FakeLLMClient

    # Inject a "found" celular via the offline Null client seam the task selects.
    monkeypatch.setattr(
        "brave.clients.null_llm.NullLLMClient",
        lambda *a, **k: FakeLLMClient(
            fixture_result=WhatsAppNumberDiscovery(phone=_RAW_CELULAR, confidence=0.9)
        ),
    )
    outreach: list = []
    monkeypatch.setattr(
        pipeline.outreach_task, "delay", lambda *a, **k: outreach.append(a)
    )

    rio = _make_dlq_atrativo(
        db_session, routing="in_progress", sub_state="aguardando_consulta_whatsapp"
    )
    db_session.commit()

    pipeline.discover_whatsapp_number_task.run(str(rio.id))

    db_session.refresh(rio)
    assert rio.sub_state == "whatsapp_in_progress"
    # Raw E.164 for the consent/outreach path; MASKED candidate for the board (LGPD R3).
    assert rio.normalized["contacts"]["phone_e164"] == _RAW_CELULAR
    assert rio.normalized["contact"]["whatsapp_candidate"] == _MASKED_CELULAR
    assert outreach == [(str(rio.id),)]

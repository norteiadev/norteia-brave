"""End-to-end distrito/subdistrito passthrough (IBGE DTB → Rio → Mar → push).

Locks the field-propagation chain traced in the plan: an attraction whose Nascente
`canonical` sub-dict carries the distrito localization must thread through the Rio
normalize cherry-pick (process_nascente_record) into `normalized`, survive
promote_to_mar into the Mar `canonical`, and appear in
`MarPushPayload.model_dump()["canonical"]` — the exact dict POSTed to norteia-api.

Golden localization (Arraial d'Ajuda, distrito of Porto Seguro BA):
    distrito_name = "Arraial D'Ajuda", distrito_code = "292530307".

Regression guard: an attraction WITHOUT any distrito signal (TA lane / a Places
result with no admin_area_level_3) still promotes cleanly with the keys simply
absent — the score floor is untouched (no new required field).

100% offline: the Rio repo / dedup / event sinks are patched out, the Mar repo is
patched to first-time-creation, no Postgres / Places / norteia-api contact.
RUN_REAL_EXTERNALS is irrelevant (no client is constructed).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import brave.core.mar.service as ms
import brave.core.rio.routing as routing
from brave.config.settings import ScoreConfig
from brave.core.mar.service import build_push_payload, promote_to_mar
from brave.core.models import NascenteRecord
from brave.core.rio.routing import process_nascente_record

# Arraial d'Ajuda — distrito of Porto Seguro (BA); IBGE DTB 2025 golden case.
_DISTRITO_NAME = "Arraial D'Ajuda"
_DISTRITO_CODE = "292530307"
_PORTO_SEGURO_IBGE = "2925303"


def _attraction_payload(*, with_distrito: bool) -> dict:
    """Build a Nascente attraction payload with full Mar-clearing score inputs.

    All five reliability *_value criteria are maxed (score 100 ≥ threshold_mar 80),
    so routing lands on 'mar' regardless of the distrito keys — the passthrough must
    not perturb the score floor.
    """
    canonical: dict = {
        "name": "Igreja Nossa Senhora d'Ajuda",
        "uf": "BA",
        "municipio": "Porto Seguro",
        "ibge_code": _PORTO_SEGURO_IBGE,
    }
    if with_distrito:
        # Written by the Places discovery lane after resolve_distrito matches the
        # admin_area_level_3 hint. distrito_municipio_ibge is the parent-município
        # relation (the matched distrito's 7-digit ibge_code). subdistrito_* are
        # reserved-null (no signal).
        canonical["distrito_name"] = _DISTRITO_NAME
        canonical["distrito_code"] = _DISTRITO_CODE
        canonical["distrito_municipio_ibge"] = _PORTO_SEGURO_IBGE
        canonical["subdistrito_name"] = None
        canonical["subdistrito_code"] = None
        canonical["distrito_source"] = "places_admin_area_level_3"

    return {
        "name": "Igreja Nossa Senhora d'Ajuda",
        "lat": -16.4886,
        "lng": -39.0736,
        "municipio_id": _PORTO_SEGURO_IBGE,
        "canonical": canonical,
        # Max score inputs — clear the Mar gate deterministically.
        "origem_value": 100.0,
        "completude_value": 100.0,
        "corroboracao_value": 100.0,
        "atualidade_value": 100.0,
        "validacao_humana_value": 100.0,
    }


def _nascente(payload: dict) -> NascenteRecord:
    """Transient NascenteRecord (no session) carrying the attraction payload."""
    return NascenteRecord(
        id=uuid.uuid4(),
        source="mtur",
        source_ref="mtur:BA:atr:arraial-igreja",
        entity_type="attraction",
        uf="BA",
        payload=payload,
        content_hash="deadbeef",
    )


@contextmanager
def _offline_rio():
    """Patch the Rio routing I/O seams so process_nascente_record runs pure-in-memory.

    _rio_repo.get_by_canonical_key → None (no idempotent early return); find_duplicate
    → None (no dedup collapse); record_event → noop (no DB timeline write).
    """
    repo = MagicMock()
    repo.get_by_canonical_key.return_value = None
    with patch.object(routing, "_rio_repo", repo), patch.object(
        routing, "find_duplicate", return_value=None
    ), patch.object(routing, "record_event"):
        yield


def _run_rio(payload: dict):
    """Run the full Rio normalize/score/route offline; return the RioRecord."""
    nascente = _nascente(payload)
    with _offline_rio():
        return process_nascente_record(MagicMock(), nascente, ScoreConfig())


def _promote(rio):
    """Promote a scored RioRecord to Mar offline (first-time creation)."""
    with patch.object(ms, "_mar_repo") as repo:
        repo.get_active_by_source_ref.return_value = None
        return promote_to_mar(MagicMock(), rio)


# ---------------------------------------------------------------------------
# Golden path: distrito threads Nascente → normalized → Mar → push payload
# ---------------------------------------------------------------------------


def test_distrito_threads_through_rio_normalize():
    """canonical.distrito_* survives the Rio cherry-pick into normalized."""
    rio = _run_rio(_attraction_payload(with_distrito=True))

    assert rio.routing == "mar"  # score floor intact (100 ≥ 80)
    assert rio.normalized["distrito_name"] == _DISTRITO_NAME
    assert rio.normalized["distrito_code"] == _DISTRITO_CODE
    assert rio.normalized["distrito_municipio_ibge"] == _PORTO_SEGURO_IBGE
    assert rio.normalized["distrito_source"] == "places_admin_area_level_3"
    # Reserved-null subdistrito keys carry no value → not cherry-picked into normalized.
    assert "subdistrito_name" not in rio.normalized
    assert "subdistrito_code" not in rio.normalized


def test_distrito_survives_promote_to_mar_and_push_payload():
    """distrito_code reaches Mar canonical AND MarPushPayload.model_dump()['canonical']."""
    rio = _run_rio(_attraction_payload(with_distrito=True))
    # SignalAgent-supplied recency (added downstream of Rio normalize) so the
    # attraction backstop in promote_to_mar does not route it to DLQ.
    rio.normalized["most_recent_review_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()

    mar = _promote(rio)
    assert mar is not None
    assert mar.canonical["distrito_code"] == _DISTRITO_CODE
    assert mar.canonical["distrito_name"] == _DISTRITO_NAME
    assert mar.canonical["distrito_municipio_ibge"] == _PORTO_SEGURO_IBGE

    push = build_push_payload(mar, rio).model_dump()
    assert push["entity_type"] == "attraction"
    assert push["canonical"]["distrito_code"] == _DISTRITO_CODE
    assert push["canonical"]["distrito_name"] == _DISTRITO_NAME
    # The NEW parent-município relation survives the full Rio→Mar→push chain.
    assert push["canonical"]["distrito_municipio_ibge"] == _PORTO_SEGURO_IBGE


# ---------------------------------------------------------------------------
# Regression: an attraction with NO distrito signal still promotes cleanly
# ---------------------------------------------------------------------------


def test_attraction_without_distrito_promotes_clean_no_floor_regression():
    """No distrito keys → absent everywhere; score/routing unchanged (no floor regression)."""
    rio = _run_rio(_attraction_payload(with_distrito=False))

    assert rio.routing == "mar"
    assert float(rio.score) == 100.0
    for key in (
        "distrito_name",
        "distrito_code",
        "distrito_municipio_ibge",
        "subdistrito_name",
        "subdistrito_code",
    ):
        assert key not in rio.normalized

    rio.normalized["most_recent_review_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()
    mar = _promote(rio)

    assert mar is not None
    assert mar.entity_type == "attraction"
    for key in (
        "distrito_name",
        "distrito_code",
        "distrito_municipio_ibge",
        "subdistrito_name",
        "subdistrito_code",
    ):
        assert key not in mar.canonical

    push = build_push_payload(mar, rio).model_dump()
    assert "distrito_code" not in push["canonical"]

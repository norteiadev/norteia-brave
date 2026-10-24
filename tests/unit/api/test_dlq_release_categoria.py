"""liberar-google on a market under "Lojas" (google_tipo_negocio) must also clear the
category gate: no re-enrichment follows a release, so without the mark the record waits
forever behind categoria_nao_confirmada with no description (and no DLQ row to show it)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("reason", ["google_tipo_negocio", "google_fechado_revisao"])
def test_release_marks_the_category_liberada(monkeypatch, reason) -> None:
    from brave.api.routers import dlq
    from brave.domains.places.places_enrichment import categoria_nao_confirmada

    rio = SimpleNamespace(
        id=uuid.uuid4(), entity_type="attraction", routing="dlq", dlq_reason=reason,
        canonical_key="tripadvisor:attraction:1",
        normalized={"category": "Lojas especializadas e de suvenir", "google_types": ["store"]},
    )
    db = MagicMock()
    db.get.return_value = rio

    def _persist(_db, r, _before, after):
        r.normalized = after

    monkeypatch.setattr(dlq, "persist_normalized", _persist)
    monkeypatch.setattr(dlq, "route_by_score", lambda *_a: None)
    monkeypatch.setattr(dlq, "load_effective_config", lambda _db: MagicMock())
    monkeypatch.setattr(dlq, "write_audit", lambda **_k: None)

    dlq.release_google_dlq_record(rio.id, db=db)

    assert categoria_nao_confirmada(rio.canonical_key, rio.normalized) is False

"""Offline edge-table tests for the generic stage-transition endpoints (UI-PAINEL-2).

Both `transition_destino` (cms.py) and `transition_atrativo` (atrativos.py) are
gated by a SERVER-SIDE edge allow-list — the server twin of the client mapDrop
(dashboard/lib/painel-actions.ts). The allow-list IS the security boundary
(T-17.1-03-01): any (expected, to) pair absent from it returns 409 and NEVER
mutates a record; notably every ("mar", *) edge is absent so a live Mar record
can never be depublished/moved (T-17.1-03-03).

These tests are exhaustive over the 6-column model and fully offline
(MagicMock session, no DB, no externals).
"""

import uuid
from itertools import product
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from brave.api.routers.cms import (
    _ALLOWED_EDGES,
    TransitionBody,
    transition_destino,
)
from brave.core.models import RioRecord

# The 6-column board model (TransitionBody Literal).
COLUMNS = ["nascente", "rio", "whatsapp", "mar", "dlq", "descarte"]

# The ONLY destino edges that may mutate (expected → to). Must match _ALLOWED_EDGES
# edge-for-edge — this set is the documented paired contract with client mapDrop.
DESTINO_ALLOWED = {
    ("rio", "mar"),
    ("rio", "descarte"),
    ("rio", "dlq"),
    ("dlq", "rio"),
    ("dlq", "mar"),
    ("dlq", "descarte"),
}


def _rio(routing="in_progress", **kw):
    base = dict(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="destination",
        uf="BA",
        routing=routing,
        canonical_key="cand-1",
    )
    base.update(kw)
    return RioRecord(**base)


def _db_for(rio):
    db = MagicMock()

    def _get(model, _id, *a, **kw):
        if model is RioRecord:
            return rio
        return None

    db.get.side_effect = _get
    return db


# ---------------------------------------------------------------------------
# Exhaustive destino edge-table assertions (pure dict — no DB)
# ---------------------------------------------------------------------------


def test_destino_allow_list_is_exactly_the_paired_contract():
    """_ALLOWED_EDGES contains exactly the documented destino edges and nothing else."""
    assert set(_ALLOWED_EDGES.keys()) == DESTINO_ALLOWED


def test_destino_every_unmapped_edge_is_absent():
    """For every (expected, to) over the 6 columns: present IFF in the allow-list."""
    for expected, to in product(COLUMNS, COLUMNS):
        if (expected, to) in DESTINO_ALLOWED:
            assert (expected, to) in _ALLOWED_EDGES
        else:
            assert (expected, to) not in _ALLOWED_EDGES


def test_destino_no_mar_edge_ever_present():
    """Every ("mar", X) edge is absent — a live Mar destino is never moved (T-17.1-03-03)."""
    for to in COLUMNS:
        assert ("mar", to) not in _ALLOWED_EDGES


# ---------------------------------------------------------------------------
# Endpoint: unmapped / mar→* edges 409 and NEVER mutate (no helper called)
# ---------------------------------------------------------------------------


def test_transition_destino_mar_to_anything_is_409_and_never_mutates():
    rio = _rio(routing="mar")
    db = _db_for(rio)

    with patch("brave.core.dlq.service.validate_and_promote_rio") as promote, patch(
        "brave.core.rio.routing.reprocess_record"
    ) as reprocess, patch("brave.api.routers.cms.write_audit") as audit, pytest.raises(
        HTTPException
    ) as exc:
        transition_destino(
            rio_id=rio.id,
            body=TransitionBody(to="descarte", expected="mar"),
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "transição não suportada"
    promote.assert_not_called()
    reprocess.assert_not_called()
    audit.assert_not_called()
    db.commit.assert_not_called()
    assert rio.routing == "mar"  # untouched


def test_transition_destino_unmapped_edge_is_409():
    rio = _rio(routing="in_progress")
    db = _db_for(rio)

    with patch("brave.api.routers.cms.write_audit") as audit, pytest.raises(
        HTTPException
    ) as exc:
        transition_destino(
            rio_id=rio.id,
            body=TransitionBody(to="whatsapp", expected="rio"),
            db=db,
        )

    assert exc.value.status_code == 409
    audit.assert_not_called()
    db.commit.assert_not_called()


def test_transition_destino_expected_mismatch_is_409():
    """edge exists but caller's expected column is stale → optimistic-concurrency 409."""
    rio = _rio(routing="dlq")  # current column = dlq
    db = _db_for(rio)

    with patch("brave.core.dlq.service.validate_and_promote_rio") as promote, pytest.raises(
        HTTPException
    ) as exc:
        transition_destino(
            rio_id=rio.id,
            body=TransitionBody(to="mar", expected="rio"),  # ("rio","mar") allowed, but current is dlq
            db=db,
        )

    assert exc.value.status_code == 409
    promote.assert_not_called()


def test_transition_destino_404_when_missing():
    db = _db_for(rio=None)
    with pytest.raises(HTTPException) as exc:
        transition_destino(
            rio_id=uuid.uuid4(),
            body=TransitionBody(to="mar", expected="rio"),
            db=db,
        )
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Endpoint: allowed edges mutate via the REUSED helper + audit + commit
# ---------------------------------------------------------------------------


def test_transition_destino_rio_to_descarte_sets_routing_and_audits():
    rio = _rio(routing="in_progress")
    db = _db_for(rio)

    with patch("brave.api.routers.cms.write_audit") as audit:
        result = transition_destino(
            rio_id=rio.id,
            # Merge: in_progress derives column "dlq" now, so expected="dlq".
            body=TransitionBody(to="descarte", expected="dlq"),
            db=db,
        )

    assert rio.routing == "descarte"
    assert audit.call_args.kwargs["action"] == "transition_descarte"
    db.commit.assert_called_once()
    assert result == {"status": "ok", "to": "descarte"}


def test_transition_destino_rio_to_mar_reuses_promote_helper():
    rio = _rio(routing="in_progress")
    db = _db_for(rio)

    with patch("brave.core.dlq.service.validate_and_promote_rio") as promote, patch(
        "brave.api.routers.cms.write_audit"
    ) as audit:
        result = transition_destino(
            rio_id=rio.id,
            # Merge: in_progress derives column "dlq" now, so expected="dlq".
            body=TransitionBody(to="mar", expected="dlq"),
            db=db,
        )

    promote.assert_called_once()
    assert audit.call_args.kwargs["action"] == "transition_mar"
    db.commit.assert_called_once()
    assert result == {"status": "ok", "to": "mar"}


def test_transition_destino_dlq_to_rio_reuses_reprocess_helper():
    rio = _rio(routing="dlq")
    db = _db_for(rio)

    with patch("brave.core.rio.routing.reprocess_record") as reprocess, patch(
        "brave.api.routers.cms.write_audit"
    ) as audit:
        result = transition_destino(
            rio_id=rio.id,
            body=TransitionBody(to="rio", expected="dlq"),
            db=db,
        )

    reprocess.assert_called_once()
    assert audit.call_args.kwargs["action"] == "transition_rio"
    db.commit.assert_called_once()
    assert result == {"status": "ok", "to": "rio"}


def test_transition_body_rejects_extra_fields():
    with pytest.raises(Exception):
        TransitionBody(to="mar", expected="rio", sneaky="x")


# ===========================================================================
# ATRATIVO edge table (atrativos.py) — backward/force edges + whatsapp delegate
# ===========================================================================

from brave.api.routers.atrativos import (  # noqa: E402
    _ATRATIVO_ALLOWED_EDGES,
    transition_atrativo,
)

# The ONLY atrativo edges that may mutate (expected → to).
ATRATIVO_ALLOWED = {
    ("rio", "dlq"),       # force send-to-review
    ("dlq", "rio"),       # reprocess / reopen (NEW)
    ("rio", "mar"),       # borderline promotion via the §7.6 gate
    ("rio", "descarte"),  # descarte
    # Rio/DLQ column merge: atrativos now rest in the "dlq"-keyed "Rio · revisão"
    # column, so promote/descarte must be reachable from "dlq" too (twins of the
    # now-dead "rio" edges above; mirrors the destino contract).
    ("dlq", "mar"),       # promotion from the merged Rio column
    ("dlq", "descarte"),  # descarte from the merged Rio column
    ("whatsapp", "whatsapp"),  # into-whatsapp: delegate to the audited gate approve
}


def _atr(routing="in_progress", sub_state=None, **kw):
    base = dict(
        id=uuid.uuid4(),
        nascente_id=uuid.uuid4(),
        entity_type="attraction",
        uf="BA",
        routing=routing,
        sub_state=sub_state,
        canonical_key="tripadvisor:1",
    )
    base.update(kw)
    return RioRecord(**base)


def test_atrativo_allow_list_is_exactly_the_paired_contract():
    assert set(_ATRATIVO_ALLOWED_EDGES.keys()) == ATRATIVO_ALLOWED


def test_atrativo_every_unmapped_edge_is_absent():
    for expected, to in product(COLUMNS, COLUMNS):
        if (expected, to) in ATRATIVO_ALLOWED:
            assert (expected, to) in _ATRATIVO_ALLOWED_EDGES
        else:
            assert (expected, to) not in _ATRATIVO_ALLOWED_EDGES


def test_atrativo_dlq_to_rio_is_allowed():
    assert ("dlq", "rio") in _ATRATIVO_ALLOWED_EDGES


def test_atrativo_no_mar_edge_ever_present():
    for to in COLUMNS:
        assert ("mar", to) not in _ATRATIVO_ALLOWED_EDGES


def test_transition_atrativo_mar_to_descarte_is_409_and_never_mutates():
    rio = _atr(routing="mar")
    db = _db_for(rio)

    with patch("brave.api.routers.atrativos.validate_and_promote_rio") as promote, patch(
        "brave.api.routers.atrativos.write_audit"
    ) as audit, pytest.raises(HTTPException) as exc:
        transition_atrativo(
            rio_id=rio.id,
            body=TransitionBody(to="descarte", expected="mar"),
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "transição não suportada"
    promote.assert_not_called()
    audit.assert_not_called()
    db.commit.assert_not_called()
    assert rio.routing == "mar"


def test_transition_atrativo_dlq_to_rio_reuses_reprocess_helper():
    rio = _atr(routing="dlq")
    db = _db_for(rio)

    with patch("brave.core.rio.routing.reprocess_record") as reprocess, patch(
        "brave.api.routers.atrativos.write_audit"
    ) as audit:
        result = transition_atrativo(
            rio_id=rio.id,
            body=TransitionBody(to="rio", expected="dlq"),
            db=db,
        )

    reprocess.assert_called_once()
    assert audit.call_args.kwargs["action"] == "transition_rio"
    db.commit.assert_called_once()
    assert result == {"status": "ok", "to": "rio"}


def test_transition_atrativo_rio_to_mar_reuses_validate_and_promote():
    rio = _atr(routing="in_progress")
    db = _db_for(rio)

    with patch("brave.api.routers.atrativos.validate_and_promote_rio") as promote, patch(
        "brave.api.routers.atrativos.write_audit"
    ) as audit:
        result = transition_atrativo(
            rio_id=rio.id,
            # Rio/DLQ merge: an in_progress atrativo now rests in the "dlq"-keyed
            # "Rio · revisão" column, so the promote edge is (dlq → mar).
            body=TransitionBody(to="mar", expected="dlq"),
            db=db,
        )

    promote.assert_called_once()
    assert audit.call_args.kwargs["action"] == "transition_mar"
    db.commit.assert_called_once()
    assert result == {"status": "ok", "to": "mar"}


def test_transition_atrativo_into_whatsapp_delegates_only_from_aguardando():
    """The into-whatsapp edge delegates to the audited gate approve — and ONLY
    when sub_state is aguardando_consulta_whatsapp (else 409, no delegate)."""
    rio = _atr(routing="in_progress", sub_state="aguardando_consulta_whatsapp")
    db = _db_for(rio)

    sentinel = {"status": "accepted", "rio_id": str(rio.id)}
    with patch(
        "brave.api.routers.atrativos_gate.approve_whatsapp_gate",
        return_value=sentinel,
    ) as approve:
        result = transition_atrativo(
            rio_id=rio.id,
            body=TransitionBody(to="whatsapp", expected="whatsapp"),
            db=db,
        )

    approve.assert_called_once()
    assert result is sentinel  # delegated; no duplicate outreach/audit here


def test_transition_atrativo_into_whatsapp_409_when_not_aguardando():
    rio = _atr(routing="in_progress", sub_state="discovered")
    db = _db_for(rio)

    with patch(
        "brave.api.routers.atrativos_gate.approve_whatsapp_gate"
    ) as approve, pytest.raises(HTTPException) as exc:
        transition_atrativo(
            rio_id=rio.id,
            body=TransitionBody(to="whatsapp", expected="whatsapp"),
            db=db,
        )

    assert exc.value.status_code == 409
    approve.assert_not_called()

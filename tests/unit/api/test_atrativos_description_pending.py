"""GET /api/v1/atrativos: the derived description_pending flag (kanban "Sem descrição").

Offline twin of the integration test in tests/test_cms_endpoints.py: list_atrativos is
called directly with a MagicMock session serving plain rows.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from brave.api.routers.cms import list_atrativos


def _rio(routing="dlq", **normalized):
    return SimpleNamespace(
        id=uuid.uuid4(),
        entity_type="attraction",
        uf="AP",
        routing=routing,
        dlq_reason=None,
        sub_state=None,
        score=None,
        canonical_key="tripadvisor:1",
        normalized={"name": "X", **normalized},
    )


def test_description_pending_true_false_and_descarte():
    rows = [_rio(), _rio(descricao_editorial="Texto."), _rio(routing="descarte")]
    db = MagicMock()
    db.scalar.return_value = len(rows)
    db.scalars.return_value.all.return_value = rows

    body = list_atrativos(
        uf=None, sub_state=None, parent_mar_id=None, routing=None, offset=0, limit=50, db=db
    )

    assert [i["description_pending"] for i in body["items"]] == [True, False, False]

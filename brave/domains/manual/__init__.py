"""``manual`` source domain — operator-authored territorial records (Phase G).

A CRUD facade over Nascente/Rio for records a human steward enters by hand. Every
manual record is written with ``source="manual"`` and is human-authoritative:
``origem_value=100`` (hand-entered by a trusted operator) and
``validacao_humana_value=100`` (human-validated by construction).

Manual is NOT a sweep lane — ``discover`` is a no-op and it is absent from
``enabled_sources``; it is driven on demand by the CMS. Mutations are gated behind
the Phase C editing lock (Motor Pausado): a create/update only proceeds when the
engine mode is PAUSADO or DESLIGADO, exactly like the ``require_editing_unlocked``
FastAPI dependency.

Kept import-light (docstring only); the registry lazy-imports ``.controllers``.
"""

"""Indexes for the sweep / describe / painel hot paths.

Built with CREATE INDEX CONCURRENTLY inside ``autocommit_block()`` — the online build that
0012's docstring says a plain ``op.create_index`` cannot do (SHARE lock on rio_records for
a full scan while the workers are writing). Consequences of leaving the transaction:

  * every migration before this one in the same ``alembic upgrade`` is COMMITTED when the
    block opens (env.py runs the whole upgrade in one transaction);
  * a CONCURRENTLY build that fails leaves an INVALID index behind. Every statement is
    therefore DROP INDEX IF EXISTS + CREATE, so re-running the upgrade repairs it.

ix_rio_territorial_key has been declared in models.py since the dedup work (D-07) but no
migration ever created it; its leading column also serves the plain ``uf`` lookups, so there
is no separate rio_records(uf) index. nascente_records(source, source_ref) is skipped:
ix_nascente_source_ref already resolves that lookup to ~1 row.

ix_rio_description_candidates — the predicate spells the CAST(... AS VARCHAR) exactly as
SQLAlchemy renders ``normalized["descricao_editorial"].as_string()`` in
description_candidates_filter(). Without the cast the planner cannot prove the query implies
the index predicate and never uses it (checked with EXPLAIN); ``->>`` on json is immutable,
so no JSON→JSONB migration is needed. tests/integration/test_migration_0016.py fails if the
two drift apart.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-18
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None

# Must stay in step with the Index() declarations in brave/core/models.py.
_INDEXES: dict[str, str] = {
    "ix_rio_territorial_key": "rio_records (uf, municipio_id, entity_type)",
    "ix_rio_records_sub_state": "rio_records (sub_state)",
    "ix_rio_description_candidates": (
        "rio_records (uf, id) WHERE entity_type = 'attraction' "
        "AND descricao_batch_id IS NULL "
        "AND CAST((normalized ->> 'descricao_editorial') AS VARCHAR) IS NULL"
    ),
    "ix_mar_records_rio_id": "mar_records (rio_id)",
    "ix_audit_log_record_id": "audit_log (record_id)",
    "ix_record_events_created_at": "record_events (created_at)",
    "ix_record_events_ref_created": "record_events (source_ref, created_at)",
}


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, spec in _INDEXES.items():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE INDEX CONCURRENTLY {name} ON {spec}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

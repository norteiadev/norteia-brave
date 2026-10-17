"""Nascente version uniqueness, rio_records.nascente_id index, Rio field backfill.

* ``uq_nascente_source_ref_version`` — store_raw did SELECT-then-INSERT with no constraint,
  so two writers of one source_ref forked duplicate v1s / two active v2s. With the unique
  index the loser gets an IntegrityError inside store_raw's savepoint and retries. A
  partial unique on (source, source_ref) WHERE superseded_by_id IS NULL cannot work: the
  new version is inserted while the old one is still active (the pointer is set after).
  Existing forks are repaired first: versions renumbered per source_ref, and every active
  row but the newest pointed at the newest.
* ``ix_rio_records_nascente_id`` — dedup stage 1 (get_by_nascente_id) and the Painel's
  'unrouted' LEFT JOIN seq-scanned rio_records once per card.
* Backfill of what the Rio cherry-pick used to drop, from the row's Nascente payload:
  ``category`` (attractions; Mar maps it to the API type) and ``participates_mtur`` /
  ``mtur_categoria`` / ``regiao_turistica`` (destinations; the push always sent False).
  Only keys the row does not have yet. MarRecords are untouched, but a changed row already
  in Mar gets ``processed_at = now()``: the outbox re-pushes an in-Mar row only when
  processed_at > its last push, and nothing else re-scores a destino — without the bump
  every destino on the API kept participates_mtur=false (and TA atrativos type='outros').

Indexes are built CONCURRENTLY inside ``autocommit_block()`` like 0016 (DROP IF EXISTS +
CREATE so a failed online build is repaired by re-running). The data repairs run first, in
the migration transaction, which the block commits when it opens.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-17
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None

# Must stay in step with brave/core/models.py.
_INDEXES: dict[str, str] = {
    "uq_nascente_source_ref_version": (
        "UNIQUE INDEX CONCURRENTLY uq_nascente_source_ref_version "
        "ON nascente_records (source, source_ref, version)"
    ),
    "ix_rio_records_nascente_id": (
        "INDEX CONCURRENTLY ix_rio_records_nascente_id ON rio_records (nascente_id)"
    ),
}


def upgrade() -> None:
    # Forked versions → 1..n per source_ref, in version/ingest order.
    op.execute(
        """
        UPDATE nascente_records n SET version = r.rn
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY source, source_ref ORDER BY version, ingested_at, id
            ) AS rn
            FROM nascente_records
        ) r
        WHERE n.id = r.id AND n.version <> r.rn
        """
    )
    # More than one active row per source_ref → the newest stays active.
    op.execute(
        """
        UPDATE nascente_records n SET superseded_by_id = w.id
        FROM (
            SELECT DISTINCT ON (source, source_ref) id, source, source_ref
            FROM nascente_records
            WHERE superseded_by_id IS NULL
            ORDER BY source, source_ref, version DESC
        ) w
        WHERE n.superseded_by_id IS NULL
          AND n.source = w.source AND n.source_ref = w.source_ref AND n.id <> w.id
        """
    )
    op.execute(
        """
        UPDATE rio_records r
        SET normalized = (r.normalized::jsonb
                          || jsonb_build_object('category', n.payload -> 'category'))::json,
            processed_at = CASE WHEN r.routing = 'mar' THEN now() ELSE r.processed_at END
        FROM nascente_records n
        WHERE n.id = r.nascente_id
          AND r.entity_type = 'attraction'
          AND r.normalized IS NOT NULL
          AND COALESCE(n.payload ->> 'category', '') <> ''
          AND r.normalized ->> 'category' IS NULL
        """
    )
    for key in ("participates_mtur", "mtur_categoria", "regiao_turistica"):
        op.execute(
            f"""
            UPDATE rio_records r
            SET normalized = (r.normalized::jsonb || jsonb_build_object(
                '{key}', n.payload::jsonb -> 'canonical' -> '{key}'))::json,
                processed_at = CASE WHEN r.routing = 'mar' THEN now() ELSE r.processed_at END
            FROM nascente_records n
            WHERE n.id = r.nascente_id
              AND r.entity_type = 'destination'
              AND r.normalized IS NOT NULL
              AND (n.payload::jsonb -> 'canonical') ? '{key}'
              AND NOT (r.normalized::jsonb ? '{key}')
            """
        )

    with op.get_context().autocommit_block():
        for name, create in _INDEXES.items():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            op.execute(f"CREATE {create}")


def downgrade() -> None:
    # The data repairs are not reverted (renumbered versions and the backfilled keys are
    # what the code now writes anyway).
    with op.get_context().autocommit_block():
        for name in _INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

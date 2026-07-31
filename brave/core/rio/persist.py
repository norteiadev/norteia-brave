"""Concurrency-safe write of the RioRecord JSONB ``normalized`` column.

Home: ``brave/core/rio/`` and not a lane package because the hazard belongs to the COLUMN,
not to the atrativos lane — every agent (both lanes) reads ``normalized``, spends seconds in
network I/O, then writes the whole dict back, and every RioRecord-shaped helper the lanes
already share (normalize, routing, dedup, label) lives here.

D-18 boundary: core, so both lanes may import it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.orm.attributes import flag_modified

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from brave.core.models import RioRecord


def persist_normalized(
    session: Session, rio: RioRecord, before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Merge ONLY the keys this run changed onto the row's CURRENT ``normalized``. Returns it.

    Lost update: an agent reads ``normalized``, snapshots it, then spends seconds in network
    I/O before writing the whole JSONB column back from that pre-I/O snapshot — silently
    erasing whatever ANY other writer committed in that window (workers run in parallel;
    prefetch 1 is not concurrency 1).

    The concrete case is the batched-description lane: collect commits ``descricao_editorial``
    + the lifted ``completude_value`` and clears ``descricao_batch_id`` in one transaction, a
    stale dict wipes both, and with the stamp already NULL the record is instantly eligible
    again — so the next tick PAYS for the same description a second time.

    Re-reading under FOR UPDATE and writing a delta (not a snapshot) fixes it for every
    concurrent writer.

    CALL IT INSTEAD OF ``rio.normalized = ...``, never after: ``Session.refresh`` EXPIRES the
    attribute before it autoflushes, so a pending assignment made just above would be dropped
    on the floor rather than flushed. ``rio`` must be persistent (not pending) — refresh
    raises otherwise; every caller today gets its row from ``session.get``.

    Deletions are not propagated (``after`` is always ``dict(before)`` plus writes, so no
    caller deletes): the merge cannot resurrect a key another writer removed, and it cannot
    carry a removal of its own. Add an explicit tombstone list here if a caller ever needs one.
    """
    delta = {k: v for k, v in after.items() if k not in before or before[k] != v}
    session.refresh(rio, ["normalized"], with_for_update=True)
    merged = {**(rio.normalized or {}), **delta}
    rio.normalized = merged
    flag_modified(rio, "normalized")
    return merged

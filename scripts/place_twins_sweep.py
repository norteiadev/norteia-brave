"""Discard the atrativo twins already in the base: one keeper per (município, google_place_id).

Places enrichment now resolves twins as it writes the place_id (resolve_place_twins); this
sweep applies the same rule to the rows enriched before it. A loser with an active Mar is
merged into the keeper's, so the outbox pushes it as visibility=hidden.

Dry run unless --apply (the dry run flushes, prints, then rolls back).

    python scripts/place_twins_sweep.py [--uf RJ ...] [--apply]
"""

from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import func, select

from brave.core.models import RioRecord
from brave.core.rio.dedup import resolve_place_twins
from brave.tasks.pipeline import _get_session

ACTOR = "place_twins_sweep"


def run(session, ufs: list[str], apply: bool) -> Counter:
    pid = RioRecord.normalized["google_place_id"].as_string()
    where = [
        RioRecord.entity_type == "attraction",
        RioRecord.routing != "descarte",
        RioRecord.municipio_id.is_not(None),
        pid.is_not(None),
    ]
    if ufs:
        where.append(RioRecord.uf.in_(ufs))
    groups = session.execute(
        select(RioRecord.municipio_id, pid)
        .where(*where)
        .group_by(RioRecord.municipio_id, pid)
        .having(func.count() > 1)
    ).all()
    counts: Counter = Counter()
    for municipio_id, place_id in groups:
        group = session.scalars(select(RioRecord).where(*where, RioRecord.municipio_id ==
                                                        municipio_id, pid == place_id)).all()
        if len(group) < 2:  # an earlier group's merge already took it
            continue
        rio, before = group[0], {r.id: r.routing for r in group}
        losers = resolve_place_twins(session, rio, actor=ACTOR)
        keeper = next(r for r in group if r not in losers)
        for loser in losers:
            counts[(loser.uf, before[loser.id])] += 1
            print(f"{loser.uf}\t{before[loser.id]}\t{loser.canonical_key}\t"
                  f"{(loser.normalized or {}).get('name')}\t->\t{keeper.normalized.get('name')}")
    if apply:
        session.commit()
    print(f"{'APPLIED' if apply else 'DRY RUN'}: {len(groups)} groups, "
          f"{sum(counts.values())} discarded {dict(sorted(counts.items()))}")
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uf", nargs="*", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    session, _ = _get_session()
    try:
        run(session, args.uf, args.apply)
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()

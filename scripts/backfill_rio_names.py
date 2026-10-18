"""Re-derive Rio names stored by the old str.title() normalize_name (F19/F52).

The Rio never re-normalizes an existing record, so names ingested before the PT-BR-aware
normalize_name still read "Masp - Museu De Arte De São Paulo" / "Dom Pedro Ii". For every
Rio whose normalized name is EXACTLY the old title-cased form of its Nascente payload name
(i.e. nobody edited it since), this rewrites it with today's normalize_name of the source
name. Edited names are left alone. Rio only: an already-promoted MarRecord picks the new
name up on its next promote/push.

Usage (worker container or host with BRAVE_DB_URL):
    python scripts/backfill_rio_names.py            # dry run: counts + a sample
    python scripts/backfill_rio_names.py --apply
"""

from __future__ import annotations

import re
import sys

from sqlalchemy import select

from brave.core.models import NascenteRecord, RioRecord
from brave.core.rio.normalize import normalize_name
from brave.core.rio.persist import persist_normalized
from brave.tasks.pipeline import _get_session


def _old_title(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip()).title()


def renamed(current: str | None, raw: str | None) -> str | None:
    """The corrected name, or None when the record must be left as is."""
    if not raw or current != _old_title(raw):
        return None
    new = normalize_name(raw)
    return new if new != current else None


def main(argv: list[str]) -> None:
    apply = "--apply" in argv
    session, _ = _get_session()
    try:
        rows = session.execute(
            select(RioRecord.id, NascenteRecord.payload)
            .join(NascenteRecord, NascenteRecord.id == RioRecord.nascente_id)
        ).all()
        changed = 0
        for rio_id, payload in rows:
            rio = session.get(RioRecord, rio_id)
            before = dict(rio.normalized or {})
            new = renamed(before.get("name"), (payload or {}).get("name"))
            if new is None:
                continue
            changed += 1
            if changed <= 20:
                print(f"{before.get('name')!r} -> {new!r}")
            if apply:
                persist_normalized(session, rio, before, {**before, "name": new})
                session.commit()  # per record: releases the row lock
        print(f"{changed} of {len(rows)} names {'rewritten' if apply else 'to rewrite'}")
    finally:
        session.close()


if __name__ == "__main__":
    main(sys.argv[1:])

"""Migration 0023 (editorial lane): up and down, in a rolled-back transaction.

Postgres DDL is transactional, so the test never leaves the shared test DB changed: whatever
state it started in (tables there or not), it ends in it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from brave.core.models import Base

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[2]
_TABLES = {"editorial_documents", "editorial_claims", "editorial_destinations"}


def _migration():
    path = next((_ROOT / "alembic" / "versions").glob("0023_*.py"))
    spec = importlib.util.spec_from_file_location("m0023", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_revision_chain() -> None:
    from alembic.config import Config

    scripts = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini")))
    rev = scripts.get_revision("0023")
    assert rev.down_revision == "0022"


def test_up_down_is_clean_and_matches_models(db_engine) -> None:
    mod = _migration()
    with db_engine.connect() as conn:
        trans = conn.begin()
        try:
            with Operations.context(MigrationContext.configure(conn)):
                if _TABLES & set(inspect(conn).get_table_names()):
                    mod.downgrade()  # the test DB is already at head: start from 0022
                assert not _TABLES & set(inspect(conn).get_table_names())

                mod.upgrade()
                assert set(inspect(conn).get_table_names()) >= _TABLES
                diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
                ours = [
                    d for d in diff if _TABLES & {str(getattr(x, "name", "")) for x in _flat(d)}
                ]
                assert ours == [], ours

                mod.downgrade()
                assert not _TABLES & set(inspect(conn).get_table_names())
        finally:
            trans.rollback()


def _flat(diff):
    """The Table / Index / Column objects inside one compare_metadata entry."""
    items = diff if isinstance(diff, list) else [diff]
    out = []
    for it in items:
        for part in it if isinstance(it, tuple) else (it,):
            out.append(part)
            if hasattr(part, "table"):
                out.append(part.table)
    return out

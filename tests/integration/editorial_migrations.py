"""The editorial lane's migrations (0023 onwards), loaded as modules for in-transaction tests.

Postgres DDL is transactional: a test runs ``downgrade`` / ``upgrade`` inside a transaction it
rolls back, so the shared test DB ends in the state it started in. Later migrations (0025…)
must be undone before an earlier one is, and redone after it: ``chain`` gives them in order.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).resolve().parents[2]
FIRST = 23


def load(number: int) -> ModuleType:
    path = next((_ROOT / "alembic" / "versions").glob(f"{number:04d}_*.py"))
    spec = importlib.util.spec_from_file_location(f"m{number:04d}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def chain() -> list[tuple[int, ModuleType]]:
    """(number, module) of every migration from 0023 to the head, ascending."""
    numbers = sorted(
        int(p.name[:4])
        for p in (_ROOT / "alembic" / "versions").glob("[0-9][0-9][0-9][0-9]_*.py")
        if int(p.name[:4]) >= FIRST
    )
    return [(n, load(n)) for n in numbers]


def downgrade_to(chain_: list[tuple[int, ModuleType]], number: int) -> None:
    """Undo every migration newer than ``number`` and ``number`` itself (state: number-1)."""
    for n, mod in reversed(chain_):
        if n >= number:
            mod.downgrade()


def upgrade_from(chain_: list[tuple[int, ModuleType]], number: int) -> None:
    for n, mod in chain_:
        if n >= number:
            mod.upgrade()

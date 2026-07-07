"""Regression guard: brave/ package must never import the tests tree.

The production wheel ships only packages=["brave"] (pyproject.toml). Any
`import tests` or `from tests ...` statement inside brave/ is a packaging-
integrity break — the tests tree is not shipped, so the import raises
ModuleNotFoundError at runtime under the documented default config.

This guard walks every .py file under brave/ and fails if any file contains
an import STATEMENT matching `^\\s*(from|import)\\s+tests\\b` (anchored regex,
not a bare substring). Docstrings and comments that mention "tests/" or
"tests.fakes" are safe and must NOT trigger the assertion.

See: INT-BLOCKER-01 (Phase 09, plan 01) — fixed by adding NullPlacesClient and
NullLLMClient in brave/clients/ and rewiring the offline-branch import sites in
brave/tasks/pipeline.py.
"""

import re
from pathlib import Path


# Compiled regex anchored to import STATEMENTS only.
# Matches:
#   from tests import ...
#   from tests.fakes import ...
#   import tests
#   import tests.fakes
# Does NOT match:
#   # from tests/fakes/ (comment)
#   "lives in brave/ (NOT tests/)" (docstring)
_IMPORT_STMT_RE = re.compile(r"^\s*(from|import)\s+tests\b")


def test_brave_package_never_imports_tests_tree() -> None:
    """No .py file under brave/ may contain an import statement for the tests tree.

    Scans every *.py file under brave/ (relative to this file's repo root) and
    asserts zero matches against `^\\s*(from|import)\\s+tests\\b`. On failure the
    assertion message lists every offending (file, lineno, line) triple.
    """
    # Resolve brave/ relative to this test file's location (tests/unit/ -> ../../brave/)
    repo_root = Path(__file__).parent.parent.parent
    brave_dir = repo_root / "brave"

    assert brave_dir.is_dir(), f"brave/ directory not found at {brave_dir}"

    violations: list[tuple[Path, int, str]] = []

    for py_file in sorted(brave_dir.rglob("*.py")):
        # rglob("*.py") naturally skips .pyc files; __pycache__ dirs still found
        # but their contents are .pyc not .py so excluded by the glob pattern.
        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            # If a file can't be read, skip silently — not a tests-import problem.
            continue

        for lineno, line in enumerate(lines, start=1):
            if _IMPORT_STMT_RE.match(line):
                violations.append((py_file, lineno, line.rstrip()))

    if violations:
        lines_report = "\n".join(
            f"  {path.relative_to(repo_root)}:{lineno}: {line}"
            for path, lineno, line in violations
        )
        raise AssertionError(
            f"Found {len(violations)} import statement(s) targeting the tests tree "
            f"inside brave/:\n{lines_report}\n\n"
            "The production wheel ships only packages=['brave']. Any `from tests` "
            "or `import tests` statement in brave/ will raise ModuleNotFoundError "
            "at runtime under the default offline config. "
            "Replace with the matching in-package Null client under brave/clients/."
        )

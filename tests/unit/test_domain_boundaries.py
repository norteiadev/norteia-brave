"""Regression guard: generalized D-18 import boundaries for the Phase G layout.

Two static checks over the ``brave/`` source tree (mirrors the anchored-regex
style of ``test_no_test_imports_in_brave.py`` — import STATEMENTS only, so
docstrings/comments never false-positive):

  CHECK A — kernel purity: ``brave/core`` and ``brave/shared`` must NEVER import
    ``brave.domains``, ``brave.tasks`` (or ``brave.lanes``). The kernel sits below
    the sources; a kernel→domain import inverts the layering.

  CHECK B — no cross-domain imports: a module under ``brave/domains/<x>/`` must
    NOT import ``brave.domains.<y>`` for a sibling domain ``y``. Domains import the
    kernel + clients ONLY; the registry (``brave/domains/__init__.py``) and
    ``base.py`` are the two root files allowed to reference every domain, so files
    directly at the domains root are exempt.

See docs/ultraplan-refactor-brave.md (Phase G).
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent
_BRAVE_DIR = _REPO_ROOT / "brave"

# CHECK A: kernel (core/shared) must not import these top-level packages.
_KERNEL_FORBIDDEN_RE = re.compile(r"^\s*(?:from|import)\s+brave\.(domains|tasks|lanes)\b")

# CHECK B: capture the first path segment after `brave.domains.`
_CROSS_DOMAIN_RE = re.compile(
    r"^\s*(?:from|import)\s+brave\.domains\.([A-Za-z_][A-Za-z0-9_]*)"
)


def _iter_py(root: Path):
    """Yield every readable *.py file under root (rglob skips .pyc)."""
    for py_file in sorted(root.rglob("*.py")):
        try:
            lines = py_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        yield py_file, lines


def test_kernel_never_imports_domains_or_tasks() -> None:
    """CHECK A: no file under brave/core or brave/shared imports domains/tasks/lanes."""
    assert _BRAVE_DIR.is_dir(), f"brave/ not found at {_BRAVE_DIR}"

    violations: list[tuple[Path, int, str]] = []
    for scope in ("core", "shared"):
        scope_dir = _BRAVE_DIR / scope
        if not scope_dir.is_dir():
            continue
        for py_file, lines in _iter_py(scope_dir):
            for lineno, line in enumerate(lines, start=1):
                if _KERNEL_FORBIDDEN_RE.match(line):
                    violations.append((py_file, lineno, line.rstrip()))

    if violations:
        report = "\n".join(
            f"  {p.relative_to(_REPO_ROOT)}:{n}: {ln}" for p, n, ln in violations
        )
        raise AssertionError(
            "Kernel purity violation (generalized D-18): brave.core / brave.shared "
            "must never import brave.domains / brave.tasks / brave.lanes:\n" + report
        )


def test_domains_never_import_sibling_domains() -> None:
    """CHECK B: no domain package imports a *sibling* domain package."""
    domains_root = _BRAVE_DIR / "domains"
    if not domains_root.is_dir():
        import pytest

        pytest.skip("brave/domains not present (Phase G pending)")

    domain_names = {
        p.name
        for p in domains_root.iterdir()
        if p.is_dir() and p.name != "__pycache__"
    }
    # Sanity: the three built-in domains must exist so this guard is meaningful.
    assert {"places", "tripadvisor", "manual"} <= domain_names, domain_names

    violations: list[tuple[Path, int, str]] = []
    for py_file, lines in _iter_py(domains_root):
        rel = py_file.relative_to(domains_root).parts
        if len(rel) == 1:
            # Root files (registry __init__.py, base.py) may reference every domain.
            continue
        owner = rel[0]
        for lineno, line in enumerate(lines, start=1):
            m = _CROSS_DOMAIN_RE.match(line)
            if m is None:
                continue
            target = m.group(1)
            if target in domain_names and target != owner:
                violations.append((py_file, lineno, line.rstrip()))

    if violations:
        report = "\n".join(
            f"  {p.relative_to(_REPO_ROOT)}:{n}: {ln}" for p, n, ln in violations
        )
        raise AssertionError(
            "Cross-domain import violation (generalized D-18): a domain must not "
            "import a sibling domain (kernel + clients only):\n" + report
        )

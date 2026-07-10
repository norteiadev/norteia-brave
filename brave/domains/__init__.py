"""Brave source-domain registry (Phase G).

The single place that knows every collection source. Adding a source = a new
``brave/domains/<fonte>/`` package + one ``_LAZY`` line here.

``get_domain(name)`` returns the :class:`~brave.domains.base.SourceDomain`
descriptor for a source (``"default"`` = the Google Places attraction track /
``"tripadvisor"`` / ``"manual"``). ``enabled_sources(config)`` returns the
sweepable lanes that are enabled in config (delegates to
``brave.config.runtime`` — the config overlay stays the source of truth;
``manual`` is registered but never a sweep lane, so it is absent).

Lazy on purpose: controllers are imported on first ``get_domain`` rather than at
package import, so the ``brave.lanes.*`` re-export shims (which import individual
domain submodules and therefore run this ``__init__``) never eagerly pull the
TripAdvisor HTTP client, and an import error in one domain cannot break unrelated
shim imports.

Import posture (D-18): this registry may import every domain (it is the one
exception); the domains themselves NEVER import each other. Enforced by
``tests/unit/test_domain_boundaries.py``.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from brave.domains.base import SourceDomain, build_score_input

if TYPE_CHECKING:
    from brave.config.settings import AppConfig

# name -> (module, attribute). "default" is the engine source name for the Google
# Places attraction track (brave.core.engine `_VALID_SOURCES`, brave:engine:source);
# the retired Mtur destino-seed no longer registers a domain.
_LAZY: dict[str, tuple[str, str]] = {
    "default": ("brave.domains.places.controllers", "PLACES_DOMAIN"),
    "tripadvisor": ("brave.domains.tripadvisor.controllers", "TRIPADVISOR_DOMAIN"),
    "manual": ("brave.domains.manual.controllers", "MANUAL_DOMAIN"),
}

# Registered-but-not-a-sweep-lane names (excluded from enabled_sources).
_NON_SWEEP: frozenset[str] = frozenset({"manual"})

_CACHE: dict[str, SourceDomain] = {}

__all__ = [
    "SourceDomain",
    "build_score_input",
    "get_domain",
    "register",
    "registered_source_names",
    "enabled_sources",
]


def register(name: str, module: str, attribute: str) -> None:
    """Register a source domain by name → (module, attribute). Overwrites in place.

    Intended for tests / plugins; the built-in sources are declared in ``_LAZY``.
    """
    _LAZY[name] = (module, attribute)
    _CACHE.pop(name, None)


def get_domain(name: str) -> SourceDomain:
    """Return the registered :class:`SourceDomain` for ``name``.

    Raises:
        KeyError: when ``name`` is not a registered source.
    """
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    try:
        module_path, attribute = _LAZY[name]
    except KeyError:
        raise KeyError(
            f"unknown source domain {name!r}; registered: {sorted(set(_LAZY))}"
        ) from None
    domain: SourceDomain = getattr(import_module(module_path), attribute)
    _CACHE[name] = domain
    return domain


def registered_source_names() -> list[str]:
    """All registered source names (``"default"`` / ``"tripadvisor"`` / ``"manual"``), sorted."""
    return sorted(set(_LAZY))


def enabled_sources(config: AppConfig) -> list[str]:
    """The enabled *sweepable* collection sources, in config declaration order.

    Delegates to :func:`brave.config.runtime.enabled_sources` (the config overlay
    is authoritative) and drops any non-sweep source (e.g. ``manual``). The live
    single-source *selector* stays Redis-authoritative (``brave.core.engine``).
    """
    from brave.config.runtime import enabled_sources as _cfg_enabled_sources

    return [name for name in _cfg_enabled_sources(config) if name not in _NON_SWEEP]

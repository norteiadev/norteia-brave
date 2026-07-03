"""TripAdvisorRepository — data-access seam for the ``tripadvisor`` domain (Phase G).

A thin facade over the three stateful stores the TA lane touches, so callers
depend on one repository surface instead of reaching into individual modules:
  - sweep progress   (``sweep_progress`` — Redis hash ``brave:ta:sweep:progress``)
  - the Redis session (``client.BRAVE_TA_SESSION_KEY`` cookie jar)
  - geo / ibge caches (``geo.resolve_geo_id`` + ``ibge`` resolvers)

Import posture (D-18): kernel + clients + own-domain submodules only. The heavy
imports are lazy so importing the repository does not eagerly load the HTTP client.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from brave.domains.tripadvisor.ibge import IbgeMunicipio


class TripAdvisorRepository:
    """Stateless facade over TA sweep-progress, the Redis session, and geo/ibge."""

    # --- sweep progress -----------------------------------------------------
    def progress(self, redis: Any) -> dict[str, Any]:
        """Current sweep-progress snapshot (state/pages/attractions/offsets)."""
        from brave.domains.tripadvisor import sweep_progress

        return sweep_progress.get_progress(redis)

    def resume_offset(self, redis: Any) -> int:
        """Offset to resume a paginated sweep from (0 when none persisted)."""
        from brave.domains.tripadvisor import sweep_progress

        return sweep_progress.get_resume_offset(redis)

    # --- Redis session ------------------------------------------------------
    def session_raw(self, redis: Any) -> Any:
        """Raw ``brave:ta:session`` value (cookie jar JSON), or None if absent."""
        from brave.domains.tripadvisor.client import BRAVE_TA_SESSION_KEY

        return redis.get(BRAVE_TA_SESSION_KEY)

    def has_session(self, redis: Any) -> bool:
        """True when a TA session cookie jar is present in Redis."""
        return self.session_raw(redis) is not None

    # --- geo / ibge caches --------------------------------------------------
    def resolve_geo_id(self, uf: str, redis: Any, config: Any) -> int:
        """Resolve a UF → TripAdvisor geoId (Redis cache → seed JSON → ValueError)."""
        from brave.domains.tripadvisor.geo import resolve_geo_id

        return resolve_geo_id(uf, redis, config)

    def load_ibge(self, path: Path | str) -> list[IbgeMunicipio]:
        """Load the IBGE municipality dataset from a CSV path."""
        from brave.domains.tripadvisor.ibge import load_ibge_csv

        return load_ibge_csv(path)

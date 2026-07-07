"""``tripadvisor`` source domain (Phase G).

Full TripAdvisor collection lane moved here from ``brave.lanes.tripadvisor``:
the HTTP ``client``, the ``atrativos`` / ``destinos`` ingest producers, the
``geo`` / ``ibge`` resolvers, ``scoring``, ``schemas``, ``session`` cookie
write-back, ``sweep_progress`` state, and ``uf_names``. The ``controllers`` /
``repositories`` facades add the SourceDomain implementation and a data-access
seam over sweep-progress + the Redis TA session + geo/ibge caches.

Kept import-light (docstring only) so the ``brave.lanes.tripadvisor.*`` re-export
shims and ``from brave.domains.tripadvisor import sweep_progress`` do not eagerly
pull the HTTP client. The registry lazy-imports ``.controllers`` on first
``get_domain("tripadvisor")``.
"""

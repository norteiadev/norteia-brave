"""``tripadvisor`` source domain (Phase G).

The full TripAdvisor collection lane:
the HTTP ``client``, the ``atrativos`` / ``destinos`` ingest producers, the
``geo`` / ``ibge`` resolvers, ``scoring``, ``schemas``, ``session`` cookie
write-back, ``sweep_progress`` state, and ``uf_names``. ``controllers`` adds the
SourceDomain implementation.

Kept import-light (docstring only) so importing one submodule (e.g.
``from brave.domains.tripadvisor import sweep_progress``) does not eagerly pull the
HTTP client. The registry lazy-imports ``.controllers`` on first
``get_domain("tripadvisor")``.
"""

"""``places`` source domain — the "default" Google Places attraction track.

Owns the Google Places attraction lane (``discover_atrativo`` → ``find_contacts``
→ ``gather_signals``) and its LLM extraction schemas. Registered under the legacy
engine source name ``"default"`` (see ``brave.domains`` registry); the retired
Mtur destino-seed no longer participates — parent destinos are resolved from the
DB reference tables (``ensure_destino``), not seeded through the pipeline.

Kept import-light (docstring only): the registry lazy-imports
``brave.domains.places.controllers`` on first ``get_domain(...)``, so importing
this package never eagerly pulls the Google Places / LLM clients.
"""

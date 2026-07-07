"""``mtur`` source domain — the "default" collection track (Phase G).

Owns the Mtur destino seed (``services.MturSeedIngest``) plus the Google Places
attraction track (``discovery`` / ``contact`` / ``signal`` / ``number_discovery``)
and their LLM extraction schemas (``dtos``). Registered under BOTH ``"mtur"`` and
the legacy engine source name ``"default"`` (see ``brave.domains`` registry).

Kept import-light (docstring only): the registry lazy-imports
``brave.domains.mtur.controllers`` on first ``get_domain(...)``, and the old
``brave.lanes.*`` re-export shims import individual submodules directly, so
importing this package never eagerly pulls clients.
"""

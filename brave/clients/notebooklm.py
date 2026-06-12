"""Real NotebookLMClient — reads local JSON report files for municipality ingest (D-02).

Implements NotebookLMClientProtocol (brave/clients/base.py).

No network I/O — fully offline. Reads structured JSON tourism reports from
data/notebooklm/{uf}/{ibge_code}.json. Returns {} when no report exists for
a municipality (graceful degradation — producer continues with next municipality).

municipio format accepted by fetch_report:
  - "nome:uf:ibge"  (e.g. "Porto Seguro:BA:2927408") — preferred, unambiguous
  - plain name only (e.g. "Porto Seguro") — fallback, no file lookup attempted

Usage:
    from brave.clients.notebooklm import NotebookLMClient

    client = NotebookLMClient()
    report = await client.fetch_report("Porto Seguro:BA:2927408")
    # {} if no report file exists, or the parsed JSON dict

References:
  - 02-CONTEXT.md D-02: ingest all reports; overlap resolved by dedup
  - brave/clients/base.py NotebookLMClientProtocol: Protocol this class implements
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

REPORTS_PATH = pathlib.Path(__file__).parent.parent.parent / "data" / "notebooklm"


class NotebookLMClient:
    """Real NotebookLM client — reads structured report JSON files.

    Implements NotebookLMClientProtocol via structural typing (no explicit inheritance).
    Fully offline — no network calls. Reads from data/notebooklm/{uf}/{ibge}.json.

    Returns {} when no report file exists for the requested municipality.
    This is the expected state for most municipalities in early pipeline runs.
    """

    async def fetch_report(self, municipio: str) -> dict[str, Any]:
        """Fetch a structured NotebookLM tourism report for a municipality.

        Parses the municipio string to extract uf and ibge_code for file lookup.
        Falls back to {} for any municipality where the report file is absent.

        Args:
            municipio: Municipality identifier. Supported formats:
                - "nome:uf:ibge" — e.g. "Porto Seguro:BA:2927408" (preferred)
                - Plain name — e.g. "Porto Seguro" (no file lookup; returns {})

        Returns:
            Parsed JSON dict from the report file, or {} if no report exists.
        """
        parts = municipio.split(":")
        if len(parts) >= 3:
            # Format: "nome:uf:ibge" (or more parts — take last two as uf, ibge)
            uf = parts[-2].strip().upper()
            ibge = parts[-1].strip()
            report_path = REPORTS_PATH / uf / f"{ibge}.json"
            try:
                with open(report_path, encoding="utf-8") as f:
                    return json.load(f)
            except OSError:
                # File not found or unreadable — return empty dict (graceful degradation)
                return {}
        # Plain name or unsupported format — no file lookup
        return {}


# Structural type check: NotebookLMClient must satisfy NotebookLMClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import NotebookLMClientProtocol

    _client: NotebookLMClientProtocol = NotebookLMClient()  # noqa: F841

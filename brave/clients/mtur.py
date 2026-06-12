"""Real MturClient — reads bundled CSV seed file for municipality ingest (D-01).

Implements MturClientProtocol (brave/clients/base.py).

No network I/O — fully offline. Reads the latest municipios_mtur_*.csv from
data/mtur/ (sorted descending by filename, so the highest year wins).

Categoria mapping (_map_categoria):
  Old nomenclature: A/B → Oferta Principal; C/D → Complementar; E → Apoio
  New nomenclature (2025+): "turísticos"/"turisticos" → Oferta Principal;
    "complementar" → Complementar; "apoio" → Apoio

Usage:
    from brave.clients.mtur import MturClient

    client = MturClient()
    municipalities = await client.fetch_municipalities("BA")
    # [{"ibge_code": "2927408", "name": "Porto Seguro", "categoria": "Oferta Principal", "uf": "BA"}, ...]

References:
  - 02-CONTEXT.md D-01: bundled static seed dataset, not live REST
  - brave/clients/base.py MturClientProtocol: Protocol this class implements
"""

from __future__ import annotations

import csv
import pathlib
from typing import Any

DATA_PATH = pathlib.Path(__file__).parent.parent.parent / "data" / "mtur"


def _load_csv() -> list[dict[str, Any]]:
    """Glob DATA_PATH for municipios_mtur_*.csv, pick the latest by filename sort.

    Returns:
        List of dicts from csv.DictReader (one per CSV row).

    Raises:
        FileNotFoundError: If no matching CSV exists under data/mtur/.
    """
    candidates = sorted(DATA_PATH.glob("municipios_mtur_*.csv"), reverse=True)
    if not candidates:
        raise FileNotFoundError(
            f"No Mtur seed CSV found in {DATA_PATH}. "
            "Expected a file matching 'municipios_mtur_YYYY.csv'. "
            "Download the official Mtur dataset and place it under data/mtur/."
        )
    path = candidates[0]
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _map_categoria(raw: str) -> str:
    """Map a raw Mtur categoria value to the canonical Norteia label.

    Handles both old nomenclature (A/B/C/D/E, published pre-2025) and new
    nomenclature ("Municípios turísticos", "com oferta turística complementar",
    "de apoio ao turismo", published 2025+).

    Args:
        raw: Raw categoria string from the Mtur CSV.

    Returns:
        One of "Oferta Principal", "Complementar", or "Apoio".
        Falls back to "Apoio" for any unrecognized value.
    """
    raw_clean = raw.strip().upper()
    # Old nomenclature: A and B → Oferta Principal
    # New nomenclature: "turísticos"/"turisticos" → Oferta Principal
    if raw_clean in ("A", "B") or "TURÍSTICOS" in raw_clean or "TURISTICOS" in raw_clean:
        return "Oferta Principal"
    # Old nomenclature: C and D → Complementar
    # New nomenclature: "complementar" → Complementar
    elif raw_clean in ("C", "D") or "COMPLEMENTAR" in raw_clean:
        return "Complementar"
    # Old nomenclature: E → Apoio
    # New nomenclature: "apoio" → Apoio
    elif raw_clean in ("E",) or "APOIO" in raw_clean:
        return "Apoio"
    # Safe default: unknown values treated as Apoio (lowest priority)
    return "Apoio"


class MturClient:
    """Real Mtur municipality client — reads bundled CSV seed file.

    Implements MturClientProtocol via structural typing (no explicit inheritance).
    Fully offline — no network calls. Reads from data/mtur/municipios_mtur_*.csv.

    Raises FileNotFoundError on fetch_municipalities if no CSV exists under
    data/mtur/ — the producer must catch this and log appropriately (T-02-03-03).
    """

    async def fetch_municipalities(self, uf: str) -> list[dict[str, Any]]:
        """Fetch Mtur-categorized municipalities for a Brazilian UF.

        Reads and parses the bundled CSV seed, filters by UF, and maps the
        raw categoria to the canonical Norteia label.

        Args:
            uf: Two-letter state code (e.g. "BA", "RJ", "SP"). Case-insensitive.

        Returns:
            List of dicts with keys: ibge_code, name, categoria, uf.
            Empty list if no municipalities found for the given UF.

        Raises:
            FileNotFoundError: If no Mtur seed CSV exists under data/mtur/.
        """
        rows = _load_csv()
        result: list[dict[str, Any]] = []
        for row in rows:
            row_uf = (row.get("sg_uf") or row.get("uf") or "").strip().upper()
            if row_uf != uf.upper():
                continue
            ibge = (row.get("co_municipio") or row.get("codigo_ibge") or "").strip()
            name = (row.get("no_municipio") or row.get("nome_municipio") or "").strip()
            categoria_raw = (row.get("categoria") or row.get("ds_categoria") or "").strip()
            result.append(
                {
                    "ibge_code": ibge,
                    "name": name,
                    "categoria": _map_categoria(categoria_raw),
                    "uf": uf.upper(),
                }
            )
        return result


# Structural type check: MturClient must satisfy MturClientProtocol at static analysis time
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import MturClientProtocol

    _client: MturClientProtocol = MturClient()  # noqa: F841

"""TripAdvisor stateName → IBGE 2-letter UF mapping (TA-ftx).

The d3d4987463b78a39 query returns stateName in two observed forms:
'State of {X}' (English, e.g. 'State of Parana') for most states, or a bare
English name with no prefix (e.g. 'Federal District' for DF — live-confirmed
2026-06-30). Pure dict, no runtime dependency.

ToS/LGPD: aggregate geo only (cityName/stateName/geoIds), no PII.
"""
from __future__ import annotations

import unicodedata

# Lowercase ASCII-folded keys → 2-letter IBGE UF codes.
# DF has TWO keys to handle both the Portuguese form ('distrito federal')
# and the English bare form ('federal district') observed in live TA data.
# All other states arrive as 'State of {Portuguese name}' — after stripping
# the prefix and NFKD-folding, the remaining text is the Portuguese ASCII name.
_TA_STATE_CANONICAL: dict[str, str] = {
    "acre": "AC",
    "alagoas": "AL",
    "amapa": "AP",
    "amazonas": "AM",
    "bahia": "BA",
    "ceara": "CE",
    "distrito federal": "DF",
    "federal district": "DF",
    "espirito santo": "ES",
    "goias": "GO",
    "maranhao": "MA",
    "mato grosso": "MT",
    "mato grosso do sul": "MS",
    "minas gerais": "MG",
    "para": "PA",
    "paraiba": "PB",
    "parana": "PR",
    "pernambuco": "PE",
    "piaui": "PI",
    "rio de janeiro": "RJ",
    "rio grande do norte": "RN",
    "rio grande do sul": "RS",
    "rondonia": "RO",
    "roraima": "RR",
    "santa catarina": "SC",
    "sao paulo": "SP",
    "sergipe": "SE",
    "tocantins": "TO",
}


def state_name_to_uf(state_name: str) -> str | None:
    """Map a TripAdvisor stateName string to a 2-letter Brazilian UF code.

    Handles two observed TA forms from the d3d4987463b78a39 response:
      - 'State of {X}' (most states) — strips the prefix before mapping.
      - Bare English name without prefix (e.g. 'Federal District' for DF —
        live-confirmed 2026-06-30; strip is CONDITIONAL, only when prefix
        is present so 'Federal District' is NOT mangled).

    Applies NFKD Unicode normalization + ASCII encoding for accent folding
    (e.g. 'São Paulo' → 'Sao Paulo') before the dict lookup.

    Args:
        state_name: Raw stateName value from d3d4987463b78a39 locationData.

    Returns:
        2-letter UF code (e.g. 'PR', 'DF') or None when not found.

    ToS/LGPD: aggregate geo only — no PII in this mapping.
    """
    if not isinstance(state_name, str) or not state_name.strip():
        return None
    text = state_name.strip()
    # Conditional strip: only remove "State of " when the prefix is present.
    # The 9-character prefix is lowercase-checked to be case-insensitive safe.
    if text.lower().startswith("state of "):
        text = text[9:]  # remove "State of " (9 chars)
    # NFKD normalize → ASCII-encode (drops accents) → decode → lowercase
    normalized = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )
    return _TA_STATE_CANONICAL.get(normalized)

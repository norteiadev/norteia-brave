"""Normalization helpers for Rio pipeline.

These functions normalize raw payload fields into a consistent form
before scoring and dedup comparison.
"""

import re


def normalize_name(name: str) -> str:
    """Normalize an entity name: strip whitespace, titlecase, remove double spaces.

    Args:
        name: Raw entity name.

    Returns:
        Normalized name (titlecase, no double spaces, stripped).
    """
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    return name.title()


def normalize_coordinates(
    lat: float | None, lon: float | None
) -> tuple[float | None, float | None]:
    """Normalize GPS coordinates to 6 decimal places.

    Args:
        lat: Latitude (optional).
        lon: Longitude (optional).

    Returns:
        Tuple of (lat, lon) rounded to 6 decimal places. None preserved.
    """
    if lat is not None:
        lat = round(lat, 6)
    if lon is not None:
        lon = round(lon, 6)
    return lat, lon


def normalize_address(address: str | None) -> str | None:
    """Normalize a street address: strip whitespace, titlecase.

    Args:
        address: Raw address string (optional).

    Returns:
        Normalized address, or None if input is None.
    """
    if address is None:
        return None
    address = address.strip()
    if not address:
        return None
    return address.title()

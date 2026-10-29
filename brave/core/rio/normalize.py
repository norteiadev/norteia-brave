"""Normalization helpers for Rio pipeline.

These functions normalize raw payload fields into a consistent form
before scoring and dedup comparison.
"""

import re
import unicodedata

# Lowercase PT-BR connectors inside a name ("Praia do Forte", "São João del-Rei").
_CONNECTORS = frozenset(
    {"da", "de", "do", "das", "dos", "e", "em", "na", "no", "nas", "nos", "del"}
)
# I..XXXIX — the numerals names actually carry (Dom Pedro II, Rua XV de Novembro). No
# L/C/D/M: "Mix"/"Di"/"Civil" would read as numerals.
_ROMAN = re.compile(r"X{0,3}(IX|IV|V?I{0,3})", re.IGNORECASE)
_TOKEN = re.compile(r"[\s\-/]+")


def _case_word(word: str, first: bool) -> str:
    lower = word.lower()
    if (
        not first
        and lower.startswith("d'")
        and len(word) > 2
        and word in (lower, word.upper())  # a mixed "D'Oeste" (IBGE's spelling) is kept
    ):
        return "d'" + _capitalize(lower[2:])  # elision: Pedra d'Água, Arraial d'Ajuda
    letters = [c for c in word if c.isalpha()]
    if any(c.isupper() for c in letters[1:]):
        return word  # source casing is deliberate: MASP, SESC, II, McDonald's
    if not first and lower in _CONNECTORS:
        return lower
    if len(word) > 1 and _ROMAN.fullmatch(word):
        return word.upper()  # also repairs str.title()'s "Ii"/"Xv"
    return _capitalize(word)


def _capitalize(word: str) -> str:
    """Uppercase the first letter only — never lowercases the rest (unlike str.title).

    A word that starts with a digit is left alone ("2º", "3d", an id).
    """
    for i, c in enumerate(word):
        if c.isdigit():
            return word
        if c.isalpha():
            return word[:i] + c.upper() + word[i + 1 :]
    return word


def normalize_name(name: str) -> str:
    """Normalize an entity name: strip, collapse spaces, PT-BR-aware capitalization.

    An all-caps name with a word longer than 4 letters is lowercased first. Words whose
    letters past the first carry uppercase are kept verbatim (acronyms, roman numerals,
    possessives, a source's "D'Oeste"); connectors (da/de/do/das/dos/e/em/na/no/nas/nos/del) go lowercase
    except as the first word; any other word gets its first letter uppercased. Idempotent —
    places_enrichment keys its consensus cache on normalize_name of an already-normalized
    name. Never feeds canonical_key/source_ref (those come from source ids).

    Args:
        name: Raw entity name.

    Returns:
        Normalized name.
    """
    name = re.sub(r"\s+", " ", name.strip())
    # A shouted name ("IGREJA MATRIZ DE SANTANA") is not a row of acronyms: lowercase it
    # first, then the PT-BR rules apply. Only short all-caps names ("MASP") stay verbatim.
    if name.upper() == name and any(
        sum(c.isalpha() for c in w) > 4 for w in _TOKEN.split(name)
    ):
        name = name.lower()
    parts = _TOKEN.split(name)
    seps = _TOKEN.findall(name)
    out: list[str] = []
    for i, word in enumerate(parts):
        out.append(_case_word(word, first=(i == 0)))
        if i < len(seps):
            out.append(seps[i])
    return "".join(out)


# Connectors a second TA card of the same place drops or swaps ("Praia Itaúnas").
_NAME_KEY_STOPWORDS = frozenset({"de", "do", "da", "dos", "das", "e"})


def name_key(name: str | None) -> str:
    """Comparison key of a name: accent-folded, lowercase, no connectors/punctuation/spaces.

    "Praia do Francês" and "Praia Frances" → "praiafrances". Only for matching two records
    of one place inside a município — never stored, never a source_ref.
    """
    folded = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return "".join(w for w in re.findall(r"[a-z0-9]+", folded) if w not in _NAME_KEY_STOPWORDS)


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
    """Normalize a street address: strip and collapse whitespace (casing untouched).

    No title-casing: sources already case addresses, and str.title() broke them
    ("Rua Xv De Novembro", "R. Da Praia, S/N").

    Args:
        address: Raw address string (optional).

    Returns:
        Normalized address, or None if input is None or blank.
    """
    if address is None:
        return None
    return re.sub(r"\s+", " ", address.strip()) or None

"""Taxonomy labeling for Rio pipeline (Phase 1 stub).

Adds Norteia taxonomy labels to a normalized record dict.
Real NLP-based labeling is deferred to Phase 2 when lane data arrives.
"""


def label_entity(entity_type: str, normalized: dict) -> dict:
    """Add Norteia taxonomy labels to a normalized entity record.

    Phase 1 stub: adds a "labels" key with entity_type and taxonomy_version.
    Real NLP labeling (deferred to Phase 2) will classify entities into
    the Norteia taxonomy (e.g., "praia", "destino_turístico", "cachoeira").

    Args:
        entity_type: "destination" or "attraction".
        normalized:  Normalized record dict (modified in-place and returned).

    Returns:
        The normalized dict with a "labels" key added.
    """
    result = dict(normalized)
    result["labels"] = {
        "entity_type": entity_type,
        "taxonomy_version": "v1.0",
        # Phase 2: add "category", "tags", "primary_type" from NLP classification
    }
    return result

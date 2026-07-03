"""ManualRepository — data-access + payload shaping for the ``manual`` domain.

Builds the human-authoritative Nascente payload (``origem=100`` /
``validação humana=100``), derives a stable ``source_ref`` so re-saving the same
logical record supersedes rather than duplicates (append-only store, D-03), and
reads back the active row. Import posture (D-18): kernel only.
"""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from brave.core.models import NascenteRecord

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Fixed §7.6 inputs for a hand-entered record: authoritative + human-validated.
MANUAL_ORIGEM_VALUE = 100.0
MANUAL_VALIDACAO_HUMANA_VALUE = 100.0

SOURCE = "manual"


def _slugify(value: str) -> str:
    """ASCII-fold + lowercase + hyphenate — a stable ref token for a manual name."""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or "sem-nome"


class ManualRepository:
    """Payload shaping + read access for operator-authored records."""

    def make_source_ref(
        self, entity_type: str, uf: str, municipio_id: str | None, name: str
    ) -> str:
        """Deterministic ``manual:<entity>:<uf>:<ibge|slug>`` reference.

        Uses the IBGE code when available (stable identity), else a slug of the
        name — so update() re-saves under the same ref and supersedes the old row.
        """
        tail = municipio_id.strip() if municipio_id and municipio_id.strip() else _slugify(name)
        return f"{SOURCE}:{entity_type}:{uf.upper()}:{tail}"

    def build_payload(
        self,
        *,
        entity_type: str,
        uf: str,
        name: str,
        municipio_id: str | None = None,
        canonical: dict[str, Any] | None = None,
        completude_value: float = 100.0,
        corroboracao_value: float = 0.0,
        atualidade_value: float = 100.0,
    ) -> dict[str, Any]:
        """Assemble the Nascente payload for a manual record.

        ``origem_value`` and ``validacao_humana_value`` are pinned to 100 — a
        manual record is human-authoritative and human-validated by construction.
        """
        uf_up = uf.upper()
        canonical_dict: dict[str, Any] = dict(canonical or {})
        canonical_dict.setdefault("name", name)
        canonical_dict.setdefault("uf", uf_up)
        if municipio_id:
            canonical_dict.setdefault("ibge_code", municipio_id)
            canonical_dict.setdefault("municipio", canonical_dict.get("municipio", name))

        payload: dict[str, Any] = {
            "name": name,
            "uf": uf_up,
            "entity_type": entity_type,
            # §7.6 criterion *_value fields (read by the Rio normalizer/route)
            "origem_value": MANUAL_ORIGEM_VALUE,
            "completude_value": completude_value,
            "corroboracao_value": corroboracao_value,
            "atualidade_value": atualidade_value,
            "validacao_humana_value": MANUAL_VALIDACAO_HUMANA_VALUE,
            "canonical": canonical_dict,
            "source_note": "manual entry (operator-authored)",
        }
        if municipio_id:
            payload["municipio_id"] = municipio_id
        return payload

    def get_active(
        self, session: Session, source_ref: str
    ) -> NascenteRecord | None:
        """Return the active (non-superseded) manual Nascente row for a ref, or None."""
        return session.scalar(
            select(NascenteRecord).where(
                NascenteRecord.source == SOURCE,
                NascenteRecord.source_ref == source_ref,
                NascenteRecord.superseded_by_id.is_(None),
            )
        )

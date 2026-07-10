"""Seed the static reference tables — idempotent (municipios/distritos/uf_geoids).

Bulk-loads the three DB-backed reference tables that replace the mtur destino-seed
lane. Row data lives HERE (not in migration 0011): the collection lanes read their
parent destinos / geo resolution from these tables instead of running static CSV
data through the whole Brave pipeline.

Sources:
  - municipios  — data/ibge/ibge_municipios.csv (5571 rows), with the mtur turistic
                  categoria/regiao_turistica folded in from
                  data/mtur/municipios_mtur_2025.csv keyed by co_municipio.
  - distritos   — data/ibge/ibge_distritos.csv (10751 rows).
  - uf_geoids   — data/tripadvisor/uf_geoids.json (27 rows).

Idempotent: per-table count-gate — a table is bulk-loaded only when it is empty;
otherwise it is left untouched. Safe to re-run.

reset-brave-db interaction: the reset PRESERVES these tables (they are static
carga-inicial, not pipeline data), so a normal reset never needs this re-run. It is
run at migrate time (docker-compose migrate service) after `alembic upgrade head`.

Usage (env must be loaded so BRAVE_DB_URL is set):
    set -a; source .env; set +a
    .venv/bin/python -m scripts.seed_reference_data
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from brave.core.models import Distrito, Municipio, UfGeoid

# Repo root — scripts/ lives at <repo>/scripts/seed_reference_data.py
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
IBGE_MUNICIPIOS_CSV = DATA_DIR / "ibge" / "ibge_municipios.csv"
IBGE_DISTRITOS_CSV = DATA_DIR / "ibge" / "ibge_distritos.csv"
MTUR_2025_CSV = DATA_DIR / "mtur" / "municipios_mtur_2025.csv"
UF_GEOIDS_JSON = DATA_DIR / "tripadvisor" / "uf_geoids.json"


def _map_categoria(raw: str) -> str:
    """Map a raw Mtur categoria value to the canonical Norteia label.

    Moved verbatim from brave/clients/mtur.py — the only bit of MturClient worth
    keeping after the mtur destino-seed lane is retired. Handles both old
    nomenclature (A/B/C/D/E, published pre-2025) and new nomenclature ("Municípios
    turísticos", "com oferta turística complementar", "de apoio ao turismo",
    published 2025+).

    Args:
        raw: Raw categoria string from the Mtur CSV.

    Returns:
        One of "Oferta Principal", "Complementar", or "Apoio".
        Falls back to "Apoio" for any unrecognized value.
    """
    raw_clean = raw.strip().upper()
    # Old nomenclature: A and B → Oferta Principal
    # New nomenclature: "Município turístico" (singular — the live 2025 portal
    # string) / "Municípios turísticos" (plural) → Oferta Principal. Match the
    # singular stem "TURÍSTICO" so both forms hit; "TURÍSTICA" (feminine, in the
    # Complementar label "oferta turística complementar") does NOT contain it.
    if raw_clean in ("A", "B") or "TURÍSTICO" in raw_clean or "TURISTICO" in raw_clean:
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


def _is_empty(session: Session, model: Any) -> bool:
    """Per-table count-gate: True when the table has zero rows (safe to bulk-load)."""
    return session.scalar(select(func.count()).select_from(model)) == 0


def _load_mtur_categorias() -> dict[str, tuple[str, str | None]]:
    """Build {co_municipio: (categoria, regiao_turistica)} from the 2025 mtur CSV.

    CSV header: co_municipio,no_municipio,sg_uf,categoria,no_regiao_turistica.
    Keyed by co_municipio (the IBGE code) so it folds onto matching municipios rows.
    Uses utf-8-sig to strip a possible BOM (mirrors the old MturClient reader).
    """
    fold: dict[str, tuple[str, str | None]] = {}
    with MTUR_2025_CSV.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ibge = (row.get("co_municipio") or "").strip()
            if not ibge:
                continue
            categoria = _map_categoria(row.get("categoria") or "")
            regiao = (row.get("no_regiao_turistica") or "").strip() or None
            fold[ibge] = (categoria, regiao)
    return fold


def _municipios_mappings() -> list[dict[str, Any]]:
    """Parse ibge_municipios.csv and fold in the mtur categoria/regiao_turistica.

    CSV header: ibge_code,nome,uf,lat,lng (parse shape reused from
    brave/domains/tripadvisor/ibge.py::load_ibge_csv).
    """
    fold = _load_mtur_categorias()
    mappings: list[dict[str, Any]] = []
    with IBGE_MUNICIPIOS_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ibge_code = row["ibge_code"].strip()
            categoria, regiao = fold.get(ibge_code, (None, None))
            mappings.append(
                {
                    "ibge_code": ibge_code,
                    "nome": row["nome"].strip(),
                    "uf": row["uf"].strip(),
                    "lat": float(row["lat"]),
                    "lng": float(row["lng"]),
                    "categoria": categoria,
                    "regiao_turistica": regiao,
                }
            )
    return mappings


def _distritos_mappings() -> list[dict[str, Any]]:
    """Parse ibge_distritos.csv (parse shape reused from ibge_distritos.py).

    CSV header: distrito_code,nome,ibge_code,municipio_nome,uf.
    """
    mappings: list[dict[str, Any]] = []
    with IBGE_DISTRITOS_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            mappings.append(
                {
                    "distrito_code": row["distrito_code"].strip(),
                    "nome": row["nome"].strip(),
                    "ibge_code": row["ibge_code"].strip(),
                    "municipio_nome": row["municipio_nome"].strip(),
                    "uf": row["uf"].strip(),
                }
            )
    return mappings


def _uf_geoids_mappings() -> list[dict[str, Any]]:
    """Parse uf_geoids.json → 27 rows (parse shape reused from geo.py::load_uf_geoids)."""
    data: dict[str, Any] = json.loads(UF_GEOIDS_JSON.read_text(encoding="utf-8"))
    return [{"uf": uf, "geo_id": int(geo_id)} for uf, geo_id in data.items()]


def seed_reference_data(session: Session) -> dict[str, int]:
    """Bulk-load the three reference tables IF EMPTY (per-table count-gate).

    Returns a per-table dict of the number of rows inserted (0 = table already
    populated, left untouched). Caller commits.
    """
    inserted: dict[str, int] = {"municipios": 0, "distritos": 0, "uf_geoids": 0}

    if _is_empty(session, Municipio):
        rows = _municipios_mappings()
        session.bulk_insert_mappings(Municipio, rows)
        inserted["municipios"] = len(rows)

    if _is_empty(session, Distrito):
        rows = _distritos_mappings()
        session.bulk_insert_mappings(Distrito, rows)
        inserted["distritos"] = len(rows)

    if _is_empty(session, UfGeoid):
        rows = _uf_geoids_mappings()
        session.bulk_insert_mappings(UfGeoid, rows)
        inserted["uf_geoids"] = len(rows)

    return inserted


def main() -> int:
    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        print("ERROR: BRAVE_DB_URL not set. Run: set -a; source .env; set +a")
        return 1

    engine = create_engine(db_url, echo=False)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        inserted = seed_reference_data(session)
        session.commit()

    print(
        "reference tables seeded (empty-only, idempotent): "
        f"municipios={inserted['municipios']}, "
        f"distritos={inserted['distritos']}, "
        f"uf_geoids={inserted['uf_geoids']} row(s) inserted."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

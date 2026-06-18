# Phase 7: Real Places Hardening + Targeted Atrativos Discovery + Mtur Refresh - Pattern Map

**Mapped:** 2026-06-17
**Files analyzed:** 7 (3 modify, 1 new service, 1 new script, 1 new data file, 1 new test file)
**Analogs found:** 7 / 7

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `brave/clients/places.py` (MODIFY) | client | request-response | itself (current `place_details` + `RealLLMClient` pattern from `brave/clients/llm.py`) | exact — self-modification |
| `brave/lanes/atrativos/discovery_agent.py` (MODIFY) | service/lane | request-response | itself (current `produce()`) | exact — self-modification |
| `brave/core/dlq/service.py` (NEW) | service | CRUD | `brave/api/routers/dlq.py` lines 127-193 (`validate_dlq_record` inline) + `brave/core/mar/service.py` | exact extract |
| `scripts/loadtest_destinos_atrativos.py` (NEW) | script | CRUD/batch | `scripts/ingest_destinos.py` | role-match |
| `data/mtur/municipios_mtur_2025.csv` (NEW) | data | file-I/O | `data/mtur/municipios_mtur_2024.csv` | exact schema |
| `tests/unit/clients/test_real_places_client.py` (NEW) | test | request-response | `tests/unit/clients/test_real_llm_client.py` | role-match |
| `tests/unit/lanes/test_discovery_agent.py` (MODIFY) | test | request-response | itself (current tests) | exact — self-modification |

---

## Pattern Assignments

### `brave/clients/places.py` (client, request-response) — MODIFY

**Analogs:**
- Self (existing `place_details` with `metadata` kwarg already present — lines 183-247)
- `brave/clients/llm.py` for the real-client guard + retry pattern

**Imports pattern** (lines 1-29, existing — no change needed):
```python
from __future__ import annotations
from typing import Any
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
```

**Real-client guard pattern** (lines 88-105, existing `__init__`):
```python
def __init__(self, api_key: str) -> None:
    from brave.config.settings import AppConfig
    if not AppConfig().run_real_externals:
        raise RuntimeError(
            "RealPlacesClient: run_real_externals=False — "
            "use FakePlacesClient in default test suite. "
            "Set RUN_REAL_EXTERNALS=true to enable real API calls."
        )
    if not api_key:
        raise RuntimeError(
            "RealPlacesClient: api_key is empty — "
            "set BRAVE_PLACES_API_KEY environment variable."
        )
    self._api_key = api_key
    self._client = None  # Lazy init — avoid import-time SDK setup
```

**D-01 fix — field mask constants (ADD at module top, before class):**
```python
# Correct field masks — different prefixes for search_text vs get_place (Places API New)
# search_text response = SearchTextResponse.places[] → requires "places." prefix
# get_place response = bare Place → NO "places." prefix
_TEXT_SEARCH_FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.types,"
    "places.location,"
    "places.addressComponents"
)

_GET_PLACE_FIELD_MASK = (
    "id,"
    "displayName,"
    "formattedAddress,"
    "types,"
    "location,"
    "addressComponents,"
    "businessStatus,"
    "regularOpeningHours,"
    "reviews,"
    "internationalPhoneNumber,"
    "websiteUri"
)
```

**D-01 fix — text_search: add metadata kwarg** (mirrors existing `place_details` metadata call at line 217):

The existing `text_search` call at line 155 is:
```python
response = await client.search_text(request)
```
Replace with:
```python
response = await client.search_text(
    request,
    metadata=[("x-goog-fieldmask", _TEXT_SEARCH_FIELD_MASK)],
)
```

**D-01 fix — place_details: fix wrong `places.` prefix** (lines 201-210):

The existing `field_mask` string at lines 201-209 uses `places.id, places.displayName, ...` — WRONG for `get_place`.
Replace the entire `field_mask` variable with the `_GET_PLACE_FIELD_MASK` constant.
The existing `metadata=[("x-goog-fieldmask", field_mask)]` call at line 217 becomes:
```python
place = await client.get_place(
    request,
    metadata=[("x-goog-fieldmask", _GET_PLACE_FIELD_MASK)],
)
```
Also change line 232 `place.current_opening_hours` → `place.regular_opening_hours`.

**D-02 fix — addressComponents parsing helper (ADD as module-level function):**
```python
def _extract_municipio_from_components(address_components) -> tuple[str, str]:
    """Return (municipio_nome, uf_short) from Places API addressComponents.

    Types:
      "administrative_area_level_2" → município name (long_text)
      "administrative_area_level_1" → state (short_text = "BA")
    Returns ("", "") if components are missing/empty.
    """
    municipio_nome = ""
    uf_short = ""
    for comp in address_components:
        types = list(comp.types)
        if "administrative_area_level_2" in types:
            municipio_nome = comp.long_text
        elif "administrative_area_level_1" in types:
            uf_short = comp.short_text  # "BA", "RJ", etc.
    return municipio_nome, uf_short
```

**D-02 fix — name→IBGE lookup (ADD as module-level helper):**
```python
import unicodedata

def _normalize_name(name: str) -> str:
    nfd = unicodedata.normalize("NFD", name.lower().strip())
    return nfd.encode("ascii", "ignore").decode()

def build_mtur_ibge_lookup(mtur_rows: list[dict]) -> dict[tuple[str, str], str]:
    """Build {(normalized_name, UF): ibge_code} from all Mtur rows."""
    lookup: dict[tuple[str, str], str] = {}
    for row in mtur_rows:
        name = row.get("name", "")
        uf = row.get("uf", "").upper()
        ibge = row.get("ibge_code", "")
        if name and uf and ibge:
            lookup[(_normalize_name(name), uf)] = ibge
    return lookup
```

**D-02 fix — text_search result builder: add municipio fields** (lines 160-175):

After building each `result` dict, append `municipio_nome` and `municipio_ibge`:
```python
municipio_nome, _uf_short = _extract_municipio_from_components(
    place.address_components or []
)
# Resolve IBGE from in-process lookup (built once from Mtur data)
ibge_key = (_normalize_name(municipio_nome), uf.upper())
municipio_ibge = self._ibge_lookup.get(ibge_key, "")
result["municipio_nome"] = municipio_nome
result["municipio_ibge"] = municipio_ibge
```
Note: `self._ibge_lookup` requires a new `__init__` parameter (or lazy load from `MturClient`).

**Retry + error handling pattern** (lines 124-128, copy for both methods — already in place):
```python
@retry(
    retry=retry_if_exception(_is_retryable),  # WR-01: transient only
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
```

---

### `brave/lanes/atrativos/discovery_agent.py` (service/lane, request-response) — MODIFY

**Analog:** Self (existing `produce()` at lines 194-365)

**D-02 fix — `_resolve_parent_destino` empty-ibge guard** (INSERT at top of function, before line 130):
```python
def _resolve_parent_destino(
    session: Session,
    uf: str,
    municipio_ibge: str,
) -> MarRecord | None:
    # D-02 guard: empty ibge would match ANY source_ref via contains("") — never query
    if not municipio_ibge or not municipio_ibge.strip():
        return None
    ...
    # existing primary lookup unchanged (line 130-138)
    result = session.scalar(
        select(MarRecord).where(
            and_(
                MarRecord.entity_type == "destination",
                MarRecord.superseded_by_id.is_(None),
                MarRecord.source_ref.contains(municipio_ibge),
            )
        )
    )
    ...
```

**D-03 — `produce_for_destino` targeted method (ADD to DiscoveryAgent class):**

Copy the overall loop structure from `produce()` (lines 194-365). Key differences:
- Accepts `parent_mar: MarRecord` directly (skip `_resolve_parent_destino` entirely)
- Queries are municipality-specific: `f"pontos turísticos em {municipio_nome} {uf}"` and `f"o que fazer em {municipio_nome} {uf}"`
- Short-circuits when `created >= target_count`
- Returns `int` (count of Rio records created/existing for this parent)

```python
async def produce_for_destino(
    self,
    parent_mar: "MarRecord",
    target_count: int = 10,
) -> int:
    """Run targeted Places discovery for a single Mar destino municipality.

    Bypasses _resolve_parent_destino — parent is known.
    Returns count of Rio records created for this parent in this call.
    """
    canonical = parent_mar.canonical or {}
    municipio_nome = canonical.get("municipio") or canonical.get("name", "")
    uf = canonical.get("uf", "")
    municipio_ibge = canonical.get("ibge_code", "")

    if not municipio_nome or not uf:
        logger.warning(
            "produce_for_destino_missing_fields",
            parent_mar_id=str(parent_mar.id),
        )
        return 0

    search_queries = [
        f"pontos turísticos em {municipio_nome} {uf}",
        f"o que fazer em {municipio_nome} {uf}",
    ]

    created = 0
    seen_place_ids: set[str] = set()

    for query in search_queries:
        if created >= target_count:
            break
        try:
            places_results = await self._places_client.text_search(query=query, uf=uf)
        except Exception as exc:
            quarantine_poison(
                session=self._session,
                nascente_id=None,
                task_name="brave.discover_atrativo",
                error=f"places_search_failed: {exc}",
                payload={"uf": uf, "query": query},
            )
            continue

        for place in places_results:
            if created >= target_count:
                break
            place_id: str = place.get("place_id", "")
            if not place_id or place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)

            # ... same LLM extraction + store_raw + process_nascente_record + advance_sub_state
            # pattern as produce() lines 258-364, but with parent_mar injected directly
            # (parent_mar_id = str(parent_mar.id)) — no _resolve_parent_destino call
            ...
            created += 1

    return created
```

**Core payload pattern** (copy from `produce()` lines 289-314 — same structure, same keys):
```python
payload: dict[str, Any] = {
    "origem_value": 60.0,
    "completude_value": completude,
    "corroboracao_value": 0.0,
    "atualidade_value": 0.0,
    "validacao_humana_value": 0.0,
    "place_id_cache": place_id,
    "canonical": {
        "place_id": place_id,
        "nome": result.nome,
        "tipo": result.tipo,
        "posicionamento": result.posicionamento,
        "municipio_nome": result.municipio_nome,
        "municipio_ibge": result.municipio_ibge,
        "uf": result.uf,
    },
    "parent_mar_id": str(parent_mar.id),
    "municipio_id": municipio_ibge,
    "name": result.nome,
    "entity_type": "attraction",
    "source_note": "LLM-extracted, pending contact/signal/validation",
}
```

---

### `brave/core/dlq/service.py` (service, CRUD) — NEW

**Analog:** `brave/api/routers/dlq.py` lines 127-193 (`validate_dlq_record`) and lines 218-265 (inline loop in `validate_batch`) + `brave/core/mar/service.py`

This is a pure extraction of inlined logic. Copy the exact 4-step pattern from `dlq.py`.

**Imports pattern** (model from `brave/core/mar/service.py` lines 1-20):
```python
"""DLQ service — validate_and_promote_rio helper (D-06, Phase 7).

Extracted from brave/api/routers/dlq.py validate_dlq_record inline logic.
Called by both the DLQ router and the loadtest harness.
"""
from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from brave.config.settings import ScoreConfig
from brave.core.models import MarRecord, RioRecord
from brave.core.rio.routing import reprocess_record
from brave.core.mar.service import promote_to_mar
```

**Core pattern** (extracted verbatim from `dlq.py` lines 155-182):
```python
def validate_and_promote_rio(
    session: Session,
    rio: RioRecord,
    config: ScoreConfig | None = None,
) -> MarRecord | None:
    """Set validacao_humana=100 → re-score → promote_to_mar if routing=='mar'.

    Extracted from dlq.py validate_dlq_record (Pitfalls 3 and 4 apply).

    Returns:
        MarRecord if promoted to Mar, None if routing != 'mar' after re-score.
    """
    config = config or ScoreConfig()

    # Step 1: CRITICAL — reassign + flag_modified (Pitfall 3: SQLAlchemy does
    # not auto-track in-place JSON column mutations).
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    session.flush()

    # Step 2: re-score via reprocess_record — NOT process_nascente_record
    # (Pitfall 4: process_nascente_record returns early if canonical_key exists).
    reprocess_record(session, rio.id, config)
    session.refresh(rio)

    # Step 3: promote to Mar only when routing == 'mar'
    if rio.routing == "mar":
        return promote_to_mar(session, rio)
    return None
```

**No audit write here** — audit is the caller's responsibility (the router writes its own audit row; the harness may skip it or write its own). This matches `brave/core/mar/service.py`'s pattern of not writing audit rows inside service functions.

---

### `scripts/loadtest_destinos_atrativos.py` (script, CRUD/batch) — NEW

**Analog:** `scripts/ingest_destinos.py` (lines 1-67) — copy structure exactly

**Imports pattern** (mirrors `ingest_destinos.py` lines 17-31):
```python
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from brave.clients.mtur import MturClient
from brave.clients.places import RealPlacesClient
from brave.clients.llm import RealLLMClient
from brave.config.settings import AppConfig, LLMConfig, ScoreConfig
from brave.core.models import MarRecord, RioRecord
from brave.core.dlq.service import validate_and_promote_rio
from brave.lanes.destinos.mtur import MturSeedIngest
from brave.lanes.atrativos.discovery_agent import DiscoveryAgent
```

**Main entrypoint pattern** (mirrors `ingest_destinos.py` lines 33-67):
```python
def main() -> None:
    ufs = [a.upper() for a in sys.argv[1:]] or ["BA"]
    target_count = int(os.environ.get("ATRATIVO_TARGET_COUNT", "10"))

    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        print("ERROR: BRAVE_DB_URL not set. Run: set -a; source .env; set +a")
        sys.exit(1)

    engine = create_engine(db_url, echo=False)
    SessionFactory = sessionmaker(bind=engine)
    config = ScoreConfig()
    app_config = AppConfig()

    with SessionFactory() as session:
        # Step 1: ingest destinos (mirrors ingest_destinos.py exactly)
        client = MturClient()
        for uf in ufs:
            asyncio.run(MturSeedIngest(client, session, config).produce(uf))
            session.commit()
            print(f"[{uf}] destinos ingested")

        # Step 2: promote DLQ→Mar for up to 10 destinos per UF
        # uses validate_and_promote_rio from brave/core/dlq/service.py

        # Step 3: run produce_for_destino for each promoted MarRecord

        # Step 4: routing summary (mirrors ingest_destinos.py lines 54-63)
        rows = session.execute(
            select(RioRecord.uf, RioRecord.routing, func.count(RioRecord.id))
            .where(RioRecord.entity_type == "destination", RioRecord.uf.in_(ufs))
            .group_by(RioRecord.uf, RioRecord.routing)
            .order_by(RioRecord.uf, RioRecord.routing)
        ).all()

    print("\nrouting summary:")
    for uf, routing, n in rows:
        print(f"  {uf}  {routing:<12} {n}")


if __name__ == "__main__":
    main()
```

**Warning pattern for non-clean DB** (print header before run):
```python
existing_mar = session.scalar(
    select(func.count(MarRecord.id)).where(
        MarRecord.entity_type == "destination",
        MarRecord.superseded_by_id.is_(None),
    )
)
if existing_mar and existing_mar > 0:
    print(
        f"WARNING: {existing_mar} active Mar destinos already exist. "
        "Run TRUNCATE nascente_records, rio_records, mar_records CASCADE "
        "for a clean baseline before re-running."
    )
```

---

### `data/mtur/municipios_mtur_2025.csv` (data file) — NEW

**Analog:** `data/mtur/municipios_mtur_2024.csv` — exact schema

**Schema (copy header verbatim):**
```
co_municipio,no_municipio,sg_uf,categoria,no_regiao_turistica
```

**Column mapping rules** (from `brave/clients/mtur.py` `fetch_municipalities` lines 115-118):
- `co_municipio` — 7-digit IBGE code (also accepted as `codigo_ibge`)
- `no_municipio` — municipality name (also accepted as `nome_municipio`)
- `sg_uf` — 2-letter state code (also accepted as `uf`)
- `categoria` — raw string; `_map_categoria` normalizes A/B/E or new nomenclature
- `no_regiao_turistica` — tourism region name (free text)

The loader at `brave/clients/mtur.py` line 43 globs `municipios_mtur_*.csv` sorted descending — the `2025` suffix makes it win automatically.

**NOTE:** This file requires a manual browser download from `https://www.mapa.turismo.gov.br/mapa/init.html` → "Município Categorizado Detalhado Excel" → year 2025, all UFs → convert to CSV. See RESEARCH.md RQ-5 for full steps. If unavailable before the harness run, `municipios_mtur_2024.csv` is a valid fallback for BA.

---

### `tests/unit/clients/test_real_places_client.py` (test, request-response) — NEW

**Analog:** `tests/unit/clients/test_real_llm_client.py` (role-match: same real-client guard + mock-SDK pattern)

**Imports pattern** (mirrors `test_real_llm_client.py` lines 1-18):
```python
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
```

**Real-client guard test** (mirrors `test_real_llm_client.py` lines 58-73):
```python
def test_guard_raises_when_run_real_externals_false(monkeypatch):
    """RealPlacesClient raises RuntimeError when RUN_REAL_EXTERNALS is absent/false."""
    monkeypatch.delenv("RUN_REAL_EXTERNALS", raising=False)

    from brave.clients.places import RealPlacesClient

    with pytest.raises(RuntimeError, match="run_real_externals=False"):
        RealPlacesClient(api_key="test-key")
```

**Field mask assertion pattern for text_search** (from RESEARCH.md RQ-6):
```python
@pytest.mark.asyncio
async def test_text_search_sends_field_mask_with_places_prefix(monkeypatch):
    """text_search metadata must include x-goog-fieldmask with 'places.' prefix."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    mock_client = MagicMock()
    mock_client.search_text = AsyncMock(return_value=_make_search_response())

    with patch(
        "google.maps.places_v1.PlacesAsyncClient",
        return_value=mock_client,
    ):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(api_key="test-key")
        client._client = mock_client  # inject mock into lazy init

        await client.text_search(query="praias em Porto Seguro", uf="BA")

    call_kwargs = mock_client.search_text.call_args.kwargs
    metadata = dict(call_kwargs.get("metadata", []))
    assert "x-goog-fieldmask" in metadata
    mask = metadata["x-goog-fieldmask"]
    assert mask.startswith("places."), f"text_search mask must start with 'places.', got: {mask!r}"
    assert "addressComponents" in mask
```

**Field mask assertion pattern for place_details** (must NOT have `places.` prefix):
```python
@pytest.mark.asyncio
async def test_place_details_sends_field_mask_without_places_prefix(monkeypatch):
    """place_details metadata must NOT have 'places.' prefix in x-goog-fieldmask."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    mock_client = MagicMock()
    mock_client.get_place = AsyncMock(return_value=_make_place_response())

    with patch(
        "google.maps.places_v1.PlacesAsyncClient",
        return_value=mock_client,
    ):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(api_key="test-key")
        client._client = mock_client

        await client.place_details(place_id="ChIJtest001")

    call_kwargs = mock_client.get_place.call_args.kwargs
    metadata = dict(call_kwargs.get("metadata", []))
    assert "x-goog-fieldmask" in metadata
    mask = metadata["x-goog-fieldmask"]
    assert not mask.startswith("places."), (
        f"place_details mask must NOT start with 'places.', got: {mask!r}"
    )
    assert "regularOpeningHours" in mask
    assert "businessStatus" in mask
```

**addressComponents → municipio_nome mapping assertion:**
```python
@pytest.mark.asyncio
async def test_text_search_maps_address_components_to_municipio_fields(monkeypatch):
    """text_search results include municipio_nome + municipio_ibge from addressComponents."""
    monkeypatch.setenv("RUN_REAL_EXTERNALS", "true")

    mock_client = MagicMock()
    mock_client.search_text = AsyncMock(return_value=_make_search_response_with_components(
        municipio="Porto Seguro", uf_short="BA"
    ))

    with patch("google.maps.places_v1.PlacesAsyncClient", return_value=mock_client):
        from brave.clients.places import RealPlacesClient

        client = RealPlacesClient(api_key="test-key", ibge_lookup={("porto seguro", "BA"): "2927408"})
        client._client = mock_client

        results = await client.text_search(query="praias", uf="BA")

    assert len(results) == 1
    assert results[0]["municipio_nome"] == "Porto Seguro"
    assert results[0]["municipio_ibge"] == "2927408"
```

**Proto-like fixture builder pattern** (from RESEARCH.md RQ-6):
```python
def _make_search_response_with_components(municipio: str = "Porto Seguro", uf_short: str = "BA"):
    """Canned SearchTextResponse with addressComponents for municipio mapping tests."""
    place = MagicMock()
    place.id = "ChIJtest001"
    place.display_name.text = "Praia de Trancoso"
    place.formatted_address = f"Trancoso, {municipio} - {uf_short}, Brasil"
    place.types = ["tourist_attraction"]
    place.location.latitude = -16.57
    place.location.longitude = -39.08

    comp_municipio = MagicMock()
    comp_municipio.long_text = municipio
    comp_municipio.types = ["administrative_area_level_2", "political"]

    comp_state = MagicMock()
    comp_state.long_text = "Bahia"
    comp_state.short_text = uf_short
    comp_state.types = ["administrative_area_level_1", "political"]

    place.address_components = [comp_municipio, comp_state]

    response = MagicMock()
    response.places = [place]
    return response
```

---

### `tests/unit/lanes/test_discovery_agent.py` (test, request-response) — MODIFY

**Analog:** Self (existing tests, lines 32-44) — extend `_make_places_result` and add new tests

**Extend `_make_places_result`** (existing lines 32-44 — municipio fields already present):
```python
def _make_places_result(
    place_id: str = "ChIJtest001",
    municipio_ibge: str = "2919207",   # already present
    municipio_nome: str = "Porto Seguro",  # already present
) -> dict[str, Any]:
    ...
```
No change needed — existing fixture already has the fields. New tests can call it directly.

**New test: empty ibge guard fires quarantine** (mirrors `test_discovery_skips_when_no_parent_destino` lines 81-124):
```python
@pytest.mark.asyncio
async def test_discovery_quarantines_when_empty_municipio_ibge() -> None:
    """_resolve_parent_destino with empty ibge must quarantine (never mislink via contains(''))."""
    # ... places_result with municipio_ibge=""
    # ... session.scalar.assert_not_called() — DB must NOT be queried at all
    # ... quarantine_poison called with error containing "parent_destino_absent"
```

**New test: `produce_for_destino` links to known parent** (mirrors `test_discovery_stores_raw_with_place_id_only` lines 127-205):
```python
@pytest.mark.asyncio
async def test_produce_for_destino_links_to_known_parent() -> None:
    """produce_for_destino stores place with parent_mar_id from the passed MarRecord."""
    # ... mock_mar with canonical = {"municipio": "Porto Seguro", "uf": "BA", "ibge_code": "2927408"}
    # ... FakePlacesClient with key "pontos turísticos em Porto Seguro BA"
    # ... assert store_raw called with payload["parent_mar_id"] == str(mock_mar.id)
    # ... assert session.scalar NOT called (no _resolve_parent_destino call)
```

**FakePlacesClient fixture extension for targeted discovery** (query key matches new format):
```python
fake_places = FakePlacesClient(
    fixture_results={
        "pontos turísticos em Porto Seguro BA": [_make_places_result()],
        "o que fazer em Porto Seguro BA": [_make_places_result(place_id="ChIJtest002")],
    }
)
```

---

## Shared Patterns

### Real-client guard
**Source:** `brave/clients/places.py` lines 88-105 and `brave/clients/llm.py` (same pattern)
**Apply to:** `brave/clients/places.py` (already present, preserve), `tests/unit/clients/test_real_places_client.py` T1 test
```python
if not AppConfig().run_real_externals:
    raise RuntimeError("...: run_real_externals=False — ...")
```

### Pitfall 3 — flag_modified for JSON column mutation
**Source:** `brave/api/routers/dlq.py` lines 155-161
**Apply to:** `brave/core/dlq/service.py`
```python
from sqlalchemy.orm.attributes import flag_modified
normalized = dict(rio.normalized or {})
normalized["validacao_humana_value"] = 100.0
rio.normalized = normalized
flag_modified(rio, "normalized")
session.flush()
```

### Pitfall 4 — reprocess_record not process_nascente_record
**Source:** `brave/api/routers/dlq.py` lines 164-168 (with docstring warning)
**Apply to:** `brave/core/dlq/service.py`
```python
# Re-score: reprocess_record resets routing → in_progress → re-routes via §7.6
# Never call process_nascente_record here — it returns early if canonical_key exists (Pitfall 4)
from brave.core.rio.routing import reprocess_record
reprocess_record(session, rio.id, ScoreConfig())
```

### tenacity retry decorator
**Source:** `brave/clients/places.py` lines 124-128 (already on both methods)
**Apply to:** Both modified methods in `places.py` — preserve existing decorators
```python
@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
```

### structlog structured logging
**Source:** `brave/lanes/atrativos/discovery_agent.py` lines 357-364
**Apply to:** All new method code in `discovery_agent.py`, `dlq/service.py`, harness script
```python
logger.info("atrativo_ingested", source_ref=source_ref, nome=result.nome, uf=uf, ...)
logger.warning("parent_destino_absent", place_id=place_id, municipio_ibge=municipio_ibge, uf=uf)
logger.error("places_text_search_error", query=query, uf=uf, error=str(exc))
```

### AsyncMock + patch for offline tests
**Source:** `tests/unit/lanes/test_discovery_agent.py` lines 113-114
**Apply to:** `tests/unit/clients/test_real_places_client.py`
```python
with patch("brave.lanes.atrativos.discovery_agent.store_raw") as mock_store_raw, \
     patch("brave.lanes.atrativos.discovery_agent.quarantine_poison") as mock_quarantine:
    await agent.produce(uf="BA")
```

### pytest skipif guard for real-external tests
**Source:** `tests/unit/clients/test_real_llm_client.py` (monkeypatch env pattern, lines 64-66)
**Apply to:** `test_real_places_client.py` — any test that needs `RUN_REAL_EXTERNALS`
```python
pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_REAL_EXTERNALS"),
    reason="RUN_REAL_EXTERNALS not set — skipping real Places client smoke test",
)
```

---

## No Analog Found

None. All seven files have direct analogs in the codebase.

---

## Key Pitfalls to Encode in Plans

These are documented in RESEARCH.md and must be referenced in the plan tasks:

1. **Wrong `places.` prefix on `get_place` mask** — use `_GET_PLACE_FIELD_MASK` (no prefix); test asserts mask does NOT start with `places.`
2. **`source_ref.contains("")` matches everything** — guard at top of `_resolve_parent_destino`: `if not municipio_ibge: return None`
3. **`flag_modified` omission** — copy the exact 3-line pattern from `dlq.py` lines 157-161; never mutate `rio.normalized` in-place without it
4. **`process_nascente_record` vs `reprocess_record`** — harness and service always use `reprocess_record` for re-scoring
5. **`publish_time` proto Timestamp** — existing `place_details` calls `.isoformat()` directly (line 226); add a unit test with a mock Timestamp to verify behavior before the harness run

---

## Metadata

**Analog search scope:** `brave/clients/`, `brave/core/`, `brave/lanes/atrativos/`, `brave/api/routers/`, `scripts/`, `tests/unit/clients/`, `tests/unit/lanes/`, `tests/fakes/`, `data/mtur/`
**Files read:** 12 source files + 2 context files
**Pattern extraction date:** 2026-06-17

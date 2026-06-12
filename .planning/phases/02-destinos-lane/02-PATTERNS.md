# Phase 2: Destinos Lane - Pattern Map

**Mapped:** 2026-06-12
**Files analyzed:** 14 new/modified files
**Analogs found:** 14 / 14

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `brave/lanes/destinos/__init__.py` | config | — | `brave/lanes/__init__.py` | exact |
| `brave/lanes/destinos/mtur.py` | producer | batch / CRUD | `brave/core/nascente/service.py` + `brave/lanes/base.py` | role-match |
| `brave/lanes/destinos/notebooklm.py` | producer | batch / CRUD | `brave/lanes/destinos/mtur.py` (sibling) | exact |
| `brave/lanes/destinos/desmembramento.py` | llm-agent | request-response | `brave/tasks/pipeline.py` (quarantine pattern) + `brave/clients/base.py` (LLMClientProtocol) | role-match |
| `brave/lanes/destinos/schemas.py` | data-schema | — | `brave/core/score/schemas.py` | role-match |
| `brave/clients/mtur.py` | client-impl | file-I/O | `brave/clients/norteia_api.py` + `brave/clients/null_norteia_api.py` | role-match |
| `brave/clients/notebooklm.py` | client-impl | file-I/O | `brave/clients/mtur.py` (sibling) | exact |
| `brave/api/routers/dlq.py` | api-endpoint | request-response | itself (extend) | self |
| `brave/tasks/pipeline.py` | celery-task | request-response | itself — `push_mar` task (extend) | self |
| `data/mtur/municipios_mtur_YYYY.csv` | data-seed | file-I/O | — | none |
| `tests/fakes/fake_mtur.py` | fake | — | `tests/fakes/fake_places.py` + `tests/fakes/fake_norteia_api.py` | exact |
| `tests/fakes/fake_notebooklm.py` | fake | — | `tests/fakes/fake_places.py` | exact |
| `tests/unit/test_score_engine.py` | unit-test | — | itself (extend with producer boundary cases) | self |
| `tests/unit/test_desmembramento.py` | unit-test | — | `tests/unit/test_routing.py` | role-match |
| `tests/integration/test_destinos_lane.py` | integration-test | — | `tests/integration/test_end_to_end_pipeline.py` | role-match |

---

## Pattern Assignments

### `brave/lanes/destinos/__init__.py` (config)

**Analog:** `brave/lanes/__init__.py` (empty package marker)

**Pattern:** Empty `__init__.py`. No imports, no re-exports. Keeps the package discoverable without pulling in lane code at import time.

```python
# Empty file — package marker only
```

---

### `brave/lanes/destinos/mtur.py` (producer, batch/CRUD)

**Analogs:**
- `brave/lanes/base.py` — `LaneProtocol.produce(uf)` signature to implement
- `brave/core/nascente/service.py` — `store_raw` call to write Nascente records

**LaneProtocol contract** (`brave/lanes/base.py` lines 12-28):
```python
class LaneProtocol(Protocol):
    async def produce(self, uf: str) -> None:
        """Ingest one full UF sweep for this lane.

        Implementors write raw payloads to Nascente via the NascenteService.
        Called by the Celery sweep_uf task for each UF in the fan-out.
        """
        ...
```

**store_raw call pattern** (`brave/core/nascente/service.py` lines 21-97):
```python
# Signature — call from every producer:
store_raw(
    session=session,
    source="mtur",                      # source tag matches source_ref prefix
    source_ref=f"mtur:{uf}:{ibge_code}",
    entity_type="destination",
    uf=uf,
    payload={
        "name": mun["name"],
        "municipio_id": mun["ibge_code"],  # 7-digit IBGE code (D-10)
        "uf": uf,
        "categoria": categoria,
        # §7.6 criterion *_value fields — routing.py reads these at line 142-148:
        "origem_value": 100.0,
        "completude_value": _completude_from_fields(mun),
        "corroboracao_value": 0.0,
        "atualidade_value": _atualidade_from_publish_date(dataset_year),
        "validacao_humana_value": 0.0,
    },
)
```

**MturClient real implementation structure** (from RESEARCH.md §Mtur Dataset):
```python
# brave/clients/mtur.py (real impl — see brave/clients/norteia_api.py for the
# async context-manager + injected-client pattern to copy if you add HTTP later)
import csv
import pathlib
from typing import Any

DATA_PATH = pathlib.Path(__file__).parent.parent.parent / "data" / "mtur"

def _load_csv() -> list[dict]:
    candidates = sorted(DATA_PATH.glob("municipios_mtur_*.csv"), reverse=True)
    if not candidates:
        raise FileNotFoundError("No Mtur seed CSV found in data/mtur/")
    path = candidates[0]
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def _map_categoria(raw: str) -> str:
    raw_clean = raw.strip().upper()
    if raw_clean in ("A", "B") or "TURÍSTICOS" in raw_clean or "TURISTICOS" in raw_clean:
        return "Oferta Principal"
    elif raw_clean in ("C", "D") or "COMPLEMENTAR" in raw_clean:
        return "Complementar"
    elif raw_clean in ("E",) or "APOIO" in raw_clean:
        return "Apoio"
    return "Apoio"  # safe default

class MturClient:
    async def fetch_municipalities(self, uf: str) -> list[dict[str, Any]]:
        rows = _load_csv()
        result = []
        for row in rows:
            row_uf = (row.get("sg_uf") or row.get("uf") or "").strip().upper()
            if row_uf != uf.upper():
                continue
            ibge = (row.get("co_municipio") or row.get("codigo_ibge") or "").strip()
            name = (row.get("no_municipio") or row.get("nome_municipio") or "").strip()
            categoria_raw = (row.get("categoria") or row.get("ds_categoria") or "").strip()
            result.append({
                "ibge_code": ibge,
                "name": name,
                "categoria": _map_categoria(categoria_raw),
                "uf": uf,
            })
        return result
```

**D-18 boundary check:** `MturSeedIngest.produce` imports from `brave.core.nascente.service` and `brave.clients.mtur`. It does NOT import from any other lane. `brave/core/` never imports from `brave/lanes/`.

**Protocol compliance check (copy from `tests/fakes/fake_norteia_api.py` line 80-82):**
```python
def _check_protocol_compliance() -> None:
    _client: MturClientProtocol = MturClient()  # noqa: F841
```

---

### `brave/lanes/destinos/notebooklm.py` (producer, batch/CRUD)

**Analog:** `brave/lanes/destinos/mtur.py` (sibling — mirror the same `LaneProtocol.produce` shape)

**Differences from Mtur:**
- `source="notebooklm"`, `source_ref=f"notebooklm:{uf}:{ibge_code}"`, `origem_value=80.0`
- Client protocol is `NotebookLMClientProtocol.fetch_report(municipio)` (one report per municipality, not bulk CSV)
- Phase 2 implementation: local-file-backed (reports as JSON files under `data/notebooklm/`)

**store_raw call** — same pattern as Mtur, with `source="notebooklm"` and `origem_value=80.0`:
```python
store_raw(
    session=session,
    source="notebooklm",
    source_ref=f"notebooklm:{uf}:{ibge_code}",
    entity_type="destination",
    uf=uf,
    payload={
        "name": report["name"],
        "municipio_id": ibge_code,
        "uf": uf,
        "origem_value": 80.0,
        "completude_value": _completude_from_report(report),
        "corroboracao_value": 0.0,
        "atualidade_value": _atualidade_from_report_date(report),
        "validacao_humana_value": 0.0,
    },
)
```

**Corroboration boost (Pitfall 2 mitigation — lane code, not core):**
After `store_raw`, if a `RioRecord` already exists for the same `municipio_id` (Mtur record), the NotebookLM lane MUST boost `corroboracao_value` on the surviving record's `normalized` dict and call `reprocess_record`. This is the mechanism for Mar promotion:
```python
# After store_raw, check for existing Mtur record by municipio_id
from sqlalchemy import select
from brave.core.models import RioRecord
from sqlalchemy.orm.attributes import flag_modified

existing = session.scalar(
    select(RioRecord).where(
        RioRecord.municipio_id == ibge_code,
        RioRecord.uf == uf,
        RioRecord.entity_type == "destination",
    )
)
if existing is not None:
    normalized = dict(existing.normalized or {})
    # Boost corroboracao — NotebookLM confirms the Mtur record
    normalized["corroboracao_value"] = min(
        100.0, float(normalized.get("corroboracao_value", 0.0)) + 50.0
    )
    existing.normalized = normalized
    flag_modified(existing, "normalized")
    session.flush()
    from brave.core.rio.routing import reprocess_record
    reprocess_record(session, existing.id, config)
```

---

### `brave/lanes/destinos/desmembramento.py` (llm-agent, request-response)

**Analogs:**
- `brave/tasks/pipeline.py` lines 78-110 — `quarantine_poison` call pattern + error classification
- `brave/clients/base.py` lines 24-47 — `LLMClientProtocol.extract` signature
- `tests/fakes/fake_llm.py` — `FakeLLMClient` (inject in tests)

**LLMClientProtocol.extract call pattern** (`brave/clients/base.py` lines 36-47):
```python
async def extract(
    self,
    prompt: str,
    schema: type,
    mode: str = "tools",
) -> Any:
    ...
```

**Fan-out pattern** (one LLM call per Oferta Principal município):
```python
for mun in await mtur_client.fetch_municipalities(uf):
    if mun["categoria"] != "Oferta Principal":
        continue
    prompt = DESMEMBRAMENTO_PROMPT.format(
        municipio_nome=mun["name"],
        uf=uf,
        ibge_code=mun["ibge_code"],
    )
    try:
        result: DesmembramentoResult = await llm_client.extract(
            prompt=prompt,
            schema=DesmembramentoResult,
            mode="tools",  # instructor Mode.TOOLS — D-09 carried forward
        )
    except Exception as exc:
        # Quarantine the failure — NOT the §7.6 DLQ (from RESEARCH.md §Validate-or-Quarantine)
        quarantine_poison(
            session=session,
            nascente_id=None,
            task_name="brave.desmembramento",
            error=str(exc),
            payload={"municipio_ibge": mun["ibge_code"], "municipio_nome": mun["name"]},
        )
        continue  # Skip this município, continue fan-out
    # Each valid destino → Nascente with origem=40
    for destino in result.destinos:
        slug = destino.nome.lower().replace(" ", "-")
        store_raw(
            session=session,
            source="desm",
            source_ref=f"desm:{uf}:{mun['ibge_code']}:{slug}",
            entity_type="destination",
            uf=uf,
            payload={
                "name": destino.nome,
                "municipio_id": mun["ibge_code"],
                "uf": uf,
                "tipo": destino.tipo,
                "posicionamento": destino.posicionamento,
                "source_note": "LLM-generated, pending validation",
                "origem_value": 40.0,
                "completude_value": _completude_desmembramento(destino),
                "corroboracao_value": 0.0,
                "atualidade_value": 0.0,
                "validacao_humana_value": 0.0,
            },
        )
```

**quarantine_poison** (`brave/tasks/pipeline.py` lines 78-110):
```python
quarantine = PoisonQuarantine(
    id=uuid.uuid4(),
    nascente_id=nascente_id,        # None for DesmembramentoAgent failures
    task_name=task_name,            # "brave.desmembramento"
    error_message=error,
    payload=payload or {},
)
session.add(quarantine)
session.flush()
```

**Import:**
```python
from brave.tasks.pipeline import quarantine_poison  # re-use existing function
```

---

### `brave/lanes/destinos/schemas.py` (data-schema)

**Analog:** `brave/core/score/schemas.py` — Pydantic BaseModel with Field validators

**Pydantic v2 model pattern** (`brave/core/score/schemas.py` — inferred from test_score_engine.py lines 9-10):
```python
from pydantic import BaseModel, Field
from typing import Literal

class DestinoItem(BaseModel):
    nome: str = Field(..., min_length=2, description="Nome turístico do destino")
    tipo: Literal["distrito", "praia", "vila", "localidade", "ilha", "balneario", "outros"]
    posicionamento: str = Field(..., min_length=5, description="Breve posicionamento turístico")

class DesmembramentoResult(BaseModel):
    municipio_ibge: str = Field(..., pattern=r"^\d{7}$")
    municipio_nome: str
    destinos: list[DestinoItem] = Field(default_factory=list)
```

**Note:** `instructor` validates this schema on the way out of `LLMClientProtocol.extract`. Validation failure raises; `desmembramento.py` catches and calls `quarantine_poison`.

---

### `brave/clients/mtur.py` (client-impl, file-I/O)

**Analog:** `brave/clients/norteia_api.py` — real implementation of a client Protocol; `brave/clients/null_norteia_api.py` — the null/offline variant pattern

**Module header docstring pattern** (`brave/clients/norteia_api.py` lines 1-23):
```python
"""Real MturClient — reads bundled CSV seed file for municipality ingest.

Implements MturClientProtocol (brave/clients/base.py).

No network I/O — fully offline. Reads the latest CSV from data/mtur/.
...
"""
```

**Protocol compliance guard** (copy from `brave/clients/norteia_api.py` structural typing approach — note the type annotation pattern from `tests/fakes/fake_norteia_api.py` lines 80-82):
```python
# At module bottom:
def _check_protocol_compliance() -> None:
    _client: MturClientProtocol = MturClient()  # noqa: F841
```

**Key rule:** Implementation lives in `brave/clients/mtur.py` (real, file-backed). The null/offline stub lives in `brave/clients/null_mtur.py` following the `NullNorteiaApiClient` pattern from `brave/clients/null_norteia_api.py` lines 20-27 — production code selects between real and null based on `AppConfig.run_real_externals`.

---

### `brave/clients/notebooklm.py` (client-impl, file-I/O)

**Analog:** `brave/clients/mtur.py` (sibling — local-file-backed client)

**Difference:** `fetch_report(municipio)` returns a single structured report dict for one municipality. Phase 2 implementation reads from `data/notebooklm/{uf}/{ibge_code}.json`. Returns `{}` when no report exists for that municipality (graceful degradation).

```python
class NotebookLMClient:
    async def fetch_report(self, municipio: str) -> dict[str, Any]:
        # municipio format: "Porto Seguro:BA:2927408" or "Porto Seguro" fallback
        # Try data/notebooklm/{uf}/{ibge_code}.json
        ...
        return {}  # empty dict when no report exists — caller must handle
```

**Protocol compliance guard:**
```python
def _check_protocol_compliance() -> None:
    _client: NotebookLMClientProtocol = NotebookLMClient()  # noqa: F841
```

---

### `brave/api/routers/dlq.py` (api-endpoint — extend existing file)

**Analog:** itself — copy the reprocess and descarte endpoint shapes (lines 57-119)

**Existing reprocess pattern** (`brave/api/routers/dlq.py` lines 57-89) — the exact dispatch-or-sync-fallback to mirror:
```python
@router.patch("/api/v1/dlq/{rio_id}/reprocess", status_code=202)
def reprocess_dlq_record(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    # Dispatch Celery task; fall back to synchronous when no broker (tests/dev)
    try:
        from brave.tasks.pipeline import reprocess_record_task
        reprocess_record_task.delay(str(rio_id))
    except Exception:
        from brave.config.settings import ScoreConfig
        from brave.core.rio.routing import reprocess_record
        reprocess_record(db, rio_id, ScoreConfig())

    write_audit(
        session=db,
        action="dlq_reprocessed",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state={"routing": "dlq", "dlq_reason": rio.dlq_reason},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id)}
```

**New validate endpoint to add** (D-07, extends dlq.py after existing endpoints):
```python
@router.patch("/api/v1/dlq/{rio_id}/validate", status_code=202)
def validate_dlq_record(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Steward validates a DLQ record: sets validacao_humana=100 → re-score → Mar + push."""
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "score": float(rio.score or 0)}

    # CRITICAL: reassign + flag_modified — JSON column mutation not auto-tracked (Pitfall 3)
    from sqlalchemy.orm.attributes import flag_modified
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    db.flush()

    # Re-score (reprocess_record resets routing → in_progress → re-routes)
    from brave.config.settings import ScoreConfig
    from brave.core.rio.routing import reprocess_record
    reprocess_record(db, rio_id, ScoreConfig())

    # If now routing == "mar", dispatch push_destination_task
    db.refresh(rio)
    if rio.routing == "mar":
        try:
            from brave.tasks.pipeline import push_destination_task
            push_destination_task.delay(str(rio_id))
        except Exception:
            # Sync fallback (no broker in tests/dev)
            from brave.core.mar.service import promote_to_mar
            promote_to_mar(db, rio)

    write_audit(
        session=db,
        action="dlq_validated",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"routing": rio.routing, "score": float(rio.score or 0)},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id), "routing": rio.routing}
```

**New batch-by-state validate endpoint to add** (D-08):
```python
@router.post("/api/v1/dlq/validate-batch", status_code=202)
def validate_batch(
    uf: str = Query(..., description="Two-letter UF code (e.g. 'BA')"),
    entity_type: str = Query("destination"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    """Validate all DLQ records for a UF — thin wrapper over single-record validate."""
    rows = list(db.scalars(
        select(RioRecord).where(
            RioRecord.routing == "dlq",
            RioRecord.uf == uf,
            RioRecord.entity_type == entity_type,
        ).limit(limit)
    ).all())

    validated = 0
    for rio in rows:
        # Reuse single-record validate logic inline (same IBGE/reassign/flag pattern)
        from sqlalchemy.orm.attributes import flag_modified
        normalized = dict(rio.normalized or {})
        normalized["validacao_humana_value"] = 100.0
        rio.normalized = normalized
        flag_modified(rio, "normalized")
        db.flush()
        from brave.config.settings import ScoreConfig
        from brave.core.rio.routing import reprocess_record
        reprocess_record(db, rio.id, ScoreConfig())
        db.refresh(rio)
        if rio.routing == "mar":
            try:
                from brave.tasks.pipeline import push_destination_task
                push_destination_task.delay(str(rio.id))
            except Exception:
                from brave.core.mar.service import promote_to_mar
                promote_to_mar(db, rio)
        validated += 1

    return {"status": "accepted", "uf": uf, "validated": validated}
```

**Critical anti-patterns to avoid (from RESEARCH.md):**
- Never mutate `rio.normalized["key"] = val` in-place; always reassign + `flag_modified`
- Never call `process_nascente_record` (idempotent — returns existing, no re-score); call `reprocess_record`
- Never call `promote_to_mar` without checking `rio.routing == "mar"` first

---

### `brave/tasks/pipeline.py` (celery-task — extend existing file)

**Analog:** `push_mar` task in `brave/tasks/pipeline.py` lines 253-378 — exact pattern to mirror for `push_destination_task`

**push_mar task structure** (lines 253-348) — copy for `push_destination_task`:
```python
@shared_task(
    bind=True,
    max_retries=3,
    name="brave.push_destination",   # new task name
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def push_destination_task(self, rio_id: str) -> None:
    """Promote a validated DLQ destino to Mar and push to norteia-api (D-09)."""
    from brave.core.mar.service import promote_to_mar
    from brave.clients.null_norteia_api import NullNorteiaApiClient

    session, engine = _get_session()
    try:
        rio_uuid = uuid.UUID(rio_id)
        rio = session.get(RioRecord, rio_uuid)
        if rio is None:
            raise PermanentError(f"RioRecord {rio_id} not found")

        # Idempotency: only process mar-routed records
        if rio.routing != "mar":
            return

        # Promote + push (same as push_mar, entity_type="destination" only)
        mar = promote_to_mar(session, rio)
        session.commit()

        app_config = AppConfig()
        if app_config.run_real_externals:
            api_client = NorteiaApiClient(
                base_url=os.environ.get("BRAVE_NORTEIA_API_URL", ""),
                service_token=os.environ.get("BRAVE_NORTEIA_API_SERVICE_TOKEN", ""),
            )
        else:
            api_client = NullNorteiaApiClient()

        payload = _build_push_payload(mar, rio)

        async def _push() -> dict[str, Any]:
            if isinstance(api_client, NorteiaApiClient):
                async with api_client as client:
                    return await client.push_destination(payload)
            else:
                return await api_client.push_destination(payload)

        asyncio.run(_push())

    except PermanentError:
        session.rollback()
    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            pass
    finally:
        session.close()
        engine.dispose()
```

**Key difference from `push_mar`:** `push_destination_task` always calls `push_destination` (never `push_attraction`) because it is destinos-specific. The `_build_push_payload` helper is reused unchanged.

---

### `tests/fakes/fake_mtur.py` (fake)

**Analog:** `tests/fakes/fake_places.py` (exact structure match) + `tests/fakes/fake_norteia_api.py` (call-recording pattern)

**fake_places.py structure** (lines 1-75) — the exact module structure to copy:
```python
"""Fake Mtur client for offline testing.

FakeMturClient implements MturClientProtocol (structural typing, D-09).
Phase 2 — used in lane unit tests and integration tests.

Usage:
    from tests.fakes.fake_mtur import FakeMturClient

    fake = FakeMturClient(fixtures=[{"ibge_code": "2927408", "name": "Porto Seguro", ...}])
    results = await fake.fetch_municipalities("BA")
    assert fake.calls[0] == "BA"
"""

from typing import Any
from brave.clients.base import MturClientProtocol


class FakeMturClient:
    """Fake Mtur client returning configurable municipality fixtures."""

    def __init__(self, fixtures: list[dict] | None = None) -> None:
        self._fixtures = fixtures or [
            {
                "ibge_code": "2927408",
                "name": "Porto Seguro",
                "categoria": "Oferta Principal",
                "uf": "BA",
            },
        ]
        self.calls: list[str] = []  # records each uf passed to fetch_municipalities

    async def fetch_municipalities(self, uf: str) -> list[dict[str, Any]]:
        self.calls.append(uf)
        return [m for m in self._fixtures if m.get("uf") == uf]


# Structural type check: FakeMturClient must satisfy MturClientProtocol
def _check_protocol_compliance() -> None:
    _client: MturClientProtocol = FakeMturClient()  # noqa: F841
```

---

### `tests/fakes/fake_notebooklm.py` (fake)

**Analog:** `tests/fakes/fake_places.py` (exact structure) — fixture-keyed by `municipio` string

```python
"""Fake NotebookLM client for offline testing.

FakeNotebookLMClient implements NotebookLMClientProtocol (structural typing, D-09).

Usage:
    fake = FakeNotebookLMClient(reports={"Porto Seguro": {"name": "Porto Seguro", ...}})
    report = await fake.fetch_report("Porto Seguro")
"""

from typing import Any
from brave.clients.base import NotebookLMClientProtocol


class FakeNotebookLMClient:
    """Fake NotebookLM client returning fixture reports keyed by municipio."""

    def __init__(self, reports: dict[str, dict[str, Any]] | None = None) -> None:
        self._reports = reports or {}
        self.calls: list[str] = []  # records each municipio passed to fetch_report

    async def fetch_report(self, municipio: str) -> dict[str, Any]:
        self.calls.append(municipio)
        return self._reports.get(municipio, {})


# Structural type check
def _check_protocol_compliance() -> None:
    _client: NotebookLMClientProtocol = FakeNotebookLMClient()  # noqa: F841
```

---

### `tests/unit/test_score_engine.py` (unit-test — extend existing)

**Analog:** itself — copy the `@pytest.mark.parametrize` pattern from lines 18-73

**Import block to keep** (lines 1-10):
```python
import pytest

from brave.config.settings import ScoreConfig
from brave.core.score.engine import compute_score
from brave.core.score.schemas import ScoreInput, ScoreResult
```

**Parametrize pattern** (lines 18-73) — copy and extend with Phase 2 producer boundary cases from RESEARCH.md §Score Boundary test cases:
```python
@pytest.mark.parametrize("origem,completude,corroboracao,atualidade,validacao_humana,expected_routing", [
    # D-06 firewall: origem=40 + validacao=0 → NEVER Mar (max=67.0)
    (40, 100, 100, 100, 0, "dlq"),
    # Mtur cold-start safe landing
    (100, 70, 0, 50, 0, "dlq"),           # 30+14+0+7.5+0 = 51.5 ✓
    # Mtur cold-start descarte risk
    (100, 70, 0, 30, 0, "descarte"),      # 30+14+0+4.5+0 = 48.5
    # NotebookLM minimum DLQ
    (80, 100, 0, 50, 0, "dlq"),           # 24+20+0+7.5+0 = 51.5 ✓
    # After validation, Mtur + corroboration → Mar
    (100, 100, 50, 70, 100, "mar"),       # 30+20+10+10.5+15 = 85.5 ✓
    # After validation, Mtur no corroboration → DLQ (Pitfall 2)
    (100, 100, 0, 100, 100, "dlq"),       # 30+20+0+15+15 = 80.0
    # Desmembramento post-validate, modest values → DLQ (never Mar)
    (40, 100, 0, 70, 100, "dlq"),         # 12+20+0+10.5+15 = 57.5
])
def test_producer_score_boundaries(origem, completude, corroboracao, atualidade, validacao_humana, expected_routing):
    """D-06 firewall and per-producer DLQ/Mar boundary cases."""
    config = ScoreConfig()
    inp = ScoreInput(
        origem_value=origem, completude_value=completude,
        corroboracao_value=corroboracao, atualidade_value=atualidade,
        validacao_humana_value=validacao_humana,
    )
    result = compute_score(inp, config)
    assert result.routing == expected_routing
```

---

### `tests/unit/test_desmembramento.py` (unit-test, new file)

**Analog:** `tests/unit/test_routing.py` — offline unit tests with transient model objects; no DB

**test_routing.py fixture pattern** (lines 14-35):
```python
import pytest

from tests.fakes.fake_llm import FakeLLMClient
from brave.lanes.destinos.schemas import DesmembramentoResult, DestinoItem
# and the agent class itself


@pytest.fixture
def score_config():
    from brave.config.settings import ScoreConfig
    return ScoreConfig()
```

**Happy path test** (from RESEARCH.md §DesmembramentoAgent offline test):
```python
def test_desmembramento_agent_happy_path(db_session, score_config):
    """DesmembramentoAgent writes destinos to Nascente with origem=40."""
    fake_result = DesmembramentoResult(
        municipio_ibge="2927408",
        municipio_nome="Porto Seguro",
        destinos=[
            DestinoItem(nome="Trancoso", tipo="vila", posicionamento="Vila histórica"),
        ],
    )
    fake_llm = FakeLLMClient(fixture_result=fake_result)
    fake_mtur = FakeMturClient(fixtures=[{
        "ibge_code": "2927408",
        "name": "Porto Seguro",
        "categoria": "Oferta Principal",
        "uf": "BA",
    }])
    # instantiate agent with fake clients, call produce("BA")
    # assert: NascenteRecord created with source="desm", origem_value=40
    # assert: fake_llm.calls has one entry with schema="DesmembramentoResult"
```

**Malformed LLM output test:**
```python
def test_desmembramento_agent_malformed_output_quarantined(db_session):
    """FakeLLMClient raises → PoisonQuarantine created, no NascenteRecord written."""
    fake_llm = FakeLLMClient(raise_on_call=ValueError("instructor retry exhausted"))
    fake_mtur = FakeMturClient(fixtures=[{
        "ibge_code": "2927408", "name": "Porto Seguro",
        "categoria": "Oferta Principal", "uf": "BA",
    }])
    # assert: PoisonQuarantine row exists (task_name="brave.desmembramento")
    # assert: no NascenteRecord with source="desm" was created
```

---

### `tests/integration/test_destinos_lane.py` (integration-test, new file)

**Analog:** `tests/integration/test_end_to_end_pipeline.py` — uses `db_session` fixture, calls real core services with fake external clients

**Fixture pattern** (`tests/conftest.py` lines 89-97 + integration test setup):
```python
import pytest
from tests.fakes.fake_mtur import FakeMturClient
from tests.fakes.fake_notebooklm import FakeNotebookLMClient
from tests.fakes.fake_llm import FakeLLMClient
from tests.fakes.fake_norteia_api import FakeNorteiaApiClient


@pytest.mark.integration
def test_mtur_lane_end_to_end(db_session):
    """MturSeedIngest.produce("BA") writes Nascente → Rio → DLQ (cold start)."""
    from brave.lanes.destinos.mtur import MturSeedIngest
    from brave.config.settings import ScoreConfig
    from brave.core.models import NascenteRecord, RioRecord

    fake_mtur = FakeMturClient(fixtures=[...])
    lane = MturSeedIngest(mtur_client=fake_mtur, session=db_session, config=ScoreConfig())
    # asyncio.run or pytest-anyio to call async produce
    # assert NascenteRecord created with source="mtur"
    # assert RioRecord.routing in ("dlq", "descarte")  # cold start — no Mar
```

**Integration test pattern** (`tests/integration/test_fastapi_endpoints.py` lines 111-164) for DLQ validate:
```python
@pytest.mark.integration
def test_dlq_validate_endpoint_promotes_to_mar(client, db_session):
    """PATCH /api/v1/dlq/{rio_id}/validate sets validacao_humana=100 and routes to Mar."""
    # Setup: create Nascente + RioRecord in DLQ
    # Patch: PATCH /api/v1/dlq/{rio_id}/validate
    # Assert: RioRecord.routing == "mar"
    # Assert: AuditLog row with action="dlq_validated"
```

---

## Shared Patterns

### Flag-modified for JSON column mutation
**Source:** `brave/api/routers/dlq.py` (new validate endpoint, RESEARCH.md Pattern 5)
**Apply to:** `brave/api/routers/dlq.py` validate + validate-batch endpoints; any lane code that updates `normalized` on an existing RioRecord
```python
from sqlalchemy.orm.attributes import flag_modified
normalized = dict(rio.normalized or {})
normalized["validacao_humana_value"] = 100.0
rio.normalized = normalized
flag_modified(rio, "normalized")
db.flush()
```

### Dispatch-or-sync-fallback for Celery tasks
**Source:** `brave/api/routers/dlq.py` lines 72-79 (reprocess endpoint)
**Apply to:** New validate endpoint, validate-batch endpoint
```python
try:
    from brave.tasks.pipeline import some_task
    some_task.delay(str(record_id))
except Exception:
    # Sync fallback (no broker in tests/dev)
    sync_equivalent(session, record_id, config)
```

### Structural Protocol compliance guard
**Source:** `tests/fakes/fake_norteia_api.py` lines 80-82; `tests/fakes/fake_places.py` lines 72-75
**Apply to:** All new fake clients (`fake_mtur.py`, `fake_notebooklm.py`) and real client impls (`brave/clients/mtur.py`, `brave/clients/notebooklm.py`)
```python
def _check_protocol_compliance() -> None:
    _client: SomeProtocol = ConcreteImpl()  # noqa: F841
```

### Audit write
**Source:** `brave/observability/audit.py` lines 22-74; `brave/api/routers/dlq.py` lines 81-89
**Apply to:** All new DLQ endpoints (validate, validate-batch)
```python
write_audit(
    session=db,
    action="dlq_validated",       # or "dlq_batch_validated"
    entity_type=rio.entity_type,
    record_id=rio.id,
    before_state={"routing": "dlq", "score": float(rio.score or 0)},
    after_state={"routing": rio.routing, "score": float(rio.score or 0)},
    actor="steward",
)
```

### Celery task boilerplate
**Source:** `brave/tasks/pipeline.py` lines 118-126 (`process_nascente` decorator) and lines 253-261 (`push_mar` decorator)
**Apply to:** New `push_destination_task`
```python
@shared_task(
    bind=True,
    max_retries=3,
    name="brave.push_destination",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
```

### Session factory in Celery tasks
**Source:** `brave/tasks/pipeline.py` lines 60-70 (`_get_session`)
**Apply to:** `push_destination_task` — reuse `_get_session()` directly; no new session factory needed

### Source-ref format
**Source:** RESEARCH.md Pattern 2 (derived from `_build_push_payload` at `pipeline.py` lines 237-238)
**Apply to:** All three producers
```python
source_ref = f"mtur:{uf}:{ibge_code}"         # MturSeedIngest
source_ref = f"notebooklm:{uf}:{ibge_code}"   # NotebookLMIngest
source_ref = f"desm:{uf}:{ibge_code}:{slug}"  # DesmembramentoAgent
```

### Pydantic v2 with pydantic-settings
**Source:** `brave/config/settings.py` lines 15-38 (ScoreConfig)
**Apply to:** Any new Pydantic schemas in `brave/lanes/destinos/schemas.py`; no BaseSettings needed for schemas — use `BaseModel` directly

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `data/mtur/municipios_mtur_YYYY.csv` | data-seed | file-I/O | No bundled seed data files exist yet; first data asset in the repo |

The data seed file has no code analog. The planner should add a Wave 0 task to download and inspect the real Mtur CSV from dados.gov.br before implementing `MturClient`. Column names (`co_municipio` vs `codigo_ibge`, `sg_uf` vs `uf`, etc.) are ASSUMED (see RESEARCH.md §Mtur Dataset). The `MturClient._load_csv` must handle at least two column-name variants and raise a clear error if neither matches.

---

## Metadata

**Analog search scope:** `brave/` (all Python files), `tests/` (all Python files)
**Files scanned:** 44 Python source files read or inspected
**Pattern extraction date:** 2026-06-12

**Critical notes for planner:**

1. **DesmembramentoAgent descarte risk is real** — RESEARCH.md §Score Calibration confirms origen=40 records score ≤47.0 at cold start, which is below `threshold_dlq=51`. The planner must schedule Wave 0 calibration (simulation.py harness) and decide between lowering `threshold_dlq` to ~40 OR implementing explicit corroboração injection before national fan-out.

2. **Corroboração boost is load-bearing for Mar promotion** — Mtur records without corroboração score max 80.0 after human validation (< 85 threshold). The NotebookLM lane code (not the core) must boost `corroboracao_value` on the surviving record when IBGE dedup match is found (RESEARCH.md Pitfall 2). This is a `flag_modified` + `reprocess_record` call in the lane, not a core change.

3. **Pact contract gap (RISK-01)** — The frozen Pact `DESTINATION_PAYLOAD` carries `canonical.municipio` as a name string, not an IBGE code. D-10 requires IBGE resolution on the norteia-api side. The planner should schedule a Wave 0 task to add `ibge_code` to `canonical` and re-run the Pact test before the first real push.

4. **D-18 boundary is enforced** — All new code in `brave/lanes/destinos/` imports from `brave.core.*` and `brave.clients.*`. Nothing in `brave/core/` imports from `brave/lanes/`. The `push_destination_task` in `brave/tasks/pipeline.py` extends the tasks layer, which is also a valid consumer of core.

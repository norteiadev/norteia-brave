# Phase 11: TripAdvisor Source Lane — Pattern Map

**Mapped:** 2026-06-23
**Files analyzed:** 32 new/modified files
**Analogs found:** 30 / 32

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `brave/lanes/tripadvisor/__init__.py` | config | — | `brave/lanes/destinos/__init__.py` | exact |
| `brave/lanes/tripadvisor/client.py` | service | request-response | `brave/clients/null_places.py` + task lazy-import pattern | role-match |
| `brave/lanes/tripadvisor/geo.py` | utility | request-response | `brave/lanes/destinos/mtur.py` (Redis cache pattern in tasks) | role-match |
| `brave/lanes/tripadvisor/ibge.py` | utility | transform | `brave/lanes/destinos/mtur.py` (`_completude_from_fields`) | role-match |
| `brave/lanes/tripadvisor/schemas.py` | model | transform | `brave/lanes/atrativos/schemas.py` | exact |
| `brave/lanes/tripadvisor/scoring.py` | utility | transform | `brave/core/score/engine.py` (ScoreInput → compute_score) | role-match |
| `brave/lanes/tripadvisor/destinos.py` | service | CRUD | `brave/lanes/destinos/mtur.py` | exact |
| `brave/lanes/tripadvisor/atrativos.py` | service | CRUD | `brave/lanes/destinos/mtur.py` | exact |
| `brave/clients/base.py` | config | — | self (add `TripAdvisorClientProtocol`) | exact |
| `brave/clients/null_tripadvisor.py` | service | — | `brave/clients/null_places.py` | exact |
| `brave/config/settings.py` | config | — | self (add `TripAdvisorConfig`) | exact |
| `brave/core/rio/routing.py` | service | transform | self (add `mar_ready` flag in `route_by_score`) | exact |
| `brave/core/models.py` | model | — | self (add `mar_ready` column to `RioRecord`) | exact |
| `alembic/versions/0006_*.py` | migration | — | `alembic/versions/0005_conversation_message.py` | exact |
| `brave/tasks/pipeline.py` | service | event-driven | self (`sweep_uf`, `engine_sweep_run`) | exact |
| `brave/core/engine.py` | service | event-driven | self (`set_depth/get_depth` → `set_source/get_source`) | exact |
| `brave/api/routers/engine.py` | controller | request-response | self (`/start` depth validation → source validation) | exact |
| `brave/api/routers/atrativos.py` | controller | request-response | `brave/api/routers/dlq.py` (validate/validate-batch pattern) | exact |
| `brave/core/promote/service.py` | service | CRUD | `brave/core/dlq/service.py` (`validate_and_promote_rio`) | exact |
| `tests/fakes/fake_tripadvisor.py` | test | — | `tests/fakes/fake_places.py` | exact |
| `dashboard/lib/engine-api.ts` | utility | request-response | self (`EngineDepth`, `startEngine`) | exact |
| `dashboard/lib/mar-ready-api.ts` | utility | request-response | `dashboard/lib/dlq-api.ts` | exact |
| `dashboard/components/engine/EngineControl.tsx` | component | request-response | self (add source radiogroup + UF chips) | exact |
| `dashboard/components/mar-ready/MarReadyList.tsx` | component | request-response | `dashboard/components/dlq/` pattern | role-match |
| `dashboard/components/mar-ready/MarReadyActions.tsx` | component | request-response | `dashboard/components/dlq/dlq-actions.ts` | exact |
| `dashboard/app/mar-ready/page.tsx` | component | request-response | `dashboard/app/dlq/page.tsx` | role-match |
| `dashboard/app/page.tsx` | component | — | self (add `SURFACES` nav entry) | exact |
| `dashboard/mocks/handlers/engine.ts` | test | — | self (add source fields to existing handlers) | exact |
| `data/ibge/ibge_municipios.csv` | config | — | `data/mtur/municipios_mtur_2024.csv` | role-match |
| `data/ibge/README` | config | — | `data/mtur/README` | exact |
| `data/tripadvisor/uf_geoids.json` | config | — | `data/mtur/municipios_mtur_2024.csv` | role-match |
| `data/tripadvisor/README` + `SOURCES.md` | config | — | `data/mtur/README` | exact |

---

## Pattern Assignments

---

### `brave/lanes/tripadvisor/destinos.py` and `atrativos.py` (service, CRUD)

**Analog:** `brave/lanes/destinos/mtur.py`

**Imports pattern** (mtur.py:21-31):
```python
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from brave.config.settings import ScoreConfig
from brave.core.nascente.service import store_raw
from brave.core.rio.routing import process_nascente_record

if TYPE_CHECKING:
    from brave.clients.base import MturClientProtocol
```

**Class constructor pattern** (mtur.py:95-103):
```python
class MturSeedIngest:
    def __init__(
        self,
        mtur_client: "MturClientProtocol",
        session: Session,
        config: ScoreConfig,
    ) -> None:
        self._client = mtur_client
        self._session = session
        self._config = config
```

**Core produce() pattern** (mtur.py:105-173):
```python
async def produce(self, uf: str, *, run_rio: bool = True) -> None:
    municipalities = await self._client.fetch_municipalities(uf)

    for mun in municipalities:
        source_ref = f"mtur:{uf}:{ibge_code}"

        payload: dict[str, Any] = {
            "name": name,
            "uf": uf,
            # §7.6 criterion *_value fields — routing.py reads these at normalize step
            "origem_value": 100.0,
            "completude_value": _completude_from_fields(mun),
            "corroboracao_value": 0.0,
            "atualidade_value": MTUR_ATUALIDADE_DEFAULT,
            "validacao_humana_value": 0.0,
            "canonical": { ... },
        }

        nascente = store_raw(
            session=self._session,
            source="mtur",
            source_ref=source_ref,
            entity_type="destination",
            uf=uf,
            payload=payload,
        )

        if run_rio:
            process_nascente_record(
                session=self._session,
                nascente=nascente,
                config=self._config,
            )
```

**Completude helper pattern** (mtur.py:50-73):
```python
def _completude_from_fields(mun: dict[str, Any]) -> float:
    fields = [
        mun.get("ibge_code", ""),
        mun.get("name", ""),
        mun.get("categoria", ""),
        mun.get("uf", ""),
    ]
    count = sum(1 for f in fields if f)
    return float(count * 25)
```

**Adaptation notes for TripAdvisor:**
- `TripAdvisorDestinosIngest`: `source="tripadvisor"`, `entity_type="destination"`, `origem_value=65.0`. `source_ref = f"tripadvisor:destination:{locationId}"`. `run_rio` depth gate identical.
- `TripAdvisorAtrativosIngest`: same but `entity_type="attraction"`, `source_ref = f"tripadvisor:attraction:{locationId}"`. Also carries `parent_rio_id`, `parent_source_ref`, `parent_mar_id` (only when available) in payload. `completude_from_fields` covers ×20 fields with atrativo cap 100.
- Replace `_completude_from_fields` with `completude_from_fields(entity: dict) -> float` checking TA-specific fields.

---

### `brave/lanes/tripadvisor/scoring.py` (utility, transform)

**No direct analog** — pure math functions. Use ScoreInput/ScoreConfig types from existing score engine.

**ScoreInput construction pattern** (routing.py:49-56):
```python
score_input = ScoreInput(
    origem_value=float(normalized.get("origem_value", 0.0)),
    completude_value=float(normalized.get("completude_value", 0.0)),
    corroboracao_value=float(normalized.get("corroboracao_value", 0.0)),
    atualidade_value=float(normalized.get("atualidade_value", 0.0)),
    validacao_humana_value=float(normalized.get("validacao_humana_value", 0.0)),
)
```

**Scoring functions to implement** (per CONTEXT.md TA-04):
- `corroboracao_from_reviews(count: int, rating: float) -> float` — log curve, saturates at ~500 reviews × rating gate.
- `atualidade_from_recency(most_recent_review_at: datetime | None) -> float` — step function: ≤30d→100 / ≤180d→70 / ≤365d→40 / ≤730d→20 / else 0.

---

### `brave/lanes/tripadvisor/schemas.py` (model, transform)

**Analog:** `brave/lanes/atrativos/schemas.py` and `brave/lanes/destinos/schemas.py`

**Pydantic schema pattern with LGPD boundary** (atrativos/schemas.py:38-45):
```python
class AtrativoResult(BaseModel):
    nome: str = Field(
        ...,
        min_length=2,
        description="Nome do atrativo turístico, conforme aparece no Google Places.",
    )
    ...
```

**LGPD-enforced schema** (RESEARCH.md §7):
```python
from pydantic import BaseModel, ConfigDict
from datetime import datetime

class TripAdvisorReviewSignals(BaseModel):
    review_count: int = 0
    rating: float = 0.0
    most_recent_review_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")  # LGPD boundary: no author/text
```

---

### `brave/lanes/tripadvisor/ibge.py` (utility, transform)

**No direct analog** — new pure-function utility. Follows the pattern of `_completude_from_fields` for pure transform helpers.

**Pattern from RESEARCH.md §3:**
```python
import csv
from dataclasses import dataclass
from pathlib import Path
from rapidfuzz import fuzz, process

@dataclass
class IbgeMunicipio:
    ibge_code: str
    nome: str
    uf: str
    lat: float
    lng: float

def resolve_municipio(
    name: str,
    uf: str,
    records: list[IbgeMunicipio],
    *,
    threshold: int = 88,
    max_distance_km: float = 15.0,
    candidate_lat: float | None = None,
    candidate_lng: float | None = None,
) -> IbgeMunicipio | None:
    uf_records = [r for r in records if r.uf == uf]
    choices = [r.nome for r in uf_records]
    result = process.extractOne(name, choices, scorer=fuzz.token_sort_ratio, score_cutoff=threshold)
    if result:
        return uf_records[result[2]]
    # Haversine fallback
    if candidate_lat is not None and candidate_lng is not None:
        for r in uf_records:
            if haversine_km(candidate_lat, candidate_lng, r.lat, r.lng) < max_distance_km:
                return r
    return None  # → quarantine ibge_unmatched
```

---

### `brave/lanes/tripadvisor/geo.py` (utility, request-response)

**No direct analog** for Redis-cached geo resolution. Mirror the Redis pattern from `brave/core/engine.py` (`_DEPTH_KEY`, `set_depth`, `get_depth`) for the `brave:ta:session` and geoId cache.

**Redis key pattern** (engine.py:43-46):
```python
_STATE_KEY = "brave:engine:state"
_DEPTH_KEY = "brave:engine:depth"

def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
```

---

### `brave/lanes/tripadvisor/client.py` (service, request-response)

**Analog:** `brave/clients/null_places.py` (NullClient pattern); lazy-import pattern from `brave/tasks/pipeline.py`.

**Lazy-import pattern for Playwright** (pipeline.py:809, task docstring, RESEARCH.md §1):
```python
def _bootstrap_session(self) -> dict:
    # Only import at call time — Playwright never loads in CI or dashboard
    from playwright.sync_api import sync_playwright  # noqa: PLC0415
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, proxy=self._proxy_args())
        ...
```

**Client seam pattern** — constructor receives no heavy dep; Playwright only accessed through a bootstrap method.

---

### `brave/clients/null_tripadvisor.py` (service, -)

**Analog:** `brave/clients/null_places.py` (lines 16-54)

**Full null client pattern** (null_places.py:16-54):
```python
class NullPlacesClient:
    """No-network PlacesClient stub (structural protocol match).

    Returns empty list for text_search and empty dict for place_details —
    no Google Maps API call, no network I/O.
    Safe to use when RUN_REAL_EXTERNALS is unset/false.
    """

    async def text_search(self, query: str, uf: str) -> list[dict[str, Any]]:
        return []

    async def place_details(self, place_id: str) -> dict[str, Any]:
        return {}


# Structural type check: NullPlacesClient must satisfy PlacesClientProtocol
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    from brave.clients.base import PlacesClientProtocol

    _client: PlacesClientProtocol = NullPlacesClient()  # noqa: F841
```

**Adaptation for TripAdvisor:**
```python
class NullTripAdvisorClient:
    """No-network TA stub — returns empty fixtures."""
    async def fetch_destinations(self, uf: str) -> list[dict]: return []
    async def fetch_attractions(self, geo_id: int, offset: int = 0) -> list[dict]: return []
    async def resolve_geo_id(self, uf: str) -> int: return 0

def _check_protocol_compliance() -> None:
    from brave.clients.base import TripAdvisorClientProtocol
    _client: TripAdvisorClientProtocol = NullTripAdvisorClient()  # noqa: F841
```

---

### `brave/clients/base.py` (config, -)

**Analog:** self — add `TripAdvisorClientProtocol` following the existing 8-protocol pattern.

**Protocol pattern** (base.py:105-133):
```python
class PlacesClientProtocol(Protocol):
    """Google Places (New API) client — Discovery and Signal agents (Phase 3)."""

    async def text_search(self, query: str, uf: str) -> list[dict[str, Any]]:
        ...

    async def place_details(self, place_id: str) -> dict[str, Any]:
        ...
```

**Adaptation:** Add `TripAdvisorClientProtocol` with `fetch_destinations(uf: str)`, `fetch_attractions(geo_id: int, offset: int)`, `resolve_geo_id(uf: str)`.

---

### `brave/config/settings.py` (config, -)

**Analog:** self — add `TripAdvisorConfig` following `WhatsAppConfig` / `RampConfig` pattern.

**Sub-config pattern** (settings.py:148-187):
```python
class WhatsAppConfig(BaseSettings):
    """WhatsApp BSP configuration (Twilio launch path, D-09).

    No env-var aliases (CR-02): each field resolves from its exact BRAVE_WA_ prefixed name only.
    """

    twilio_account_sid: str = Field(
        default="",
        description="Twilio account SID for WhatsApp Business API (starts with AC...).",
    )
    ...
    model_config = SettingsConfigDict(env_prefix="BRAVE_WA_", populate_by_name=True)
```

**AppConfig nesting pattern** (settings.py:223-248):
```python
class AppConfig(BaseSettings):
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    ramp: RampConfig = Field(default_factory=RampConfig)
```

**Adaptation:** Add:
```python
class TripAdvisorConfig(BaseSettings):
    proxy_url: str = Field(default="")
    session_ttl: int = Field(default=1800)
    query_id_override: dict[str, str] = Field(default_factory=dict)
    ibge_match_threshold: int = Field(default=88)
    ibge_max_distance_km: float = Field(default=15.0)

    model_config = SettingsConfigDict(env_prefix="BRAVE_TA_")
    # CR-02: NO Field(alias=...) anywhere
```

Add `mar_ready_atualidade_bar: float = Field(default=70.0)` and `mar_ready_corrob_bar: float = Field(default=60.0)` to `ScoreConfig` (not TripAdvisorConfig — these are score-engine thresholds, env: `BRAVE_SCORE_MAR_READY_ATUALIDADE_BAR`, `BRAVE_SCORE_MAR_READY_CORROB_BAR`).

Add to `AppConfig`:
```python
tripadvisor: TripAdvisorConfig = Field(default_factory=TripAdvisorConfig)
```

---

### `brave/core/rio/routing.py` (service, transform)

**Analog:** self — add `mar_ready` flag logic at end of `route_by_score`.

**Insertion point** (routing.py:60-81) — add AFTER the `dlq_reason` block before `return rio_record`:
```python
# Set mar_ready for TA attractions with sufficient corroboracao and atualidade
rio_record.mar_ready = (
    rio_record.entity_type == "attraction"
    and (rio_record.canonical_key or "").startswith("tripadvisor:")
    and score_input.atualidade_value >= config.mar_ready_atualidade_bar
    and score_input.corroboracao_value >= config.mar_ready_corrob_bar
)
```

**Existing mutation pattern to mirror** (routing.py:61-79):
```python
rio_record.score = result.score
rio_record.routing = result.routing
rio_record.score_version = result.score_version
rio_record.score_breakdown = { ... }
rio_record.processed_at = datetime.now(timezone.utc)

if result.routing == "dlq":
    rio_record.dlq_reason = (
        f"score={result.score:.2f} below threshold_mar={config.threshold_mar}"
    )
else:
    rio_record.dlq_reason = None

return rio_record
```

**`process_nascente_record` parent_mar_id copy pattern** (routing.py:160-163) — already exists, tripadvisor atrativos must also carry `parent_rio_id`:
```python
if nascente.entity_type == "attraction" and "parent_mar_id" in payload:
    normalized["parent_mar_id"] = payload["parent_mar_id"]
```

---

### `brave/core/models.py` (model, -)

**Analog:** self — add `mar_ready` column to `RioRecord`.

**Existing column declaration pattern** (models.py:134-138):
```python
routing: Mapped[str] = mapped_column(
    String(32), nullable=False, default="in_progress", index=True
)
```

**New column to add:**
```python
mar_ready: Mapped[bool] = mapped_column(
    Boolean, nullable=False, server_default=text("false"), index=True
)
```

---

### `alembic/versions/0006_add_rio_mar_ready.py` (migration, -)

**Analog:** `alembic/versions/0005_conversation_message.py` (full file)

**Migration header pattern** (0005:1-31):
```python
"""Add conversation_message table — ...

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-16
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None
```

**upgrade/downgrade pattern** (0005:33-73):
```python
def upgrade() -> None:
    op.create_table(...)
    # Standard B-tree index — not CONCURRENTLY (inside Alembic transaction, new table).
    op.create_index("ix_conversation_message_rio_id", "conversation_message", ["rio_id"])

def downgrade() -> None:
    op.drop_index("ix_conversation_message_rio_id", table_name="conversation_message")
    op.drop_table("conversation_message")
```

**Adaptation for 0006 (add-column shape):**
```python
"""Add rio_records.mar_ready — TA promote-override gate (TA-05).

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-XX
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None

def upgrade() -> None:
    op.add_column(
        "rio_records",
        sa.Column("mar_ready", sa.Boolean, nullable=False, server_default="false"),
    )
    # Standard B-tree index — not CONCURRENTLY (inside Alembic transaction)
    op.create_index("ix_rio_records_mar_ready", "rio_records", ["mar_ready"])

def downgrade() -> None:
    op.drop_index("ix_rio_records_mar_ready", table_name="rio_records")
    op.drop_column("rio_records", "mar_ready")
```

---

### `brave/core/promote/service.py` (service, CRUD)

**Analog:** `brave/core/dlq/service.py` (full file, lines 1-51)

**`validate_and_promote_rio` pattern** (dlq/service.py:18-51):
```python
def validate_and_promote_rio(
    session: Session,
    rio: RioRecord,
    config: ScoreConfig | None = None,
) -> MarRecord | None:
    config = config or ScoreConfig()

    # Step 1: CRITICAL — reassign + flag_modified (Pitfall 3)
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    session.flush()

    # Step 2: re-score — reprocess_record NOT process_nascente_record (Pitfall 4)
    reprocess_record(session, rio.id, config)
    session.refresh(rio)

    # Step 3: promote only when routing == 'mar'
    if rio.routing == "mar":
        return promote_to_mar(session, rio)
    return None
```

**Adaptation for `promote_override`** — add `mar_ready` guard + force routing:
```python
def promote_override(
    session: Session,
    rio: RioRecord,
    reason: str,
    config: ScoreConfig | None = None,
) -> MarRecord:
    if not rio.mar_ready:
        raise PromoteNotAllowed(f"RioRecord {rio.id} is not mar_ready")

    config = config or ScoreConfig()

    # Step 1: flag_modified pattern (dlq/service.py:37-40)
    normalized = dict(rio.normalized or {})
    normalized["validacao_humana_value"] = 100.0
    rio.normalized = normalized
    flag_modified(rio, "normalized")
    session.flush()

    # Step 2: reprocess_record (not process_nascente_record — dlq/service.py:43-44)
    reprocess_record(session, rio.id, config)
    session.refresh(rio)

    # Step 3: force routing="mar" and promote directly (bypass ≥85 gate)
    rio.routing = "mar"
    rio.provenance = {**(rio.provenance or {}), "promotion_reason": reason}
    return promote_to_mar(session, rio)
```

**PromoteNotAllowed exception** — new class in same file, maps to HTTP 409 in router.

---

### `brave/tasks/pipeline.py` (service, event-driven)

**Analog:** self — add `sweep_tripadvisor` task and modify `engine_sweep_run`.

**`sweep_uf` task structure to mirror** (pipeline.py:771-887):
```python
@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.sweep_uf",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=600,
)
def sweep_uf(self, uf: str, depth: str | None = None) -> None:
    from brave.core import engine as collection_engine
    from brave.core.quarantine import quarantine_poison as _quarantine

    run_rio = depth != collection_engine.NASCENTE
    session, engine = _get_session()
    try:
        config = ScoreConfig()
        app_config = AppConfig()
        ...
        session.commit()
    except PermanentError as exc:
        ...
    except Exception as exc:
        ...
    finally:
        session.close()
        engine.dispose()
```

**`engine_sweep_run` dispatch branch to add** (pipeline.py:1629-1641):
```python
if nascente_only:
    sweep_uf.delay(uf, depth=effective_depth)
else:
    if lane in ("destinos", "both"):
        sweep_uf.delay(uf, depth=effective_depth)
    if lane in ("atrativos", "both"):
        discover_atrativo_task.delay(uf, depth=effective_depth)
```

**Adaptation:** Add `source: str = "default"` arg to `engine_sweep_run`; when `source == "tripadvisor"` dispatch `sweep_tripadvisor.delay(uf, depth)` instead of `sweep_uf` + `discover_atrativo_task`. Honor `NASCENTE`-only branch (no per-record fee for TA, but still gated by `run_rio`).

---

### `brave/core/engine.py` (service, event-driven)

**Analog:** self — add `set_source/get_source` following `set_depth/get_depth` pattern exactly.

**`set_depth/get_depth` pattern** (engine.py:102-121):
```python
_DEPTH_KEY = "brave:engine:depth"
_VALID_DEPTHS = frozenset({NASCENTE, NASCENTE_RIO, NASCENTE_RIO_MAR})

def set_depth(redis: Any, depth: str) -> None:
    """Persist the chosen pipeline depth. Rejects anything outside the contract.

    Invalid values raise ValueError and are never written — the engine must not
    silently spend on an unrecognized (possibly more expensive) reach.
    """
    if depth not in _VALID_DEPTHS:
        raise ValueError(
            f"invalid depth {depth!r}; expected one of {sorted(_VALID_DEPTHS)}"
        )
    redis.set(_DEPTH_KEY, depth)


def get_depth(redis: Any) -> str | None:
    """Persisted depth, or None when absent/corrupt (unset → required at the edge)."""
    raw = _decode(redis.get(_DEPTH_KEY))
    return raw if raw in _VALID_DEPTHS else None
```

**`get_status` snapshot to extend** (engine.py:123-131):
```python
def get_status(redis: Any) -> dict[str, Any]:
    return {
        "state": get_state(redis),
        "current_uf": _decode(redis.get(_CURRENT_UF_KEY)) or None,
        "ufs_done": int(_decode(redis.get(_UFS_DONE_KEY)) or 0),
        "ufs_total": int(_decode(redis.get(_UFS_TOTAL_KEY)) or 0),
        "depth": get_depth(redis),
    }
```

**Adaptation:** Add `_SOURCE_KEY = "brave:engine:source"`, `_VALID_SOURCES = frozenset({"default", "tripadvisor"})`, `set_source/get_source` mirroring `set_depth/get_depth` exactly. Add `"source": get_source(redis)` to `get_status` return.

---

### `brave/api/routers/engine.py` (controller, request-response)

**Analog:** self — add `source` validation before `start_run`.

**Depth validation pattern** (engine.py:104-119):
```python
# Validate depth BEFORE start_run (and before the already-running/409 branch):
depth = body.get("depth")
if depth not in collection_engine._VALID_DEPTHS:
    raise HTTPException(
        status_code=422,
        detail="depth is required: nascente|nascente_rio|nascente_rio_mar",
    )

if not collection_engine.start_run(redis, ufs_total=len(ufs)):
    raise HTTPException(
        status_code=409,
        detail="Engine already running — stop it before starting a new run.",
    )

collection_engine.set_depth(redis, depth)
```

**Adaptation:** Add source validation immediately after depth validation:
```python
source = body.get("source", "default")
if source not in collection_engine._VALID_SOURCES:
    raise HTTPException(
        status_code=422,
        detail="source must be 'default' or 'tripadvisor'",
    )
```
Set `collection_engine.set_source(redis, source)` after `set_depth`. Pass `source=source` to `engine_sweep_run.delay(...)`. Echo `source` in the return dict and in `get_status`.

---

### `brave/api/routers/atrativos.py` (controller, request-response)

**Analog:** `brave/api/routers/dlq.py`

**List endpoint pattern** (dlq.py:53-86):
```python
@router.get("/api/v1/dlq")
def list_dlq(
    uf: str | None = Query(None),
    entity_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(RioRecord).where(RioRecord.routing == "dlq")
    if uf:
        query = query.where(RioRecord.uf == uf)
    ...
    rows = list(db.scalars(query).all())
    return [{"id": str(r.id), ...} for r in rows]
```

**validate single endpoint pattern** (dlq.py:130-198):
```python
@router.patch(
    "/api/v1/dlq/{rio_id}/validate",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def validate_dlq_record(rio_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"routing": rio.routing, "score": float(rio.score or 0)}
    validate_and_promote_rio(db, rio)
    db.refresh(rio)

    if rio.routing == "mar":
        try:
            from brave.tasks.pipeline import push_destination_task
            push_destination_task.delay(str(rio_id))
        except Exception as exc:
            from brave.config.settings import AppConfig
            if AppConfig().run_real_externals:
                logger.error("dlq_push_dispatch_failed", ...)
                raise HTTPException(status_code=503, detail="...broker unavailable...") from exc

    write_audit(session=db, action="dlq_validated", ...)
    return {"status": "accepted", "rio_id": str(rio_id), "routing": rio.routing}
```

**validate-batch endpoint pattern** (dlq.py:201-278):
```python
@router.post(
    "/api/v1/dlq/validate-batch",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def validate_batch(
    uf: str = Query(...),
    entity_type: str = Query("destination"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    rows = list(db.scalars(
        select(RioRecord)
        .where(RioRecord.routing == "dlq", RioRecord.uf == uf, ...)
        .limit(limit)
    ).all())
    validated = 0
    for rio in rows:
        validate_and_promote_rio(db, rio)
        db.refresh(rio)
        if rio.routing == "mar":
            try:
                push_destination_task.delay(str(rio.id))
            except Exception as exc:
                if AppConfig().run_real_externals:
                    raise HTTPException(status_code=503, ...) from exc
        write_audit(...)
        validated += 1
    return {"status": "accepted", "uf": uf, "validated": validated}
```

**Adaptation for atrativos router:**
- `GET /api/v1/atrativos/mar-ready`: filter on `RioRecord.mar_ready == True` AND `source == 'tripadvisor'` AND `routing == 'dlq'`.
- `PATCH /api/v1/atrativos/{rio_id}/promote`: call `promote_override(db, rio, reason="steward_override_review_validated")` instead of `validate_and_promote_rio`; catch `PromoteNotAllowed` → 409; dispatch `push_attraction_task.delay(str(rio_id))`; audit `"atrativo_promoted_override"`.
- `POST /api/v1/atrativos/promote-batch`: filter `mar_ready=True AND source='tripadvisor' AND routing='dlq'`; loop with same broker-down 503 contract.

---

### `tests/fakes/fake_tripadvisor.py` (test, -)

**Analog:** `tests/fakes/fake_places.py` (full file)

**Call-recording fake pattern** (fake_places.py:20-69):
```python
class FakePlacesClient:
    def __init__(
        self,
        fixture_results: dict[str, list[dict[str, Any]]] | None = None,
        fixture_details: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._fixture_results = fixture_results or {}
        self._fixture_details = fixture_details or {}
        self.text_search_calls: list[dict[str, Any]] = []
        self.place_details_calls: list[str] = []

    async def text_search(self, query: str, uf: str) -> list[dict[str, Any]]:
        self.text_search_calls.append({"query": query, "uf": uf})
        return self._fixture_results.get(query, [])
```

**Protocol compliance assertion** (fake_places.py:73-75):
```python
def _check_protocol_compliance() -> None:
    _client: PlacesClientProtocol = FakePlacesClient()  # noqa: F841
```

---

### `dashboard/lib/engine-api.ts` (utility, request-response)

**Analog:** self — add `source` to `EngineStatus`, `startEngine`, type definitions.

**Existing type and function pattern** (engine-api.ts:16-88):
```typescript
export type EngineDepth = "nascente" | "nascente_rio" | "nascente_rio_mar";

export const DEPTH_LABELS: Record<EngineDepth, string> = {
  nascente: "Apenas nascente",
  ...
};

export interface EngineStatus {
  state: EngineState;
  current_uf: string | null;
  ufs_done: number;
  ufs_total: number;
  counts: EnginePipelineCounts;
  depth: EngineDepth | null;
}

export function startEngine(
  body?: {
    ufs?: string[];
    lane?: "destinos" | "atrativos" | "both";
    depth?: EngineDepth;
  },
): Promise<EngineActionResult> {
  return apiFetch<EngineActionResult>("api/v1/engine/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}
```

**Adaptation:** Add `export type EngineSource = "default" | "tripadvisor"`. Add `source?: EngineSource | null` to `EngineStatus`. Add `source?: EngineSource` to `startEngine` body type. Add `SOURCE_LABELS: Record<EngineSource, string>`.

---

### `dashboard/lib/mar-ready-api.ts` (utility, request-response)

**Analog:** `dashboard/lib/dlq-api.ts` (full file)

**Query keys pattern** (dlq-api.ts:63-69):
```typescript
export const dlqKeys = {
  all: ["dlq"] as const,
  list: (uf?: string, entityType?: string) =>
    ["dlq", "list", { uf: uf ?? null, entityType: entityType ?? null }] as const,
  detail: (rioId: string) => ["dlq", "detail", rioId] as const,
};
```

**Typed fetcher pattern** (dlq-api.ts:80-134):
```typescript
export function fetchDlqList(uf?: string, entityType?: string, limit = 50): Promise<DlqListItem[]> {
  return apiFetch<DlqListItem[]>(`api/v1/dlq${qs({ uf, entity_type: entityType, limit })}`);
}

export function validateDlqRecord(rioId: string): Promise<MutationResult> {
  return apiFetch<MutationResult>(`api/v1/dlq/${rioId}/validate`, { method: "PATCH" });
}

export interface BatchResult {
  status: string;
  uf: string;
  validated: number;
}

export function validateDlqBatch(uf: string, entityType = "destination", limit = 100): Promise<BatchResult> {
  return apiFetch<BatchResult>(
    `api/v1/dlq/validate-batch${qs({ uf, entity_type: entityType, limit })}`,
    { method: "POST" },
  );
}
```

**Adaptation:** New `marReadyKeys`, `MarReadyItem` interface (with `mar_ready`, `canonical_key`, `source`), `fetchMarReadyList`, `promoteAtrativo(rioId)` (PATCH), `promoteAtrativoBatch({ufs, limit})` (POST).

---

### `dashboard/components/engine/EngineControl.tsx` (component, request-response)

**Analog:** self — add source radiogroup and UF multi-select chips. Mirrors the depth radiogroup pattern already in the component.

**Depth radiogroup pattern** (EngineControl.tsx:119-155):
```typescript
<div
  className="flex flex-wrap items-center gap-1.5"
  role="radiogroup"
  aria-label="Profundidade da varredura"
  data-testid="engine-depth"
>
  {DEPTH_OPTIONS.map((depth) => {
    const active = selectedDepth === depth;
    return (
      <button
        key={depth}
        type="button"
        role="radio"
        aria-checked={active}
        disabled={pending}
        onClick={() => setSelectedDepth(depth)}
        data-testid={`engine-depth-${depth}`}
        className={`rounded-md border px-2.5 py-1 text-[12px] transition-colors ${...}`}
      >
        {DEPTH_LABELS[depth]}
      </button>
    );
  })}
</div>
```

**Active-depth read-back pattern** (EngineControl.tsx:170-178):
```typescript
{state !== "idle" && data?.depth && (
  <p
    className="mt-2 text-[12px] text-muted-foreground"
    data-testid="engine-active-depth"
  >
    Profundidade: {DEPTH_LABELS[data.depth]}
  </p>
)}
```

**start mutation pattern** (EngineControl.tsx:71-76):
```typescript
const start = useMutation({
  mutationFn: (depth: EngineDepth) => startEngine({ depth }),
  onError: (err) => toast.error(explainError(err)),
  onSuccess: () => toast.success("Motor ligado — varredura iniciada"),
  onSettled: invalidate,
});
```

**Adaptation:** Add `data-testid="engine-source"` radiogroup for `EngineSource`. UF chips appear only when `selectedSource === "tripadvisor"`. Thread `startEngine({ depth, source, ufs })`. Add `data-testid="engine-active-source"` read-back when running.

---

### `dashboard/components/mar-ready/MarReadyActions.tsx` (component, request-response)

**Analog:** `dashboard/components/dlq/dlq-actions.ts` (full file)

**Optimistic single-mutation pattern** (dlq-actions.ts:64-113):
```typescript
type DlqListSnapshot = Array<{
  queryKey: readonly unknown[];
  data: DlqListItem[] | undefined;
}>;

export function useValidateDlqRecord(...): UseMutationResult<...> {
  const qc = useQueryClient();

  return useMutation({
    mutationFn: (rioId: string) => validateDlqRecord(rioId),
    onMutate: async (rioId: string) => {
      await qc.cancelQueries({ queryKey: dlqKeys.all });

      const entries = qc.getQueriesData<DlqListItem[]>({ queryKey: ["dlq", "list"] });
      const snapshot: DlqListSnapshot = entries.map(([queryKey, data]) => ({ queryKey, data }));

      for (const { queryKey, data } of snapshot) {
        if (data) {
          qc.setQueryData<DlqListItem[]>(queryKey, data.filter((r) => r.id !== rioId));
        }
      }

      return { snapshot };
    },
    onError: (err, _rioId, ctx) => {
      for (const { queryKey, data } of ctx?.snapshot ?? []) {
        qc.setQueryData(queryKey, data);
      }
      toast.error(explainError(err));
    },
    onSuccess: () => { toast.success("Registro validado → Mar"); },
    onSettled: () => { void qc.invalidateQueries({ queryKey: DLQ_KEY }); },
  });
}
```

**Batch mutation pattern** (dlq-actions.ts:156-172):
```typescript
export function useValidateDlqBatch(): UseMutationResult<
  BatchResult,
  unknown,
  { uf: string; entityType?: string; limit?: number }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ uf, entityType, limit }) => validateDlqBatch(uf, entityType, limit),
    onError: (err) => toast.error(explainError(err)),
    onSuccess: (res) =>
      toast.success(`${res.validated} registros de ${res.uf} validados → Mar`),
    onSettled: () => { void qc.invalidateQueries({ queryKey: DLQ_KEY }); },
  });
}
```

---

### `dashboard/app/page.tsx` (component, -)

**Analog:** self — add `/mar-ready` nav entry to `SURFACES`.

**SURFACES pattern** (page.tsx:15-25):
```typescript
const SURFACES = [
  { href: "/dlq", title: "Fila DLQ", desc: "Revisão batch-by-state · §7.6 · aprovar/rejeitar/editar→re-score" },
  { href: "/monitor", title: "Monitor Brave", desc: "..." },
  ...
];
```

**Adaptation:** Add `{ href: "/mar-ready", title: "Mar Ready", desc: "Atrativos TripAdvisor prontos para promoção manual → Mar" }` to `SURFACES`.

---

### `dashboard/mocks/handlers/engine.ts` (test, -)

**Analog:** self — add source fields to existing MSW handlers.

**MSW handler pattern** (engine.ts:12-55):
```typescript
const BASE = "http://localhost:3000/api/api/v1/engine";

export function engineStatus(overrides: Partial<EngineStatus> = {}) {
  const status: EngineStatus = {
    state: "idle",
    current_uf: null,
    ufs_done: 0,
    ufs_total: 0,
    counts: { nascente: 0, rio: { in_progress: 0, mar: 0, dlq: 0, descarte: 0 }, mar: 0, atrativos_by_sub_state: {} },
    depth: null,
    ...overrides,
  };
  return http.get(`${BASE}/status`, () => HttpResponse.json(status));
}

export function engineStartSuccess(state: EngineState = "running") {
  return http.post(`${BASE}/start`, () =>
    HttpResponse.json(
      { status: "started", ufs_total: 27, lane: "both", depth: "nascente_rio" },
      { status: 202 },
    ),
  );
}
```

**Double-prefix rule** (engine.ts:10): `const BASE = "http://localhost:3000/api/api/v1/engine"` — BFF mount adds `/api/` prefix, FastAPI router adds `/api/v1/`.

**Adaptation:** Add `source: null` (or `"tripadvisor"`) to `EngineStatus` fixture. Add `source` field to `engineStartSuccess` response. Add new `marReadyHandlers` barrel in new `dashboard/mocks/handlers/mar-ready.ts` mirroring `atrativos.ts` shape but for `BASE = ".../atrativos/mar-ready"` and `PATCH .../atrativos/:id/promote`.

---

### `data/ibge/README` and `data/tripadvisor/README` and `SOURCES.md` (config, -)

**Analog:** `data/mtur/README`

**README structure** (data/mtur/README):
```
[Source name] — Dataset
======================

SOURCE
------
[Authority, URL, License]

CURRENT FILES
-------------
[filename, description, usage]

CSV SCHEMA
----------
[column name, type, description for each column]

LOADER BEHAVIOR
---------------
[How the code finds and uses this file]
```

**Adaptation for `data/ibge/README`:** Source = github.com/kelvins/municipios-brasileiros (IBGE official data, CC0). Schema: `ibge_code,nome,uf,lat,lng`. Static seed file, never mutated.

**Adaptation for `data/tripadvisor/README`:** Must include TA ToS scraping risk, mitigations (low rate, residential proxy, no author PII, operator-gated), LGPD basis, opt-in (`RUN_REAL_EXTERNALS=true`).

**`SOURCES.md` structure:** Table with columns Source / Ref / License / Cost / Notes covering mtur, places, tripadvisor.

---

## Shared Patterns

### Authentication (require_steward_or_bearer)
**Source:** `brave/api/routers/dlq.py` (lines 89, 131, 201) and `brave/api/deps.py`
**Apply to:** All new `atrativos.py` router endpoints
```python
@router.patch(
    "/api/v1/atrativos/{rio_id}/promote",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
```

### Broker-down 503 Contract
**Source:** `brave/api/routers/dlq.py` (lines 162-187)
**Apply to:** `atrativos.py` promote and promote-batch endpoints
```python
try:
    from brave.tasks.pipeline import push_attraction_task
    push_attraction_task.delay(str(rio_id))
except Exception as exc:
    from brave.config.settings import AppConfig
    if AppConfig().run_real_externals:
        logger.error("atrativo_promote_dispatch_failed", rio_id=str(rio_id), error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="Promote not committed — Mar push dispatch failed (broker unavailable). Retry once broker is reachable.",
        ) from exc
```

### Audit Write
**Source:** `brave/api/routers/dlq.py` (lines 189-197) and `brave/observability/audit.py`
**Apply to:** All atrativos router mutation endpoints
```python
write_audit(
    session=db,
    action="atrativo_promoted_override",
    entity_type=rio.entity_type,
    record_id=rio.id,
    before_state=before_state,
    after_state={"routing": rio.routing, "score": float(rio.score or 0)},
    actor="steward",
)
```

### Celery Task Structure
**Source:** `brave/tasks/pipeline.py` (lines 771-887, `sweep_uf`)
**Apply to:** `sweep_tripadvisor` task
```python
@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="brave.sweep_tripadvisor",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=600,
)
def sweep_tripadvisor(self, uf: str, depth: str | None = None) -> None:
    session, engine = _get_session()
    try:
        ...
        session.commit()
    except PermanentError as exc:
        session.rollback()
        q_session, q_engine = _get_session()
        try:
            _quarantine(session=q_session, nascente_id=None, task_name="brave.sweep_tripadvisor", error=str(exc), payload={"uf": uf})
            q_session.commit()
        finally:
            q_session.close(); q_engine.dispose()
    except Exception as exc:
        session.rollback()
        try:
            raise self.retry(exc=exc, max_retries=3)
        except self.MaxRetriesExceededError:
            ...
    finally:
        session.close(); engine.dispose()
```

### flag_modified Pattern (JSONB mutation tracking)
**Source:** `brave/core/dlq/service.py` (lines 37-41)
**Apply to:** `brave/core/promote/service.py`
```python
# CRITICAL — reassign + flag_modified (Pitfall 3)
normalized = dict(rio.normalized or {})
normalized["validacao_humana_value"] = 100.0
rio.normalized = normalized
flag_modified(rio, "normalized")
session.flush()
```

### CR-02 No-alias Config Rule
**Source:** `brave/config/settings.py` (lines 12-15, docstring)
**Apply to:** `TripAdvisorConfig`, ScoreConfig additions
```
CR-02: No Field(alias=...) on any field in any config class.
  Aliases let a bare env var shadow the prefixed key (secret-shadowing).
  All fields resolve ONLY from their exact prefixed env var name.
```

### Dashboard BFF Double-prefix Rule
**Source:** `dashboard/mocks/handlers/engine.ts` (line 10), `dashboard/mocks/handlers/atrativos.ts` (line 22)
**Apply to:** All new MSW handlers in `mar-ready.ts`
```typescript
// CRITICAL: double-prefix is mandatory — Pitfall 5.
// BASE = "http://localhost:3000/api/api/v1/atrativos"
//                               ^^^^ BFF mount   ^^^^ FastAPI router prefix
const BASE = "http://localhost:3000/api/api/v1/atrativos";
```

### API Fetch (dashboard)
**Source:** `dashboard/lib/dlq-api.ts` (lines 80-134)
**Apply to:** `dashboard/lib/mar-ready-api.ts`
```typescript
import { apiFetch } from "@/lib/api-client";

function qs(params: Record<string, string | number | undefined>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `brave/lanes/tripadvisor/scoring.py` | utility | transform | No log-curve scoring function exists in the codebase; scoring math is new. ScoreInput/ScoreConfig types are reused. |
| `brave/lanes/tripadvisor/geo.py` | utility | request-response | No Redis-backed geo cache utility exists; closest is engine.py Redis helpers but those are state management, not geo resolution. Pattern is clear enough from engine.py. |

---

## Metadata

**Analog search scope:** `brave/`, `alembic/`, `dashboard/`, `tests/`, `data/`
**Files scanned:** 20 source files read directly
**Pattern extraction date:** 2026-06-23

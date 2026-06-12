# Phase 3: Atrativos Lane (WhatsApp + Compliance) - Pattern Map

**Mapped:** 2026-06-12
**Files analyzed:** 19 new/modified files
**Analogs found:** 18 / 19

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `brave/lanes/atrativos/__init__.py` | config | — | `brave/lanes/destinos/__init__.py` | exact |
| `brave/lanes/atrativos/schemas.py` | model | transform | `brave/lanes/destinos/schemas.py` | exact |
| `brave/lanes/atrativos/discovery_agent.py` | service | request-response | `brave/lanes/destinos/desmembramento.py` | exact |
| `brave/lanes/atrativos/contact_finder_agent.py` | service | request-response | `brave/lanes/destinos/desmembramento.py` | role-match |
| `brave/lanes/atrativos/signal_agent.py` | service | request-response | `brave/lanes/destinos/desmembramento.py` | role-match |
| `brave/lanes/atrativos/whatsapp_agent.py` | service | event-driven | `brave/lanes/destinos/desmembramento.py` | partial-match |
| `brave/lanes/atrativos/state_machine.py` | service | CRUD | `brave/core/rio/routing.py` | role-match |
| `brave/clients/places.py` | service | request-response | `brave/clients/norteia_api.py` | role-match |
| `brave/clients/apify.py` | service | request-response | `brave/clients/norteia_api.py` | role-match |
| `brave/clients/whatsapp.py` | service | request-response | `brave/clients/norteia_api.py` | role-match |
| `brave/clients/null_whatsapp.py` | utility | — | `brave/clients/null_norteia_api.py` | exact |
| `brave/compliance/gate.py` | middleware | request-response | `brave/observability/cost_guard.py` | role-match |
| `brave/compliance/consent_log.py` | service | CRUD | `brave/observability/audit.py` | role-match |
| `brave/compliance/quality_rating.py` | utility | — | `brave/observability/cost_guard.py` | role-match |
| `brave/api/routers/atrativos_gate.py` | controller | request-response | `brave/api/routers/dlq.py` | exact |
| `brave/tasks/pipeline.py` (extend) | service | CRUD | `brave/tasks/pipeline.py` | exact |
| `brave/config/settings.py` (extend) | config | — | `brave/config/settings.py` | exact |
| `brave/core/models.py` (add ConsentLog) | model | CRUD | `brave/core/models.py` AuditLog | role-match |
| `tests/fakes/fake_apify.py` | test | — | `tests/fakes/fake_places.py` | exact |
| `tests/fakes/fake_whatsapp.py` | test | — | `tests/fakes/fake_places.py` | exact |
| `tests/fakes/fake_places.py` (extend) | test | — | `tests/fakes/fake_places.py` | exact |
| `tests/unit/compliance/test_gate.py` | test | — | `tests/integration/test_fastapi_endpoints.py` | role-match |
| Alembic migration 0004_consent_log.py | migration | CRUD | `alembic/versions/0003_partial_unique_mar_source_ref.py` | role-match |

---

## Pattern Assignments

### `brave/lanes/atrativos/schemas.py` (model, transform)

**Analog:** `brave/lanes/destinos/schemas.py`

**Imports pattern** (lines 1–10):
```python
from typing import Literal
from pydantic import BaseModel, Field
```

**Core schema pattern** (lines 21–73):
```python
class DestinoItem(BaseModel):
    nome: str = Field(..., min_length=2, description="...")
    tipo: Literal["distrito", "praia", "vila", "localidade", "ilha", "balneario", "outros"] = Field(...)
    posicionamento: str = Field(..., min_length=5, description="...")

class DesmembramentoResult(BaseModel):
    municipio_ibge: str = Field(..., pattern=r"^\d{7}$", description="...")
    municipio_nome: str = Field(..., description="...")
    destinos: list[DestinoItem] = Field(default_factory=list, description="...")
```

**Copy pattern for Phase 3:**
- `AtrativoResult` maps to `DestinoItem` — same field structure (nome, tipo, posicionamento) plus `place_id`, `municipio_ibge`, `municipio_nome`, `uf`, `origem_value`, `completude_value`
- `ContactResult` is a new sub-schema (phone_e164, website, ig_handle, email)
- `SignalResult` carries `business_status`, `weekday_text`, `atualidade_value`
- `ConversationExtractionResult` is per RESEARCH.md pattern (existe, funcionando, horarios, valor, confidence)
- Every schema uses `Field(..., description="...")` docstrings for instructor Mode.Tools compliance
- Literal types for constrained fields; `| None` for optional extraction outputs

---

### `brave/lanes/atrativos/discovery_agent.py` (service, request-response)

**Analog:** `brave/lanes/destinos/desmembramento.py`

**Imports pattern** (lines 1–29):
```python
from __future__ import annotations
from typing import TYPE_CHECKING, Any
from sqlalchemy.orm import Session
from brave.config.settings import ScoreConfig
from brave.core.nascente.service import store_raw
from brave.core.quarantine import quarantine_poison
from brave.lanes.atrativos.schemas import AtrativoResult

if TYPE_CHECKING:
    from brave.clients.base import LLMClientProtocol, PlacesClientProtocol
```

**Constructor pattern** (lines 127–138):
```python
class DesmembramentoAgent:
    def __init__(
        self,
        llm_client: "LLMClientProtocol",
        mtur_client: "MturClientProtocol",
        session: Session,
        config: ScoreConfig,
    ) -> None:
        self._llm_client = llm_client
        self._mtur_client = mtur_client
        self._session = session
        self._config = config
```

**Core produce pattern** (lines 141–230):
```python
async def produce(self, uf: str) -> None:
    municipalities = await self._mtur_client.fetch_municipalities(uf)
    for mun in municipalities:
        if mun.get("categoria") != "Oferta Principal":
            continue
        try:
            result = await self._llm_client.extract(
                prompt=prompt,
                schema=DesmembramentoResult,
                mode="tools",   # instructor Mode.TOOLS — D-09
            )
        except Exception as exc:
            quarantine_poison(
                session=self._session,
                nascente_id=None,
                task_name="brave.desmembramento",
                error=str(exc),
                payload={...},
            )
            continue   # Skip this item, never propagate
        for destino in result.destinos:
            store_raw(
                session=self._session,
                source="desm",
                source_ref=source_ref,
                entity_type="destination",
                uf=uf,
                payload={
                    "origem_value": 40.0,
                    "completude_value": ...,
                    "corroboracao_value": 0.0,
                    "atualidade_value": 0.0,
                    "validacao_humana_value": 0.0,
                    "canonical": {...},
                },
            )
```

**Key adaptations for DiscoveryAgent:**
- Replace `mtur_client.fetch_municipalities` with `places_client.text_search(query, uf)` per municipality
- Add parent destino resolution from Mar before `store_raw` (hard precondition D-03): query `MarRecord` by UF + municipio_ibge; if None → log + `quarantine_poison(…, error="parent_destino_absent")` + `continue`
- `place_id` persisted inside payload dict as cache key (D-04); never used as `source_ref`
- `source_ref` format: `"places:{uf}:{place_id}"` (stable, unique, idempotent)
- `entity_type="attraction"` (not `"destination"`)
- `source="places_discovery"` in `store_raw`
- Completude computed from field coverage of `AtrativoResult`

---

### `brave/lanes/atrativos/contact_finder_agent.py` (service, request-response)

**Analog:** `brave/lanes/destinos/desmembramento.py`

**Pattern (same class shape as DesmembramentoAgent):**
```python
class ContactFinderAgent:
    def __init__(
        self,
        places_client: "PlacesClientProtocol",
        session: Session,
    ) -> None:
        self._places_client = places_client
        self._session = session

    async def run(self, rio: RioRecord) -> None:
        """Advance sub_state from 'discovered' to 'contacts_found'.
        
        Idempotency guard — same as FSM pattern:
        if rio.sub_state != "discovered":
            return
        """
```

**Sub-state write pattern** (from RESEARCH.md Pattern 1):
```python
from sqlalchemy.orm.attributes import flag_modified
normalized = dict(rio.normalized or {})
normalized["contacts"] = contacts_dict
rio.normalized = normalized
flag_modified(rio, "normalized")
rio.sub_state = "contacts_found"

write_audit(
    session=session,
    action="sub_state_advanced",
    entity_type="attraction",
    record_id=rio.id,
    before_state={"sub_state": "discovered"},
    after_state={"sub_state": "contacts_found"},
    actor="contact_finder_agent",
)
session.flush()
```

**Key rule:** Always reassign the JSON column AND call `flag_modified` — SQLAlchemy does not auto-track in-place JSONB mutations (Phase 2 Pitfall 3 / T-02-06-04 lesson).

---

### `brave/lanes/atrativos/signal_agent.py` (service, request-response)

**Analog:** `brave/lanes/destinos/desmembramento.py` (same class shape)

**Hard descarte guard** (D-05, from RESEARCH.md):
```python
# Before scoring — if closed, route to descarte immediately, skip scoring
if place_details.get("business_status") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
    rio.routing = "descarte"
    rio.dlq_reason = "closed_place"
    rio.sub_state = None
    write_audit(session, action="hard_descarte", ..., actor="signal_agent")
    session.flush()
    return

# Apify is best-effort — failure degrades signal, never fails the record
try:
    ig_data = await self._apify_client.scrape_ig(ig_handle)
except Exception:
    ig_data = {}   # graceful degradation — non-blocking
```

**flag_modified pattern** (same as ContactFinderAgent):
```python
normalized = dict(rio.normalized or {})
normalized["signal"] = signal_dict
rio.normalized = normalized
flag_modified(rio, "normalized")
rio.sub_state = "signals_gathered"
session.flush()
```

---

### `brave/lanes/atrativos/state_machine.py` (service, CRUD)

**Analog:** `brave/core/rio/routing.py`

**Idempotent FSM task dispatch pattern** (from RESEARCH.md Pattern 1):
```python
def advance_sub_state(session: Session, rio: RioRecord, expected_state: str, next_state: str) -> bool:
    """Guard + advance. Returns True if advanced, False if already past (idempotent).
    
    SELECT FOR UPDATE SKIP LOCKED prevents two workers racing on the same record.
    """
    if rio.sub_state != expected_state:
        return False  # Already advanced — safe replay
    # ... do work ...
    rio.sub_state = next_state
    write_audit(
        session=session,
        action="sub_state_advanced",
        entity_type="attraction",
        record_id=rio.id,
        before_state={"sub_state": expected_state},
        after_state={"sub_state": next_state},
        actor="state_machine",
    )
    session.flush()
    return True
```

**Celery task idempotency short-circuit pattern** (from pipeline.py lines 125–130):
```python
# Inside each Celery task body:
existing = session.scalar(select(RioRecord).where(RioRecord.id == rio_uuid))
if existing is None:
    raise PermanentError(f"RioRecord {rio_id} not found")
if existing.sub_state != "discovered":
    return   # Already advanced — idempotent no-op
```

---

### `brave/lanes/atrativos/whatsapp_agent.py` (service, event-driven)

**Analog:** No close analog in codebase — LangGraph is new. Use RESEARCH.md Pattern 2 exclusively.

**Imports pattern** (from RESEARCH.md):
```python
from typing import TypedDict
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import asyncio
```

**Graph state pattern** (RESEARCH.md lines 539–553):
```python
class ConversationState(TypedDict):
    rio_id: str
    messages: list[dict]
    extraction: dict | None
    opted_out: bool
    window_open: bool
    last_inbound_at: str | None
    turns: int
    max_turns: int
    outreach_template: str
```

**asyncio.run wrapper for Celery** (RESEARCH.md Pitfall 5, lines 648–656):
```python
@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True, ...)
def outreach_task(self, rio_id: str) -> None:
    async def _run():
        saver = await AsyncPostgresSaver.from_conn_string(db_url)
        await saver.setup()
        graph = whatsapp_agent_graph.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": f"atrativo:{rio_id}"}}
        await graph.ainvoke({"rio_id": rio_id, ...}, config=config)
    asyncio.run(_run())
```

**thread_id keying:** `f"atrativo:{rio_id}"` — keyed by RioRecord UUID, never phone number.

**opt-out keyword set:**
```python
OPT_OUT_KEYWORDS = {"SAIR", "PARAR", "CANCELAR", "REMOVER", "STOP", "NÃO"}
```

---

### `brave/clients/places.py` (service, request-response)

**Analog:** `brave/clients/norteia_api.py`

**Imports pattern** (lines 1–33):
```python
from typing import Any
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
```

**Constructor + context-manager pattern** (lines 41–79):
```python
class NorteiaApiClient:
    def __init__(
        self,
        base_url: str | Any,
        service_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = str(base_url).rstrip("/")
        self._service_token = service_token
        self._injected_client = http_client
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NorteiaApiClient": ...
    async def __aexit__(self, *args: Any) -> None: ...
```

**Tenacity retry pattern** (lines 101–118):
```python
@retry(
    retry=retry_if_exception(_is_5xx),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def _attempt() -> dict[str, Any]:
    response = await client.post(...)
    response.raise_for_status()
    return response.json()
```

**Key adaptations for RealPlacesClient:**
- Uses `google-maps-places` 0.9.x SDK instead of raw httpx (SDK wraps the HTTP layer)
- `text_search(query, uf)` → SDK `places_client.search_text(query, location_bias=uf_region)`
- `place_details(place_id)` → SDK place details call requesting `business_status,weekday_text,reviews`
- Protocol compliance: same `PlacesClientProtocol` shape as `tests/fakes/fake_places.py`
- `AppConfig.run_real_externals` guard: raise `RuntimeError` if key not set and real externals requested

---

### `brave/clients/apify.py` (service, request-response)

**Analog:** `brave/clients/norteia_api.py`

**Pattern:** Same httpx + tenacity retry structure as `norteia_api.py`, with:
- `apify-client` 3.0.x SDK instead of raw httpx
- `scrape_ig(handle)` → `ApifyClientProtocol.scrape_ig` implementation
- Best-effort: 429/timeout returns `{}` (degraded, non-blocking) instead of raising

---

### `brave/clients/whatsapp.py` (service, request-response)

**Analog:** `brave/clients/norteia_api.py`

**Pattern:** Same structure, using `twilio` 9.10.x SDK:
```python
class TwilioWhatsAppClient:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        messaging_service_sid: str | None = None,
    ) -> None: ...

    async def send_template(
        self,
        to: str,
        template: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        # Uses twilio SDK MessagingServiceSid path
        # Returns {"message_sid": ..., "status": ...}
```

**CRITICAL:** `send_template` is NEVER called directly. It is always called through `brave/compliance/gate.py send_path_gate(...)`. Enforce this architecturally — the gate function is the only caller.

---

### `brave/clients/null_whatsapp.py` (utility)

**Analog:** `brave/clients/null_norteia_api.py`

**Exact copy pattern** (entire file, lines 1–27):
```python
"""In-package offline WhatsApp client stub (production-safe).

Used when AppConfig.run_real_externals is False. Records sends without transmitting.
Lives in brave/ (NOT tests/) so production code never imports from the test tree.
"""
from __future__ import annotations
from typing import Any
import uuid

class NullWhatsAppClient:
    """No-network WhatsAppClient (structural protocol match)."""

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []

    async def send_template(
        self,
        to: str,
        template: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        record = {"to": to, "template": template, "params": params, "sid": str(uuid.uuid4())}
        self.sent_messages.append(record)
        return {"message_sid": record["sid"], "status": "queued"}
```

---

### `brave/compliance/gate.py` (middleware, request-response)

**Analog:** `brave/observability/cost_guard.py`

**Cost guard pattern to mirror** (lines 47–67):
```python
def pre_dispatch_check(redis_client: Redis, config: LLMConfig) -> None:
    """Check BEFORE dispatching. Raises CostGuardError if budget exceeded."""
    key = _daily_key()
    raw = redis_client.get(key)
    current = float(raw) if raw is not None else 0.0
    if current >= config.usd_daily_budget:
        raise CostGuardError("Daily LLM budget exceeded...")
```

**Gate function signature pattern:**
```python
class ComplianceError(Exception):
    """Raised when any D-11 compliance gate condition fails.
    
    Always blocks the send — never advisory.
    """

def send_path_gate(
    session: Session,
    redis_client: Redis,
    rio: RioRecord,
    contact_phone: str,      # E.164 format
    template_name: str,
    params: dict[str, Any],
    settings: "WhatsAppConfig",
) -> None:
    """Synchronous D-11 compliance gate. Raises ComplianceError on any failure.
    
    Called immediately before WhatsAppClientProtocol.send_template.
    Pure code — no LLM, no network — fully offline-testable.
    
    Checks (in order):
      1. legal basis recorded    → consent_log has row for contact_phone
      2. norteia identified      → "Norteia" in params.get("body", "")
      3. opt-out honored         → consent_log.opted_out is False
      4. approved template       → template_name in settings.approved_templates
      5. 24h window              → template type vs. window_open state
      6. human gate approved     → rio.sub_state == "whatsapp_in_progress"
      7. ramp not exceeded       → check_and_increment_ramp(redis, cap, uf)
      8. quality not red         → Redis flag "wa:quality_red" not set
    """
```

**Ramp counter pattern** (from RESEARCH.md Pattern 3, lines 381–396 — CR-04 hardening):
```python
def check_and_increment_ramp(redis_client: Redis, cap: int, uf: str | None = None) -> None:
    from datetime import datetime, timezone
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"wa:ramp:{date_key}" if uf is None else f"wa:ramp:{uf}:{date_key}"
    
    count = redis_client.incr(key)       # atomic — same as cost_guard.record_spend
    if count == 1:
        redis_client.expireat(key, _next_utc_midnight())   # crash-safe TTL on first write
    if count > cap:
        redis_client.decr(key)           # undo the reserve
        raise ComplianceError(f"Ramp cap {cap} exceeded for {date_key}")
```

**TTL helper pattern** (mirrors `cost_guard._seconds_until_midnight`, lines 40–44):
```python
def _seconds_until_midnight() -> int:
    now = time.time()
    tomorrow = (int(now) // 86400 + 1) * 86400
    return max(1, int(tomorrow - now))
```

---

### `brave/compliance/consent_log.py` (service, CRUD)

**Analog:** `brave/observability/audit.py`

**write_audit signature pattern** (lines 22–74):
```python
def write_audit(
    session: Session,
    action: str,
    entity_type: str | None = None,
    record_id: uuid.UUID | None = None,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    actor: str = "pipeline",
) -> AuditLog:
    audit = AuditLog(id=uuid.uuid4(), action=action, ...)
    session.add(audit)
    session.flush()
    logger.info("audit_event", **log_data)
    return audit
```

**Copy pattern for consent_log.py:**
```python
def write_consent_record(
    session: Session,
    phone_e164: str,
    rio_id: uuid.UUID,
    legal_basis: str,
    norteia_identified: bool,
    purpose: str = "business_validation",
) -> ConsentLog:
    """Write or update a LGPD consent record for a contact."""
    # Upsert: if record exists for phone_e164+rio_id, update last_contact_at
    record = ConsentLog(id=uuid.uuid4(), phone_e164=phone_e164, rio_id=rio_id, ...)
    session.add(record)
    session.flush()
    logger.info("consent_record_created", phone_prefix=phone_e164[:5], ...)
    return record

def is_opted_out(session: Session, phone_e164: str) -> bool:
    """Return True if this phone number has opted out."""
    from sqlalchemy import select
    row = session.scalar(
        select(ConsentLog)
        .where(ConsentLog.phone_e164 == phone_e164)
        .where(ConsentLog.opted_out.is_(True))
    )
    return row is not None

def record_opt_out(
    session: Session,
    phone_e164: str,
    keyword: str,
) -> None:
    """Mark a contact as opted out (triggered by opt-out keyword in recv_reply node)."""
```

**flag_modified requirement:** ConsentLog has a nullable `metadata` JSON column; any mutation requires `flag_modified(record, "metadata")` before flush.

---

### `brave/compliance/quality_rating.py` (utility)

**Analog:** `brave/observability/cost_guard.py`

**Pattern** (mirrors `pre_dispatch_check` and `record_spend`):
```python
QUALITY_RED_KEY = "wa:quality_red"

def is_quality_red(redis_client: Redis) -> bool:
    """Return True if quality rating is Red (pause flag set)."""
    return redis_client.exists(QUALITY_RED_KEY) > 0

def set_quality_flag(redis_client: Redis, rating: str) -> None:
    """Set or clear the quality-red flag based on Meta/Twilio callback."""
    if rating == "RED":
        redis_client.set(QUALITY_RED_KEY, "1")   # no TTL — cleared on GREEN
    elif rating in ("GREEN", "YELLOW"):
        redis_client.delete(QUALITY_RED_KEY)
```

---

### `brave/api/routers/atrativos_gate.py` (controller, request-response)

**Analog:** `brave/api/routers/dlq.py` — **exact structural match**

**Imports pattern** (lines 1–22):
```python
import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_steward_config
from brave.config.settings import StewardConfig
from brave.core.models import RioRecord
from brave.observability.audit import write_audit

router = APIRouter()
```

**require_steward dependency** (lines 25–47) — copy verbatim from `dlq.py`:
```python
def require_steward(
    x_steward_secret: str | None = Header(None, alias="X-Steward-Secret"),
    steward_config: StewardConfig = Depends(get_steward_config),
) -> None:
    expected = steward_config.secret
    if not x_steward_secret or not expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="X-Steward-Secret header required")
    if not hmac.compare_digest(x_steward_secret, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid X-Steward-Secret")
```

**List queue endpoint** (mirrors `list_dlq`, lines 50–83):
```python
@router.get("/api/v1/atrativos/gate")
def list_whatsapp_gate_queue(
    uf: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List atrativos awaiting human WhatsApp gate approval (sub_state=aguardando_consulta_whatsapp)."""
    query = select(RioRecord).where(
        RioRecord.entity_type == "attraction",
        RioRecord.sub_state == "aguardando_consulta_whatsapp",
    )
    if uf:
        query = query.where(RioRecord.uf == uf)
    query = query.limit(limit)
    ...
```

**Approve endpoint** (mirrors `validate_dlq_record`, lines 127–193):
```python
@router.patch(
    "/api/v1/atrativos/gate/{rio_id}/approve",
    status_code=202,
    dependencies=[Depends(require_steward)],
)
def approve_whatsapp_gate(
    rio_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Approve: flip sub_state → whatsapp_in_progress, dispatch outreach_task (D-06)."""
    rio = db.get(RioRecord, rio_id)
    if rio is None:
        raise HTTPException(status_code=404, detail="RioRecord not found")

    before_state = {"sub_state": rio.sub_state}
    rio.sub_state = "whatsapp_in_progress"

    # Dispatch Celery task — sync fallback in tests/dev (mirrors dlq.py pattern)
    try:
        from brave.tasks.pipeline import outreach_task
        outreach_task.delay(str(rio_id))
    except Exception:
        # Sync fallback: no Celery broker in tests/dev
        pass

    write_audit(
        session=db,
        action="whatsapp_gate_approved",
        entity_type=rio.entity_type,
        record_id=rio.id,
        before_state=before_state,
        after_state={"sub_state": "whatsapp_in_progress"},
        actor="steward",
    )
    return {"status": "accepted", "rio_id": str(rio_id)}
```

**Reject endpoint** (mirrors `descarte_dlq_record`, lines 270–301):
```python
@router.patch(
    "/api/v1/atrativos/gate/{rio_id}/reject",
    status_code=200,
    dependencies=[Depends(require_steward)],
)
def reject_whatsapp_gate(rio_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    rio.sub_state = None
    rio.routing = "dlq"
    rio.dlq_reason = "steward_rejected_gate"
    write_audit(session=db, action="whatsapp_gate_rejected", ..., actor="steward")
    return {"status": "ok", "routing": "dlq", "rio_id": str(rio_id)}
```

**Quality rating webhook** (new endpoint, no analog — pure FastAPI pattern):
```python
@router.post("/api/v1/atrativos/whatsapp/quality-rating-webhook")
def quality_rating_webhook(
    payload: dict,
    db: Session = Depends(get_db),
) -> dict:
    """Receive quality rating event from Twilio/Meta; set Redis flag."""
    # Authenticate via Twilio signature (Twilio SDK validate_signature)
    rating = payload.get("quality_rating", "GREEN").upper()
    from brave.api.deps import get_redis
    from brave.compliance.quality_rating import set_quality_flag
    set_quality_flag(get_redis(), rating)
    write_audit(session=db, action="quality_rating_updated", ...)
    return {"status": "ok", "rating": rating}
```

**Inbound reply webhook** (new endpoint — routes to Celery):
```python
@router.post("/api/v1/atrativos/whatsapp/inbound")
def inbound_whatsapp_reply(payload: dict, db: Session = Depends(get_db)) -> dict:
    """n8n/Twilio relays inbound message here. Dispatches resume_conversation_task."""
    from brave.tasks.pipeline import resume_conversation_task
    from_number = payload.get("from", "")
    message_text = payload.get("body", "")
    # Lookup rio_id from consent_log by phone
    from brave.compliance.consent_log import lookup_rio_id_by_phone
    rio_id = lookup_rio_id_by_phone(db, from_number)
    if rio_id is None:
        return {"status": "ignored", "reason": "no_active_conversation"}
    try:
        resume_conversation_task.delay(str(rio_id), message_text)
    except Exception:
        pass  # no broker — handled by test environment
    return {"status": "accepted"}
```

---

### `brave/tasks/pipeline.py` (extend) (service, CRUD)

**Analog:** `brave/tasks/pipeline.py` — extend, do not rewrite

**Existing `push_destination_task` pattern to mirror** (lines 354–444) for `push_attraction_task`:
```python
@shared_task(
    bind=True,
    max_retries=3,
    name="brave.push_attraction",     # different task name
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def push_attraction_task(self, rio_id: str) -> None:
    """Promote a validated atrativo to Mar and push to norteia-api (D-10).
    
    Mirror of push_destination_task — always calls push_attraction, never push_destination.
    """
    # Step 4: Push to norteia-api — always push_attraction (D-10)
    async def _push() -> dict[str, Any]:
        if isinstance(api_client, NorteiaApiClient):
            async with api_client as client:
                return await client.push_attraction(payload)
        else:
            return await api_client.push_attraction(payload)

    asyncio.run(_push())
```

**New FSM tasks** (add after existing tasks, same `@shared_task` decorator shape):
```python
@shared_task(
    bind=True,
    max_retries=3,
    name="brave.discover_atrativo",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=600,   # Places API can be slow
)
def discover_atrativo_task(self, uf: str) -> None:
    """Fan-out discovery for one UF (sub_state → discovered)."""

@shared_task(
    bind=True,
    max_retries=3,
    name="brave.find_contacts",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def find_contacts_task(self, rio_id: str) -> None:
    """Advance one RioRecord from discovered → contacts_found."""

@shared_task(
    bind=True,
    max_retries=3,
    name="brave.gather_signals",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def gather_signals_task(self, rio_id: str) -> None:
    """Advance one RioRecord from contacts_found → signals_gathered → score."""

@shared_task(
    bind=True,
    max_retries=3,
    name="brave.outreach",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=900,   # multi-turn conversation can be slow
)
def outreach_task(self, rio_id: str) -> None:
    """Send WhatsApp outreach (gate must have approved). Uses asyncio.run + LangGraph."""

@shared_task(
    bind=True,
    max_retries=3,
    name="brave.resume_conversation",
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def resume_conversation_task(self, rio_id: str, reply_text: str) -> None:
    """Resume LangGraph conversation on inbound reply (n8n thin transport → FastAPI → here)."""
```

**`_get_session()` pattern** (lines 62–72) — copy verbatim into each new task (no change needed):
```python
def _get_session() -> tuple[Session, Any]:
    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        raise PermanentError("BRAVE_DB_URL not set — cannot create DB session")
    engine = create_engine(db_url, echo=False)
    SessionFactory = sessionmaker(bind=engine)
    return SessionFactory(), engine
```

**Error handling pattern** (lines 132–170) — all new tasks use the same try/except structure:
```python
try:
    # ... task body ...
    session.commit()
except PermanentError as exc:
    session.rollback()
    # quarantine_poison for PermanentError
except Exception as exc:
    session.rollback()
    try:
        raise self.retry(exc=exc, max_retries=3)
    except self.MaxRetriesExceededError:
        quarantine_poison(...)
finally:
    session.close()
    engine.dispose()
```

---

### `brave/config/settings.py` (extend) (config)

**Analog:** `brave/config/settings.py` — extend after `StewardConfig`

**Existing pattern to follow** (lines 107–121 — StewardConfig shape):
```python
class StewardConfig(BaseSettings):
    secret: str = Field(default="", description="Shared secret for X-Steward-Secret header")
    model_config = SettingsConfigDict(env_prefix="BRAVE_STEWARD_")
```

**Copy pattern for new config classes:**
```python
class WhatsAppConfig(BaseSettings):
    """WhatsApp BSP configuration (Twilio launch path, D-09).
    
    No env-var aliases (CR-02): each field resolves from its exact BRAVE_WA_ prefixed name only.
    """
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    from_number: str = Field(default="")             # E.164 format
    messaging_service_sid: str = Field(default="")   # optional Twilio MessagingServiceSid
    approved_templates: list[str] = Field(
        default_factory=list,
        description="Allowlist of pre-registered template names. ComplianceError on mismatch.",
    )
    model_config = SettingsConfigDict(env_prefix="BRAVE_WA_")

class RampConfig(BaseSettings):
    """WhatsApp volume ramp configuration (D-07).
    
    Global portfolio-wide daily cap is the primary constraint (Oct 2025 portfolio limits).
    Per-UF cap is an optional additional layer.
    """
    daily_cap: int = Field(
        default=50,
        description="Max outreach sends per UTC day across the whole portfolio (BRAVE_WA_RAMP_DAILY_CAP).",
    )
    quality_pause_threshold: str = Field(
        default="RED",
        description="Quality rating level that triggers auto-pause (RED | YELLOW).",
    )
    model_config = SettingsConfigDict(env_prefix="BRAVE_WA_RAMP_")
```

**AppConfig extension** (lines 124–146) — add `whatsapp` and `ramp` fields:
```python
class AppConfig(BaseSettings):
    score: ScoreConfig = Field(default_factory=ScoreConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)  # NEW
    ramp: RampConfig = Field(default_factory=RampConfig)               # NEW
    run_real_externals: bool = False
    model_config = SettingsConfigDict(env_prefix="")
```

---

### `brave/core/models.py` — add `ConsentLog` (model, CRUD)

**Analog:** `AuditLog` model (lines 267–292) — same Base, same column style

**Copy pattern:**
```python
class ConsentLog(Base):
    """LGPD consent and opt-out log per contact (COMP-01, D-11).
    
    Separate from audit_log: consent_log serves real-time suppression lookups
    (is_opted_out before every send); audit_log is the historical trail.
    Indexed on phone_e164 for fast suppression lookups.
    """
    __tablename__ = "consent_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_e164: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    rio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rio_records.id"), nullable=False)
    legal_basis: Mapped[str] = mapped_column(String(128), nullable=False)
    norteia_identified: Mapped[bool] = mapped_column(nullable=False)
    opted_out: Mapped[bool] = mapped_column(nullable=False, default=False)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opted_out_keyword: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_contact_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_contact_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    purpose: Mapped[str] = mapped_column(String(128), nullable=False, default="business_validation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    rio: Mapped["RioRecord"] = relationship("RioRecord", foreign_keys=[rio_id])
```

**flag_modified rule:** Mutations to `opted_out` must use direct column reassignment (not JSON), so `flag_modified` is not needed for simple bool columns. It IS required for any future JSON metadata column added to ConsentLog.

---

### `tests/fakes/fake_apify.py` (test)

**Analog:** `tests/fakes/fake_places.py` — **exact structural match**

**Copy pattern** (entire `FakePlacesClient` structure, lines 1–76):
```python
"""Fake Apify client for offline testing.

FakeApifyClient implements ApifyClientProtocol (structural typing, D-09).
"""
from typing import Any
from brave.clients.base import ApifyClientProtocol

class FakeApifyClient:
    def __init__(
        self,
        fixture_data: dict[str, dict[str, Any]] | None = None,
        raise_on_call: Exception | None = None,
    ) -> None:
        self._fixture_data = fixture_data or {}
        self._raise_on_call = raise_on_call
        self.scrape_ig_calls: list[str] = []

    async def scrape_ig(self, handle: str) -> dict[str, Any]:
        self.scrape_ig_calls.append(handle)
        if self._raise_on_call is not None:
            raise self._raise_on_call
        return self._fixture_data.get(handle, {})

# Structural type check
def _check_protocol_compliance() -> None:
    _client: ApifyClientProtocol = FakeApifyClient()  # noqa: F841
```

---

### `tests/fakes/fake_whatsapp.py` (test)

**Analog:** `tests/fakes/fake_places.py` / `tests/fakes/fake_norteia_api.py`

**Copy pattern:**
```python
"""Fake WhatsApp client for offline testing.

FakeWhatsAppClient implements WhatsAppClientProtocol (structural typing, D-09).
Records sends — never transmits. Used by compliance gate tests.
"""
from typing import Any
from brave.clients.base import WhatsAppClientProtocol

class FakeWhatsAppClient:
    def __init__(
        self,
        should_fail: bool = False,
    ) -> None:
        self._should_fail = should_fail
        self.sent_messages: list[dict[str, Any]] = []

    async def send_template(
        self,
        to: str,
        template: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if self._should_fail:
            raise RuntimeError("FakeWhatsAppClient: simulated send failure")
        record = {"to": to, "template": template, "params": params}
        self.sent_messages.append(record)
        return {"message_sid": "fake-sid-001", "status": "sent"}

def _check_protocol_compliance() -> None:
    _client: WhatsAppClientProtocol = FakeWhatsAppClient()  # noqa: F841
```

---

### `tests/fakes/fake_places.py` (extend) (test)

**Existing file:** `tests/fakes/fake_places.py` (already has `text_search` + `place_details`)

**Extension needed:** Add fixture shapes for `business_status` fields used by SignalAgent:
```python
# Default signal fixture (add as class constant or default fixture_details entry)
SIGNAL_FIXTURE_OPEN = {
    "place_id": "ChIJtest001",
    "business_status": "OPERATIONAL",
    "weekday_text": ["Monday: 9:00 AM – 5:00 PM", ...],
    "reviews": [
        {"publishTime": "2026-06-01T12:00:00Z", "rating": 5, "text": "Ótimo lugar!"}
    ],
}

SIGNAL_FIXTURE_CLOSED = {
    "place_id": "ChIJtest002",
    "business_status": "CLOSED_PERMANENTLY",
    "weekday_text": [],
    "reviews": [],
}
```

---

### `tests/unit/compliance/test_gate.py` (test)

**Analog:** `tests/integration/test_fastapi_endpoints.py` (auth-gate tests, lines 57–80)

**Test-per-condition pattern** (8 conditions from D-11):
```python
"""Unit tests for the D-11 compliance send-path gate.

Every gate condition has its own test proving it BLOCKS (raises ComplianceError).
Tests run 100% offline — no real DB, Redis, or WhatsApp.
Uses fakeredis for Redis conditions. Uses in-memory session for DB conditions.

Minimum 8 tests (one per D-11 condition):
  test_gate_blocks_when_no_legal_basis
  test_gate_blocks_when_norteia_not_in_message
  test_gate_blocks_when_opted_out
  test_gate_blocks_when_template_not_approved
  test_gate_blocks_when_window_closed_and_marketing_template
  test_gate_blocks_when_sub_state_not_whatsapp_in_progress
  test_gate_blocks_when_ramp_exceeded
  test_gate_blocks_when_quality_red
  test_gate_passes_when_all_conditions_met   # happy path
"""
import fakeredis
import pytest

from brave.compliance.gate import ComplianceError, send_path_gate


def test_gate_blocks_when_ramp_exceeded(db_session, ...) -> None:
    """Ramp cap=1 — after first send, second call raises ComplianceError."""
    redis = fakeredis.FakeRedis()
    # Pre-seed ramp counter to cap
    ...
    with pytest.raises(ComplianceError, match="Ramp cap"):
        send_path_gate(...)
```

---

### Alembic migration `0004_consent_log.py` (migration, CRUD)

**Analog:** `alembic/versions/0003_partial_unique_mar_source_ref.py`

**Migration header pattern** (lines 1–23):
```python
"""Add consent_log table for LGPD compliance (COMP-01, D-11).

Separate from audit_log: serves real-time suppression lookups.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-12
"""
from typing import Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None
```

**`op.create_table` pattern** (mirrors `0001_init_nascente_rio_mar.py` lines 36–60):
```python
def upgrade() -> None:
    op.create_table(
        "consent_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("phone_e164", sa.String(32), nullable=False),
        sa.Column("rio_id", UUID(as_uuid=True), nullable=False),
        sa.Column("legal_basis", sa.String(128), nullable=False),
        sa.Column("norteia_identified", sa.Boolean, nullable=False),
        sa.Column("opted_out", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opted_out_keyword", sa.String(32), nullable=True),
        sa.Column("first_contact_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("purpose", sa.String(128), nullable=False, server_default="business_validation"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["rio_id"], ["rio_records.id"], name="fk_consent_log_rio_id"),
    )
    op.create_index("ix_consent_log_phone_e164", "consent_log", ["phone_e164"])
    # Note: If adding a non-concurrent B-tree index in a migration transaction,
    # this is fine (small table at migration time). Do NOT use CONCURRENTLY here.

def downgrade() -> None:
    op.drop_index("ix_consent_log_phone_e164", table_name="consent_log")
    op.drop_table("consent_log")
```

**CONCURRENTLY rule:** Do NOT use `CREATE INDEX CONCURRENTLY` inside Alembic's transaction block (Phase 2 lesson). For `consent_log`, a standard B-tree on `phone_e164` is correct; it is not a vector index and CONCURRENTLY is not needed.

---

## Shared Patterns

### Authentication (require_steward)
**Source:** `brave/api/routers/dlq.py` lines 25–47
**Apply to:** All mutating endpoints in `atrativos_gate.py` (approve, reject, quality-rating-webhook)
```python
def require_steward(
    x_steward_secret: str | None = Header(None, alias="X-Steward-Secret"),
    steward_config: StewardConfig = Depends(get_steward_config),
) -> None:
    expected = steward_config.secret
    if not x_steward_secret or not expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="X-Steward-Secret header required")
    if not hmac.compare_digest(x_steward_secret, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid X-Steward-Secret")
```

### Celery Task Error Handling
**Source:** `brave/tasks/pipeline.py` lines 132–170
**Apply to:** All new Celery tasks (`discover_atrativo_task`, `find_contacts_task`, `gather_signals_task`, `outreach_task`, `resume_conversation_task`, `push_attraction_task`)
```python
try:
    # task body
    session.commit()
except PermanentError as exc:
    session.rollback()
    quarantine_poison(...)
except Exception as exc:
    session.rollback()
    try:
        raise self.retry(exc=exc, max_retries=3)
    except self.MaxRetriesExceededError:
        quarantine_poison(...)
finally:
    session.close()
    engine.dispose()
```

### Audit Logging
**Source:** `brave/observability/audit.py` lines 22–74
**Apply to:** Every sub-state transition, every gate approve/reject, every compliance event
```python
write_audit(
    session=session,
    action="sub_state_advanced",        # or "whatsapp_gate_approved", "consent_recorded", etc.
    entity_type="attraction",
    record_id=rio.id,
    before_state={"sub_state": "discovered"},
    after_state={"sub_state": "contacts_found"},
    actor="contact_finder_agent",       # or "steward", "compliance_gate"
)
```

### JSON Column Mutation (flag_modified)
**Source:** `brave/api/routers/dlq.py` lines 155–161 (Phase 2 T-02-06-04 lesson)
**Apply to:** Every in-place mutation of `RioRecord.normalized` or `ConsentLog` JSON columns
```python
from sqlalchemy.orm.attributes import flag_modified

normalized = dict(rio.normalized or {})
normalized["contacts"] = contacts_dict      # mutate the copy
rio.normalized = normalized                 # reassign the column
flag_modified(rio, "normalized")            # tell SQLAlchemy it changed
session.flush()
```

### Celery/asyncio.run Bridge
**Source:** `brave/tasks/pipeline.py` lines 289–304 (`push_mar` `asyncio.run(_push())`)
**Apply to:** `outreach_task`, `resume_conversation_task`, `push_attraction_task`
```python
async def _run():
    # all async work here
    ...

asyncio.run(_run())
```

### Real-vs-Null Client Selection
**Source:** `brave/tasks/pipeline.py` lines 275–285
**Apply to:** All new Celery tasks that call external clients
```python
app_config = AppConfig()
if app_config.run_real_externals:
    client = RealPlacesClient(api_key=os.environ.get("BRAVE_PLACES_API_KEY", ""))
else:
    from tests.fakes.fake_places import FakePlacesClient
    client = FakePlacesClient()    # use null/fake for offline suite
```
Note: Production tasks use `NullWhatsAppClient` (in `brave/clients/`), not the test fake. Test suites use `FakeWhatsAppClient` (in `tests/fakes/`).

### Protocol Structural Compliance Check
**Source:** `tests/fakes/fake_places.py` lines 72–75
**Apply to:** Bottom of every fake client file
```python
def _check_protocol_compliance() -> None:
    """Compile-time structural typing assertion (not called at runtime)."""
    _client: SomeProtocol = FakeSomeClient()  # noqa: F841
```

### Pydantic-settings Config (CR-02 no-alias rule)
**Source:** `brave/config/settings.py` lines 58–78 (`LLMConfig`)
**Apply to:** `WhatsAppConfig`, `RampConfig`
```python
# No Field(alias=...) on any secret field — resolves ONLY from the prefixed name.
# This prevents bare env-var shadowing (CR-02).
twilio_auth_token: str = Field(default="")
# env key: BRAVE_WA_TWILIO_AUTH_TOKEN (prefix + field name, no alias)
model_config = SettingsConfigDict(env_prefix="BRAVE_WA_", populate_by_name=True)
```

---

## No Analog Found

Files with no close match in the codebase (planner should use RESEARCH.md patterns instead):

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `brave/lanes/atrativos/whatsapp_agent.py` | service | event-driven | No LangGraph code exists yet; first event-driven graph in the project. Use RESEARCH.md Pattern 2 (LangGraph + AsyncPostgresSaver) exclusively. |

---

## Metadata

**Analog search scope:** `brave/` (all subdirectories), `tests/fakes/`, `tests/integration/`, `tests/unit/`, `alembic/versions/`
**Files scanned:** 19 source files read in full
**Pattern extraction date:** 2026-06-12

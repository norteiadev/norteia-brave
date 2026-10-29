"""TripAdvisor session injection and status endpoints (TA-10, TA-11).

Endpoints:
  POST /api/v1/tripadvisor/session        — inject operator session + canary gate
  GET  /api/v1/tripadvisor/session/status — read current session status
  GET  /api/v1/tripadvisor/sweep/progress — bulk national sweep progress
  POST /api/v1/tripadvisor/backfill       — start/resume the backfill engine run
  GET  /api/v1/tripadvisor/backfill/progress — backfill counters + pending counts

The injection endpoint accepts a DataDome session (cookies + queryIds) captured
by the operator's real browser via DevTools "Copy as cURL". It writes the session
to Redis for the worker to consume, then runs a synchronous canary validation
through the real httpx path to fail-fast on stale sessions.

Security:
  T-12-02-01: Cookie values are NEVER logged — audit records only cookie_count + query_ids keys.
  T-12-02-02: Both endpoints require require_steward_or_bearer auth.
  T-12-02-03: 64 KB content-length check before Pydantic parse.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from redis import Redis
from sqlalchemy.orm import Session

from brave.api.deps import get_db, get_redis, require_steward_or_bearer
from brave.domains.tripadvisor import sweep_progress as sweep_progress_state
from brave.domains.tripadvisor.client import BRAVE_TA_SESSION_KEY, SessionExpiredError

logger = structlog.get_logger(__name__)
router = APIRouter()

# Redis key for the bootstrap marker (set by sweep on SessionMissingError)
_TA_NEEDS_BOOTSTRAP_KEY: str = "brave:ta:needs_bootstrap"
# Mirrors brave.tasks.pipeline: the keepalive's consecutive-failure streak.
_TA_KEEPALIVE_FAILURES_KEY: str = "brave:ta:keepalive_failures"

# Maximum allowed POST body size: 64 KB
_MAX_BODY_BYTES: int = 65536

# Canary timeout in seconds
_CANARY_TIMEOUT_SECONDS: float = 15.0


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SessionInjectBody(BaseModel):
    """Request model for POST /api/v1/tripadvisor/session.

    extra="forbid" ensures unknown fields are rejected with 422.
    T-12-02-01: Cookie values are NEVER logged — only cookie_count is recorded.
    """

    model_config = {"extra": "forbid"}

    cookies: dict[str, str] = Field(
        ...,
        description=(
            "DataDome session cookies captured from a real browser. "
            "Must be non-empty. Values are NEVER logged (T-12-02-01)."
        ),
    )
    query_ids: dict[str, str] = Field(
        ...,
        description=(
            "GraphQL preRegisteredQueryId map (e.g. {'destinations': '<16-hex-id>'}). "
            "Must have at least one entry."
        ),
    )
    user_agent: str = Field(..., description="Browser User-Agent string from the capture session.")
    acquired_at: str = Field(..., description="ISO8601 timestamp when the session was captured.")
    session_id: str | None = Field(
        default=None,
        description=(
            "TASID cookie value threaded into listing query variables.sessionId. "
            "Derived from cookies['TASID'] if absent. "
            "T-13-01-01: treated as a cookie value — NEVER logged."
        ),
    )
    client_hints: dict[str, str] | None = Field(
        default=None,
        description="Optional Sec-CH-UA client hint headers captured from the browser.",
    )
    locale: str | None = Field(default="pt-BR", description="Locale override (default: pt-BR).")
    acquisition_ip: str | None = Field(
        default=None,
        description="Optional source IP of the operator's browser at capture time.",
    )

    @model_validator(mode="after")
    def _validate_non_empty(self) -> "SessionInjectBody":
        """Reject empty cookies or query_ids dicts."""
        if not self.cookies:
            raise ValueError("cookies must be non-empty")
        if not self.query_ids:
            raise ValueError("query_ids must have at least one entry")
        return self


class TASessionStatusResponse(BaseModel):
    """Response model for GET /api/v1/tripadvisor/session/status."""

    present: bool
    expires_in: int | None = None
    query_ids: list[str] | None = None
    reason: Literal["needs_bootstrap"] | None = None


class TASweepProgressResponse(BaseModel):
    """Response model for GET /api/v1/tripadvisor/sweep/progress.

    Serializes the brave:ta:sweep:progress hash verbatim. extra="forbid" prevents
    field drift so a future hash field can never silently leak through the read
    surface (T-15-03-02). The hash holds ONLY offsets/counts/state/timestamps —
    never cookie/session/datadome values.
    """

    model_config = {"extra": "forbid"}

    state: Literal["running", "done", "stopped", "stopped_needs_bootstrap", "idle"]
    pages_done: int
    pages_total: int
    attractions_ingested: int
    current_offset: int
    error_count: int
    started_at: str | None = None
    # updated_at = last write; stale = still "running" but untouched for 10 min — a dead
    # worker, not a slow page (the bare "running" used to read as alive forever).
    updated_at: str | None = None
    stale: bool = False


# ---------------------------------------------------------------------------
# Canary gate (injectable for tests)
# ---------------------------------------------------------------------------


async def _run_canary(session: dict[str, Any], ta_config: Any, redis: Redis) -> None:
    """Run a synchronous canary validation through the real httpx path.

    On success (non-empty result list): returns normally.
    On any failure (SessionExpiredError, timeout, empty result): raises
    HTTPException(422, detail="invalid_session"); an infrastructure fault raises 503.
    The caller (inject_session) puts the previous session back — this never deletes it.

    NEVER call this from tests — monkeypatch this function at the module level.
    Tests should: monkeypatch brave.api.routers.tripadvisor_session._run_canary

    T-12-02-01: Only logs cookie_count and query_ids keys — never values.
    """
    from brave.domains.tripadvisor.client import TripAdvisorClient

    # wait_offline=False: offline, the ConnectError must reach the 503 branch below, not
    # wait out the canary timeout and reject a good cURL as invalid_session.
    client = TripAdvisorClient(config=ta_config, redis=redis, wait_offline=False)
    try:
        # WR-06: a canary only needs to prove the session returns *any* data.
        # Bound it to a single page so a slow large-UF paginate can't exceed the
        # timeout and destroy a valid session.
        # geo_id=303380 = Minas Gerais (any valid UF geoId works; NOT national 294280).
        #
        # The canary MUST use the same transport the ingest lane uses
        # (fetch_attractions_paginated_gql, qid 79aaeeb847e55e58). The deprecated
        # single-page fetch_attractions is pinned to qid a5cb7fa004b5e4b5, which
        # TripAdvisor has retired: it now answers HTTP 200 with
        # [{"errors":[{"message":"PersistedQueryNotFound"}]}], which parses to zero
        # cards. Validating against it made every canary read "empty result" and
        # DELETE a perfectly good freshly-injected session — a 422 that no operator
        # could get past, while the lane itself was fine.
        # The attraction page too: DataDome can 403 it while the listing answers 200
        # (24/10, PR stopped 5 min in), and the per-UF sweep needs both surfaces.
        async def _first_page() -> list[dict[str, Any]]:
            async for _offset, cards in client.fetch_attractions_paginated_gql(
                geo_id=303380, max_pages=1
            ):
                await client.probe_page()
                return cards
            return []

        results = await asyncio.wait_for(
            _first_page(), timeout=_CANARY_TIMEOUT_SECONDS
        )
    except (SessionExpiredError, asyncio.TimeoutError) as exc:
        # Session is provably bad (DataDome 403/429) or unresponsive within the
        # bounded single-page window → fail closed (the caller restores the old key).
        logger.warning(
            "ta_session_canary_failed",
            reason=type(exc).__name__,
            cookie_count=len(session.get("cookies", {})),
            query_ids_keys=list(session.get("query_ids", {}).keys()),
            # T-12-02-01: cookie VALUES are NEVER logged here
        )
        raise HTTPException(status_code=422, detail="invalid_session") from exc
    except Exception as exc:
        # WR-02: an infrastructure fault (unknown-UF ValueError, Redis down, DNS,
        # proxy unreachable) is NOT proof the session is bad. Do NOT delete the
        # freshly-injected key — return 503 so the operator can retry without
        # re-capturing a scarce manually-obtained credential.
        # CR-01: log only the exception CLASS — str(exc) may carry cookie fragments.
        logger.warning(
            "ta_session_canary_unverified",
            reason=type(exc).__name__,
            cookie_count=len(session.get("cookies", {})),
            query_ids_keys=list(session.get("query_ids", {}).keys()),
        )
        raise HTTPException(status_code=503, detail="canary_unverified") from exc

    # Empty-result guard: a valid 200 response with empty data means stale queryId
    if not results:
        logger.warning(
            "ta_session_canary_empty_result",
            cookie_count=len(session.get("cookies", {})),
            query_ids_keys=list(session.get("query_ids", {}).keys()),
        )
        raise HTTPException(status_code=422, detail="invalid_session")


def _restore_session(
    redis: Redis, previous: Any, previous_pttl: int, *, keep_candidate: bool
) -> None:
    """Put back the session that was live before a failed injection.

    No previous session: delete the candidate, unless ``keep_candidate`` (503). WR-03: a
    Redis blip here must not turn the classified 422/503 into a raw 500.
    """
    try:
        if previous is not None and previous_pttl != -2:
            if previous_pttl > 0:
                redis.psetex(BRAVE_TA_SESSION_KEY, previous_pttl, previous)
            else:  # -1: the old key had no TTL
                redis.set(BRAVE_TA_SESSION_KEY, previous)
        elif not keep_candidate:
            redis.delete(BRAVE_TA_SESSION_KEY)
    except Exception:
        logger.warning("ta_session_restore_failed")


# ---------------------------------------------------------------------------
# POST /api/v1/tripadvisor/session
# ---------------------------------------------------------------------------


@router.post(
    "/api/v1/tripadvisor/session",
    status_code=200,
    dependencies=[Depends(require_steward_or_bearer)],
)
async def inject_session(
    request: Request,
    body: SessionInjectBody,
    redis: Redis = Depends(get_redis),
) -> dict:
    """Inject a TripAdvisor DataDome session captured by the operator's real browser.

    Workflow:
      1. Size-check the raw body (64 KB limit — T-12-02-03)
      2. Validate body (Pydantic — includes non-empty checks)
      3. Write session to Redis with TTL from TripAdvisorConfig
      4. Run synchronous canary gate through real httpx path
      5. Audit-log (cookie_count + query_ids keys — NEVER values)
      6. Return {status: "ready", canary: "ready"}

    On canary failure (422/503) the session that was live before the call is put back
    (value + remaining TTL): a mid-run re-inject whose canary times out on a saturated
    proxy used to delete the working session and stop every sweep. With no previous
    session a 422 deletes the candidate and a 503 keeps it (WR-02: an infra fault is not
    proof the scarce, hand-captured credential is bad).
    """
    # T-12-02-03: 64 KB size guard before any parsing
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_BODY_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail=f"Request body too large (max {_MAX_BODY_BYTES} bytes)",
                )
        except ValueError:
            pass  # Non-integer content-length — let FastAPI handle

    # Build the session dict to store in Redis
    # T-13-01-01: session_id is a TASID cookie value — derived from explicit field
    # or auto-extracted from cookies["TASID"]. NEVER logged (only presence as bool).
    derived_session_id: str = body.session_id or body.cookies.get("TASID") or ""
    session: dict[str, Any] = {
        "cookies": body.cookies,
        "query_ids": body.query_ids,
        "user_agent": body.user_agent,
        "acquired_at": body.acquired_at,
        "session_id": derived_session_id,
    }

    # Load TripAdvisor config for TTL
    from brave.config.settings import TripAdvisorConfig

    ta_config = TripAdvisorConfig()

    # Snapshot the live session, then write the candidate (the canary uses the same key).
    previous = redis.get(BRAVE_TA_SESSION_KEY)
    previous_pttl = redis.pttl(BRAVE_TA_SESSION_KEY)
    redis.setex(BRAVE_TA_SESSION_KEY, ta_config.session_ttl, json.dumps(session))
    logger.debug(
        "ta_session_key_written",
        cookie_count=len(body.cookies),
        query_ids_keys=list(body.query_ids.keys()),
        # T-12-02-01: cookie values NEVER logged
    )

    # Canary gate: validates the session synchronously before returning ready
    try:
        await _run_canary(session, ta_config, redis)
    except HTTPException as exc:
        _restore_session(redis, previous, previous_pttl, keep_candidate=exc.status_code == 503)
        raise

    # A session that passed the canary starts clean: the old one's keepalive streak and
    # needs_bootstrap marker would otherwise flag it after a single blip (28/10: streak 2
    # survived the inject). Best-effort, like the markers themselves.
    try:
        redis.delete(_TA_KEEPALIVE_FAILURES_KEY, _TA_NEEDS_BOOTSTRAP_KEY)
    except Exception:
        logger.warning("ta_session_markers_reset_failed")

    # Audit log (T-12-02-01: only metadata, NEVER cookie values)
    #
    # WR-04: get_db() is called directly here rather than via Depends, so the
    # `RuntimeError("BRAVE_DB_URL not set")` it raises when no DB is configured
    # must be distinguished from a genuine audit WRITE failure — otherwise a
    # missing-DB environment (e.g. staging without BRAVE_DB_URL) silently drops
    # the ta_session_injected compliance record looking identical to a DB hiccup.
    # We log the two cases under distinct event names, both at WARNING (never
    # swallowed), so a dropped audit is always observable. (No get_optional_db
    # dependency exists in brave/api/deps.py, so the override-respecting variant
    # the reviewer preferred isn't available without inventing a new dep.)
    try:
        from brave.api.deps import get_db
        from brave.observability.audit import write_audit

        # get_db is a generator function — the RuntimeError for an unconfigured
        # DB surfaces lazily on next(db_gen), not on the call itself.
        db_gen = get_db()
        try:
            db = next(db_gen)
        except StopIteration:
            # Generator yielded nothing (e.g. test override of get_db) — no DB.
            logger.warning("ta_session_audit_no_db", reason="get_db_yielded_nothing")
        except RuntimeError as no_db_exc:
            # No DB configured (BRAVE_DB_URL unset) — audit cannot be written.
            # This is a configuration gap, NOT a write failure; surface it as such
            # at WARNING rather than silently swallowing it.
            logger.warning("ta_session_audit_no_db", reason=str(no_db_exc))
        else:
            try:
                write_audit(
                    session=db,
                    action="ta_session_injected",
                    entity_type=None,
                    actor="operator",
                    after_state={
                        "cookie_count": len(body.cookies),
                        "query_ids": list(body.query_ids.keys()),
                        "acquired_at": body.acquired_at,
                        "canary_result": "ready",
                        # T-13-01-01: session_id presence as boolean only — NEVER the value
                        "session_id_present": bool(derived_session_id),
                    },
                )
            except Exception as audit_exc:
                # Audit WRITE failed (DB error mid-write) — must not block the
                # successful inject, but is distinct from "no DB configured".
                logger.warning("ta_session_audit_write_failed", error=str(audit_exc))
            finally:
                try:
                    db_gen.close()
                except Exception:
                    pass
    except Exception as audit_exc:
        logger.warning("ta_session_audit_skip", error=str(audit_exc))

    logger.info(
        "ta_session_injected",
        cookie_count=len(body.cookies),
        query_ids_keys=list(body.query_ids.keys()),
        acquired_at=body.acquired_at,
        canary_result="ready",
        # T-12-02-01: cookie VALUES are NEVER logged here
    )

    return {"status": "ready", "canary": "ready"}


# ---------------------------------------------------------------------------
# GET /api/v1/tripadvisor/session/status
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/tripadvisor/session/status",
    dependencies=[Depends(require_steward_or_bearer)],
    response_model=TASessionStatusResponse,
)
def session_status(redis: Redis = Depends(get_redis)) -> TASessionStatusResponse:
    """Return the current TripAdvisor session status.

    Response shapes:
      - Session present:     {present: True, expires_in: int, query_ids: [...], reason: null}
      - Session absent + needs_bootstrap marker: {present: False, reason: "needs_bootstrap"}
      - Session absent + no marker:              {present: False, reason: null}
    """
    raw = redis.get(BRAVE_TA_SESSION_KEY)

    if raw is not None:
        ttl_seconds = redis.ttl(BRAVE_TA_SESSION_KEY)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            stored_session = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            stored_session = {}

        return TASessionStatusResponse(
            present=True,
            expires_in=max(ttl_seconds, 0),
            query_ids=list(stored_session.get("query_ids", {}).keys()),
            reason=None,
        )

    # Session absent — check for the needs_bootstrap marker
    has_marker = bool(redis.get(_TA_NEEDS_BOOTSTRAP_KEY))
    return TASessionStatusResponse(
        present=False,
        reason="needs_bootstrap" if has_marker else None,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/tripadvisor/sweep/progress
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/tripadvisor/sweep/progress",
    dependencies=[Depends(require_steward_or_bearer)],
    response_model=TASweepProgressResponse,
)
def sweep_progress_status(redis: Redis = Depends(get_redis)) -> TASweepProgressResponse:
    """Return the live TripAdvisor bulk-sweep progress snapshot.

    Read-only — the writer is the Celery sweep worker (15-07); this endpoint only
    serializes the brave:ta:sweep:progress hash for the dashboard panel (15-08).
    Same auth as the sibling TA endpoints (require_steward_or_bearer — constant-time,
    fail-closed: an unauthenticated request is rejected with 401).

    Shapes:
      - No run started:  {state: "idle", pages_done: 0, ...zeros, started_at: null}
      - Running/terminal: the live counters + state.

    T-15-03-02: the response carries NO cookie/session/datadome value — the source
    hash is secret-free and extra="forbid" blocks any field drift.
    """
    return TASweepProgressResponse(**sweep_progress_state.get_progress(redis))


# ---------------------------------------------------------------------------
# TripAdvisor backfill (brave.backfill_ta_atrativos): manual trigger + progress
# ---------------------------------------------------------------------------


class TABackfillProgressResponse(BaseModel):
    """GET /api/v1/tripadvisor/backfill/progress — the backfill hash plus what is left.

    ``ta_pending`` / ``places_pending`` are live DB counts of the candidates (the
    task's own selector); the hash fields are the current/last run's counters.
    """

    model_config = {"extra": "forbid"}

    state: str = "idle"
    run_id: str | None = None
    processed: int = 0
    failed: int = 0
    last_status: str | None = None
    reason: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    # The Places key id (sha256 prefix) the WORKER used on its last link — compare with
    # /api/v1/cost places.key_id to see whether a swapped .env key actually reached it.
    places_key_id: str | None = None
    ta_pending: int
    places_pending: int


@router.post(
    "/api/v1/tripadvisor/backfill",
    status_code=202,
    dependencies=[Depends(require_steward_or_bearer)],
)
def start_backfill(
    redis: Redis = Depends(get_redis),
    db: Session = Depends(get_db),
) -> dict:
    """Start (or resume) the TA backfill as an engine run — also the resume path after a
    Places key swap or a billing/budget pause (start turns the motor LIGADO).

    409 when externals are off, the lane is disabled, there is no live TA session, the
    current Places key's credit is already spent (resuming would re-pause at once), or a
    run is already active; 503 when the broker refuses the first link (run aborted).
    """
    from brave.config.runtime import enabled_sources, load_effective_config  # noqa: PLC0415
    from brave.tasks.pipeline import start_backfill_run  # noqa: PLC0415

    cfg = load_effective_config(db)
    if not cfg.run_real_externals:
        raise HTTPException(status_code=409, detail="Backfill requer externals reais.")
    if "tripadvisor" not in enabled_sources(cfg):
        raise HTTPException(status_code=409, detail="Lane tripadvisor desligada no config.")
    ttl = redis.ttl(BRAVE_TA_SESSION_KEY)
    if ttl != -1 and ttl <= 0:
        raise HTTPException(
            status_code=409,
            detail="Backfill TripAdvisor requer uma sessão com TTL válido — injete um cURL primeiro.",
        )
    if cfg.places_enrichment_enabled:
        import os  # noqa: PLC0415

        from brave.clients.places import USD_DETAILS_ENTERPRISE  # noqa: PLC0415
        from brave.observability.cost_guard import (  # noqa: PLC0415
            places_key_id,
            places_total_spend,
        )

        # This process's key. The worker reads its own env: a swapped .env reaches neither
        # until the containers are recreated — the usual cause of an instant re-pause.
        key = os.environ.get("BRAVE_PLACES_API_KEY", "")
        if places_total_spend(redis, key) + USD_DETAILS_ENTERPRISE > cfg.places_total_usd_cap:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Crédito Places da chave atual ({places_key_id(key)}) esgotado — troque "
                    "BRAVE_PLACES_API_KEY no .env e recrie worker e api (docker compose up -d "
                    "worker api); restart não relê o .env."
                ),
            )
    try:
        run_id = start_backfill_run(redis, db)
    except Exception as exc:
        logger.error("ta_backfill_dispatch_failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Backfill não despachado (broker indisponível)."
        ) from exc
    if run_id is None:
        raise HTTPException(
            status_code=409, detail="Engine already running — stop it before starting a new run."
        )
    logger.info("ta_backfill_started", run_id=run_id)
    return {"status": "started", "run_id": run_id}


@router.get(
    "/api/v1/tripadvisor/backfill/progress",
    dependencies=[Depends(require_steward_or_bearer)],
    response_model=TABackfillProgressResponse,
)
def backfill_progress(
    redis: Redis = Depends(get_redis),
    db: Session = Depends(get_db),
) -> TABackfillProgressResponse:
    """The backfill's live counters + how many records each part still has to do."""
    from sqlalchemy import func, select  # noqa: PLC0415

    from brave.core.models import RioRecord  # noqa: PLC0415
    from brave.tasks.pipeline import (  # noqa: PLC0415
        _BACKFILL_PROGRESS_KEY,
        backfill_pending_filter,
    )

    raw = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in (redis.hgetall(_BACKFILL_PROGRESS_KEY) or {}).items()
    }
    fields = {k: (v or None) for k, v in raw.items() if k in TABackfillProgressResponse.model_fields}
    for key in ("processed", "failed"):
        fields[key] = int(fields.get(key) or 0)
    fields["state"] = fields.get("state") or "idle"

    def _count(*where: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(RioRecord).where(*where)) or 0)

    # TA part: the selector without Places; Places part: what Places adds on top of it.
    ta_where = backfill_pending_filter(places_on=False)
    both = backfill_pending_filter(places_on=True)
    ta_pending = _count(*ta_where)
    return TABackfillProgressResponse(
        **fields,
        ta_pending=ta_pending,
        places_pending=_count(*both) - ta_pending,
    )

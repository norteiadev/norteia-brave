"""Pydantic-settings configuration hierarchy for norteia-brave.

Three root config classes (D-10, D-12):
  - ScoreConfig        — reliability weights and thresholds (env_prefix BRAVE_SCORE_)
  - LLMConfig          — LLM provider slugs and budget (env_prefix BRAVE_LLM_)
  - DBConfig           — database and Redis URLs (env_prefix BRAVE_DB_)
  - WhatsAppConfig     — WhatsApp BSP (Twilio) config (env_prefix BRAVE_WA_)
  - RampConfig         — volume ramp limits (env_prefix BRAVE_WA_RAMP_)
  - TripAdvisorConfig  — TripAdvisor scraper config (env_prefix BRAVE_TA_)

Plus a composite AppConfig that aggregates all six.

CR-02: No Field(alias=...) on any field in any config class.
  Aliases let a bare env var shadow the prefixed key (secret-shadowing).
  All fields resolve ONLY from their exact prefixed env var name.
"""

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoreConfig(BaseSettings):
    """Reliability scoring weights and the single Mar routing threshold.

    All weight fields default to the reliability calibration values.
    Routing is binary: score >= threshold_mar → "mar", else → "dlq"
    (no descarte band). threshold_mar is a tunable knob — treat as a starting
    point and calibrate on the first state before national fan-out
    (see D-14, PITFALLS §1).
    """

    # Weights (must sum to 100)
    weight_origem: float = 30.0
    weight_completude: float = 20.0
    weight_corroboracao: float = 20.0
    weight_atualidade: float = 15.0
    weight_validacao_humana: float = 15.0

    # Single routing threshold — binary Mar/DLQ gate (D-02).
    # score >= threshold_mar → "mar"; everything below → "dlq" (no descarte band).
    # Re-calibrate on real BA data before national fan-out.
    # Env override: BRAVE_SCORE_THRESHOLD_MAR
    threshold_mar: float = 80.0

    # Env override: BRAVE_SCORE_SCORE_VERSION
    score_version: str = "v1.1"

    model_config = SettingsConfigDict(env_prefix="BRAVE_SCORE_")


class LLMConfig(BaseSettings):
    """LLM provider configuration.

    Primary slug + ordered fallback list pinned in config (D-10).
    provider_data_collection must be 'deny' at all times — asserted in tests.
    """

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # No alias: with env_prefix the key resolves ONLY from BRAVE_LLM_OPENROUTER_API_KEY.
    # An alias would let a bare `openrouter_api_key` env var shadow the prefixed key
    # (secret-shadowing, CR-02). field name + populate_by_name still allows kwargs.
    openrouter_api_key: str = Field(default="")

    # Primary slug validated for Mode.Tools + function-calling before use
    deepseek_primary_slug: str = "deepseek/deepseek-chat"
    deepseek_fallback_slugs: list[str] = ["deepseek/deepseek-v3.2"]

    # Enforce on every OpenRouter request body — NEVER "default"
    provider_data_collection: str = "deny"

    # Enforcing daily cost ceiling (CostGuardError on breach, not advisory)
    usd_daily_budget: float = 10.0

    # Anthropic (Claude Sonnet — Phase 3 WhatsApp; stubbed in Phase 1)
    # No alias (CR-02): resolves ONLY from BRAVE_LLM_ANTHROPIC_API_KEY.
    anthropic_api_key: str = Field(default="")

    model_config = SettingsConfigDict(env_prefix="BRAVE_LLM_", populate_by_name=True)


class DBConfig(BaseSettings):
    """Database and cache configuration.

    url is required — must be provided via BRAVE_DB_URL environment variable.
    Uses psycopg 3 driver (D-19): postgresql+psycopg://...
    """

    url: str  # Required — no default; set BRAVE_DB_URL
    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(env_prefix="BRAVE_DB_")


class WebhookConfig(BaseSettings):
    """Webhook authentication configuration.

    BRAVE_WEBHOOK_SECRET is required for the error-report webhook endpoint.
    The secret is compared with hmac.compare_digest (constant-time) — never logged.
    T-02-01: Static shared-secret enforced in Phase 1 (future enhancement: HMAC-of-body).
    """

    secret: str = Field(default="", description="Shared secret for X-Webhook-Secret header")

    model_config = SettingsConfigDict(env_prefix="BRAVE_WEBHOOK_")


class StewardConfig(BaseSettings):
    """Steward authentication for mutating DLQ endpoints (T-02-06-01 / CR-01).

    BRAVE_STEWARD_SECRET gates the DLQ reprocess/validate/validate-batch/descarte
    endpoints — these promote records to Mar and push to the production norteia-api,
    a write-to-production trust boundary. Compared with hmac.compare_digest
    (constant-time) — never logged. Fail-closed: an unset secret rejects all callers.

    Phase 4 (DASH-06) replaces this with the dashboard's Bearer-header auth; this is
    the minimal internal guard so the boundary is not open before then.
    """

    secret: str = Field(default="", description="Shared secret for X-Steward-Secret header")

    model_config = SettingsConfigDict(env_prefix="BRAVE_STEWARD_")


class DashboardConfig(BaseSettings):
    """Dashboard Bearer-header auth (DASH-06, D-02).

    BRAVE_DASHBOARD_BEARER_TOKEN gates the read-only dashboard endpoints (and,
    either-or with X-Steward-Secret, the mutation endpoints the BFF drives).
    Compared with hmac.compare_digest (constant-time) — never logged. Fail-closed:
    an unset token rejects all callers. Single operator token this milestone
    (multi-user/RBAC deferred).

    No env-var alias (CR-02 secret-shadowing rule): the token resolves from the
    exact BRAVE_DASHBOARD_BEARER_TOKEN name only.
    """

    bearer_token: str = Field(default="", description="Shared operator Bearer token")

    model_config = SettingsConfigDict(env_prefix="BRAVE_DASHBOARD_")


class WhatsAppConfig(BaseSettings):
    """WhatsApp BSP configuration (Twilio launch path, D-09).

    No env-var aliases (CR-02): each field resolves from its exact BRAVE_WA_ prefixed
    name only. An alias would let a bare env var shadow the prefixed key (secret-shadowing).

    Env prefix: BRAVE_WA_
      BRAVE_WA_TWILIO_ACCOUNT_SID
      BRAVE_WA_TWILIO_AUTH_TOKEN
      BRAVE_WA_FROM_NUMBER
      BRAVE_WA_MESSAGING_SERVICE_SID
      BRAVE_WA_APPROVED_TEMPLATES   (JSON list string)
    """

    twilio_account_sid: str = Field(
        default="",
        description="Twilio account SID for WhatsApp Business API (starts with AC...).",
    )
    twilio_auth_token: str = Field(
        default="",
        description="Twilio auth token for WhatsApp API. Never logged.",
    )
    from_number: str = Field(
        default="",
        description="WhatsApp sender number in E.164 format (e.g. +5511999999999).",
    )
    messaging_service_sid: str = Field(
        default="",
        description="Twilio MessagingServiceSid for template sending (starts with MG...).",
    )
    approved_templates: list[str] = Field(
        default_factory=list,
        description=(
            "Allowlist of pre-registered BSP template names. "
            "ComplianceError raised if template not in this list (D-11)."
        ),
    )

    model_config = SettingsConfigDict(env_prefix="BRAVE_WA_", populate_by_name=True)


class RampConfig(BaseSettings):
    """WhatsApp volume ramp configuration (D-07, RESEARCH.md Pitfall 4).

    Global portfolio-wide daily cap is the primary constraint (Oct 2025 portfolio limits:
    new portfolios start at 250 unique contacts/24h — ramp conservatively below this).
    Per-UF cap is an optional additional layer layered on top.

    No env-var aliases (CR-02).

    Env prefix: BRAVE_WA_RAMP_
      BRAVE_WA_RAMP_DAILY_CAP
      BRAVE_WA_RAMP_QUALITY_PAUSE_THRESHOLD
    """

    daily_cap: int = Field(
        default=50,
        description=(
            "Max outreach sends per UTC day across the whole portfolio "
            "(BRAVE_WA_RAMP_DAILY_CAP). Conservative default: 50 (well under the "
            "250 cold-start portfolio limit). Ramp up as quality rating improves."
        ),
    )
    quality_pause_threshold: str = Field(
        default="RED",
        description=(
            "Quality rating level that triggers auto-pause "
            "(BRAVE_WA_RAMP_QUALITY_PAUSE_THRESHOLD). "
            "Values: RED | YELLOW. RED = pause all sends; YELLOW = reduce cap 50%."
        ),
    )

    model_config = SettingsConfigDict(env_prefix="BRAVE_WA_RAMP_", populate_by_name=True)


class TripAdvisorConfig(BaseSettings):
    """TripAdvisor GraphQL scraper configuration (TA-01).

    Controls the Playwright DataDome session bootstrap, httpx persisted-query
    client, proxy seam, and IBGE municipality resolver thresholds.

    No env-var aliases (CR-02): each field resolves from its exact BRAVE_TA_
    prefixed name only.

    Env prefix: BRAVE_TA_
      BRAVE_TA_PROXY_URL             — residential proxy URL (empty = no proxy)
      BRAVE_TA_SESSION_TTL           — DataDome cookie TTL in seconds (default 1800)
      BRAVE_TA_QUERY_ID_OVERRIDE     — JSON dict of queryId overrides (e.g. {"destinations": "abc"})
      BRAVE_TA_IBGE_MATCH_THRESHOLD  — rapidfuzz token_sort_ratio cutoff (default 88)
      BRAVE_TA_IBGE_MAX_DISTANCE_KM  — haversine fallback radius in km (default 15.0)
      BRAVE_TA_PAGE_THROTTLE_SECONDS — sleep between sequential -oa{N}- page GETs (default 2.0)
      BRAVE_TA_ATTRACTIONS_TRANSIENT_MAX_RETRIES — bounded retries on AttractionsFusion soft-failure (default 3)
      BRAVE_TA_ATTRACTIONS_TRANSIENT_RETRY_SLEEP_SECONDS — sleep between transient retries (default 1.0)
    """

    proxy_url: str = Field(
        default="",
        description=(
            "Residential proxy URL for DataDome bypass (e.g. 'http://user:pass@proxy:port'). "
            "Empty string = no proxy (dev default). Never emitted in logs — T-11-01-01."
        ),
    )
    session_ttl: int = Field(
        default=1800,
        description=(
            "DataDome session cookie TTL in seconds (BRAVE_TA_SESSION_TTL). "
            "Conservative default: 1800s (30 min). Tune based on empirical cookie expiry."
        ),
    )
    query_id_override: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Override map for GraphQL queryIds (BRAVE_TA_QUERY_ID_OVERRIDE). "
            "Keys: 'destinations', 'attractions'. Set when TA rotates queryIds "
            "faster than the live-capture bootstrap can recover."
        ),
    )
    ibge_match_threshold: int = Field(
        default=88,
        description=(
            "rapidfuzz token_sort_ratio cutoff for IBGE municipality name matching "
            "(BRAVE_TA_IBGE_MATCH_THRESHOLD). Values below 80 risk false matches."
        ),
    )
    ibge_max_distance_km: float = Field(
        default=15.0,
        description=(
            "Haversine distance threshold in km for the IBGE coordinate fallback "
            "(BRAVE_TA_IBGE_MAX_DISTANCE_KM). Used when name fuzzy-match falls below threshold."
        ),
    )
    page_throttle_seconds: float = Field(
        default=2.0,
        description=(
            "Seconds to sleep between sequential -oa{N}- attractions page GETs "
            "(BRAVE_TA_PAGE_THROTTLE_SECONDS). DataDome endurance + politeness on "
            "the 334-page full-Brazil sweep. 0 disables throttling (tests)."
        ),
    )
    keepalive_interval_seconds: int = Field(
        default=600,
        description=(
            "Keep-alive beat interval in seconds (BRAVE_TA_KEEPALIVE_INTERVAL_SECONDS). "
            "Default 600s (10 min) — well under session_ttl/2=900s for a safe margin. "
            "Set 0 to disable (beat will still fire but skip when no session)."
        ),
    )
    # CR-02: NO Field(alias=...) — resolves ONLY from BRAVE_TA_KEEPALIVE_INTERVAL_SECONDS.
    attractions_transient_max_retries: int = Field(
        default=3,
        description=(
            "Bounded retry count for AttractionsFusion soft-failures "
            "(BRAVE_TA_ATTRACTIONS_TRANSIENT_MAX_RETRIES). AttractionsFusion "
            "intermittently returns HTTP 200 with Result[0].status.success==false "
            "for a VALID geoId; retrying the identical request succeeds. This bounds "
            "the retries so a persistently-failing geo returns [] after max_retries+1 "
            "calls (T-has-01) — no unbounded loop. Set 0 to disable retries."
        ),
    )
    attractions_transient_retry_sleep_seconds: float = Field(
        default=1.0,
        description=(
            "Seconds to sleep between AttractionsFusion transient retries "
            "(BRAVE_TA_ATTRACTIONS_TRANSIENT_RETRY_SLEEP_SECONDS). Set 0 in tests to "
            "keep the offline suite fast."
        ),
    )
    # CR-02: NO Field(alias=...) — both resolve ONLY from their exact BRAVE_TA_ names.

    model_config = SettingsConfigDict(env_prefix="BRAVE_TA_")
    # CR-02: NO Field(alias=...) anywhere in this class.


class NominatimConfig(BaseSettings):
    """OpenStreetMap Nominatim geocoder configuration (TA-14).

    No env-var aliases (CR-02): each field resolves from its exact BRAVE_NOMINATIM_
    prefixed name only.

    Env prefix: BRAVE_NOMINATIM_
      BRAVE_NOMINATIM_BASE_URL             — Nominatim search endpoint (default public OSM)
      BRAVE_NOMINATIM_USER_AGENT           — identifiable UA string (required by policy)
      BRAVE_NOMINATIM_MIN_REQUEST_INTERVAL — seconds between requests (default 1.1)
      BRAVE_NOMINATIM_CACHE_TTL            — geocode Redis TTL seconds (default 2592000 = 30d)
      BRAVE_NOMINATIM_TIMEOUT_SECONDS      — httpx timeout (default 15)
    """

    base_url: str = Field(
        default="https://nominatim.openstreetmap.org/search",
        description=(
            "Nominatim search endpoint (BRAVE_NOMINATIM_BASE_URL). "
            "Override for a self-hosted instance."
        ),
    )
    user_agent: str = Field(
        default="norteia-brave/1.0 (leandro.freire08@gmail.com)",
        description=(
            "HTTP User-Agent sent to Nominatim (BRAVE_NOMINATIM_USER_AGENT). "
            "Required by Nominatim usage policy — must be identifiable."
        ),
    )
    min_request_interval: float = Field(
        default=1.1,
        description=(
            "Minimum seconds between consecutive Nominatim requests "
            "(BRAVE_NOMINATIM_MIN_REQUEST_INTERVAL). Policy: ≤1 req/s; 1.1 adds margin."
        ),
    )
    cache_ttl: int = Field(
        default=86_400 * 30,
        description=(
            "Redis TTL for cached geocode results in seconds "
            "(BRAVE_NOMINATIM_CACHE_TTL). Default 30 days — geocodes are stable."
        ),
    )
    timeout_seconds: float = Field(
        default=15.0,
        description="httpx request timeout in seconds (BRAVE_NOMINATIM_TIMEOUT_SECONDS).",
    )

    model_config = SettingsConfigDict(env_prefix="BRAVE_NOMINATIM_")
    # CR-02: NO Field(alias=...) anywhere in this class.


class EngineConfig(BaseModel):
    """Engine operator-mode overlay (Phase D, config_settings key ``engine.mode``).

    ``mode`` default DESLIGADO makes the clean/seeded base start with the motor OFF:
    on a fresh "carga inicial" base seed_default_config writes this into the
    ``config_settings`` engine.mode row, and get_status(session=...) self-heals Redis
    from it, so no sweep auto-dispatches until an operator turns the engine on. This is
    a plain BaseModel (not BaseSettings): it has no env precedent and is populated only
    by the config_settings overlay (brave.config.runtime.load_effective_config) or its
    code default.

    IMPORTANT: the LIVE operator mode remains Redis-authoritative
    (brave.core.engine get_mode/set_mode drives dispatch + the Kanban card
    edit-lock). This field is the CONFIGURED default surfaced in the effective-config
    snapshot; it is NOT wired into dispatch in this phase (behavior-neutral).
    """

    mode: str = "DESLIGADO"


def _default_sources() -> dict[str, bool]:
    """Both known collection lanes enabled by default (Phase D).

    Kept as a module-level factory (not a lambda) so the mutable default is a fresh
    dict per AppConfig instance and the two known lanes are documented in one place.
    """
    return {"default": True, "tripadvisor": True}


class AppConfig(BaseSettings):
    """Composite application configuration.

    Aggregates ScoreConfig, LLMConfig, WhatsAppConfig, RampConfig as nested models.
    Also exposes top-level feature flags.
    """

    score: ScoreConfig = Field(default_factory=ScoreConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    ramp: RampConfig = Field(default_factory=RampConfig)
    tripadvisor: TripAdvisorConfig = Field(default_factory=TripAdvisorConfig)
    nominatim: NominatimConfig = Field(default_factory=NominatimConfig)

    # Per-source enable flags (Phase D overlay key ``source.<name>.enabled``).
    # Both known lanes enabled by default → effective config == pre-Phase-D behavior.
    # brave.config.runtime.enabled_sources() reads this. The LIVE single-source
    # *selector* stays Redis-authoritative (brave.core.engine get_source/set_source);
    # this is the registered/enabled overlay only, consumed in a later phase.
    sources: dict[str, bool] = Field(default_factory=_default_sources)
    engine: EngineConfig = Field(default_factory=EngineConfig)

    # run_real_externals=True enables real API calls (tests and CI default to False)
    run_real_externals: bool = False

    model_config = SettingsConfigDict(env_prefix="")

    @classmethod
    def load(cls, db_url: str | None = None) -> "AppConfig":
        """Load AppConfig, optionally overriding the DB URL.

        DBConfig is not nested directly because it has a required field (url).
        Use brave.config.get_db_config() to load DBConfig separately.
        """
        return cls()

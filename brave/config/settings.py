"""Pydantic-settings configuration hierarchy for norteia-brave.

Three root config classes (D-10, D-12):
  - ScoreConfig  — §7.6 weights and thresholds (env_prefix BRAVE_SCORE_)
  - LLMConfig    — LLM provider slugs and budget (env_prefix BRAVE_LLM_)
  - DBConfig     — database and Redis URLs (env_prefix BRAVE_DB_)

Plus a composite AppConfig that aggregates all three.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScoreConfig(BaseSettings):
    """§7.6 scoring weights and routing thresholds.

    All weight fields default to the §7.6 calibration values.
    Thresholds (threshold_mar / threshold_dlq) are tunable knobs — treat
    as starting points and calibrate on the first state before national fan-out
    (see D-14, PITFALLS §1).
    """

    # Weights (must sum to 100)
    weight_origem: float = 30.0
    weight_completude: float = 20.0
    weight_corroboracao: float = 20.0
    weight_atualidade: float = 15.0
    weight_validacao_humana: float = 15.0

    # Routing thresholds
    threshold_mar: float = 85.0
    # Lowered from 51.0 to 40.0 in Phase 2 (D-05 calibration) so that
    # DesmembramentoAgent cold-start records (origem=40, corroboração=0)
    # land in DLQ instead of descarte, enabling steward review.
    # The §7.6 math proves 51.0 routes ALL Desmembramento records to descarte
    # (max cold-start score = 47.0 < 51.0 — descarte black-hole).
    # Re-calibrate on real BA data before national fan-out.
    # Env override: BRAVE_SCORE_THRESHOLD_DLQ
    threshold_dlq: float = 40.0

    # Bumped to v1.1 in Phase 2 due to threshold_dlq calibration (40.0 from 51.0).
    # Old scored records from Phase 1 tests remain valid — score values are
    # unchanged; only the DLQ/descarte boundary moved.
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


class AppConfig(BaseSettings):
    """Composite application configuration.

    Aggregates ScoreConfig, LLMConfig, DBConfig as nested models.
    Also exposes top-level feature flags.
    """

    score: ScoreConfig = Field(default_factory=ScoreConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)

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

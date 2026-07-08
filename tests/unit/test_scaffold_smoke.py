"""Scaffold smoke tests — verify import correctness and attribute expectations.

These tests prove:
1. brave.config.settings imports and ScoreConfig() yields exact reliability defaults
2. brave.core.models imports and table names match spec
3. brave.clients.base imports and the core Protocol interfaces are defined
4. Layer boundary: NascenteRecord has no 'routing' column; RioRecord has 'routing'
5. MarRecord.source_ref is UNIQUE (per D-15)
6. LLMConfig.provider_data_collection defaults to 'deny' (security invariant)
"""

import inspect

import pytest


# ---------------------------------------------------------------------------
# 1. ScoreConfig — reliability defaults
# ---------------------------------------------------------------------------


class TestScoreConfig:
    def test_weight_origem(self) -> None:
        from brave.config.settings import ScoreConfig

        assert ScoreConfig().weight_origem == 30.0

    def test_weight_completude(self) -> None:
        from brave.config.settings import ScoreConfig

        assert ScoreConfig().weight_completude == 20.0

    def test_weight_corroboracao(self) -> None:
        from brave.config.settings import ScoreConfig

        assert ScoreConfig().weight_corroboracao == 20.0

    def test_weight_atualidade(self) -> None:
        from brave.config.settings import ScoreConfig

        assert ScoreConfig().weight_atualidade == 15.0

    def test_weight_validacao_humana(self) -> None:
        from brave.config.settings import ScoreConfig

        assert ScoreConfig().weight_validacao_humana == 15.0

    def test_threshold_mar(self) -> None:
        from brave.config.settings import ScoreConfig

        assert ScoreConfig().threshold_mar == 80.0  # binary Mar/DLQ gate (Phase B)

    def test_weights_sum_to_100(self) -> None:
        from brave.config.settings import ScoreConfig

        c = ScoreConfig()
        total = (
            c.weight_origem
            + c.weight_completude
            + c.weight_corroboracao
            + c.weight_atualidade
            + c.weight_validacao_humana
        )
        assert total == pytest.approx(100.0)

    def test_score_version_default(self) -> None:
        from brave.config.settings import ScoreConfig

        assert ScoreConfig().score_version == "v1.1"


# ---------------------------------------------------------------------------
# 2. Model table names and structure
# ---------------------------------------------------------------------------


class TestModels:
    def test_nascente_tablename(self) -> None:
        from brave.core.models import NascenteRecord

        assert NascenteRecord.__tablename__ == "nascente_records"

    def test_rio_tablename(self) -> None:
        from brave.core.models import RioRecord

        assert RioRecord.__tablename__ == "rio_records"

    def test_mar_tablename(self) -> None:
        from brave.core.models import MarRecord

        assert MarRecord.__tablename__ == "mar_records"

    def test_llm_generation_tablename(self) -> None:
        from brave.core.models import LLMGeneration

        assert LLMGeneration.__tablename__ == "llm_generations"

    def test_audit_log_tablename(self) -> None:
        from brave.core.models import AuditLog

        assert AuditLog.__tablename__ == "audit_log"

    def test_poison_quarantine_tablename(self) -> None:
        from brave.core.models import PoisonQuarantine

        assert PoisonQuarantine.__tablename__ == "poison_quarantine"

    def test_nascente_has_no_routing(self) -> None:
        """NascenteRecord must NOT have a routing column (D-01 boundary)."""
        from brave.core.models import NascenteRecord

        assert not hasattr(NascenteRecord, "routing"), (
            "NascenteRecord must not have a routing column — "
            "routing belongs to RioRecord (D-01, D-02)"
        )

    def test_rio_has_routing(self) -> None:
        """RioRecord MUST have a routing column (D-02)."""
        from brave.core.models import RioRecord

        assert hasattr(RioRecord, "routing"), (
            "RioRecord must have a routing column for DLQ/descarte values (D-02)"
        )

    def test_rio_has_embedding(self) -> None:
        """RioRecord must have an embedding column for pgvector dedup (D-08)."""
        from brave.core.models import RioRecord

        assert hasattr(RioRecord, "embedding")

    def test_rio_has_score_version(self) -> None:
        """RioRecord must have score_version column (D-13)."""
        from brave.core.models import RioRecord

        assert hasattr(RioRecord, "score_version")

    def test_mar_has_score_version(self) -> None:
        """MarRecord must have score_version column (D-13)."""
        from brave.core.models import MarRecord

        assert hasattr(MarRecord, "score_version")

    def test_mar_source_ref_is_unique(self) -> None:
        """MarRecord.source_ref is unique among ACTIVE rows only (D-15 + D-03).

        A plain global UNIQUE would forbid supersession (D-03 keeps the old row
        alongside the new active row). Uniqueness is enforced by the partial index
        uq_mar_active_source_ref (WHERE superseded_by_id IS NULL).
        """
        from brave.core.models import MarRecord

        partial = next(
            (idx for idx in MarRecord.__table__.indexes
             if idx.name == "uq_mar_active_source_ref"),
            None,
        )
        assert partial is not None, (
            "MarRecord must declare the partial unique index uq_mar_active_source_ref"
        )
        assert partial.unique, "uq_mar_active_source_ref must be a UNIQUE index"
        assert "source_ref" in {c.name for c in partial.columns}

    def test_nascente_has_superseded_by_id(self) -> None:
        """NascenteRecord must have superseded_by_id for supersession (D-03)."""
        from brave.core.models import NascenteRecord

        assert hasattr(NascenteRecord, "superseded_by_id")

    def test_mar_has_superseded_by_id(self) -> None:
        """MarRecord must have superseded_by_id for supersession (D-03)."""
        from brave.core.models import MarRecord

        assert hasattr(MarRecord, "superseded_by_id")

    def test_six_tables_in_metadata(self) -> None:
        """Base.metadata must include all 6 Phase 1 tables."""
        from brave.core.models import Base

        expected = {
            "nascente_records",
            "rio_records",
            "mar_records",
            "llm_generations",
            "audit_log",
            "poison_quarantine",
        }
        actual = set(Base.metadata.tables.keys())
        assert expected.issubset(actual), (
            f"Missing tables in Base.metadata: {expected - actual}"
        )


# ---------------------------------------------------------------------------
# 3. Client Protocol interfaces
# ---------------------------------------------------------------------------


EXPECTED_PROTOCOLS = [
    "LLMClientProtocol",
    "NorteiaApiClientProtocol",
    "PlacesClientProtocol",
    "OTAClientProtocol",
    "WhatsAppClientProtocol",
    "MturClientProtocol",
]


class TestClientProtocols:
    @pytest.mark.parametrize("protocol_name", EXPECTED_PROTOCOLS)
    def test_protocol_exists(self, protocol_name: str) -> None:
        """Each listed client Protocol interface must be importable from brave.clients.base."""
        import brave.clients.base as base_module

        assert hasattr(base_module, protocol_name), (
            f"{protocol_name} not found in brave.clients.base"
        )

    @pytest.mark.parametrize("protocol_name", EXPECTED_PROTOCOLS)
    def test_protocol_is_class(self, protocol_name: str) -> None:
        """Each listed client Protocol must be a class."""
        import brave.clients.base as base_module

        cls = getattr(base_module, protocol_name)
        assert inspect.isclass(cls), f"{protocol_name} is not a class"

    def test_llm_client_has_extract(self) -> None:
        from brave.clients.base import LLMClientProtocol

        assert hasattr(LLMClientProtocol, "extract")

    def test_norteia_api_has_push_destination(self) -> None:
        from brave.clients.base import NorteiaApiClientProtocol

        assert hasattr(NorteiaApiClientProtocol, "push_destination")

    def test_norteia_api_has_push_attraction(self) -> None:
        from brave.clients.base import NorteiaApiClientProtocol

        assert hasattr(NorteiaApiClientProtocol, "push_attraction")

    def test_places_has_text_search(self) -> None:
        from brave.clients.base import PlacesClientProtocol

        assert hasattr(PlacesClientProtocol, "text_search")

    def test_places_has_place_details(self) -> None:
        from brave.clients.base import PlacesClientProtocol

        assert hasattr(PlacesClientProtocol, "place_details")


# ---------------------------------------------------------------------------
# 4. Package boundary: core/ and lanes/ and clients/ are importable
# ---------------------------------------------------------------------------


class TestPackageBoundaries:
    def test_brave_config_importable(self) -> None:
        import brave.config.settings  # noqa: F401

    def test_brave_core_importable(self) -> None:
        import brave.core.models  # noqa: F401

    def test_brave_lanes_importable(self) -> None:
        import brave.lanes.base  # noqa: F401

    def test_brave_clients_importable(self) -> None:
        import brave.clients.base  # noqa: F401

    def test_brave_observability_importable(self) -> None:
        import brave.observability  # noqa: F401

    def test_brave_tasks_importable(self) -> None:
        import brave.tasks  # noqa: F401

    def test_brave_api_importable(self) -> None:
        import brave.api  # noqa: F401


# ---------------------------------------------------------------------------
# 5. LLMConfig security invariant
# ---------------------------------------------------------------------------


class TestLLMConfig:
    def test_provider_data_collection_deny(self) -> None:
        """provider_data_collection must default to 'deny' (security invariant)."""
        from brave.config.settings import LLMConfig

        assert LLMConfig().provider_data_collection == "deny", (
            "LLMConfig.provider_data_collection must default to 'deny' — "
            "this is a security invariant (PITFALLS §5)"
        )

    def test_deepseek_primary_slug(self) -> None:
        from brave.config.settings import LLMConfig

        assert LLMConfig().deepseek_primary_slug == "deepseek/deepseek-chat"

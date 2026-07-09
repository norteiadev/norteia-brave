"""Schema unit tests for Phase 3 atrativos scaffolding (03-01).

Tests:
  - AtrativoResult round-trip valid dict succeeds
  - AtrativoResult with invalid municipio_ibge pattern raises ValidationError
  - ConversationExtractionResult defaults confidence=0.0
  - ContactResult all-None is valid
  - FakeWhatsAppClient.send_template appends to sent_messages
  - ConsentLog model is importable from brave.core.models
  - WhatsAppConfig and RampConfig are importable and have defaults
"""

import asyncio

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# AtrativoResult tests
# ---------------------------------------------------------------------------


def test_atrativo_result_valid_round_trip() -> None:
    """AtrativoResult round-trip with valid data succeeds."""
    from brave.lanes.atrativos.schemas import AtrativoResult

    data = {
        "nome": "Praia do Forte",
        "tipo": "praia",
        "posicionamento": "Bela praia com recifes de coral na Costa dos Coqueiros",
        "municipio_nome": "Mata de São João",
        "municipio_ibge": "2921005",
        "uf": "BA",
        "place_id": "ChIJabc123",
        "origem_value": 60.0,
        "completude_value": 50.0,
    }
    result = AtrativoResult(**data)
    assert result.nome == "Praia do Forte"
    assert result.tipo == "praia"
    assert result.municipio_ibge == "2921005"
    assert result.uf == "BA"
    assert result.place_id == "ChIJabc123"


def test_atrativo_result_invalid_municipio_ibge_raises() -> None:
    """AtrativoResult with invalid municipio_ibge pattern raises ValidationError."""
    from brave.lanes.atrativos.schemas import AtrativoResult

    data = {
        "nome": "Parque Nacional",
        "tipo": "parque",
        "posicionamento": "Parque com exuberante vegetação nativa e fauna diversa",
        "municipio_nome": "São Paulo",
        "municipio_ibge": "123",  # invalid — must be 7 digits
        "uf": "SP",
        "place_id": "ChIJxyz",
        "origem_value": 60.0,
        "completude_value": 50.0,
    }
    with pytest.raises(ValidationError):
        AtrativoResult(**data)


def test_atrativo_result_all_tipo_literals() -> None:
    """AtrativoResult accepts all 11 valid tipo values."""
    from brave.lanes.atrativos.schemas import AtrativoResult

    valid_tipos = [
        "praia",
        "parque",
        "museu",
        "cachoeira",
        "trilha",
        "mirante",
        "centro_historico",
        "experiencia_gastronomica",
        "show_cultural",
        "esporte_aventura",
        "outros",
    ]
    base = {
        "nome": "Atrativo Teste",
        "posicionamento": "Descrição longa do atrativo para teste completo",
        "municipio_nome": "Lençóis",
        "municipio_ibge": "2919207",
        "uf": "BA",
        "place_id": "ChIJtest",
        "origem_value": 60.0,
        "completude_value": 50.0,
    }
    for tipo in valid_tipos:
        result = AtrativoResult(**{**base, "tipo": tipo})
        assert result.tipo == tipo


def test_atrativo_result_invalid_tipo_raises() -> None:
    """AtrativoResult with unknown tipo raises ValidationError."""
    from brave.lanes.atrativos.schemas import AtrativoResult

    data = {
        "nome": "Atrativo",
        "tipo": "festival",  # not in the allowed Literal
        "posicionamento": "Descrição longa do atrativo para teste completo",
        "municipio_nome": "Lençóis",
        "municipio_ibge": "2919207",
        "uf": "BA",
        "place_id": "ChIJtest",
        "origem_value": 60.0,
        "completude_value": 50.0,
    }
    with pytest.raises(ValidationError):
        AtrativoResult(**data)


# ---------------------------------------------------------------------------
# _compute_completude degrau tests (new descricao_editorial degrau = 90)
# ---------------------------------------------------------------------------


def _full_atrativo(**overrides):
    from brave.lanes.atrativos.schemas import AtrativoResult

    base = {
        "nome": "Praia do Forte",
        "tipo": "praia",
        "posicionamento": "Bela praia com recifes de coral na Costa dos Coqueiros",
        "municipio_nome": "Mata de São João",
        "municipio_ibge": "2921005",
        "uf": "BA",
        "place_id": "ChIJabc123",
    }
    base.update(overrides)
    return AtrativoResult(**base)


def test_completude_ceiling_without_description_is_75() -> None:
    """All five discovery fields, no descricao_editorial → 75.0 (unchanged floor)."""
    from brave.domains.mtur.discovery import _compute_completude

    assert _compute_completude(_full_atrativo()) == 75.0


def test_completude_new_degrau_with_description_is_90() -> None:
    """All five fields + a curated descricao_editorial → the new 90.0 degrau."""
    from brave.domains.mtur.discovery import _compute_completude

    result = _full_atrativo(
        descricao_editorial="Descrição editorial curada, na voz da Norteia."
    )
    assert _compute_completude(result) == 90.0


def test_completude_description_below_ceiling_stays_50() -> None:
    """Missing ibge/place_id keeps the 50 degrau even with a description (no jump)."""
    from brave.domains.mtur.discovery import _compute_completude

    from brave.lanes.atrativos.schemas import AtrativoResult

    result = AtrativoResult(
        nome="Atrativo",
        tipo="praia",
        posicionamento="Descrição longa o suficiente para contar",
        municipio_nome="Lugar",
        municipio_ibge="2921005",  # pattern requires 7 digits; keep valid
        uf="BA",
        place_id="",  # missing place_id → below the ceiling
        descricao_editorial="Descrição editorial curada.",
    )
    assert _compute_completude(result) == 50.0


# ---------------------------------------------------------------------------
# ConversationExtractionResult tests
# ---------------------------------------------------------------------------


def test_conversation_extraction_result_defaults_confidence_zero() -> None:
    """ConversationExtractionResult default confidence is 0.0."""
    from brave.shared.whatsapp.schemas import ConversationExtractionResult

    result = ConversationExtractionResult()
    assert result.confidence == 0.0


def test_conversation_extraction_result_all_none() -> None:
    """ConversationExtractionResult with all-None fields is valid."""
    from brave.shared.whatsapp.schemas import ConversationExtractionResult

    result = ConversationExtractionResult(
        existe=None,
        funcionando=None,
        horarios=None,
        valor=None,
        confidence=0.0,
    )
    assert result.existe is None
    assert result.funcionando is None


def test_conversation_extraction_result_filled() -> None:
    """ConversationExtractionResult with valid answers populates correctly."""
    from brave.shared.whatsapp.schemas import ConversationExtractionResult

    result = ConversationExtractionResult(
        existe="sim",
        funcionando="sim",
        horarios="Terça a domingo, 9h às 18h",
        valor="R$ 20 por pessoa",
        confidence=0.9,
    )
    assert result.existe == "sim"
    assert result.funcionando == "sim"
    assert result.confidence == 0.9


def test_conversation_extraction_result_confidence_out_of_range_raises() -> None:
    """ConversationExtractionResult confidence outside [0, 1] raises ValidationError."""
    from brave.shared.whatsapp.schemas import ConversationExtractionResult

    with pytest.raises(ValidationError):
        ConversationExtractionResult(confidence=1.5)


# ---------------------------------------------------------------------------
# ContactResult tests
# ---------------------------------------------------------------------------


def test_contact_result_all_none_is_valid() -> None:
    """ContactResult with all-None fields is valid."""
    from brave.lanes.atrativos.schemas import ContactResult

    result = ContactResult()
    assert result.phone_e164 is None
    assert result.website is None
    assert result.ig_handle is None
    assert result.email is None


def test_contact_result_partial_fill() -> None:
    """ContactResult with partial fields is valid."""
    from brave.lanes.atrativos.schemas import ContactResult

    result = ContactResult(phone_e164="+5573999999999", website="https://example.com")
    assert result.phone_e164 == "+5573999999999"
    assert result.website == "https://example.com"
    assert result.ig_handle is None


# ---------------------------------------------------------------------------
# SignalResult tests
# ---------------------------------------------------------------------------


def test_signal_result_valid() -> None:
    """SignalResult with valid data populates correctly."""
    from brave.lanes.atrativos.schemas import SignalResult

    result = SignalResult(
        business_status="OPERATIONAL",
        weekday_text=["Monday: 9:00 AM – 5:00 PM"],
        atualidade_value=100.0,
        reviews_recent_count=3,
    )
    assert result.business_status == "OPERATIONAL"
    assert result.reviews_recent_count == 3


# ---------------------------------------------------------------------------
# FakeWhatsAppClient tests
# ---------------------------------------------------------------------------


def test_fake_whatsapp_client_appends_to_sent_messages() -> None:
    """FakeWhatsAppClient.send_template appends to sent_messages."""
    from tests.fakes.fake_whatsapp import FakeWhatsAppClient

    client = FakeWhatsAppClient()
    assert client.sent_messages == []

    result = asyncio.run(
        client.send_template(
            to="+5573999999999",
            template="norteia_business_verify_v1",
            params={"body": "Olá, somos da Norteia"},
        )
    )
    assert result["message_sid"] == "fake-sid-001"
    assert result["status"] == "sent"
    assert len(client.sent_messages) == 1
    assert client.sent_messages[0]["to"] == "+5573999999999"


def test_fake_whatsapp_client_raises_when_should_fail() -> None:
    """FakeWhatsAppClient(should_fail=True).send_template raises RuntimeError."""
    from tests.fakes.fake_whatsapp import FakeWhatsAppClient

    client = FakeWhatsAppClient(should_fail=True)
    with pytest.raises(RuntimeError, match="simulated send failure"):
        asyncio.run(
            client.send_template(
                to="+5573999999999",
                template="norteia_business_verify_v1",
                params={},
            )
        )


# ---------------------------------------------------------------------------
# ConsentLog import test
# ---------------------------------------------------------------------------


def test_consent_log_importable() -> None:
    """ConsentLog is importable from brave.core.models."""
    from brave.core.models import ConsentLog

    assert ConsentLog.__tablename__ == "consent_log"


# ---------------------------------------------------------------------------
# WhatsAppConfig and RampConfig tests
# ---------------------------------------------------------------------------


def test_whatsapp_config_defaults() -> None:
    """WhatsAppConfig is importable and has expected defaults."""
    from brave.config.settings import WhatsAppConfig

    config = WhatsAppConfig()
    assert config.twilio_account_sid == ""
    assert config.twilio_auth_token == ""
    assert config.from_number == ""
    assert config.messaging_service_sid == ""
    assert config.approved_templates == []


def test_ramp_config_defaults() -> None:
    """RampConfig daily_cap default is 50."""
    from brave.config.settings import RampConfig

    config = RampConfig()
    assert config.daily_cap == 50
    assert config.quality_pause_threshold == "RED"


def test_app_config_has_whatsapp_and_ramp() -> None:
    """AppConfig has whatsapp and ramp nested configs."""
    from brave.config.settings import AppConfig

    config = AppConfig()
    assert config.ramp.daily_cap == 50
    assert config.whatsapp.approved_templates == []


def test_settings_has_no_alias() -> None:
    """WhatsAppConfig and RampConfig must not use env-var aliases (CR-02)."""
    from brave.config.settings import RampConfig, WhatsAppConfig

    for field_name, field_info in WhatsAppConfig.model_fields.items():
        # pydantic-settings Field should not have alias set to a bare var name
        if hasattr(field_info, "alias") and field_info.alias is not None:
            # An alias that exactly matches the field name is fine (it's no-op)
            # but we must not have aliases that bypass the prefix
            alias_val = str(field_info.alias).lower()
            assert field_name.lower() == alias_val or alias_val.startswith(
                "brave_wa_"
            ), f"WhatsAppConfig.{field_name} has suspicious alias: {field_info.alias}"

    for field_name, field_info in RampConfig.model_fields.items():
        if hasattr(field_info, "alias") and field_info.alias is not None:
            alias_val = str(field_info.alias).lower()
            assert field_name.lower() == alias_val or alias_val.startswith(
                "brave_wa_ramp_"
            ), f"RampConfig.{field_name} has suspicious alias: {field_info.alias}"


# ---------------------------------------------------------------------------
# fake_places.py fixture constants tests
# ---------------------------------------------------------------------------


def test_signal_fixture_open_has_operational_status() -> None:
    """SIGNAL_FIXTURE_OPEN business_status is OPERATIONAL."""
    from tests.fakes.fake_places import SIGNAL_FIXTURE_OPEN

    assert SIGNAL_FIXTURE_OPEN["business_status"] == "OPERATIONAL"
    assert SIGNAL_FIXTURE_OPEN["place_id"] == "ChIJtest001"


def test_signal_fixture_closed_has_closed_permanently_status() -> None:
    """SIGNAL_FIXTURE_CLOSED business_status is CLOSED_PERMANENTLY."""
    from tests.fakes.fake_places import SIGNAL_FIXTURE_CLOSED

    assert SIGNAL_FIXTURE_CLOSED["business_status"] == "CLOSED_PERMANENTLY"
    assert SIGNAL_FIXTURE_CLOSED["place_id"] == "ChIJtest002"
    assert SIGNAL_FIXTURE_CLOSED["weekday_text"] == []
    assert SIGNAL_FIXTURE_CLOSED["reviews"] == []


# ---------------------------------------------------------------------------
# NullWhatsAppClient tests
# ---------------------------------------------------------------------------


def test_null_whatsapp_client_records_without_transmitting() -> None:
    """NullWhatsAppClient records send without network transmit."""
    from brave.clients.null_whatsapp import NullWhatsAppClient

    client = NullWhatsAppClient()
    assert client.sent_messages == []

    result = asyncio.run(
        client.send_template(
            to="+5573999999999",
            template="norteia_business_verify_v1",
            params={"body": "Olá, somos da Norteia"},
        )
    )
    assert result["status"] == "queued"
    assert "message_sid" in result
    assert len(client.sent_messages) == 1

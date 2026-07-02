"""Pydantic v2 schemas for the Atrativos lane (Phase 3).

Five schemas:
  - AtrativoResult       — LLM extraction output for one attraction (instructor Mode.Tools)
  - ContactResult        — ContactFinderAgent output (Places Details + site/IG/email)
  - SignalResult         — SignalAgent output (business_status / weekday_text / reviews)
  - ConversationExtractionResult — DeepSeek extraction of owner WhatsApp responses
  - WhatsAppNumberDiscovery — LLM-discovered phone/WhatsApp number (Phase F number-discovery)

Every Field has a description= kwarg for instructor Mode.Tools tool-calling compliance.
Literal types are used for constrained fields; `| None` for optional extraction outputs.

ATR-01..04, COMP-01, D-04, D-05, D-08, D-09, D-11
"""

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# AtrativoResult — output of DiscoveryAgent / Places → DeepSeek extraction
# ---------------------------------------------------------------------------

AtrativoTipo = Literal[
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


class AtrativoResult(BaseModel):
    """Structured output for a single attraction extracted by DiscoveryAgent.

    All fields have description= for instructor Mode.Tools tool-calling compliance (D-09).
    municipio_ibge must be a 7-digit IBGE code (e.g. "2919207").
    place_id is the only Google field persisted as a cache key (D-04 / COMP-03).
    """

    nome: str = Field(
        ...,
        min_length=2,
        description="Nome do atrativo turístico, conforme aparece no Google Places.",
    )
    tipo: AtrativoTipo = Field(
        ...,
        description=(
            "Categoria do atrativo. Valores aceitos: praia, parque, museu, cachoeira, "
            "trilha, mirante, centro_historico, experiencia_gastronomica, show_cultural, "
            "esporte_aventura, outros."
        ),
    )
    posicionamento: str = Field(
        ...,
        min_length=10,
        description=(
            "Breve descrição de posicionamento do atrativo (mínimo 10 caracteres). "
            "Destaque o diferencial do local."
        ),
    )
    municipio_nome: str = Field(
        ...,
        description="Nome do município onde o atrativo está localizado.",
    )
    municipio_ibge: str = Field(
        ...,
        pattern=r"^\d{7}$",
        description="Código IBGE do município (7 dígitos numéricos, e.g. '2919207').",
    )
    uf: str = Field(
        ...,
        min_length=2,
        max_length=2,
        description="Sigla do estado (UF) em letras maiúsculas, e.g. 'BA'.",
    )
    place_id: str = Field(
        ...,
        description=(
            "Google Places place_id persistido como cache key (D-04 / COMP-03). "
            "Nunca usar como identidade canônica — apenas para re-fetch e dedup."
        ),
    )
    origem_value: float = Field(
        default=60.0,
        description=(
            "Valor do critério origem §7.6 para este atrativo (padrão 60.0 para "
            "Google Places — fonte autoritativa mas não oficial gov)."
        ),
    )
    completude_value: float = Field(
        default=0.0,
        description=(
            "Valor do critério completude §7.6 (0–100), calculado a partir da cobertura "
            "de campos obrigatórios: nome, coordenadas, telefone/WhatsApp, horários, tipo."
        ),
    )


# ---------------------------------------------------------------------------
# ContactResult — output of ContactFinderAgent (Places Details + site/IG/email)
# ---------------------------------------------------------------------------


class ContactResult(BaseModel):
    """Contact information found by ContactFinderAgent.

    All fields are optional — ContactFinder may find partial data or none.
    phone_e164 is the primary field used by the WhatsApp gate (D-06, D-11).
    """

    phone_e164: str | None = Field(
        default=None,
        description=(
            "Número de telefone/WhatsApp do responsável em formato E.164 (+55...). "
            "Usado como chave de opt-out no consent_log (COMP-01)."
        ),
    )
    website: str | None = Field(
        default=None,
        description="URL do site oficial do atrativo (incluindo https://).",
    )
    ig_handle: str | None = Field(
        default=None,
        description="Handle do Instagram do atrativo (e.g. '@praiadobonito').",
    )
    email: str | None = Field(
        default=None,
        description="E-mail de contato do atrativo ou responsável.",
    )


# ---------------------------------------------------------------------------
# SignalResult — output of SignalAgent (business_status / reviews)
# ---------------------------------------------------------------------------


class SignalResult(BaseModel):
    """Operating signal data gathered by SignalAgent.

    business_status: CLOSED_PERMANENTLY or CLOSED_TEMPORARILY triggers hard
    descarte before §7.6 scoring (D-05). Corroboração is a fixed 0.0 constant
    (the Apify IG social-signal source was retired in Phase E); it never fails the record.
    """

    business_status: str = Field(
        ...,
        description=(
            "Status de funcionamento do Google Places: OPERATIONAL, CLOSED_PERMANENTLY, "
            "CLOSED_TEMPORARILY, ou outro valor retornado pela API. "
            "CLOSED_PERMANENTLY ou CLOSED_TEMPORARILY → hard descarte (D-05)."
        ),
    )
    weekday_text: list[str] = Field(
        default_factory=list,
        description=(
            "Lista de horários de funcionamento por dia da semana, conforme retornado "
            "pelo Google Places (e.g. ['Monday: 9:00 AM – 5:00 PM', ...])."
        ),
    )
    atualidade_value: float = Field(
        default=0.0,
        description=(
            "Valor do critério atualidade §7.6 (0–100). 100 se reviews recentes "
            "(≤30 dias); 50 se 1–6 meses; 0 se sem reviews recentes."
        ),
    )
    reviews_recent_count: int = Field(
        default=0,
        description="Quantidade de reviews com publishTime nos últimos 30 dias.",
    )


# ---------------------------------------------------------------------------
# ConversationExtractionResult — DeepSeek extraction of WhatsApp owner replies
# ---------------------------------------------------------------------------


class ConversationExtractionResult(BaseModel):
    """Structured extraction of owner WhatsApp conversation answers.

    Extracted by DeepSeek via instructor Mode.Tools (D-08, D-09).
    All answer fields are optional — a partial conversation may not yield all answers.
    confidence represents the overall extraction confidence (0.0–1.0).

    Used by WhatsAppAgent finalize node to drive re-score + Mar/DLQ routing (D-10).
    """

    existe: Literal["sim", "nao"] | None = Field(
        default=None,
        description=(
            "O negócio/atrativo existe? Resposta normalizada: 'sim' ou 'nao'. "
            "None se o proprietário não respondeu claramente."
        ),
    )
    funcionando: Literal["sim", "nao", "temporariamente_fechado"] | None = Field(
        default=None,
        description=(
            "Está funcionando atualmente? Valores: 'sim', 'nao', 'temporariamente_fechado'. "
            "None se inconclusivo."
        ),
    )
    horarios: str | None = Field(
        default=None,
        description=(
            "Horários de funcionamento confirmados pelo proprietário (texto livre). "
            "None se não informado."
        ),
    )
    valor: str | None = Field(
        default=None,
        description=(
            "Valor de entrada ou faixa de preço confirmado pelo proprietário (texto livre). "
            "None se gratuito ou não informado."
        ),
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Confiança geral da extração (0.0–1.0). Estimado pelo DeepSeek com base "
            "na clareza e coerência das respostas do proprietário."
        ),
    )


# ---------------------------------------------------------------------------
# WhatsAppNumberDiscovery — LLM number-discovery output (Phase F)
# ---------------------------------------------------------------------------


class WhatsAppNumberDiscovery(BaseModel):
    """LLM-discovered WhatsApp/phone number for an atrativo (Phase F number-discovery).

    Emitted by the number-discovery LLM (instructor Mode.Tools) when an atrativo
    reaches the WhatsApp gate WITHOUT a celular candidate captured at enrichment
    time. The offline (Null) client never returns this — it yields no number, so the
    record routes straight back to DLQ with dlq_reason="no_contact_found".

    `phone` is a RAW candidate string (E.164 / 55-prefixed / bare national) or None
    when the model could not find a plausible number. Celular-validation + LGPD
    masking happen downstream (whatsapp_candidate_from_phone); this schema carries the
    raw candidate only so the outreach/consent path can normalize it to E.164.
    """

    phone: str | None = Field(
        default=None,
        description=(
            "Número de WhatsApp/telefone do responsável, se encontrado, em formato "
            "E.164 (+55DDDNNNNNNNNN) ou nacional (DDD + número). None se nenhum "
            "número plausível foi encontrado."
        ),
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Confiança de que o número encontrado pertence a este atrativo (0.0–1.0). "
            "Estimado pelo modelo com base nas fontes consultadas."
        ),
    )

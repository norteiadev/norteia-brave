"""WhatsApp-conversation Pydantic schemas (Phase G — kernel move).

``ConversationExtractionResult`` is the DeepSeek/instructor Mode.Tools 2nd-layer
validator for owner WhatsApp replies (D-08, D-09). It moved here from
``brave.lanes.atrativos.schemas`` so the shared WhatsApp agent can validate
extractions without ``brave.shared`` importing ``brave.lanes`` (D-18).

Dependency-free: imports only ``typing`` + ``pydantic``.
"""

from typing import Literal

from pydantic import BaseModel, Field


class ConversationExtractionResult(BaseModel):
    """Structured extraction of owner WhatsApp conversation answers.

    Extracted by DeepSeek via instructor Mode.Tools (D-08, D-09).
    All answer fields are optional — a partial conversation may not yield all answers.
    confidence represents the overall extraction confidence (0.0–1.0).

    Used by the WhatsApp agent finalize node to drive re-score + Mar/DLQ routing (D-10).
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

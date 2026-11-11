"""What the extraction model returns, validated against norteia-api's limits.

One ``Extraction`` = the entities of one post, each with only the columns the post states
(``Fact`` = a rewritten ``value`` + a short literal ``evidence`` kept for audit, never
published). Limits and the ``pace_profile`` enum mirror the API's destination ingest, so a
value the API would 422 is rejected here, per entity, instead of poisoning a push.

Stability is NOT asked of the model: it is fixed per column (``stability_of``). ``tips`` is
cleaned of prices, opening hours and people limits before it is stored (D3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from brave.core.mar.service import EDITORIAL_ATTRACTION_FIELDS


def _short(n: int) -> Any:
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=n)]


def _clip(v: Any) -> Any:
    return v[:300] if isinstance(v, str) else v


Text = _short(2000)
Seasons = Annotated[list[_short(300)], Field(min_length=1, max_length=12)]


class Fact[T](BaseModel):
    model_config = ConfigDict(extra="ignore")  # e.g. a "stability" hint: the code decides

    value: T
    evidence: Annotated[str, BeforeValidator(_clip)] = ""


class DestinoFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    best_time_to_visit: Fact[Text] | None = None
    seasonality: Fact[Seasons] | None = None
    how_to_get_there: Fact[Text] | None = None
    recommended_duration: Fact[_short(100)] | None = None
    tips: Fact[Text] | None = None
    safety_alerts: Fact[Text] | None = None
    estimated_spending_range: Fact[_short(100)] | None = None
    predominant_audience_profile: Fact[_short(200)] | None = None
    pace_profile: Fact[Literal["lento", "equilibrado", "intenso"]] | None = None
    connectivity_level: Fact[_short(50)] | None = None

    @field_validator("seasonality", mode="before")
    @classmethod
    def _season_as_array(cls, v: Any) -> Any:
        if isinstance(v, dict) and isinstance(v.get("value"), str):
            return {**v, "value": [v["value"]]}
        return v


class AtrativoFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    free_entry: Fact[bool] | None = None
    price: Fact[Text] | None = None
    opening_hours: Fact[Text] | None = None
    how_to_get_there: Fact[Text] | None = None
    tips: Fact[Text] | None = None
    local_infrastructure: Fact[Text] | None = None
    curiosities: Fact[Text] | None = None
    accessibility: Fact[Text] | None = None
    safety_alerts: Fact[Text] | None = None
    estimated_capacity: Fact[Text] | None = None
    conservation_level: Fact[Text] | None = None


class DestinoEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["destino"]
    name: _short(256)  # type: ignore[valid-type]
    municipio: str | None = None
    fields: DestinoFields = DestinoFields()


class AtrativoEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["atrativo"]
    name: _short(256)  # type: ignore[valid-type]
    municipio: str | None = None
    fields: AtrativoFields = AtrativoFields()


Entity = Annotated[DestinoEntity | AtrativoEntity, Field(discriminator="kind")]


class Extraction(BaseModel):
    entities: list[Entity]


_ENTITY = TypeAdapter(Entity)


def parse_extraction(raw: Any) -> tuple[list[DestinoEntity | AtrativoEntity], list[str]]:
    """Validate entity by entity: ``(valid entities, one error line per rejected entity)``."""
    items = raw.get("entities") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return [], ["no 'entities' list in the model output"]
    entities: list[DestinoEntity | AtrativoEntity] = []
    errors: list[str] = []
    for i, item in enumerate(items):
        try:
            entities.append(_ENTITY.validate_python(item))
        except ValidationError as exc:
            name = item.get("name") if isinstance(item, dict) else None
            where = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            errors.append(f"entity {i} {name!r}: {where}")
    return entities, errors


# -- stability and what travels ----------------------------------------------------------

VOLATILE_FIELDS = frozenset(
    {
        "free_entry",
        "price",
        "opening_hours",
        "estimated_capacity",
        "conservation_level",
        "safety_alerts",
    }  # fmt: skip
)
# Stored, pushed: every stable attraction column, and every stable destination column.
ATTRACTION_PUSHED: tuple[str, ...] = EDITORIAL_ATTRACTION_FIELDS
DESTINATION_PUSHED: tuple[str, ...] = tuple(
    f for f in DestinoFields.model_fields if f not in VOLATILE_FIELDS
)


def stability_of(field: str) -> Literal["estavel", "volatil"]:
    return "volatil" if field in VOLATILE_FIELDS else "estavel"


# D3: a price, an opening hour or a people limit is volatile and must not ride in ``tips``.
_VOLATILE_TIP = re.compile(
    r"R\$\s*\d"
    r"|\b\d{1,2}\s*(?:h|hs|:)\s*\d{0,2}\s*(?:às|as|a|até|-|–)\s*\d{1,2}\s*(?:h|hs|:|horas)"
    r"|\b(?:abre|abrem|fecha|fecham|funciona|funcionam|aberto|aberta)\b[^.]{0,60}"
    r"\d{1,2}\s*(?:h\b|hs\b|:\d{2}|horas)"
    r"|\bhor[áa]rio"
    r"|\b(?:limite|m[áa]ximo|limitad[oa]s?|até|apenas|somente)\b[^.]{0,40}\b\d+\s+pessoas"
    r"|\b\d+\s+pessoas\s+(?:por|ao|a cada)\s+(?:dia|grupo|vez|hor[áa]rio|turno)",
    re.IGNORECASE,
)


def strip_volatile_tips(text: str) -> str:
    """``text`` minus every sentence that states a price, opening hours or a people limit."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(s for s in sentences if s and not _VOLATILE_TIP.search(s))


@dataclass(frozen=True)
class ClaimDraft:
    field: str
    value: Any  # str | bool | list[str]
    stability: str
    evidence: str


def claim_drafts(entity: DestinoEntity | AtrativoEntity) -> list[ClaimDraft]:
    """One draft per filled column; ``tips`` cleaned by D3 (dropped when nothing is left)."""
    drafts: list[ClaimDraft] = []
    for name in type(entity.fields).model_fields:
        fact = getattr(entity.fields, name)
        if fact is None:
            continue
        value = fact.value
        if name == "tips":
            value = strip_volatile_tips(value)
            if not value:
                continue
        drafts.append(ClaimDraft(name, value, stability_of(name), fact.evidence))
    return drafts

"""What the extraction model returns, validated against norteia-api's limits.

One extraction = the entities of one post, each with only the columns the post states
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


_ENTITY = TypeAdapter(Entity)


def _where(exc: ValidationError) -> list[tuple[str | None, str]]:
    """(column or None, "loc: msg") per error; column when the error sits under fields.<col>."""
    out: list[tuple[str | None, str]] = []
    for e in exc.errors():
        loc = [str(p) for p in e["loc"]]
        column = loc[loc.index("fields") + 1] if "fields" in loc[:-1] else None
        out.append((column, f"{'.'.join(loc)}: {e['msg']}"))
    return out


def parse_extraction(raw: Any) -> tuple[list[DestinoEntity | AtrativoEntity], list[str]]:
    """Validate entity by entity: ``(valid entities, one error line per dropped fact/entity)``.

    A value outside the API's limits or enum drops ONLY that fact (the entity and its other
    columns stay, like an unreadable free_entry in ``from_wire``); an entity is dropped only
    when something outside its columns is invalid (e.g. an empty name).
    """
    items = raw.get("entities") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return [], ["no 'entities' list in the model output"]
    entities: list[DestinoEntity | AtrativoEntity] = []
    errors: list[str] = []
    for i, item in enumerate(items):
        name = item.get("name") if isinstance(item, dict) else None
        current = item
        for _ in range(2):  # the second pass validates what is left after dropping facts
            try:
                entities.append(_ENTITY.validate_python(current))
                break
            except ValidationError as exc:
                where = _where(exc)
                bad = {col: msg for col, msg in where if col is not None}
                fields = current.get("fields") if isinstance(current, dict) else None
                if not bad or len(bad) < len(where) or not isinstance(fields, dict):
                    errors.append(f"entity {i} {name!r}: " + "; ".join(m for _, m in where))
                    break
                errors.extend(
                    f"entity {i} {name!r}: dropped {col}: {msg}" for col, msg in bad.items()
                )
                current = {**current, "fields": {k: v for k, v in fields.items() if k not in bad}}
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


# The claim statuses the resolver (re-)matches on every run. Every other status is a state a
# claim was moved to (``held_volatile``, ``superseded``, …): the row stays — nothing collected
# is deleted — and is never matched, resolved nor pushed.
RESOLVABLE_STATUSES: tuple[str, ...] = ("unmatched", "ambiguous", "matched")


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


# -- the wire format of the strict extraction tool ------------------------------------------
#
# What the MODEL fills, not what the code validates. strict: true caps a request at 24 optional
# parameters and 16 union-typed ones (over that: 400 "Schema is too complex for compilation")
# and supports neither minLength/maxLength nor oneOf. One optional, nullable field per column
# (the shape above) blows both caps, so the tool takes a flat list of facts: every property
# required, no unions, no string limits. ``from_wire`` turns it back into the column shape,
# and ``parse_extraction`` applies the API's limits and enum to it as before.

COLUMNS: tuple[str, ...] = tuple(
    sorted(set(DestinoFields.model_fields) | set(AtrativoFields.model_fields))
)


class WireFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal[COLUMNS]  # type: ignore[valid-type]
    value: str
    evidence: str


class WireEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["destino", "atrativo"]
    name: str
    municipio: str  # "" when the post does not say
    facts: list[WireFact]


class WireExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[WireEntity]


class WireError(ValueError):
    """The tool input does not match the wire schema."""


def _strict(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strict(v) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_strict(v) for v in node]
    return node


def wire_rules() -> str:
    """The API's limits and enum, in words, for the tool description: the strict wire schema
    cannot carry string limits, so the model reads them here (generated from the column
    models: one source). A value outside them is dropped as a fact by parse_extraction."""
    rules: dict[str, str] = {}
    for model in (DestinoFields, AtrativoFields):
        schema = model.model_json_schema()
        for column, prop in schema["properties"].items():
            ref = next((a["$ref"] for a in prop.get("anyOf", []) if "$ref" in a), None)
            if ref is None:
                continue
            value = schema["$defs"][ref.rsplit("/", 1)[-1]]["properties"]["value"]
            if value.get("enum"):
                rules[column] = " | ".join(value["enum"])
            elif value.get("maxLength", 2000) < 2000:
                rules[column] = f"até {value['maxLength']} caracteres"
    rules["free_entry"] = "sim | não"
    rules["seasonality"] = 'períodos curtos separados por ";"'
    body = "; ".join(f"{c}: {r}" for c, r in sorted(rules.items()))
    return f"Limites da norteia-api (um valor fora deles é descartado): {body}."


def wire_json_schema() -> dict[str, Any]:
    """The extraction tool's input_schema (strict mode: see the section comment)."""
    return _strict(WireExtraction.model_json_schema())


_YES = {"sim", "true", "yes", "gratuito", "gratuita", "gratis", "grátis"}
_NO = {"não", "nao", "false", "no", "pago", "paga"}


def from_wire(raw: Any) -> dict[str, Any]:
    """The column shape ``parse_extraction`` reads (``{"entities": [{kind, name, municipio,
    fields: {column: {value, evidence}}}]}``). Typed columns: ``free_entry`` sim/não → bool
    (anything else dropped), ``seasonality`` ";"-separated → list. A repeated column keeps
    its first value. Raises ``WireError`` when ``raw`` is not the wire schema."""
    try:
        wire = WireExtraction.model_validate(raw)
    except ValidationError as exc:
        raise WireError(str(exc)[:500]) from exc
    entities: list[dict[str, Any]] = []
    for e in wire.entities:
        fields: dict[str, Any] = {}
        for fact in e.facts:
            if fact.field in fields:
                continue
            value: Any = fact.value
            if fact.field == "free_entry":
                word = value.strip().lower()
                if word not in _YES | _NO:
                    continue
                value = word in _YES
            elif fact.field == "seasonality":
                value = [p.strip() for p in value.split(";") if p.strip()]
            fields[fact.field] = {"value": value, "evidence": fact.evidence}
        entities.append(
            {
                "kind": e.kind,
                "name": e.name,
                "municipio": e.municipio.strip() or None,
                "fields": fields,
            }
        )
    return {"entities": entities}

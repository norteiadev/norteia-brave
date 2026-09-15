#!/usr/bin/env python
"""POC: can Gemini replace Claude Sonnet 4.5 in the TourismCopywriter lane?

Read-only probe. Touches NOTHING in the pipeline: it imports the real
``COPYWRITER_SYSTEM`` + ``_build_context`` from ``brave.lanes.atrativos.copywriter``
so the prompt is byte-identical to production, then runs the same prompt against:

  1. Gemini (``google_search`` grounding tool = the Anthropic ``web_search`` analogue)
  2. Claude Sonnet 4.5 (optional, ``--with-sonnet``) for a head-to-head

and prints both texts + measured token/search usage + computed USD cost per record.

No DB, no Redis, no Celery, no writes. Two live API calls per atrativo at most.

Usage:
    export GEMINI_API_KEY=...            # aistudio.google.com/apikey
    .venv/bin/python scripts/poc/gemini_copywriter_poc.py
    .venv/bin/python scripts/poc/gemini_copywriter_poc.py --with-sonnet --extract
    .venv/bin/python scripts/poc/gemini_copywriter_poc.py --model gemini-3.5-flash-lite
    .venv/bin/python scripts/poc/gemini_copywriter_poc.py --no-search    # free-tier shape

Free tier note: on an unbilled key, tokens are free BUT ``google_search`` grounding is
"Not available" per the pricing page, and inputs/outputs are used to improve Google
products. ``--no-search`` reproduces exactly that shape so the quality delta is visible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brave.lanes.atrativos.copywriter import (  # noqa: E402
    COPYWRITER_SYSTEM,
    WEB_SEARCH_TOOL,
    _build_context,
    _strip_dashes,
)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# USD per 1M tokens. Gemini figures are the promo prices in force through 2026-12-31
# (they double on 2027-01-01 — see ai.google.dev/gemini-api/docs/pricing).
PRICES: dict[str, dict[str, float]] = {
    "gemini-3.7-flash": {"in": 0.75, "out": 3.75, "search": 0.014},
    "gemini-3.6-flash": {"in": 0.75, "out": 3.75, "search": 0.014},
    "gemini-3.5-flash": {"in": 1.50, "out": 9.00, "search": 0.014},
    "gemini-3.5-flash-lite": {"in": 0.30, "out": 2.50, "search": 0.014},
    "gemini-3.1-pro-preview": {"in": 2.00, "out": 12.00, "search": 0.014},
    "claude-sonnet-4-5": {"in": 3.00, "out": 15.00, "search": 0.010},
}

# Two records: one famous (search finds plenty), one obscure (where a weaker model
# hallucinates). Places context mirrors what PlacesEnrichmentAgent actually passes.
SAMPLES: list[dict[str, Any]] = [
    {
        "nome": "Convento da Penha",
        "municipio": "Vila Velha",
        "uf": "ES",
        "places_context": {
            "editorial_summary": "Santuário do século XVI no alto de um penhasco, com vista para a baía.",
            "types": ["tourist_attraction", "place_of_worship"],
            "formatted_address": "Ilha do Príncipe, Vila Velha - ES",
            "reviews": [
                {"text": "A subida é puxada mas a vista lá de cima compensa demais."},
                {"text": "Fui de manhã cedo, sem fila, e o silêncio da igreja impressiona."},
            ],
        },
    },
    {
        "nome": "Cachoeira da Fumaça",
        "municipio": "Alegre",
        "uf": "ES",
        "places_context": {
            "editorial_summary": "",
            "types": ["tourist_attraction"],
            "formatted_address": "Zona rural, Alegre - ES",
            "reviews": [{"text": "Água gelada, poço bom para banho, acesso por estrada de terra."}],
        },
    },
]


# --enriched: the §4.1 hypothesis. Replaces the web search with facts a deterministic
# pipeline already can fetch for free (Wikidata / OSM / Melhores Destinos — all mapped in
# earlier spikes). Tests whether a search-less model writes FAITHFULLY from supplied facts
# instead of inventing. Values below are the facts Sonnet's live web_search actually returned.
ENRICHED_FACTS: dict[str, list[str]] = {
    "Convento da Penha": [
        "Fundado em 1558 pelo frei espanhol Pedro Palácios.",
        "Situado no alto de um penhasco a cerca de 154 metros de altitude.",
        "Tombado pelo IPHAN em 1943.",
        "Altar rococó de 1800; interior em cedro entalhado por José Fernandes Pereira.",
        "Obras de Vitor Meireles entregues em 1877.",
        "É um dos santuários marianos mais antigos do Brasil.",
    ],
    "Cachoeira da Fumaça": [
        "Queda de 144 metros de altura.",
        "Maior cachoeira de água perene do Espírito Santo.",
        "Protegida pelo Parque Estadual Cachoeira da Fumaça, criado em 1984.",
        "Fica na bacia do rio Braço Norte Direito, afluente do rio Itapemirim.",
        "Integra o Corredor Ecológico da Mata Atlântica Central desde 2002.",
        "Fauna registrada: lontras, maitacas e gatos-do-mato-pequenos.",
    ],
}


def enrich(context: str, nome: str) -> str:
    """Append the deterministic fact block the §4.1 architecture would supply."""
    facts = ENRICHED_FACTS.get(nome)
    if not facts:
        return context
    block = "\n".join(f"- {f}" for f in facts)
    return (
        f"{context}\n\nFatos verificados de fontes estruturadas (Wikidata / OSM / IPHAN). "
        f"Use SOMENTE estes fatos como base factual, sem acrescentar outros:\n{block}"
    )


def usd(tokens_in: int, tokens_out: int, searches: int, model: str) -> float:
    p = PRICES.get(model, PRICES["gemini-3.7-flash"])
    return (tokens_in * p["in"] + tokens_out * p["out"]) / 1_000_000 + searches * p["search"]


async def run_gemini(
    client: httpx.AsyncClient, model: str, system: str, context: str, *, search: bool
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": context}]}],
        "generationConfig": {"maxOutputTokens": 2048},
    }
    if search:
        body["tools"] = [{"google_search": {}}]

    # Header auth, NOT ?key= — the newer "AQ."-prefixed keys 429 on the query-param path.
    r = await client.post(
        f"{GEMINI_BASE}/models/{model}:generateContent",
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        json=body,
        timeout=180.0,
    )
    if r.status_code != 200:
        return {"error": f"HTTP {r.status_code}: {r.text[:600]}"}
    data = r.json()

    cand = (data.get("candidates") or [{}])[0]
    text = "".join(
        part.get("text", "") for part in (cand.get("content", {}).get("parts") or [])
    )
    gm = cand.get("groundingMetadata") or {}
    queries = gm.get("webSearchQueries") or []
    um = data.get("usageMetadata") or {}
    tokens_in = int(um.get("promptTokenCount") or 0)
    # thinking tokens bill as output on Gemini 3.x
    tokens_out = int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0)

    return {
        "text": _strip_dashes(text),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "searches": len(queries),
        "queries": queries,
        "sources": [
            (c.get("web") or {}).get("title", "") for c in (gm.get("groundingChunks") or [])
        ][:6],
        "usd": usd(tokens_in, tokens_out, len(queries), model),
    }


async def run_sonnet(system: str, context: str, model: str = "claude-sonnet-4-5") -> dict[str, Any]:
    from anthropic import AsyncAnthropic

    key = os.environ.get("BRAVE_LLM_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return {"error": "no BRAVE_LLM_ANTHROPIC_API_KEY in env"}

    client = AsyncAnthropic(api_key=key)
    messages: list[dict[str, Any]] = [{"role": "user", "content": context}]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": 2048,
        "system": system,
        "messages": messages,
        "tools": [WEB_SEARCH_TOOL],
    }
    resp = await client.messages.create(**kwargs)  # type: ignore[arg-type]

    def _st(u: Any) -> int:
        v = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0)
        return v if isinstance(v, int) else 0

    tokens_in, tokens_out, searches = resp.usage.input_tokens, resp.usage.output_tokens, _st(resp.usage)
    turns = 0
    while resp.stop_reason == "pause_turn" and turns < 4:
        turns += 1
        messages = messages + [{"role": "assistant", "content": resp.content}]
        kwargs["messages"] = messages
        resp = await client.messages.create(**kwargs)  # type: ignore[arg-type]
        tokens_in += resp.usage.input_tokens
        tokens_out += resp.usage.output_tokens
        searches += _st(resp.usage)

    text = "".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
    return {
        "text": _strip_dashes(text),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "searches": searches,
        "usd": usd(tokens_in, tokens_out, searches, model),
    }


async def run_extraction_parity(model: str) -> dict[str, Any]:
    """Does instructor Mode.TOOLS work against Gemini's OpenAI-compatible endpoint?

    This is the other half of the migration: ``LLMClientProtocol.extract`` (DeepSeek via
    OpenRouter today). If this returns a validated object, the extract seam is portable.
    """
    import instructor
    from openai import AsyncOpenAI
    from pydantic import BaseModel, Field

    class AtrativoProbe(BaseModel):
        nome: str = Field(description="Nome oficial do atrativo")
        municipio: str
        uf: str = Field(description="Sigla de 2 letras")
        e_atrativo_turistico: bool

    client = instructor.from_openai(
        AsyncOpenAI(
            api_key=os.environ["GEMINI_API_KEY"],
            base_url=f"{GEMINI_BASE}/openai/",
        ),
        mode=instructor.Mode.TOOLS,
    )
    try:
        obj = await client.chat.completions.create(
            model=model,
            response_model=AtrativoProbe,
            messages=[
                {
                    "role": "user",
                    "content": "Extraia os campos: 'Convento da Penha, Vila Velha, Espírito Santo'",
                }
            ],
        )
        return {"ok": True, "obj": obj.model_dump()}
    except Exception as exc:  # noqa: BLE001 — POC: the failure IS the finding
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemini-3.7-flash")
    ap.add_argument("--with-sonnet", action="store_true", help="also run Claude Sonnet 4.5")
    ap.add_argument("--no-search", action="store_true", help="free-tier shape: no grounding")
    ap.add_argument("--extract", action="store_true", help="probe instructor Mode.TOOLS parity")
    ap.add_argument(
        "--enriched",
        action="store_true",
        help="§4.1: inject deterministic facts (Wikidata/OSM) instead of relying on search",
    )
    ap.add_argument("--json", action="store_true", help="dump raw result dicts")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit("GEMINI_API_KEY not set — get one at https://aistudio.google.com/apikey")

    totals: dict[str, float] = {}
    async with httpx.AsyncClient() as http:
        for sample in SAMPLES:
            context = _build_context(
                sample["nome"], sample["municipio"], sample["uf"], sample["places_context"]
            )
            if args.enriched:
                context = enrich(context, sample["nome"])
            print("=" * 78)
            print(f"{sample['nome']} — {sample['municipio']}/{sample['uf']}")
            print("=" * 78)

            g = await run_gemini(
                http, args.model, COPYWRITER_SYSTEM, context, search=not args.no_search
            )
            if "error" in g:
                print(f"[{args.model}] ERROR: {g['error']}\n")
            else:
                print(f"\n--- {args.model} ---")
                print(g["text"])
                print(
                    f"\n[in={g['tokens_in']} out={g['tokens_out']} searches={g['searches']} "
                    f"usd={g['usd']:.5f}]  queries={g['queries']}"
                )
                totals[args.model] = totals.get(args.model, 0.0) + g["usd"]
                if args.json:
                    print(json.dumps(g, ensure_ascii=False, indent=2))

            if args.with_sonnet:
                s = await run_sonnet(COPYWRITER_SYSTEM, context)
                if "error" in s:
                    print(f"[sonnet] ERROR: {s['error']}\n")
                else:
                    print("\n--- claude-sonnet-4-5 ---")
                    print(s["text"])
                    print(
                        f"\n[in={s['tokens_in']} out={s['tokens_out']} searches={s['searches']} "
                        f"usd={s['usd']:.5f}]"
                    )
                    totals["claude-sonnet-4-5"] = totals.get("claude-sonnet-4-5", 0.0) + s["usd"]
            print()

    if args.extract:
        print("=" * 78)
        print("instructor Mode.TOOLS against Gemini OpenAI-compat endpoint")
        print("=" * 78)
        print(json.dumps(await run_extraction_parity(args.model), ensure_ascii=False, indent=2))
        print()

    n = len(SAMPLES)
    print("=" * 78)
    print("COST PER DESCRIPTION (measured)")
    for model, total in totals.items():
        print(f"  {model:<26} ${total / n:.5f}/atrativo   →  ${total / n * 1000:.2f} / 1.000")
    if len(totals) == 2:
        a, b = totals.values()
        hi, lo = max(a, b), min(a, b)
        print(f"  ratio: {hi / lo:.1f}x")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())

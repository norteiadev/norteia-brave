#!/usr/bin/env python3
"""Run ONE TripAdvisor attraction through the real nascente → rio pipeline and
report the score application, description fields, and the new distrito fields.

This is an operator probe (not a test): it hits TripAdvisor, Nominatim, Melhores
Destinos and the LLM for a single locationId, so it needs RUN_REAL_EXTERNALS=true,
a live DB (BRAVE_DB_URL) + Redis, and a fresh TripAdvisor session cookie jar.

Default target: locationId 2401600 — "Igreja Matriz Nossa Senhora d'Ajuda",
distrito Arraial d'Ajuda, município Porto Seguro (BA). (From the g303270/d2401600
DevTools capture.) Because this is the TripAdvisor lane, the distrito_* fields are
ALWAYS null here — TA cards carry no sub-município address text; distrito only
populates via the Places discovery lane. The keys are printed so the shape is visible.

What it reports:
  1. Per-criterion score application (value × weight → contribution → running total →
     routing gate) — mirrors the dashboard card Log tab (score_breakdown).
  2. The initial description (TA lane captures none — reported explicitly) and, after an
     explicit description-enrichment step, the descricao_editorial (Norteia-voice) field.
  3. The full attraction JSON: nascente canonical (incl. distrito_*/subdistrito_*) +
     rio normalized (incl. *_value inputs, municipio, descricao_editorial) + score fields.

Usage:
  set -a; . ./.env; set +a          # load DB/Redis/LLM keys (also sets RUN_REAL_EXTERNALS)
  .venv/bin/python -m scripts.single_attraction.run_single_attraction \
      --curl scripts/single_attraction/ta_session.curl
  # options: --location-id 2401600 --uf BA --name "Igreja Matriz Nossa Senhora d'Ajuda"
  #          --no-enrich-description   (skip the LLM/Melhores Destinos step)
  #          --rescore-after-enrich    (re-run scoring after the completude bump)

The cookie jar is read from --curl (a DevTools "Copy as cURL (bash)") and injected into
Redis key brave:ta:session — the same session the collector reads. No API server needed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# scripts.ta_bootstrap.parse_curl handles cookies/UA/query_ids extraction; we only need
# cookies + user_agent + session_id (single-attraction fetches use baked query-ids, not
# the session's). Import it before touching the venv-only deps below.
from scripts.ta_bootstrap import parse_curl  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TA_SESSION_KEY = "brave:ta:session"


def _load_session_from_curl(curl_path: Path) -> dict:
    """Parse a DevTools cURL file into a brave:ta:session dict.

    Handles bash $'...' ANSI-C quoting (which parse_curl's ['"] anchor would miss) by
    stripping the leading $ before each quote, and decoding the one \\u0021 (!) that
    appears in the roybatty cookie so the value is not corrupted.
    """
    raw = curl_path.read_text(encoding="utf-8")
    # $'...'  ->  '...'   so the -b / --data-raw regexes anchor on the quote.
    normalized = raw.replace("$'", "'").replace("\\u0021", "!")
    session = parse_curl(normalized)
    if not session.get("cookies"):
        sys.exit(f"ERROR: no cookies parsed from {curl_path} — is it a 'Copy as cURL (bash)'?")
    if "datadome" not in session["cookies"]:
        print("WARNING: no 'datadome' cookie found — TA may return 403 (session expired).")
    return session


def _print_score_log(rio, config, source: str) -> None:
    """Reproduce the dashboard card Log tab: per-criterion contribution + routing gate."""
    n = rio.normalized or {}
    bd = rio.score_breakdown or {}
    criteria = [
        ("origem", "origem_value", config.weight_origem),
        ("completude", "completude_value", config.weight_completude),
        ("corroboracao", "corroboracao_value", config.weight_corroboracao),
        ("atualidade", "atualidade_value", config.weight_atualidade),
        ("validacao_humana", "validacao_humana_value", config.weight_validacao_humana),
    ]
    print("\n" + "=" * 74)
    print("SCORE APPLICATION LOG  (value × weight ÷ 100 = contribution)")
    print("=" * 74)
    print(f"{'criterion':<18}{'value':>8}{'weight':>9}{'contrib':>10}{'running':>10}")
    print("-" * 74)
    running = 0.0
    for label, vkey, weight in criteria:
        value = float(n.get(vkey, 0.0))
        contrib = bd.get(label, round(value * weight / 100.0, 2))
        running = round(running + contrib, 2)
        print(f"{label:<18}{value:>8.1f}{weight:>9.1f}{contrib:>10.2f}{running:>10.2f}")
    print("-" * 74)
    threshold = getattr(config, "threshold_mar", 80.0)
    gate = "mar (≥ threshold → promote)" if (rio.score or 0) >= threshold else "dlq (< threshold → review)"
    print(f"final score = {rio.score}   score_version = {rio.score_version}")
    print(f"threshold_mar = {threshold}   routing = {rio.routing}  →  {gate}")
    if rio.dlq_reason:
        print(f"dlq_reason = {rio.dlq_reason}")
    # Frontend-parity Log object (dashboard/components/painel/PainelDrawer.tsx:150).
    print("\ncard Log object (frontend parity):")
    print(json.dumps({
        "score_breakdown": bd,
        "dlq_reason": rio.dlq_reason,
        "source": source,
        "processed_at": rio.processed_at.isoformat() if rio.processed_at else None,
    }, indent=2, ensure_ascii=False, default=str))


def _full_attraction_json(session, rio) -> dict:
    """Assemble the full attraction view: nascente canonical + rio normalized + score."""
    from brave.core.models import NascenteRecord

    nascente = session.get(NascenteRecord, rio.nascente_id)
    payload = (nascente.payload if nascente else {}) or {}
    canonical = payload.get("canonical", {}) or {}
    norm = rio.normalized or {}
    # distrito_* are written to canonical at ingest (Places lane) OR to rio.normalized at
    # enrichment (MD breadcrumb, TA lane). Read normalized first, fall back to canonical.
    def _distrito(k):
        return norm.get(k) if norm.get(k) is not None else canonical.get(k)
    return {
        "source": nascente.source if nascente else None,
        "source_ref": nascente.source_ref if nascente else None,
        "entity_type": rio.entity_type,
        "uf": rio.uf,
        "municipio_id": rio.municipio_id,
        "score": rio.score,
        "routing": rio.routing,
        "score_version": rio.score_version,
        "score_breakdown": rio.score_breakdown,
        "dlq_reason": rio.dlq_reason,
        "sub_state": rio.sub_state,
        "nascente_source": nascente.source if nascente else None,
        # distrito relation (canonical @ ingest OR normalized @ MD-breadcrumb enrichment):
        "distrito_name": _distrito("distrito_name"),
        "distrito_code": _distrito("distrito_code"),
        "distrito_municipio_ibge": _distrito("distrito_municipio_ibge"),
        "subdistrito_name": _distrito("subdistrito_name"),
        "subdistrito_code": _distrito("subdistrito_code"),
        "distrito_source": _distrito("distrito_source"),
        "canonical": canonical,
        "normalized": rio.normalized,
    }


async def _fetch_card(ta_client, location_id: int, name: str) -> dict:
    """Confirm the session works via fetch_attraction_geo and build the ingest card.

    lat/lng are left None; TripAdvisorAtrativosIngest._ingest_one geocodes name+UF via
    Nominatim (TA-15) to resolve the município — the faithful production path.
    """
    geo = None
    try:
        geo = await ta_client.fetch_attraction_geo(location_id)
    except Exception as exc:  # noqa: BLE001 — surface session problems, keep going
        print(f"WARNING: fetch_attraction_geo failed ({type(exc).__name__}: {exc}).")
    if geo:
        print(f"TA geo: city={geo.get('city_name')} state={geo.get('state_name')} "
              f"(city_geo_id={geo.get('city_geo_id')})")
    else:
        print("WARNING: no geo returned — proceeding with name-based geocode only.")
    return {
        "locationId": location_id,
        "name": name,
        "category": "",
        "lat": None,
        "lng": None,
        "review_count": 0,
        "rating": 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one TA attraction through nascente→rio.")
    ap.add_argument("--location-id", type=int, default=2401600)
    ap.add_argument("--uf", default="BA")
    ap.add_argument("--name", default="Igreja Matriz Nossa Senhora d'Ajuda")
    # TA geo/Nominatim often can't coordinate a specific church by name; seed the
    # card with the attraction's real lat/lng so resolve_municipio's haversine hits
    # the parent município (Porto Seguro seat). Default = Arraial d'Ajuda church.
    ap.add_argument("--lat", type=float, default=-16.4886)
    ap.add_argument("--lng", type=float, default=-39.0722)
    ap.add_argument("--curl", default=str(Path(__file__).parent / "ta_session.curl"))
    ap.add_argument("--no-enrich-description", action="store_true")
    ap.add_argument("--rescore-after-enrich", action="store_true")
    args = ap.parse_args()

    # Force real externals BEFORE constructing any client (mirrors loadtest).
    os.environ["RUN_REAL_EXTERNALS"] = "true"
    if "BRAVE_DB_URL" not in os.environ:
        sys.exit("ERROR: BRAVE_DB_URL not set. Run: set -a; . ./.env; set +a")

    import redis as redis_lib
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from brave.config.settings import AppConfig, ScoreConfig, TripAdvisorConfig
    from brave.clients.nominatim import NominatimGeocoderClient
    from brave.domains.tripadvisor.ibge import load_ibge_csv
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest
    from brave.lanes.tripadvisor.client import TripAdvisorClient
    from brave.core.models import RioRecord
    from sqlalchemy import select

    app_config = AppConfig()
    score_config = ScoreConfig()
    redis_url = os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0")
    redis_client = redis_lib.from_url(redis_url)

    # 1. Inject the fresh cookie jar into the session Redis key the collector reads.
    session_dict = _load_session_from_curl(Path(args.curl))
    redis_client.set(_TA_SESSION_KEY, json.dumps(session_dict))
    print(f"Injected TA session into Redis {_TA_SESSION_KEY} "
          f"({len(session_dict['cookies'])} cookies, ua={'yes' if session_dict.get('user_agent') else 'no'}).")

    engine = create_engine(os.environ["BRAVE_DB_URL"])
    Session = sessionmaker(bind=engine)
    session = Session()

    ta_client = TripAdvisorClient(config=TripAdvisorConfig(), redis=redis_client)
    geocoder = NominatimGeocoderClient(config=app_config.nominatim, redis=redis_client)
    ibge_csv = _REPO_ROOT / "data" / "ibge" / "ibge_municipios.csv"
    ibge_records = load_ibge_csv(ibge_csv)

    ingest = TripAdvisorAtrativosIngest(
        ta_client=ta_client,
        session=session,
        config=score_config,
        ibge_records=ibge_records,
        destino_rio_map=None,
        geocoder=geocoder,
        ta_config=TripAdvisorConfig(),
    )

    print(f"\n>>> Ingesting locationId={args.location_id} '{args.name}' (UF={args.uf}) "
          "through nascente → rio (real TA + Nominatim)...")
    card = asyncio.run(_fetch_card(ta_client, args.location_id, args.name))
    card["lat"] = args.lat
    card["lng"] = args.lng
    asyncio.run(ingest._ingest_one(args.uf, card, run_rio=True, enrich_reviews=True))
    session.commit()

    source_ref = f"tripadvisor:attraction:{args.location_id}"
    from brave.core.models import NascenteRecord
    rio = session.execute(
        select(RioRecord)
        .join(NascenteRecord, RioRecord.nascente_id == NascenteRecord.id)
        .where(NascenteRecord.source_ref == source_ref)
    ).scalars().first()
    if rio is None:
        sys.exit(f"ERROR: no RioRecord for {source_ref} — the card likely quarantined "
                 "(ibge_unmatched / parent_destino_absent). Check poison_quarantine.")

    nascente = session.get(NascenteRecord, rio.nascente_id)
    source = nascente.source if nascente else "tripadvisor"
    _print_score_log(rio, score_config, source)

    # Initial description — the TA lane captures none.
    print("\n" + "=" * 74)
    print("DESCRIPTION")
    print("=" * 74)
    print("initial description (TA lane): NONE — TripAdvisor cards carry no description "
          "field; only the MTUR/Places lane sets 'posicionamento'.")
    print(f"descricao_editorial (before enrichment): "
          f"{(rio.normalized or {}).get('descricao_editorial')!r}")

    # Explicit enrichment step (normally inline in produce() / the enrich_places task):
    # PlacesEnrichmentAgent does description (copywriter) + distrito + hours/contact/price
    # + liveness off one Google place_details call. The MD lane was removed.
    if not args.no_enrich_description:
        from brave.clients.llm import RealLLMClient
        from brave.clients.places import (
            RealPlacesClient,
            load_municipio_name_ibge_lookup,
        )
        from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent
        from brave.shared.ibge_distritos import load_distritos

        # The agent accepts entry sub_state None (TA inline) or "signals_gathered".
        rio.sub_state = None
        session.commit()
        places_client = RealPlacesClient(
            api_key=os.environ.get("BRAVE_PLACES_API_KEY", ""),
            ibge_lookup=load_municipio_name_ibge_lookup(session),
        )
        llm_client = RealLLMClient(
            config=app_config.llm, redis_client=redis_client,
            session=session, lane="atrativo_copywriter",
        )
        agent = PlacesEnrichmentAgent(
            places_client=places_client, session=session, config=score_config,
            llm_client=llm_client, distritos=load_distritos(session),
            voice_model_slug=app_config.atrativo_voice_model_slug,
        )
        print("\n>>> Running PlacesEnrichmentAgent (Google Places + copywriter web_search)...")
        try:
            asyncio.run(agent.run(rio))
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(f"enrichment step failed ({type(exc).__name__}: {exc}) — floor kept.")
        session.refresh(rio)
        descricao = (rio.normalized or {}).get("descricao_editorial")
        if descricao:
            print(f"descricao_editorial (after enrichment):\n{descricao}")
        else:
            print("descricao_editorial (after enrichment): None — no confident Places "
                  "match and web search produced nothing usable (floor kept).")

        if args.rescore_after_enrich:
            from brave.core.rio.routing import reprocess_record
            reprocess_record(session, rio.id, score_config)
            session.commit()
            session.refresh(rio)
            print("\n--- re-scored after completude bump ---")
            _print_score_log(rio, score_config, source)

    # Full attraction JSON.
    print("\n" + "=" * 74)
    print("FULL ATTRACTION JSON  (canonical incl. distrito_* + normalized + score)")
    print("=" * 74)
    print(json.dumps(_full_attraction_json(session, rio), indent=2, ensure_ascii=False, default=str))

    session.close()


if __name__ == "__main__":
    main()

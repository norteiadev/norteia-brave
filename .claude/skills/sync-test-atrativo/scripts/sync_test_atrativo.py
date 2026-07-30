#!/usr/bin/env python3
"""Sync test: ONE TripAdvisor atrativo, one completude degrau per stage, each pushed
to norteia-api — so you can see exactly which fields the API gains at each degrau.

Operator probe (not a test): hits TripAdvisor, Nominatim, Google Places, the LLM and
the real norteia-api. Needs RUN_REAL_EXTERNALS=true, live DB + Redis, a fresh TA cookie
jar, BRAVE_NORTEIA_API_URL + BRAVE_NORTEIA_API_SERVICE_TOKEN.

Stages (run one at a time, or --stage all):
  base       ingest TA card → rio → steward-validate → promote_to_mar → push
             (completude = TA field coverage: name/uf/location_id/lat/lng/rating/reviews)
  places     PlacesEnrichmentAgent WITHOUT the copywriter → re-score → promote → push
             (gains: hours, google coords, place_id, phone/website/price_level, distrito)
  descricao  same agent WITH the copywriter → completude degrau 90 → re-score → push
             (gains: description)

Each stage prints the exact push payload and the norteia-api HTTP result (a 422 body is
printed verbatim — that is the sync signal worth having).

Usage (inside the worker container, where the API hostname resolves):
  docker exec norteia-brave-worker-1 /app/.venv/bin/python \\
      /app/.claude/skills/sync-test-atrativo/scripts/sync_test_atrativo.py \\
      --curl /app/tmp/ta_session.curl --stage base
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_TA_SESSION_KEY = "brave:ta:session"
# .claude/skills/sync-test-atrativo/scripts/<this file> → repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
# Run by absolute path (not -m), so sys.path[0] is this scripts/ dir — put the repo root
# on the path before importing the `scripts.*` helpers the probe reuses.
sys.path.insert(0, str(_REPO_ROOT))

from scripts.ta_bootstrap import parse_curl  # noqa: E402

_STAGES = ("base", "places", "descricao")


def _load_session_from_curl(curl_path: Path) -> dict:
    """Parse a DevTools 'Copy as cURL (bash)' file into a brave:ta:session dict."""
    raw = curl_path.read_text(encoding="utf-8")
    normalized = raw.replace("$'", "'").replace("\\u0021", "!")
    session = parse_curl(normalized)
    if not session.get("cookies"):
        sys.exit(f"ERROR: no cookies parsed from {curl_path}")
    if "datadome" not in session["cookies"]:
        print("WARNING: no 'datadome' cookie — TA may 403 (session expired).")
    return session


def _print_score(rio, config, label: str) -> None:
    n = rio.normalized or {}
    bd = rio.score_breakdown or {}
    print(f"\n--- score @ {label} ---")
    for crit, key, weight in (
        ("origem", "origem_value", config.weight_origem),
        ("completude", "completude_value", config.weight_completude),
        ("corroboracao", "corroboracao_value", config.weight_corroboracao),
        ("atualidade", "atualidade_value", config.weight_atualidade),
        ("validacao_humana", "validacao_humana_value", config.weight_validacao_humana),
    ):
        value = float(n.get(key, 0.0))
        print(f"  {crit:<18}{value:>7.1f} × {weight:<5.1f} = {bd.get(crit, 0.0):>7.2f}")
    print(f"  score={rio.score}  routing={rio.routing}  dlq_reason={rio.dlq_reason}")


async def _push(payload: dict) -> None:
    """POST the payload to norteia-api and print the outcome (422 body included)."""
    import httpx

    from brave.clients.norteia_api import NorteiaApiClient

    url = os.environ.get("BRAVE_NORTEIA_API_URL", "")
    token = os.environ.get("BRAVE_NORTEIA_API_SERVICE_TOKEN", "")
    if not url or not token:
        sys.exit("ERROR: BRAVE_NORTEIA_API_URL / BRAVE_NORTEIA_API_SERVICE_TOKEN not set")
    async with NorteiaApiClient(base_url=url, service_token=token) as client:
        try:
            result = await client.push_attraction(payload)
            print(f"PUSH ok → {json.dumps(result, ensure_ascii=False)}")
        except httpx.HTTPStatusError as exc:
            print(f"PUSH FAILED {exc.response.status_code} → {exc.response.text[:2000]}")


def _promote_and_push(session, rio, config, *, validate: bool) -> None:
    """Steward-validate (first stage only), promote to Mar, push, print the payload."""
    from brave.core.dlq.service import validate_and_promote_rio
    from brave.core.mar.service import build_push_payload, promote_to_mar

    if validate:
        # The TA lane ceiling sits under threshold_mar; the steward gate (validacao_humana
        # = 100 → re-score) is the production path to Mar. Same helper the router uses.
        validate_and_promote_rio(session, rio)
        session.commit()
        session.refresh(rio)
        _print_score(rio, config, "after steward validate")

    if rio.routing != "mar":
        print(f"NOT pushed: routing={rio.routing} (dlq_reason={rio.dlq_reason}).")
        return

    mar = promote_to_mar(session, rio)
    if mar is None:
        session.commit()
        session.refresh(rio)
        print(f"NOT pushed: promote_to_mar held the record (dlq_reason={rio.dlq_reason}).")
        return
    session.commit()

    payload = build_push_payload(mar, rio)
    print("\npush payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    asyncio.run(_push(payload))


def _build_agent(session, config, app_config, redis_client, *, with_description: bool):
    from brave.clients.llm import RealLLMClient
    from brave.clients.places import RealPlacesClient, load_municipio_name_ibge_lookup
    from brave.lanes.atrativos.places_enrichment import PlacesEnrichmentAgent
    from brave.shared.ibge_distritos import load_distritos

    llm_client = (
        RealLLMClient(
            config=app_config.llm,
            redis_client=redis_client,
            session=session,
            lane="atrativo_copywriter",
        )
        if with_description
        else None
    )
    return PlacesEnrichmentAgent(
        places_client=RealPlacesClient(
            api_key=os.environ.get("BRAVE_PLACES_API_KEY", ""),
            ibge_lookup=load_municipio_name_ibge_lookup(session),
        ),
        session=session,
        config=config,
        llm_client=llm_client,
        distritos=load_distritos(session),
        voice_model_slug=app_config.atrativo_voice_model_slug,
        description_enabled=with_description,
        enable_web_search=True,  # probe is real-externals only
        max_distance_km=app_config.places_match_max_distance_km,
    )


def main() -> None:  # noqa: PLR0915 — linear operator probe, stages read top-to-bottom
    ap = argparse.ArgumentParser(description="Atrativo → norteia-api sync test by completude degrau.")
    ap.add_argument("--location-id", type=int, default=2401600)
    ap.add_argument("--uf", default="BA")
    ap.add_argument("--name", default="Igreja Matriz Nossa Senhora d'Ajuda")
    ap.add_argument("--lat", type=float, default=-16.4886)
    ap.add_argument("--lng", type=float, default=-39.0722)
    ap.add_argument("--curl", default=str(_REPO_ROOT / "tmp" / "ta_session.curl"))
    ap.add_argument("--stage", choices=(*_STAGES, "push", "all"), default="all")
    ap.add_argument("--list-geoid", type=int, help="list page-1 attraction cards for a geoId and exit")
    args = ap.parse_args()

    os.environ["RUN_REAL_EXTERNALS"] = "true"
    if "BRAVE_DB_URL" not in os.environ:
        sys.exit("ERROR: BRAVE_DB_URL not set")

    import redis as redis_lib
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from brave.clients.nominatim import NominatimGeocoderClient
    from brave.config.settings import AppConfig, ScoreConfig, TripAdvisorConfig
    from brave.core.models import NascenteRecord, RioRecord
    from brave.domains.tripadvisor.atrativos import TripAdvisorAtrativosIngest
    from brave.domains.tripadvisor.client import TripAdvisorClient
    from brave.domains.tripadvisor.ibge import load_ibge_csv

    app_config = AppConfig()
    config = ScoreConfig()
    redis_client = redis_lib.from_url(os.environ.get("BRAVE_DB_REDIS_URL", "redis://localhost:6379/0"))

    session_dict = _load_session_from_curl(Path(args.curl))
    redis_client.set(_TA_SESSION_KEY, json.dumps(session_dict))
    print(f"TA session injected ({len(session_dict['cookies'])} cookies).")

    engine = create_engine(os.environ["BRAVE_DB_URL"])
    session = sessionmaker(bind=engine)()
    ta_client = TripAdvisorClient(config=TripAdvisorConfig(), redis=redis_client)

    if args.list_geoid:
        async def _list() -> None:
            async for _offset, cards in ta_client.fetch_attractions_paginated_gql(
                args.list_geoid, start_page=1, max_pages=1
            ):
                for card in cards[:30]:
                    print(json.dumps(card, ensure_ascii=False))
        asyncio.run(_list())
        return

    source_ref = f"tripadvisor:attraction:{args.location_id}"
    rio = session.execute(
        select(RioRecord)
        .join(NascenteRecord, RioRecord.nascente_id == NascenteRecord.id)
        .where(NascenteRecord.source_ref == source_ref)
    ).scalars().first()

    stages = _STAGES if args.stage == "all" else (args.stage,)

    # ---------------- stage 1: base TA card ----------------
    if "base" in stages:
        print("\n" + "=" * 74)
        print("STAGE 1 — base (TripAdvisor card only)")
        print("=" * 74)
        if rio is None:
            ingest = TripAdvisorAtrativosIngest(
                ta_client=ta_client,
                session=session,
                config=config,
                ibge_records=load_ibge_csv(_REPO_ROOT / "data" / "ibge" / "ibge_municipios.csv"),
                destino_rio_map=None,
                geocoder=NominatimGeocoderClient(config=app_config.nominatim, redis=redis_client),
                ta_config=TripAdvisorConfig(),
            )
            card = {
                "locationId": args.location_id,
                "name": args.name,
                "category": "",
                "lat": args.lat,
                "lng": args.lng,
                "review_count": 0,
                "rating": 0.0,
            }
            asyncio.run(ingest._ingest_one(args.uf, card, run_rio=True, enrich_reviews=True))
            session.commit()
            rio = session.execute(
                select(RioRecord)
                .join(NascenteRecord, RioRecord.nascente_id == NascenteRecord.id)
                .where(NascenteRecord.source_ref == source_ref)
            ).scalars().first()
            if rio is None:
                sys.exit(f"ERROR: no RioRecord for {source_ref} (card quarantined?)")
        else:
            print(f"(rio {rio.id} already exists — reusing)")
        _print_score(rio, config, "ingest")
        _promote_and_push(session, rio, config, validate=True)

    if rio is None:
        sys.exit(f"ERROR: {source_ref} not in Rio — run --stage base first")

    # Re-push the current state (no enrichment, no extra Places spend).
    if args.stage == "push":
        _print_score(rio, config, "current")
        _promote_and_push(session, rio, config, validate=False)
        session.close()
        return

    # ---------------- stage 2: Places enrichment (no description) ----------------
    if "places" in stages:
        print("\n" + "=" * 74)
        print("STAGE 2 — Places enrichment (hours / coords / place_id / contatos / distrito)")
        print("=" * 74)
        normalized = dict(rio.normalized or {})
        normalized.pop("google_enriched", None)  # re-runnable probe
        rio.normalized = normalized
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(rio, "normalized")
        session.commit()
        agent = _build_agent(session, config, app_config, redis_client, with_description=False)
        try:
            asyncio.run(agent.run(rio))
            session.commit()
        except Exception as exc:  # noqa: BLE001 — degrade like production
            session.rollback()
            print(f"places enrichment failed ({type(exc).__name__}: {exc}) — floor kept.")
        session.refresh(rio)
        n = rio.normalized or {}
        print("gained: " + json.dumps({
            k: n.get(k) for k in (
                "weekday_text", "google_place_id", "phone", "website", "price_level",
                "lat", "lon", "most_recent_review_at", "distrito_name", "distrito_code",
            )
        }, ensure_ascii=False, default=str))
        _print_score(rio, config, "after places enrichment")
        _promote_and_push(session, rio, config, validate=False)

    # ---------------- stage 3: descricao_editorial (completude 90) ----------------
    if "descricao" in stages:
        print("\n" + "=" * 74)
        print("STAGE 3 — descricao_editorial (completude degrau 90)")
        print("=" * 74)
        # Drives the production path: on an already-Places-enriched record the agent
        # skips the PAID Places sub-step and runs the description backfill alone (the
        # copywriter grounds on web_search, no place_details SKU re-spent).
        from sqlalchemy.orm.attributes import flag_modified

        normalized = dict(rio.normalized or {})
        if normalized.pop("descricao_attempts", None) is not None:
            rio.normalized = normalized  # re-arm the copywriter budget (re-runnable probe)
            flag_modified(rio, "normalized")
            session.commit()
        agent = _build_agent(session, config, app_config, redis_client, with_description=True)
        try:
            asyncio.run(agent.run(rio))
            session.commit()
        except Exception as exc:  # noqa: BLE001 — degrade like production
            session.rollback()
            print(f"description enrichment failed ({type(exc).__name__}: {exc}) — floor kept.")
        session.refresh(rio)
        if not (rio.normalized or {}).get("descricao_editorial"):
            print("copywriter returned nothing — floor kept.")
        print(f"descricao_editorial: {(rio.normalized or {}).get('descricao_editorial')!r}")
        _print_score(rio, config, "after descricao_editorial")
        _promote_and_push(session, rio, config, validate=False)

    session.close()


if __name__ == "__main__":
    main()

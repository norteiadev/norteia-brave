# single_attraction — one-attraction pipeline probe

Runs ONE TripAdvisor attraction through the real **nascente → rio** pipeline and prints:

1. **Score application log** — per-criterion `value × weight ÷ 100 = contribution`, running
   total, and the routing gate (`mar` / `dlq`). Mirrors the dashboard card **Log** tab
   (`score_breakdown`).
2. **Description** — the initial description (the TA lane has none) and, after an explicit
   enrichment step, `descricao_editorial` (Melhores Destinos → Norteia voice).
3. **Full attraction JSON** — nascente `canonical` (incl. the new `distrito_*`/`subdistrito_*`
   keys, **null on the TA lane**) + rio `normalized` + score fields.

## Run

```bash
set -a; . ./.env; set +a        # DB/Redis/LLM keys (also sets RUN_REAL_EXTERNALS)
.venv/bin/python -m scripts.single_attraction.run_single_attraction \
    --curl scripts/single_attraction/ta_session.curl
```

Options: `--location-id 2401600` `--uf BA` `--name "..."` `--no-enrich-description`
`--rescore-after-enrich`.

## Requirements

- `RUN_REAL_EXTERNALS=true`, live `BRAVE_DB_URL` + Redis. Hits TripAdvisor, Nominatim,
  Melhores Destinos, and the LLM.
- A fresh **`ta_session.curl`** — a DevTools *Copy as cURL (bash)* from any TripAdvisor
  attraction page. Its cookie jar (esp. `datadome`, `TASession`, `TASID`) is injected into
  Redis `brave:ta:session`. **Gitignored — it is a live credential; never commit it.**

## Notes

- Default target `2401600` = *Igreja Matriz Nossa Senhora d'Ajuda*, distrito Arraial d'Ajuda,
  Porto Seguro (BA).
- `distrito_*` is **always null here** — TA cards carry no sub-município address text; distrito
  only populates via the Places discovery lane. Keys are printed so the shape is visible.
- Description enrichment is normally a post-signal FSM task; this probe runs it inline and
  sets `sub_state="signals_gathered"` to satisfy the agent's idempotency guard.

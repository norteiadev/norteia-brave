# TripAdvisor — Coleta de dados de Atrativos

> Como o `norteia-brave` coleta atrativos (attractions) do TripAdvisor: aquisição de
> sessão, transportes de fetch, parsing, geo-enriquecimento, scoring §7.6 e ingestão
> em Nascente. Extraído do código real em `brave/domains/tripadvisor/` e
> `brave/tasks/pipeline.py`.

## 0. Postura legal / LGPD (ler primeiro)

- **ToS**: scraping sistemático viola os Termos do TripAdvisor (Seção 5, "Use of Site").
  Este lane NÃO roda no beat autônomo — é **operator-gated**. Ver `data/tripadvisor/README`.
- **LGPD**: só sinais **agregados** de review são persistidos — `review_count`, `rating`,
  `most_recent_review_at`. Nomes de autores, IDs de reviewers e texto de review **nunca**
  são extraídos. `TripAdvisorReviewSignals` usa `extra="forbid"` para impedir drift para PII.
- **NO WHATSAPP OUTREACH**: atrativos TA nunca entram no pipeline de outreach WhatsApp.
- **Segredos**: cookies / `session_id` (TASID) / `proxy_url` **nunca** são logados. Auditoria
  registra só `cookie_count` + chaves de `query_ids` + presença de `session_id` (boolean).

## 1. Modelo de aquisição — sessão injetada pelo operador

Não há Playwright/headless-browser no path de produção. Um humano captura cookies de um
browser real logado no TripAdvisor (DevTools → **Copy as cURL**) e injeta via API.

### Endpoint de injeção

`POST /api/v1/tripadvisor/session` (auth: `require_steward_or_bearer`)

Body (`SessionInjectBody`, `extra="forbid"`, limite 64 KB):

```jsonc
{
  "cookies": { "datadome": "...", "TASession": "...", "TASID": "...", ... },  // não-vazio
  "query_ids": { "attractions": "a5cb7fa004b5e4b5" },                          // ≥1 entrada
  "user_agent": "Mozilla/5.0 (Macintosh; ...) Chrome/147.0.0.0 Safari/537.36",
  "acquired_at": "2026-07-03T12:00:00Z",
  "session_id": "<valor do cookie TASID>",   // opcional; derivado de cookies["TASID"] se ausente
  "locale": "pt-BR"
}
```

Workflow do endpoint:
1. Size-check 64 KB → 2. Valida (Pydantic) → 3. Grava sessão no Redis
   (`brave:ta:session`, TTL = `TripAdvisorConfig.session_ttl`, default 1800s/30min) →
4. **Canary gate** síncrono (fetch real de 1 página) → 5. Audit-log (só metadados) →
6. Retorna `{"status": "ready", "canary": "ready"}`.

**Canary** (`_run_canary`): faz `fetch_attractions(geo_id=303380, max_pages=1)` (Minas Gerais,
qualquer UF válida — NÃO nacional 294280), timeout 15s. Resultados:
- Sucesso (lista não-vazia) → 200 ready.
- `SessionExpiredError` (403/429) ou timeout → **deleta a key** e `422 invalid_session`.
- Resultado vazio (200 mas queryId stale) → deleta a key e `422 invalid_session`.
- Falha de infra (DNS, proxy, ValueError de UF) → **NÃO deleta** a sessão, `503 canary_unverified`
  (operador pode retentar sem recapturar a credencial escassa).

### Status da sessão

`GET /api/v1/tripadvisor/session/status` → `{present, expires_in, query_ids, reason}`.
`reason: "needs_bootstrap"` quando ausente + marker `brave:ta:needs_bootstrap` setado por um
sweep que bateu `SessionMissingError`.

### Shape da sessão no Redis (`brave:ta:session`)

```jsonc
{
  "cookies": { "datadome": "...", ... },   // dict plano (normaliza list-form legado Phase 11)
  "query_ids": { "attractions": "..." },
  "user_agent": "...",
  "acquired_at": "ISO8601",
  "session_id": "<TASID>"
}
```

### Cookie write-back (keep-alive)

Toda resposta HTTP bem-sucedida re-injeta os Set-Cookie via `persist_rotated_cookies`
(`session.py`): faz merge no `brave:ta:session`, re-deriva `session_id` do TASID e **desliza
o TTL**. Best-effort — nunca levanta, nunca loga valores. O beat `ta_keepalive` faz 1 GET HTML
(geoId 294280, page 1) periodicamente para re-mintar o cookie `datadome` antes de expirar.

## 2. Transportes de fetch (`client.py::TripAdvisorClient`)

Endpoint GraphQL: `https://www.tripadvisor.com/data/graphql/ids` (persisted queries).
Todo request passa cookies + `User-Agent` da sessão e, se configurado, o proxy residencial
`BRAVE_TA_PROXY_URL`. Sem proxy, o egress sai do IP do datacenter — classe walled por DataDome.

| Método | Query ID | Transporte | Uso |
|--------|----------|-----------|-----|
| `fetch_attractions(geo_id, max_pages=1)` | `a5cb7fa004b5e4b5` (**hardcoded**) | GraphQL POST | Listagem AttractionsFusion — **página única** |
| `fetch_attractions_paginated(geo_id, start, max)` | — (HTML SSR) | GET `-oa{offset}-` | **Paginação** (bulk nacional) |
| `fetch_attraction_geo(location_id)` | `d3d4987463b78a39` | GraphQL POST | Geo do pai (cityName/stateName) — sem PII |
| `fetch_attraction_detail(location_id)` | `444040f131735091` | GraphQL POST | (legado; não mais chamado no ingest) |
| `fetch_destinations(uf, max_pages)` | `_DESTINATIONS_QID` (None até capturar) | GraphQL POST | Destinos/GEO — deferido, QID não capturado |
| `resolve_geo_id(uf)` | — | Redis/seed JSON | UF → geoId inteiro |

### 2a. `fetch_attractions` — GraphQL, página única

- QID **hardcoded** `a5cb7fa004b5e4b5` — NÃO lido de `session["query_ids"]` (evita injeção de
  QID stale/errado — T-13-01-02).
- Variables reais: `request.routeParameters` = `{geoId, contentType:"attraction",
  webVariant:"AttractionsFusion", filters:[{id:"allAttractions", value:["true"]}]}`,
  `tracking.screenName:"AttractionsFusion"` + `pageviewUid` (uuid4 por tentativa),
  `sessionId`, `unitLength:"MILES"`, `currency:"USD"`.
- **PAGINATION GAP**: AttractionsFusion não tem param de página/offset confirmado; o payload é
  idêntico a cada iteração. `max_pages>1` levanta `NotImplementedError` (fail-loud, evita
  duplicar cards da página 1). Multi-página é a via HTML SSR (2b).
- **TRANSIENT-RETRY** (260701-has): às vezes retorna 200 com `Result[0].status.success==false`,
  `totalResults==0`, `sections==[]` para um geoId **válido** — retry idêntico resolve. Bounded
  por `attractions_transient_max_retries`. `status` ausente / success true → real-empty em 1 call.
- 403/429 → `SessionExpiredError`.

### 2b. `fetch_attractions_paginated` — HTML SSR (paginação real)

- URL: `https://www.tripadvisor.com/Attractions-g{geo_id}-Activities-a_allAttractions.true-oa{offset}-Brazil.html`,
  `offset = (page-1)*30`. **SSRF guard**: `geo_id` tem de ser `int` (rejeita bool/str antes de qualquer GET).
- Cap: **334 páginas / oa9990** (teto de 10000 resultados do TA), sempre clampado.
- Requer headers de navegação (`Accept: text/html...`, `Accept-Language: pt-BR...`) — DataDome
  403 num GET só-UA da página SSR (o surface XHR/GraphQL tolera UA nu; a SSR não).
- Recupera a JSON island embutida (`_extract_sections_from_html`): acha o `<script src="data:...">`
  com o marker `WebPresentation_SingleFlexCardSection`, url-decode, `json.loads` do maior literal
  string que carrega o marker, e walk recursivo (`_find_flexcard_sections`, profundidade ≤40)
  re-parseando chunks JSON aninhados. Só stdlib `re`+`json` (sem lxml/bs4/playwright). Never-raises.
- Faz `yield (offset, cards)` por página, throttle `page_throttle_seconds` entre páginas.

### 2c. Parsing dos cards (`_parse_attractions_page`)

Mantém só seções `WebPresentation_SingleFlexCardSection` e extrai de cada `singleFlexCardContent`:

| Campo normalizado | Origem no card |
|---|---|
| `name` | `cardTitle.text` |
| `locationId` (int) | `cardLink.webRoute.typedParams.detailId` |
| `rating` (float) | `bubbleRating.rating` |
| `review_count` (int) | `bubbleRating.reviewCount` |
| `category` (str) | `primaryInfo.text` |

Cards malformados são pulados com debug log (never-raises). Usa `(card.get(k) or {})` para
guardar campos present-but-null (ex. `bubbleRating=null`).

## 3. Resolução geográfica

- **UF → geoId** (`geo.py::resolve_geo_id`): Redis cache (`brave:ta:geo:{uf}`, TTL 24h) →
  seed `data/tripadvisor/uf_geoids.json` (27 UFs) → `ValueError` (fail-closed).
  ⚠️ Memória do projeto: `uf_geoids.json` tem geoIds errados em algumas UFs — validar por UF real.
- **Atrativo → município** (no ingest): resolução em cascata até casar um `IbgeMunicipio`:
  1. `resolve_municipio(name, uf, ibge, lat, lng)` com lat/lng do card (normalmente None na listagem).
  2. **Nominatim** (`geocoder.geocode`) quando 1 falha — promove lat/lng geocodados.
  3. **`fetch_attraction_geo`** (qid `d3d4987463b78a39`) — retorna `cityName`+`stateName` direto;
     `state_name_to_uf` deriva a UF; `countryId != 294280` (não-Brasil) → descartado.
  - Sem match → quarentena `ibge_unmatched` (nunca dropado silenciosamente).

## 4. Scoring §7.6 (`scoring.py`)

Cada critério vira uma chave `*_value` no payload Nascente, consumida por `compute_score()`.

| Critério | Peso | Função | Valor típico TA |
|---|---|---|---|
| origem | 30% | constante `TA_ATRATIVO_ORIGEM_VALUE = 65.0` | 65 (firewall: TA nunca cruza 85 só na origem) |
| completude | 20% | `completude_from_fields` (10 campos × 10pts, cap 100) | 40–100 |
| corroboração | 20% | `corroboracao_from_reviews` (curva log1p, satura ~500 reviews) | 0–~85 |
| atualidade | 15% | `atualidade_from_recency` (step: ≤30d=100, ≤180d=70, ≤365d=40, ≤730d=20) | **0** (sem data de review na listagem → `most_recent_review_at=None`) |
| validação humana | 15% | 0.0 no Nascente | 0 |

**Consequência de design**: um atrativo TA single-source satura ~55–67 e cai em **DLQ** — não
cruza o threshold binário `threshold_mar=80` sem corroboração multi-source. Promoção a Mar exige
transição auditada por um steward humano; não há push automático para Mar neste lane.

## 5. Ingestão (`atrativos.py::TripAdvisorAtrativosIngest`)

Dois paths que compartilham parsing/scoring mas divergem no vínculo com destino-pai:

### 5a. Per-UF — `produce(uf)` → `_ingest_one`
- `resolve_geo_id(uf)` → `fetch_attractions(geo_id)` (1 página GraphQL).
- Exige **destino-pai** em Rio: resolve `destino_rio_map[ibge_code] → (rio_id, source_ref)`.
  Sem pai → quarentena `parent_destino_absent`. ⚠️ Rodar o sweep Mtur (destinos, origem=100)
  **antes**, senão todo atrativo quarenta.

### 5b. Bulk nacional — `produce_paginated(geo_id=294280)` → `_ingest_one_bulk`
- Streama `(offset, cards)` da via HTML SSR paginada; **sem destino-pai** (gate droppado).
- UF + município **derivados** do geocode nacional (`geocode_national` + `resolve_municipio_national`,
  vizinho IBGE mais próximo ≤50km). Sem geocode/seat → quarentena `ibge_unmatched`.
- **Commit por página** (antes de `record_page`) → um 403 no meio deixa registros duráveis + ponto
  de resume correto. Erros por card (exceção OU `ibge_unmatched`) incrementam `sweep_progress.record_error`.

### Escrita comum
Valida via `TripAdvisorAtrativoPayload` (LGPD enforcement no parse), depois `store_raw(source="tripadvisor",
source_ref="tripadvisor:attraction:{location_id}", entity_type="attraction", uf, payload)`.
Payload inclui `*_value`, `review_count`/`rating`, `category`, `municipio_id`, e o sub-dict
`canonical` (contrato norteia-api). Idempotência: `store_raw` dedup por `(source, source_ref,
content_hash)`. Se `run_rio=True`, dispara `process_nascente_record` (Rio → §7.6 → Mar/DLQ).

## 6. Orquestração — task Celery `sweep_tripadvisor`

`brave/tasks/pipeline.py:879` (fila única `celery`).

```python
sweep_tripadvisor(uf, depth=None, *, bulk_national=False,
                  start_page=1, max_pages=None, geo_id=294280)
```

- **Client**: `NullTripAdvisorClient` a menos que `RUN_REAL_EXTERNALS=True` (opt-in). Real usa
  `TripAdvisorClient` + `NominatimGeocoderClient`, broker Redis (`BRAVE_DB_REDIS_URL`).
- **Depth gate**: `depth="nascente"` → `run_rio=False` (só Nascente+§7.6, sem Rio).
- **`bulk_national=True`**: path DISTINTO — pagina geoId 294280 via `produce_paginated`, sem
  destinos/destino_rio_map. Lê offset de resume de `sweep_progress` (continua da página após o
  último offset completo, não da 1), seed do hash live, commit por-página, `mark_done` no fim.
  Fail-fast compartilhado + `stop_needs_bootstrap` guardado num 403/429.
- **Per-UF** (`bulk_national=False`): constrói `destino_rio_map` de RioRecords `entity_type=
  "destination"` na UF (Mtur/IBGE origem=100), depois `produce(uf)`.
- **Progresso live**: `GET /api/v1/tripadvisor/sweep/progress` serializa `brave:ta:sweep:progress`
  (`state, pages_done, pages_total, attractions_ingested, current_offset, error_count, started_at`).
  `extra="forbid"` — hash secret-free.

## 7. Runbook mínimo (sweep real)

1. `RUN_REAL_EXTERNALS=1` no worker + API; (opcional mas recomendado) `BRAVE_TA_PROXY_URL`.
2. Capturar cURL de uma página de Attractions do TA num browser real logado (DevTools → Copy as cURL).
3. Injetar: `POST /api/v1/tripadvisor/session` com `cookies`, `query_ids={"attractions":"a5cb7fa004b5e4b5"}`,
   `user_agent`, `acquired_at`. Confirmar `{"status":"ready","canary":"ready"}`.
4. Motor: `POST /api/v1/engine/start` com `source="tripadvisor"` (ou disparar `sweep_tripadvisor`).
   - **Slice-first** (recomendado): `bulk_national=True, max_pages=1` (~30 atrativos) antes do full 334.
   - Per-UF exige sweep Mtur (destinos) rodado antes.
5. Monitorar `GET /api/v1/tripadvisor/sweep/progress`. Atrativos caem em **DLQ** (single-source, §5).
6. Sessão expira ~30min; `ta_keepalive` desliza o TTL enquanto houver tráfego.

Ver também: `data/tripadvisor/README`, `data/tripadvisor/RUNBOOK-NIVEL3.md`,
`docs/qa/QA-REPORT-tripadvisor.md`.

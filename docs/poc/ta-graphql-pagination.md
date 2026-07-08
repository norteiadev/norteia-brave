# POC — Paginação da listagem de Atrativos do TripAdvisor via GraphQL

**Status: CONFIRMADO empiricamente** (probe real contra `www.tripadvisor.com.br`, geoId 294280, 2026-07-04).

## Descoberta

O botão "próxima página" da listagem AttractionsFusion dispara **uma query GraphQL paginável** —
não o HTML SSR `-oa{offset}-` que o código atual usa. A query é:

- **Endpoint:** `POST https://www.tripadvisor.com/data/graphql/ids`
- **preRegisteredQueryId:** `79aaeeb847e55e58` (≠ do atual `a5cb7fa004b5e4b5`, que é single-page)
- **Parâmetro de paginação:** `variables.request.routeParameters.pagee` = **string do offset** (`"0"`, `"30"`, `"60"`, …)

### Variables (shape mínimo confirmado)
```json
[{"variables":{
  "request":{
    "tracking":{"screenName":"AttractionsFusion","pageviewUid":"<uuid4>"},
    "routeParameters":{"geoId":294280,"filters":[],"contentType":"attraction",
                       "webVariant":"AttractionsFusion","pagee":"30"},
    "updateToken":null},
  "commerce":{"attractionCommerce":{"pax":[{"ageBand":"ADULT","count":2}]}},
  "tracking":{"screenName":"AttractionsFusion","pageviewUid":"<uuid4>"},
  "sessionId":"<TASID>","unitLength":"KILOMETERS","currency":"BRL",
  "currentGeoPoint":null,"mapSurface":false,"debug":false,"polling":false},
  "extensions":{"preRegisteredQueryId":"79aaeeb847e55e58"}}]
```

## Evidência (probe real)

| pagee | HTTP | cards | totalResults | amostra |
|-------|------|-------|--------------|---------|
| ausente | 200 | **0** | 0 | — (página 1 SEM `pagee` retorna vazio) |
| `"0"` | 200 | **30** | 10000 | Cataratas do Iguaçu, Pão de Açúcar, Cristo Redentor, Ibirapuera |
| `"30"` | 200 | **30** | 10000 | Ilha dos Frades, Parque Lage, Museu do Amanhã |
| `"60"` | 200 | **30** | 10000 | Prainhas do Pontal, Acquamotion, Praia do Forte |

- **overlap 0∩30 = 0, 30∩60 = 0** → cada `pagee` traz 30 atrativos **distintos**. Pagina de verdade.
- `pagee` deve ser **explícito** para toda página, inclusive a 1ª (`"0"`); ausente → 0 cards (a página 1 "natural" é SSR).
- `totalResults = 10000` (teto exibível do TA = 334 páginas / offset máx 9990). Total real Brasil no filtro:
  `availableFilterGroups[...].count = 10351`.

## Shape da resposta (para parsing)

Idêntico ao que o `_parse_attractions_page` atual já consome:
`data[0].data.Result[0].sections[]` com `__typename == "WebPresentation_SingleFlexCardSection"` →
`singleFlexCardContent` (`cardTitle.text`, `cardLink.webRoute.typedParams.detailId`, `bubbleRating.rating/reviewCount`,
`primaryInfo.text`). **O parser existente serve sem mudança.**

## Implicações / diferenças vs. código atual

1. `fetch_attractions` (qid `a5cb7fa004b5e4b5`) é **single-page** (PAGINATION GAP, Phase 13). Trocar para
   qid `79aaeeb847e55e58` + `routeParameters.pagee` habilita paginação real **pela via GraphQL**.
2. Torna **desnecessário** o transporte HTML-SSR frágil (`fetch_attractions_paginated` + `_extract_sections_from_html`,
   regex/unquote sobre a JSON island). GraphQL é mais robusto e é o mesmo endpoint já usado.
3. **Per-UF e bulk usam o MESMO mecanismo** — só muda `geoId` (294280 = Brasil; 303308 = ES). Resolve o
   "só 30 atrativos" do per-UF ES.
4. `currency`/`unitLength` (BRL/KILOMETERS vs USD/MILES) **não afetam** a listagem — irrelevantes p/ os cards.

## Pontos a validar na implementação

- Confirmar que `geoId=303308` (ES) pagina igual (mecanismo é geoId-parametrizado → deve). Probe rápido no início.
- Throttle entre páginas (o atual `page_throttle_seconds`) + fail-fast 403/429 → `SessionExpiredError` (reusar).
- Cursor de término: parar quando uma página retorna 0 cards OU offset > 9990 OU offset ≥ totalResults.
- Pinnar `79aaeeb847e55e58` em config/const, com fallback documentado (qids do TA rotacionam).

## Plano de implementação (proposto — aguardando OK)

1. **Client** (`brave/domains/tripadvisor/client.py`): novo `fetch_attractions_paginated_gql(geo_id, start_page, max_pages)`
   async-generator que faz POST qid `79aaeeb847e55e58` com `pagee=str((page-1)*30)`, parseia via `_parse_attractions_page`,
   yield `(offset, cards)`. Clamp 334 páginas. Reusa cookies/proxy/UA + write-back + 403/429. **Deprecar** a via HTML-SSR.
2. **Lane** (`atrativos.py`): `produce(uf)` passa a paginar (usar o novo generator com o geoId da UF) em vez do
   `fetch_attractions` single-page. `produce_paginated` (bulk) aponta para o mesmo generator GraphQL.
3. **Config**: constante `_LISTING_QID = "79aaeeb847e55e58"` + `pagee`.
4. **Testes**: unit com resposta mockada de 2 páginas (offsets distintos, dedup, término); atualizar os que fixavam single-page.
5. **QA real**: sweep ES paginado (esperado » 30 atrativos), validar no painel.

Arquivos do probe: `$CLAUDE_JOB_DIR/tmp/ta_poc_p{0,30,60}.json` (respostas cruas; efêmeros, sem cookies).

---

# POC (parte 2) — Coleta de dados de review para o score de confiabilidade

**Status: CONFIRMADO** (probe real, locationId 312332 = Iguazu Falls, 2026-07-04).

## Onde estão os dados de review

| Critério de confiabilidade | Fonte confirmada | Observação |
|---|---|---|
| **corroboração** | listagem `bubbleRating.reviewCount` + `.rating` (grátis, no card) **ou** `totalCount` da query de reviews (mais preciso) | Já coletável no sweep paginado |
| **atualidade** | query de reviews → `reviews[0].publishedDate` (ordenado mais-recente) | **1 call por atrativo** |

O card da listagem **não** serve para atualidade: seu único campo de data é `reviewSnippets[].photo.data.publishedDateTime` (data de FOTO, cobertura 23/30, muitos timestamps batch/stale `2020-02-21`). Descartado.

## Query de reviews (confirmada)

- **preRegisteredQueryId:** `ef1a9f94012220d3`
- **Container:** `data[0].data.ReviewsProxy_getReviewListPageForLocation[0]` → keys `{preferredReviewIds, totalCount, reviews, reviewListOptions}`
- **Variables mínimas (payload enxuto):**
```json
{"locationId": 312332, "filters": [], "limit": 1, "offset": 0,
 "sortType": null, "sortBy": "SERVER_DETERMINED", "language": "pt",
 "doMachineTranslation": false, "photosPerReviewLimit": 0}
```
- **Ordenação:** default `SERVER_DETERMINED` já retorna **newest-first** (`reviews[0]` = review mais recente). `sortBy:"MOST_RECENT"` retornou vazio (enum inválido) — usar SERVER_DETERMINED.
- **filters:** `[{"axis":"LANGUAGE","selections":["pt"]}]` limita a pt (totalCount 34802); `filters:[]` = todas as línguas (totalCount 45813). Para atualidade/contagem global usar `filters:[]`.

### Campos usados (LGPD — allow-list estrita)
Do `reviews[0]`: **apenas** `publishedDate` (ou `createdDate`) + `rating`. Do container: `totalCount`.
**NUNCA** ler/persistir `text`, `title`, `username`, `userProfile`, `photos`, `photoIds`, `reviewTip` — o objeto review os contém, mas `TripAdvisorReviewSignals` (`extra="forbid"`) já barra tudo fora de `{review_count, rating, most_recent_review_at}`.

## Cálculo de score (por que atualidade importa, mas não basta sozinha p/ Mar)

Firewall single-source: origem TA=65 → 19.5 pts. Teto de um atrativo TA-only (sem validação humana):
`19.5(origem) + 20(completude máx) + 20(corroboração máx) + 15(atualidade ≤30d) = 74.5 < 80` → **DLQ**.
Ou seja: atualidade torna o score **mais fiel** (atrativo popular recém-avaliado 74.5 vs. um obsoleto 55.5) e alimenta o gate ">90 dias / sem review → DLQ", mas o Mar (≥80) ainda exige validação humana (+15 → até 89.5) ou multi-fonte. Esperado por design.

## Plano de coleta de reviews (proposto)

1. **Bulk/listagem:** corroboração via `bubbleRating` (grátis, já no card paginado). atualidade fica None → score entra em DLQ (correto).
2. **Enriquecimento seletivo** (`fetch_recent_review(locationId)` → qid `ef1a9f94012220d3`, `limit:1`): chamar **sob demanda** — candidatos a promoção / revisão DLQ / re-score — **não** nos 10k no bulk (1 call/atrativo = caro). Preenche `most_recent_review_at` → `atualidade_from_recency` → re-score.
3. Reusar cookie/proxy/UA + write-back + 403/429 do client atual. throttle entre calls.

Arquivos do probe: `$CLAUDE_JOB_DIR/tmp/ta_poc_reviews2_*.json` (efêmeros; sem PII persistida além da resposta crua local).

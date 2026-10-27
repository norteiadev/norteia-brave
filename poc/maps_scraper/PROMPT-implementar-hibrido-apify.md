# Prompt: implementar o enriquecimento híbrido Apify + Places Essentials no Brave

> Cole este arquivo inteiro numa sessão nova do Claude Code aberta no repo `norteia-brave` (checkout principal `~/Projects/norteia/norteia-brave`, ou uma worktree nova a partir de `main`). O contexto vem da sessão de 2026-10-21; os números foram medidos, não estimados.

---

## Objetivo

Trocar no `PlacesEnrichmentAgent` (e no `refresh_places`) o par **Text Search Pro + Place Details Enterprise/Atmosphere** do Google Places por um **híbrido**:

1. **Apify** (`compass/crawler-google-places`): busca o lugar e traz detalhes (horário, telefone, site, status de fechado) e **1 review, a mais nova**, que prova que o lugar está funcionando. O usuário não confia só no status: "só precisamos da review para saber se está funcionando".
2. **Google Places API (New), só Place Details Essentials** (US$ 5/1k, **10k grátis/mês**), pelo `place_id` que o Apify devolve. Fornece de forma first-party o `place_id`, as coordenadas, os `types` (enum do Google), o `formattedAddress` e os `addressComponents` (município → IBGE e distrito).

Tudo atrás de um flag, com o **default = comportamento atual (só Google)**. Nada muda em produção até o operador ligar o flag.

## Por quê (números de 2026-10-21)

- **Custo das 13 UFs que faltam** (~22.517 atrativos):
  - Hoje: US$ 0,050 por atrativo (1,02 Text Search + 0,71 Details), US$ 1.126 no total e **US$ 904 de desembolso** depois dos US$ 222 de crédito Free Trial.
  - Híbrido: Apify US$ 0,0065 por atrativo (FREE; BRONZE US$ 0,0055) + Essentials US$ 0,005. Dá **≈ US$ 146 de desembolso**, ou seja, **≈ US$ 758 de economia**.
  - O `refresh_places` cai de até US$ 150/mês para ≈ US$ 39/mês.
- **Qualidade (o motivo mais forte)**: o Places (New) devolve as reviews **por relevância**, no máximo 5, sem como ordenar. A Legacy, que tem `reviews_sort=newest`, não está habilitada no projeto (`REQUEST_DENIED`). Medido com o mesmo `place_id` nos dois:
  - Morro do Moreno: review mais nova no Places = **93 dias** (reprova no backstop de 90 dias do Mar); no Apify = **4 dias**.
  - Convento da Penha (30.128 reviews): Places = 78 dias; Apify = **no mesmo dia**.
  - Ou seja, hoje a recência do Google gera **falso negativo** em atrativo com muitas reviews.
- Contexto completo: wiki `~/AI/second-brain/wiki/projetos/norteia/brave-places-alternativas.md`, ADR de 2026-10-21 em `.../norteia/decisoes.md`, Engram #469 (custos finais), #470 e #471 (viés das reviews), #468 (preços e campos do Apify). Rode `/second-brain query apify híbrido places` se precisar de mais.

## ⚠️ Pré-condição de política (antes de mergear em main)

O projeto tem as ADRs **2026-08-19/20, "não usar scraper no lugar de API contratada"**, e **2026-10-14, "Places continua no Google, sem scraper"**. O Apify é um scraper do Google operado por terceiro: Google Maps ToS + risco à chave do Maps Platform. O usuário pediu a implementação, mas **a mudança de política ainda não foi registrada**.

- Implemente atrás do flag, com default desligado.
- **Antes do merge**, peça ao usuário para confirmar a exceção à ADR e registre uma ADR nova em `decisoes` (wiki) e no Engram (`mem_save`, type `decision`).
- Não ligue o flag em produção sem essa confirmação.

## Regras do repo que valem aqui (CLAUDE.md)

- **Testes**: nenhum teste chama Apify ou Google por padrão; a chamada real é opt-in (`RUN_REAL_EXTERNALS`). O CI roda sem chaves. Use fakes/`respx` (já são dependências) e fixtures.
- **Lógica**: no código do Brave, nunca em n8n. Todas as tasks Celery na fila `celery` (sem `options.queue`).
- **Segredos**: só por env. Nunca logar token, cookie ou chave. A linha `# BRAVE_APIFY_API_KEY=` já existe, comentada e **vazia**, no `.env` do checkout principal. Peça o token ao usuário (o token de teste da sessão anterior expira em 22/10). Adicione `BRAVE_APIFY_API_KEY=` ao `.env.example`.
- **LGPD**: o Apify devolve dados pessoais de reviewers por padrão. Passe **sempre** `scrapeReviewsPersonalData: false` e persista da review só data, nota e texto.
- Siga o estilo do código ao redor (comentários curtos explicando o porquê, `ponytail:` para atalhos com teto conhecido). Commits em conventional commits, em inglês, como o histórico. Branch nova a partir de `main` (ex.: `feat/places-hybrid-apify`) e PR para `main`.

## Mapa do código atual (leia antes de mudar)

| O quê | Onde |
|---|---|
| Contrato do client | `brave/clients/base.py:95`: `PlacesClientProtocol`, com `text_search(query, uf)` e `place_details(place_id, *, include_reviews=True)` |
| Client real | `brave/clients/places.py`: `RealPlacesClient`, field masks `_TEXT_SEARCH_FIELD_MASK` / `_GET_PLACE_FIELD_MASK(_NO_REVIEWS)`, preços `USD_TEXT_SEARCH_PRO=0.032`, `USD_DETAILS_ENTERPRISE=0.020`, `USD_DETAILS_ENTERPRISE_ATMOSPHERE=0.025`, `check_budget()`, `_raise_if_places_balance_wall`, e o shape exato do dict de `place_details()` (por volta da linha 540) |
| Client offline | `brave/clients/null_places.py`; fake de teste `tests/fakes/fake_places.py::FakePlacesClient` |
| Injeção | `brave/clients/factory.py:99`: `Clients.places` (Null sem externals; real com a env `places_api_key_env`) |
| Enriquecimento | `brave/domains/places/places_enrichment.py`: `run()` (por volta das linhas 690-760: `place_id_cache` → Text Search → `_best_match` → `place_details(include_reviews=...)`), `locate()` (linha 459), gates `_google_not_atrativo` / `_result_is_business` / `_PLACE_TYPES` / `_NOT_ATRATIVO_PRIMARY_TYPES`, regra `include_reviews = atualidade_value < 100` (TA com review < 60 dias pula as reviews do Google) |
| Fallback D de município | `brave/domains/tripadvisor/atrativos.py:835` e `:1093` (`self._places_agent.locate(...)`), usado só em card sem município |
| Refresh | `brave/tasks/pipeline.py:1434`: `refresh_places` (beat diário, Details pelo `place_id_cache`, `places_refresh_daily_limit=200`) |
| Steward DLQ | `brave/api/routers/dlq.py` (`aceitar-place` e `liberar-google` refazem o enrich com Details) |
| Cost guard | `brave/observability/cost_guard.py`: `pre_dispatch_check_places`, `record_places_spend`, `places_total_spend` (contadores por chave no Redis) |
| Horário | `brave/shared/opening_hours.py::to_hours_map` (aceita pt-BR e en; tudo ou nada, exige 7 dias) |
| Recência / Mar | `most_recent_review_at` em `places_enrichment.py`, `signal_agent.py`, `core/mar/service.py`, `core/rio/routing.py` (`no_recent_reviews`) |
| Push | `brave/core/mar/service.py` (por volta das linhas 422-505): `place_id`, `business_status`, `opening_hours`, `price_level`, `reviews_recent_count`, endereço, site, telefone, lat/lon |
| Settings | `brave/config/settings.py`: `places_enrichment_enabled` (453), `places_refresh_daily_limit` (520), `places_total_usd_cap` / `places_daily_usd_cap` (531-532), `run_real_externals` |

## O que já se sabe do Apify (medido)

- **Endpoint**: `POST https://api.apify.com/v2/acts/compass~crawler-google-places/runs?token=…&waitForFinish=300`; depois `GET /v2/datasets/{defaultDatasetId}/items?clean=true`. O run traz `status`, `usageTotalUsd` e `chargedEventCounts`.
- **Input que funcionou**:
  ```json
  {"searchStringsArray": ["place_id:ChIJ..."], "maxCrawledPlacesPerSearch": 1,
   "language": "pt-BR", "maxReviews": 1, "reviewsSort": "newest",
   "scrapeReviewsPersonalData": false, "maxImages": 0}
  ```
  Para busca por nome: `searchStringsArray: ["<nome do atrativo>"]` + `locationQuery: "<município>, <UF>, Brasil"` (uma location por run). O item devolvido traz `searchString` / `inputPlaceId`, que servem para casar de volta em lote.
- **Preço pay-per-event** (FREE → BRONZE → SILVER → GOLD):
  - `place-scraped`: 0,004 / 0,003 / 0,002 / 0,0015;
  - `place-details-scraped`: 0,002 / 0,002 / 0,0015 / 0,00105. É obrigatório para reviews e para entrada por `place_id`;
  - `review-scraped`: 0,0005 / 0,0005 / 0,00037 / 0,00026;
  - `apify-actor-start`: 0,00005.
  - Filtros pagos (`categoryFilterWords`, `searchMatching`, `placeMinimumStars`) cobram `filter-applied`: **não use**, filtre no código.
- **Campos úteis**:
  - `placeId` (ChIJ, igual ao Places), `fid`, `cid`, `title`, `location {lat,lng}`;
  - `address`, `street`, `neighborhood`, `city`, `state`, `postalCode`;
  - `phone`, `phoneUnformatted`, `website`;
  - `permanentlyClosed`, `temporarilyClosed`;
  - `openingHours: [{day, hours}]`, que **começa no dia de hoje** ("Atendimento 24 horas", nomes de dia em pt-BR);
  - `categories` (rótulos pt-BR, ex. "Atração turística", **não** o enum do Google), `categoryName`, `price` (texto, ex. "R$ 40–60", às vezes null);
  - `reviewsCount`, `totalScore`;
  - `reviews[] {publishedAtDate (ISO), stars, text}`;
  - `additionalInfo` (acessibilidade e comodidades, bônus para as colunas editoriais).
- **Fixtures reais** (Morro do Moreno, reviewers já removidos): `poc/maps_scraper/fixtures/apify_morro_do_moreno.json` e `places_morro_do_moreno.json`, na worktree `~/orca/workspaces/norteia-brave/POC-Places-alternative` (não versionada). Copie para `tests/fixtures/apify/` e `tests/fixtures/places/`.
- **Script de comparação** que rodou (Places × Apify pelo mesmo `place_id`): `/private/tmp/claude-501/-Users-leandro-orca-workspaces-norteia-brave-POC-Places-alternative/d7e91e18-6c39-4986-b32d-eee105c5bb35/scratchpad/compare/compare_reviews.py`. Pode ter sumido, porque é scratchpad; o input acima basta.

## Desenho proposto (ajuste depois de ler o código; mantenha o diff mínimo)

1. **Settings**:
   - `places_provider: Literal["google", "hybrid"] = "google"`;
   - `apify_actor_id = "compass~crawler-google-places"`, `apify_max_reviews = 1`;
   - `apify_daily_usd_cap`, `apify_total_usd_cap`;
   - env do token `BRAVE_APIFY_API_KEY`.
2. **`brave/clients/apify_places.py`**: `RealApifyPlacesClient` com httpx, tenacity só em 429/5xx, guard de `run_real_externals` igual ao do `RealPlacesClient` e balance wall (401/402/403) → `ProviderBalanceError("apify", …)`.
   - Método de lote: `lookup(items) -> dict[key, place]`, onde cada item é `place_id:` (refresh) ou nome + município/UF (busca).
   - Devolve dicts normalizados (campos acima), sem PII.
   - Null e Fake offline seguindo o padrão `null_places.py` / `fake_places.py`.
3. **Places Essentials**: em `RealPlacesClient`, um modo de Details só Essentials: mask `id,location,types,formattedAddress,addressComponents`, preço `USD_DETAILS_ESSENTIALS = 0.005`, com cost guard. **Confirme na doc de SKU** que esses 5 campos são todos Essentials: se um único campo for Pro, a chamada inteira vira Pro.
4. **Adapter Apify → dict do `place_details()`**, para os consumidores não mudarem:
   - **Status**: `permanentlyClosed` → `CLOSED_PERMANENTLY`; `temporarilyClosed` → `CLOSED_TEMPORARILY`; senão `OPERATIONAL`.
   - **Horário**: `openingHours` → `weekday_text` como `"quarta-feira: Atendimento 24 horas"`. A ordem não importa para o `to_hours_map`. Teste os formatos "08:00–17:00", "Fechado", "Atendimento 24 horas" e turno partido.
   - **Reviews**: `reviews` → `[{publishTime, rating, text}]`.
   - **Preço**: `price` → `price_level` só se houver mapeamento seguro; senão `None` (o Places também deu 0% na amostra).
   - **Tipos e coordenadas**: `types` / `primary_type` / `location` / `distrito_hint` / município saem do **Essentials** (enum do Google), não do Apify.
   - **Gate**: sem `primaryType` (que é Pro), o `_google_not_atrativo` não julga (está documentado: "No primaryType → not judged"). No híbrido, use o critério do `_result_is_business` sobre os `types` do Essentials. Escreva teste para o Faraó na Praia (restaurante em "Praias"), o Parque dos Falcões (`pet_care` + `park`) e um shopping.
5. **Fluxo no `run()`** com `places_provider == "hybrid"`:
   - Sem `place_id_cache`: Apify por nome (`maxCrawledPlacesPerSearch: 1`) → valida com Essentials pelo `placeId`: UF, distância ≤ `places_match_max_distance_km` da coordenada TA ou da sede do município, nome com rapidfuzz como no `_best_match`. Se a validação falha, **não há match** (cache negativo, como hoje), sem cair no Text Search Pro.
   - Com `place_id_cache`: Apify por `place_id:` + Essentials.
   - Mantenha a regra do operador: com review do TA < 60 dias (`atualidade_value >= 100`), use `maxReviews: 0`. A review do TA já prova funcionamento. Confirme com o usuário se ele quer a review do Google mesmo nesse caso; ele disse "só precisamos da review para saber se está funcionando", e a do TA já responde isso.
   - `CLOSED_*` passa pelos mesmos caminhos de hoje (descarte / `google_fechado_revisao` para natureza e locais históricos, brave#62).
6. **`locate()` (fallback D)** continua no Text Search do Google nesta fase: ele precisa de vários resultados para o consenso de município e só roda para card sem município. Meça a fração de chamadas que ele representa e registre como próximo passo.
7. **Lote / throughput**: um run por atrativo custa start de alguns segundos. Para sincronizar ~22,5 mil atrativos em ~2 dias (≈ 470/h), agrupe por lote do backfill (ADR de 2026-10-17: bulk + backfill em lotes) num único run com vários `searchStringsArray` e case de volta por `searchString` / `inputPlaceId`. Uma location por run: agrupe por município. Sync por registro só para o inline do sweep por UF e para as ações de steward. O gargalo conhecido é o TA (3 lanes em paralelo → 503), não o Apify: meça.
8. **Cost guard Apify**: contadores Redis próprios (`brave:cost:apify:daily:{date}`, `brave:cost:apify:total`), check antes de despachar com o custo projetado pelos preços acima e registro depois pelo `chargedEventCounts` do run (o `usageTotalUsd` sozinho não reflete os eventos). Mostre no dashboard ao lado do `_places_cost` (`brave/api/routers/dashboard.py:239`) se for barato.
9. **`refresh_places`**: no híbrido, Apify por `place_id:` (1 review) + Essentials, com o mesmo teto diário.

## Testes (obrigatórios, offline)

- Adapter: fixture `apify_morro_do_moreno.json` → dict no shape de `place_details()`, com `to_hours_map` = `{"daily": "open_24h"}` e `publishTime` = 2026-10-17.
- Status: `permanentlyClosed=true` → descarte (ou steward, se natureza/histórico); `temporarilyClosed=true` → caminho de hoje.
- Gate híbrido com `types` do Essentials (casos citados acima).
- Fluxo `run()` híbrido com fakes: com e sem `place_id_cache`, validação de distância/UF reprovando, Apify sem resultado, Apify com erro (mantém o piso do TA, como hoje), balance wall pausando.
- Cost guard Apify: projeção, registro e cap.
- `respx` para os endpoints `/runs` e `/datasets/.../items` (sucesso, 402, 429 com retry).
- Flag `google` (default): nenhum teste existente muda de comportamento. Rode a suíte inteira.

## Validação real (opt-in, depois dos testes verdes, com aprovação do usuário)

1. `sync-test-atrativo` (skill do projeto) num atrativo conhecido, com o flag ligado.
2. Piloto numa UF pequena (sugestão: **RO, 96 atrativos**, ou TO, 229) com `places_provider=hybrid`, usando a skill `run-uf-sync`. Compare com o histórico da Onda 1 / SE:
   - taxa de match (alvo ≥ a de hoje: em SE, 109/171 casaram com o Google);
   - vereditos do gate e quantidade de `CLOSED_*`;
   - % com review ≤ 90 dias e promoções ao Mar (deve **subir**, por causa do viés de relevância corrigido);
   - **custo real por atrativo** (Apify `chargedEventCounts` + contador Essentials). Alvo ≈ US$ 0,0065 + US$ 0,005.
   - Registre o resultado no Engram (`mem_save`) e peça `/second-brain ingest-sync` ao final.
3. Só depois disso, e com a ADR de exceção registrada, falar das 13 UFs restantes. Rodar na virada do mês (31/10 + 01/11) usa duas franquias de 10k do Essentials (Google ≈ US$ 13). O Apify precisa de plano pago com limite de uso ≥ ~US$ 150.

## Critérios de pronto

- [ ] Flag `places_provider` com default `google`; `hybrid` implementado no `run()`, no `refresh_places` e nos endpoints de steward.
- [ ] Places só em Essentials no modo híbrido (verificado pelo SKU na mask e pelo preço no cost guard).
- [ ] Review mais nova vinda do Apify alimentando `most_recent_review_at`, sem PII persistida.
- [ ] Cost guard Apify com caps por env.
- [ ] Suíte offline verde, sem chave; testes novos para adapter, gate, fluxo e guard.
- [ ] `.env.example` com `BRAVE_APIFY_API_KEY=`.
- [ ] PR para `main` descrevendo flag, custo medido e o pré-requisito de política; nada ligado em produção.
- [ ] Piloto real numa UF pequena com métricas registradas (se o usuário aprovar).

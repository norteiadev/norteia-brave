# POC: sweep do TripAdvisor + scraper do Google Maps no lugar da Places API

Data: 2026-10-21. Tudo fora do Brave: scripts standalone (stdlib + httpx) em `poc/maps_scraper/`, sem Postgres, Redis, Celery nem norteia-api. Os números abaixo já incluem as **Correções da verificação** (seção 4). Quando um número original e um corrigido divergem, vale o corrigido.

**Resumo dos vereditos**

| pergunta | veredito |
|---|---|
| 1. Os dados funcionam para o Brave? | **Parcialmente.** place_id, coordenadas, telefone, site e reviews batem com o Places. Horário, status, tipos secundários, município e distrito não servem como estão. |
| 2. Qual o limite de requests? | **Desconhecido.** Não houve 429, /sorry/ nem captcha em 415 requisições anônimas. O que foi observado sem sinal: ~30 rpm por ~6 min. |
| 3. Dá para substituir a API pelo scraper? | **Não.** Viola o ToS do Google e as ADRs do projeto, não é drop-in e não tem contrato. Recomendação: manter a Places API. |

---

## Como a POC rodou

| etapa | script | entrada → saída | requisições |
|---|---|---|---|
| Sweep TA | `ta_sweep.py` | piloto de 100 atrativos (`--from-pilot`) → `out/ta_atrativos.jsonl` | 0 (o TA anônimo dá 403 DataDome e não havia cURL do usuário) |
| Engenharia reversa do Maps | `maps_scraper.py` | 10 atrativos de `out/sample_in.jsonl` | 41 ao Maps |
| Rodada principal | `maps_scraper.py` | 100 do TA (todos **sem lat/lng**) → `out/maps.jsonl` + `out/maps_retry.jsonl` | 205 ao Maps |
| Ground truth | `avaliar.py --ground-truth` | 20 atrativos na Places API (New) oficial → `out/ground_truth.jsonl` | 60 à Places (~US$1,78) |
| Rampa de rate limit | `rate_probe.py` | ftids e buscas de `out/maps.jsonl` → `out/rate_probe.jsonl` | 62 ao Maps |
| **Sweep TA real** | `ta_sweep.py --uf ES --max-pages 2 --curl-file …` | sessão ativa do Brave, exportada só para leitura do Redis para um arquivo 0600 fora do repo → `out/ta_es.jsonl` (60 atrativos) | 2 ao TA (GraphQL), sem 403 |
| **Rodada ES** | `maps_scraper.py --delay 2` + `avaliar.py --ground-truth --n 20` | `out/ta_es.jsonl` → `out/maps_es.jsonl`, `out/avaliacao_es.md`, `out/ground_truth_es.jsonl` | 107 ao Maps + 28 à Places |

São 415 requisições anônimas ao Maps no dia (308 + 107), todas do mesmo IP (não é de datacenter), **sem nenhum bloqueio**.

**Sweep TA.** O loop de rede do `ta_sweep.py` rodou ao vivo com a sessão do Brave: 2 páginas, 60 cards em pt-BR, com categoria TA e número de reviews (`.com.br` + GraphQL, o mesmo caminho do Brave). O listing não traz lat/lng: elas saem sempre null. O município vem do slug da URL, em ASCII e às vezes no formato "Distrito Município".

### Rodada com TA real (ES, 60 atrativos)

| métrica | valor |
|---|---|
| match no scraper | 47/60 (78%). Sem coords do TA, então vale a mesma ressalva da regra real do Brave (seção 1) |
| HTTP | 107/107 = 200. Latência p50 377 ms, p95 749 ms |
| place_id, location, types, primary_type | 100% |
| reviews com publishTime / review ≤ 90 dias | 96% / 49% |
| weekday_text → to_hours_map | 47% |
| telefone / site / municipio_nome | 43% / 53% / 79% |
| price_level | 0% |
| gate `_google_not_atrativo` | 46 passam, 1 descarta |

Ground truth (20 dos 60 na Places API oficial): **8 pares nos dois lados, e todos iguais** em place_id, distância (0 km), business_status, gate, `to_hours_map`, telefone e domínio. A review mais recente difere 1 dia num caso (Convento da Penha); nos demais é igual.

Outros 7 foram casados só pelo scraper, e 5 por nenhum dos dois (Praia da Costa, Praia do Morro, Itaparica, Praia do Canto, Ilha do Boi). Isso é coerente com o filtro de "political" nos dois lados: praias grandes o Google trata como localidade.

Os 7 extras do scraper não estão validados. Há um **falso match** visível: "Praia Itaoca" → "Itaóca praia em Itapemirim ES." (`housing_development`), que **passa** no gate, porque `housing_development` não está na lista de negócios.

Correção aplicada antes desta rodada: `route` saiu de `_GEO_PREFIXES`, como pedia a verificação.

---

## 1. Os dados retornados funcionam para o Brave?

**Veredito: parcialmente.** O núcleo de identificação bate com o Places (place_id, coordenadas, telefone, site e reviews). Horário, status, tipos, município e distrito, porém, regridem em relação ao que o Brave usa hoje.

### Cobertura por campo (rodada de 100, corrigida para 75 place_ids únicos)

| campo | scraper | observação |
|---|---|---|
| match | 80/100 no POC, **~61/100 pela regra real do Brave** | 19 matches não têm município, e o `_best_match` sem coords do Brave os rejeitaria |
| place_id (ChIJ) | 100% | o mesmo id do Places |
| location | 100% | igual ao Places, bit a bit |
| primary_type / types | 100% / 100% | só o tipo principal, um enum e rótulos pt-BR. Sem tipos secundários |
| business_status | "100%" | **é o default OPERATIONAL, não dado lido.** Nenhum CLOSED_* apareceu nos 100 |
| weekday_text / to_hours_map | 57% | lido da **semana corrente** ([203][0]), não do horário regular |
| reviews com publishTime | 98% | de 5 a 8 por lugar |
| review mais recente ≤ 90 dias | 52% | |
| telefone | 37% | |
| site | 51% | |
| municipio_nome | 75% | parte dos vazios é falha de parse (formato "Cidade, UF") |
| price_level | 0% | o Places também deu 0% na amostra |
| municipio_ibge / distrito_hint | 0% | não existem. Por isso o distrito sai sempre null no push |

### Comparação com o Places oficial (20 atrativos, 9 pares em que os dois lados casaram)

| campo | resultado honesto |
|---|---|
| place_id | 9/9 iguais |
| distância | 0,000 km |
| to_hours_map | **5/6** quando o Places tem horário (5/5 quando os dois têm). O Bondinho não tem horário no Maps |
| telefone | 6/6 entre os não vazios |
| domínio do site | 7/7 entre os não vazios |
| reviews em comum | 43. O timestamp bate até o segundo em 40, e as outras 3 diferem em menos de 14 min. A nota bate em 43/43 |
| business_status | 8/9. Na Ilha dos Frades o Places dá UNKNOWN e o scraper dá OPERATIONAL, porque nunca emite UNKNOWN |
| gate `_google_not_atrativo` | 9/9, **mas só em casos que passam**. Nenhum caso de negócio foi comparado |
| primary_type | 6/9. Os 3 diferentes não mudam o gate |
| match | 9 nos dois lados, 5 só no scraper, 1 só no Places, 5 em nenhum. As queries diferem, então não é 1:1 |

### O que falta para o Brave

| lacuna | impacto no Brave |
|---|---|
| Horário da semana corrente | Desfaz o "D-01 fix" de `brave/clients/places.py`, que trocou current_opening_hours por regular_opening_hours de propósito. Numa semana de feriado, o horário especial vai para o Mar e para o push |
| CLOSED_TEMPORARILY sem sinal; CLOSED_PERMANENTLY ([23]==1) não validado no Places | O SignalAgent manda temporário para a DLQ e permanente para hard descarte. Se o Google marcar [23]=1 num fechamento temporário, o lugar é descartado em vez de ir para a DLQ |
| Sem tipos secundários | O resgate `_PLACE_TYPES ∩ types` dos gates fica sem dados, o que gera risco de falso descarte (Inhotim e Convento perdem hiking_area; botanical_garden também se perde) |
| Sem municipio_ibge | `_best_match` sem coords e `locate()` rejeitam tudo, e os cards TA sem coords vão para `ibge_unmatched`. Seria preciso um adapter com o lookup IBGE |
| distrito_hint fixo em "" | `resolve_distrito` nunca resolve, e distrito_code, distrito_name e distrito_municipio_ibge saem null no push |
| Detalhe indexado por ftid, não por place_id | `refresh_places`, SignalAgent, ContactFinder e o re-run do enrichment chamam Details com `place_id_cache`. Sem ftid persistido, cada refresh exige uma nova busca por nome (2 requisições e risco de trocar o place_id) |
| Interface síncrona e com assinatura diferente | Não encaixa no `PlacesClientProtocol` (que é async, com `text_search(query, uf)` e `place_details(place_id, ...)`) |
| Sem UF, o filtro de UF é pulado | Sem coords, nada garante que o lugar está na UF certa (exemplo: Trilha da Pedra Redonda) |

---

## 2. Qual o limite de requests que podemos fazer?

**Veredito: desconhecido.** Nenhum bloqueio real foi visto. O único sinal foi um HTTP 500 ambíguo. Os números abaixo são **pisos observados em janelas de minutos**, não um teto nem um ritmo sustentado.

| observação | requisições | ritmo | duração | sinal |
|---|---|---|---|---|
| Engenharia reversa | 41 | ≥2 s entre requisições | ~15 min | nenhum |
| Rodada principal (`maps.jsonl`) | 180 (+25 entre re-run e probe) | ~30 rpm (calculado, sem timestamp) | ~6 min | nenhum, todas HTTP 200 |
| Probe D1 | 30 | 24 rpm (intervalo de 2 s) | 75 s | nenhum. p50 434 ms, p95 1376 ms |
| Probe D2 | 30 OK + 1 erro | 42 rpm (intervalo de 1 s) | ~41 s | HTTP 500 sem o prefixo `)]}'` (1730 B) na 31ª requisição, ~307ª do dia |
| Recuperação | 1 | 60 s depois | - | 200 OK, **mas com outro ftid** |
| D3 a D6 (0,5 s, sem pausa, concorrência 4 e 8) | 0 | - | - | não executados |

Latência da rodada principal: média de 456 ms, p50 402, p95 771 e máximo de 1095.

**Condições:** 1 IP não-datacenter, anônimo, UA fixo, sem cookies e janela curta. O IP já vinha carregado pela rodada anterior quando o probe começou.

**Sobre o 500:** não foi 429, nem /sorry/, nem algum dos 4 marcadores de captcha. O corpo da resposta não foi salvo, então o caso não pode ser auditado. O ftid (Farol de Santa Luzia) tinha voltado 200 minutos antes. Sobram duas leituras indistinguíveis: erro transitório do servidor ou degradação suave por carga acumulada.

**Atrativos por hora.** Cada atrativo custa 2 requisições (busca + detalhe). A 24–30 rpm isso daria **720–900 atrativos/h por IP**. É só uma conta sobre minutos de dados, **não uma capacidade de sweep**. Um sweep nacional (60k a 120k requisições, mais re-scoring 24/7) levaria de 42 a 83 h contínuas por IP, o que fica de 200 a 400 vezes acima do volume observado.

**O que não foi medido:**
- cota por hora ou por dia e reputação de IP acumulada ao longo de dias;
- IP de datacenter (VPS de produção), que é sinalizado mais rápido;
- concorrência e rajada;
- variação por horário;
- expiração dos tokens do `pb` (wjPZarq…) e do viewport fixo;
- degradação suave: um 200 com conteúdo reduzido passa como OK.

Próximo teste, se houver consentimento: soak de várias horas a ~20 rpm a partir do IP de produção, com 1 retry da mesma URL em caso de 5xx e o corpo de toda resposta não-200 salvo em `out/raw/`.

---

## 3. É possível substituir a API pelo scraper?

**Veredito: não. Recomendação: manter a Places API (New), sem uso do scraper nem para triagem.**

| eixo | avaliação |
|---|---|
| Técnico | O núcleo funciona: place_id, coordenadas, telefone, site e reviews iguais ao Places, a US$0. **Mas não é drop-in.** É síncrono, indexado por ftid e não entrega municipio_ibge nem distrito. O horário vem da semana corrente, o status não é confiável e faltam os tipos secundários. O match real seria ~61/100, não 80. O ground truth tem n=9, puxado para atrativos famosos e com pouca diversidade de UFs |
| Operacional | Não há contrato: o JSON é posicional, há um protobuf em base64 ([209]) e os tokens do `pb` estão fixos. Uma mudança do Google faz campos voltarem None em silêncio, e a suíte offline não detecta. O limite é desconhecido para um sweep 24/7. O enrichment do Brave engole exceções e grava `places_refreshed_at` antes do Details, então um bloqueio não pararia o sweep e esconderia o envelhecimento dos dados. O espaçamento é por processo, e com vários workers prefork o ritmo vira N×. Retry com tenacity em 429 queima o IP |
| Compliance | Viola os Termos do Google Maps. O CLAUDE.md lista o Places ToS como constraint. As ADRs de 2026-08-19/20 ("não usar scraper ... no lugar de API contratada") e de 2026-10-14 ("Scraper do Maps ou de SERP esbarra no ToS... arriscaríamos a chave") e a decisão travada da Phase 14 (`.planning/ROADMAP.md:143`, "NOT Google-Maps browser scraping") rejeitam o scraper **independentemente do volume**. O scraping sairia do mesmo IP da chave do Maps Platform. LGPD: `out/` guarda texto de reviews com nomes de reviewers |

"Triagem ou fallback de baixo volume" **não é viável**: a violação de ToS não depende de volume, e a triagem substituiria justamente o Text Search que a ADR de 2026-10-14 já otimizou. A economia é pequena e o risco de perder a chave continua o mesmo.

---

## 4. Correções da verificação

Estas correções prevalecem sobre as métricas originais (`out/avaliacao.md`, `out/rate_probe.md`).

| métrica original | corrigida | motivo |
|---|---|---|
| match 80/100 | ~61/100 pela regra do Brave | 19 matches sem município, e o `_best_match` sem coords os rejeita |
| cobertura sobre 80 | sobre 75 únicos | 5 place_ids duplicados |
| to_hours_map igual 8/9 | 5/6 (5/5) | 3 eram None==None |
| review recente: 1 dia de diferença | sem diferença de data | o scraper trouxe 8 reviews e o Places 5. A de 2026-08-05 é uma das extras |
| telefone e site 9/9 | 6/6 e 7/7 | 3 telefones e 2 sites batiam só por estarem vazios dos dois lados |
| gate 9/9 | 9/9, só em casos que passam | nenhum caso de negócio comparado |
| business_status 100% | é o default OPERATIONAL | nenhum CLOSED_* na rodada. [23]==1 só apareceu em lojas buscadas de propósito, sem validação no Places |
| 13 dos 20 sem match são political | 11 | Rota do Lagarto e Rua do Mucugê se perderam por defeito do scraper: `route` em `_GEO_PREFIXES` vira political. Correção: tirar `route` |
| municipio vazio só em praias | parte é parse | o formato "Cidade, UF" (Guarapari, ES) não é lido. Há risco de bairro virar município (Itapuã, ES) |
| teto de 24 rpm sustentado | piso: ~30 rpm por ~6 min e 24 rpm por 75 s | nenhum dos dois é sustentado |
| "60 OK a 42 rpm antes do 500" | 30 OK a 42 rpm | as outras 30 eram de D1, a 24 rpm |
| 267 requisições no dia | 308 (41 + 205 + 62) | erro de soma |
| a recuperação "reforça que foi transitório" | não reforça | a recuperação usou outro ftid, numa única requisição depois de 62 s ociosos |
| "ftid ruim" como explicação | descartado | a mesma URL tinha voltado 200 minutos antes |
| ~720 atrativos/h como capacidade | só uma conta | apoiada em minutos de dados de 1 IP |
| triagem ou fallback viável | não viável | ToS e ADRs, independentemente do volume |

Bugs já corrigidos durante a POC, em `maps_scraper.py`:
- `tempor` casava com "contemporânea" e marcava o Inhotim como CLOSED_TEMPORARILY. Agora a regra exige "temporariamente", e há uma fixture `out/raw/detail_inhotim.txt` com assert.
- O município do TA era usado como filtro e passou a só desempatar, o que recuperou o Parque Estadual da Pedra Azul.

---

## 5. Riscos

| risco | consequência |
|---|---|
| ToS do Google e perda da chave do Maps Platform | perde-se a lane paga que hoje funciona |
| Formato posicional e tokens do `pb` sem contrato | campos voltam None em silêncio e o score degrada sem alerta |
| CLOSED_TEMPORARILY confundido com permanente | hard descarte de algo que deveria ir para a DLQ |
| Horário da semana corrente | horário de feriado publicado como regular |
| Falta de tipos secundários | falso descarte que o Places não faria |
| Bloqueio engolido pelo enrichment | o sweep continua batendo num Google que já bloqueou, e `places_refreshed_at` esconde que os dados envelheceram |
| Ritmo por processo com workers prefork | N×24 rpm reais. Seria preciso um limitador global |
| Limites de janela longa e IP de datacenter não medidos | bloqueio horas depois, no meio de um sweep de UF |
| Discovery (busca por categoria) não testado | só foi validada a busca pelo nome de um atrativo conhecido |
| Sem `check_budget` / `_record_spend` | o guard de custo é pulado e o dashboard de custo fica errado |
| Gates por categoria TA não exercitados | Na rodada ES a categoria veio, mas `avaliar.py` só roda `_google_not_atrativo`. `_ta_attraction_category` e `_ta_nature_category` (steward/DLQ) ficaram de fora. O descarte do Projeto Tamar/BA pode ser um homônimo |
| Falso match que passa no gate | Itaoca → `housing_development` (ES). Tipos que não são negócio nem lugar passam direto |
| Fluxo com coords do TA (raio de 20 km) não medido | o caminho normal do Brave ficou fora da rodada |
| LGPD | `out/` tem nomes de reviewers e fica fora do git pelo `.gitignore`, mas não há política de retenção local |
| TA: qid do listing pode rotacionar | o sweep para com `graphql_errors`. É preciso passar `--qid` novo |

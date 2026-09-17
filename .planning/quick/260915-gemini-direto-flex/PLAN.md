---
quick_id: 260915-gdf
slug: gemini-direto-flex
date: 2026-09-15
type: feature
status: planned
---

# Quick: copywriter da cascata no Gemini 2.5 Flash direto (AI Studio), tier Flex com fallback standard

## Por quê

O copywriter da cascata escreve com `google/gemini-2.5-flash` via OpenRouter: ~US$ 33 para os
10 mil atrativos do sync inicial (US$ 31,30 de tokens + 5,5% de taxa na compra de crédito).

A chave nova do AI Studio tem billing confirmado e serve `gemini-2.5-flash` com
`serviceTier: "flex"` (-50%). Medido em 2026-09-15 sobre os mesmos 100 atrativos (buscas do
Parallel da §29, `thinkingBudget: 0`, concorrência 8):

| rota | aprovados | riqueza | p50 / p95 | USD / 10k |
|---|---|---|---|---|
| OpenRouter, standard (hoje) | ~98% | 8,4 | ~4 s | ~33 |
| direto, Flex | 97 | 9,6 (mesmo modelo, ruído de amostra) | 5,8 s / **172 s** | 15,7 |

**No Flex, 21 de 100 chamadas levaram 503** e só passaram com retry; 1 falhou de vez. Por isso
a política é: **tentar Flex uma vez; se vier 503, 429 ou timeout, refazer na hora em standard**.

Estimativa: ~80% × US$ 0,00157 + ~20% × US$ 0,00314 ≈ **US$ 0,0019/atrativo, ~US$ 19 nos
10 mil**, com a latência do standard no pior caso e a qualidade de hoje.

Medições: memória `gemini-direto-flex-medido`; script `$CLAUDE_JOB_DIR/tmp/gemini_direct_probe.py`.

## Decisões de desenho

- **REST nativo via `httpx`**, não o endpoint OpenAI-compat. `serviceTier` foi verificado ao
  vivo só no `:generateContent` nativo, e `httpx` + `respx` já são o padrão do
  `RealParallelClient`. Nenhuma dependência nova.
- **Roteamento por slug em `RealLLMClient.generate()`:**
  - `gemini-*` (sem vendor) → Google direto;
  - `vendor/model` → OpenRouter (inalterado);
  - `claude-*` → Anthropic (inalterado).
- **O modelo da cascata vira configuração**, sem o valor fixo em código. `ATRATIVO_CASCADE_MODEL`
  tem default `gemini-2.5-flash`. Rollback sem deploy: `ATRATIVO_CASCADE_MODEL=google/gemini-2.5-flash`
  volta ao OpenRouter.
- **Tier configurável:** `BRAVE_LLM_GEMINI_SERVICE_TIER=flex|standard`, default `flex`.
  `standard` desliga o Flex.
- **Custo por tabela local.** O Google não devolve custo (o OpenRouter devolvia `usage.cost`).
  - A tabela vai por (modelo, tier) e cobra à parte os tokens lidos do cache.
  - Um modelo fora da tabela gera `ValueError`: é melhor falhar alto do que contar errado no cost guard.
- **Thinking desligado explicitamente** (`thinkingConfig.thinkingBudget: 0`). No OpenRouter ele já
  vinha desligado por padrão; no Google direto o padrão é thinking dinâmico, que na §26 dobrou
  custo e latência e truncou respostas.
- **D-04 (`data_collection: deny`) não se aplica ao Google direto.** O tier pago não treina com os
  dados ("Used to improve our products: No"). Isso vai documentado no docstring; o billing ativo
  é pré-requisito operacional.

## Tarefas

### 1. Configuração — `brave/config/settings.py`

- **`LLMConfig`** (prefixo `BRAVE_LLM_`):
  - `gemini_api_key: str = Field(default="")` → env `BRAVE_LLM_GEMINI_API_KEY`. Sem alias,
    seguindo a regra já comentada para `openrouter_api_key`.
  - `gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"`
  - `gemini_service_tier: str = "flex"`, validado em `flex | standard`.
  - `gemini_flex_timeout_s: float = 60.0`: teto da tentativa Flex antes de cair para standard.
- **`AppConfig`:** `atrativo_cascade_model: str = "gemini-2.5-flash"` (env-only, igual a
  `parallel_search_mode`).
- **`.env.example`:** adicionar `BRAVE_LLM_GEMINI_API_KEY=` e `BRAVE_LLM_GEMINI_SERVICE_TIER=flex`.
- **Ação do operador:** renomear `GEMINI_API_KEY` → `BRAVE_LLM_GEMINI_API_KEY` no `.env` e
  recriar o worker (memória `services-need-env-sourced`).

### 2. Client — `brave/clients/llm.py`

- **Tabela de preço** `_GEMINI_PRICES_USD_PER_MTOK[(model, tier)] = (input, output, cache_read)`,
  com os valores da página oficial em 2026-09-15:
  - `gemini-2.5-flash`: standard (0,30 / 2,50 / 0,03), flex (0,15 / 1,25 / 0,03);
  - `gemini-2.5-flash-lite`: standard (0,10 / 0,40 / 0,01), flex (0,05 / 0,20 / 0,01).
- **Custo:** `(prompt − cached) × in + cached × cache + (candidates + thoughts) × out`.
- **`generate()`:** depois do `pre_dispatch_check` que já existe, `model.startswith("gemini-")`
  → `_generate_gemini(...)`.
- **`_generate_gemini(messages, model, *, system, tools)`:**
  1. `tools` → `ValueError`, como no OpenRouter.
  2. Chave vazia → `PermanentError`. Isso não deveria acontecer, porque o build do pipeline já barra (tarefa 3).
  3. **Body:**
     - `systemInstruction`;
     - `contents`, com `user` → `user` e `assistant` → `model`;
     - `generationConfig: {maxOutputTokens: 2048, thinkingConfig: {thinkingBudget: 0}}`;
     - `serviceTier`, se o tier for flex.
     - Header `x-goog-api-key`. A §9.2 registrou que `?key=` devolve 429 enganoso.
  4. **Tentativa Flex:** uma só, com timeout `gemini_flex_timeout_s`. Em 503, 429 ou timeout,
     loga `gemini_flex_fallback` (modelo, motivo) e repete o mesmo body **sem** `serviceTier`.
  5. **Tentativa standard:** reaproveitar a política `tenacity` que já existe (3 tentativas, backoff
     exponencial, só 429/5xx/conexão).
  6. **Leitura da resposta:**
     - sem `candidates` (prompt bloqueado, `promptFeedback.blockReason`) → `PermanentError`;
     - o texto é a concatenação de `parts[].text`;
     - o tier cobrado vem de `usageMetadata.serviceTier`, que é a fonte de verdade para o preço.
  7. **Registro do gasto**, sempre antes de rejeitar:
     - `record_spend` + linha `LLMGeneration` com `model_slug=model` e
       `resolved_provider=f"google-ai-studio:{tier}"`;
     - essa coluna é o que permite medir a fatia real de Flex no piloto.
  8. `finishReason != "STOP"` → `PermanentError`. Mesmo motivo do OpenRouter: texto cortado passa
     no gate de fundamentação (§26.4).
- **Construção:** um `httpx.AsyncClient` preguiçoso, criado só na primeira chamada Gemini. Assim
  os outros lanes que constroem `RealLLMClient` não mudam.

### 3. Lane e pipeline

- **`brave/lanes/atrativos/copywriter.py`:** remover a constante `CASCADE_MODEL`, ou deixá-la como
  default de import para os testes, e documentar o slug direto.
- **`brave/lanes/atrativos/places_enrichment.py`:** trocar `CASCADE_MODEL` por um parâmetro
  `cascade_model`, recebido do pipeline.
- **`brave/tasks/pipeline.py`** (os dois pontos que constroem o copywriter, linhas ~1087 e ~1516):
  - passar `cascade_model=app_config.atrativo_cascade_model`;
  - **falhar o build quando o modelo for `gemini-*` e `BRAVE_LLM_GEMINI_API_KEY` estiver vazia.**
    - Mesmo comportamento da chave do Parallel: desliga o enriquecimento inline naquele sweep
      (`inline_enrichment_build_failed`).
    - **Por que é crítico:** se a falha só aparecer no `generate()`, o copywriter trata como falha
      comum e **gasta uma `descricao_attempt` por atrativo**. Em 3 sweeps o backlog inteiro fica
      excluído de descrição.

### 4. Testes (offline: `respx` + `fakeredis` + `sqlite_session`)

Em `tests/unit/clients/test_real_llm_client.py`, seção nova "generate() via Gemini direto":

1. **Body e roteamento:**
   - URL `…/models/gemini-2.5-flash:generateContent`, header `x-goog-api-key`;
   - `systemInstruction`, `contents`, `maxOutputTokens=2048`, `thinkingBudget=0`, `serviceTier="flex"`;
   - não chama OpenRouter nem Anthropic.
2. **Flex 503 → standard:**
   - duas requisições, a segunda sem `serviceTier`;
   - a linha diz `resolved_provider="google-ai-studio:standard"` e o custo sai pelo preço standard.
3. **Timeout no Flex → standard:** idem.
4. **Custo com cache:** `cachedContentTokenCount` cobrado pela taxa de cache; o `record_spend` soma no contador do fakeredis.
5. **`finishReason="MAX_TOKENS"`:** `PermanentError` **e** a linha de gasto gravada.
6. **Sem `candidates`** (bloqueado): `PermanentError`.
7. **Modelo fora da tabela:** `ValueError`. Com `gemini_service_tier="standard"`, sai uma requisição só, sem `serviceTier`.
8. **Regressão:** os testes de OpenRouter (`google/gemini-2.5-flash`) e Anthropic continuam verdes, sem alteração.

Nos demais arquivos:

- **Pipeline:** cascata ligada + modelo `gemini-*` + chave vazia → build falha e o enriquecimento inline fica desligado, **sem** incrementar `descricao_attempts`.
- **`tests/unit/lanes/test_copywriter_cascade.py`:** ajustar ao parâmetro `cascade_model`.

### 5. Verificação

1. **Suíte unitária**, com `RUN_REAL_EXTERNALS` desligado (memória `pytest-unset-run-real-externals`):
   `.venv/bin/python -m pytest tests/unit`, depois `ruff` e `mypy` nos arquivos tocados.
2. **Integração:** faça `pg_dump` antes, porque a suíte zera tabelas de referência (memória
   `reset-db-after-local-tests`). Rode com `BRAVE_DB_URL` setado.
3. **Smoke real, 1 atrativo** (`scripts/single_attraction`), conferindo:
   - uma linha em `llm_generations` com `model_slug=gemini-2.5-flash`;
   - `resolved_provider` flex e custo ~US$ 0,0016;
   - descrição gravada e `g ≥ 0,75`.
4. **Piloto, 200 atrativos**, com o worker recriado e as flags do overlay
   `description_enrichment_enabled` e `atrativo_description_cascade_enabled` ligadas. Métricas:

   | métrica | alvo |
   |---|---|
   | taxa de aprovação no gate | ≥ 95% |
   | fatia Flex (`resolved_provider`) | registrar; esperado ~80% |
   | custo médio por atrativo em `llm_generations` | ≤ US$ 0,0022 |
   | p95 da chamada | ≤ 60 s (teto do Flex + standard) |
   | `PermanentError` / falhas do copywriter | ≤ 2% |

5. **Sync completo.** Com o orçamento padrão de US$ 10/dia, o cost guard libera ~3.400
   atrativos/dia, porque também conta US$ 0,001 nominal da busca Parallel. 10 mil levam ~3 dias.
   **Decisão pendente com o usuário:** subir `BRAVE_LLM_USD_DAILY_BUDGET` para ~US$ 35 durante o
   sync e fechar em 1 dia.

### 6. Documentação e memória

- `docs/poc/gemini-viability.md` §30: medição Flex vs Flash-Lite vs Sonnet (`claude -p`), mais a
  verificação das 4 dicas do Gemini. Promover o probe para `scripts/poc/gemini_direct_probe.py`.
- Atualizar as memórias `gemini-direto-flex-medido` e `estado-custo-descricoes` com o que foi
  implementado.

## Riscos

| risco | mitigação |
|---|---|
| Google tira o 2.5 Flash desta chave ("no longer available to new users" já aconteceu na chave antiga) | rollback por env para `google/gemini-2.5-flash` no OpenRouter, sem deploy |
| Flex some ou fica pior que 21% de 503 | `BRAVE_LLM_GEMINI_SERVICE_TIER=standard` (US$ 31/10k, ainda abaixo do OpenRouter) |
| timeout no Flex cobrado pelo Google e também em standard (gasto duplo) | raro, e o teto é o custo standard; a linha `gemini_flex_fallback` permite medir |
| tabela de preço desatualiza | valores datados no código; o piloto compara `llm_generations` com o console de billing do Google |
| chave faltando queima tentativas | build falha antes de chamar o copywriter (tarefa 3, com teste) |

## Fora de escopo

- Batch API (async, 24 h): analisada e rejeitada para o sync (memória `cascata-gemini-2-5-flash`).
- Context caching: o prefixo fixo tem 475 tokens, abaixo do mínimo de 2.048, e as fontes são únicas.
- Flash-Lite (US$ 4/10k, 73% dos fatos): fica disponível trocando só o env `ATRATIVO_CASCADE_MODEL`,
  porque já está na tabela de preço.
- Migrar `extract()` (DeepSeek) ou outros lanes para o Google direto.

## Execução

Executar via `/gsd-quick` apontando para este plano, em branch nova a partir da `main`
(`feat/gemini-direto-flex`). Um commit por tarefa (1-3 código, 4 testes, 6 docs).

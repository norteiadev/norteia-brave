---
quick_id: 260915-gdf
slug: gemini-direto-flex
date: 2026-09-16
type: feature
status: complete
branch: feat/gemini-direto-flex
---

# Resumo: copywriter da cascata no Gemini 2.5 Flash direto (AI Studio), tier Flex

O redator da cascata de atrativos saiu do OpenRouter e passou a falar direto com o AI Studio,
no tier Flex (metade do preço), caindo para standard quando o Flex responde 503, 429 ou estoura
o teto de tempo. Medido na lane real: **US$ 0,00166 por chamada contra ~US$ 0,0033 no OpenRouter**.

## Commits

| # | Tarefa | Commit |
|---|---|---|
| 1 | Configuração (chave, tier, timeout, `ATRATIVO_CASCADE_MODEL`) | `be237b2` |
| 2 | Client: `generate()` roteia `gemini-*` para o AI Studio | `9489fc3` |
| 3 | Lane e pipeline: modelo por config + build recusa Gemini sem chave | `501d965` |
| 4 | Testes | `a503f3c` |
| 6 | Doc §30, probe promovido, memórias | (este commit) |

## O que mudou

- **Roteamento por slug** em `RealLLMClient.generate()`: `gemini-*` vai para o `:generateContent`
  do AI Studio via httpx, `vendor/model` segue no OpenRouter, `claude-*` na Anthropic.
- **Flex com rede de proteção:** uma tentativa com teto `BRAVE_LLM_GEMINI_FLEX_TIMEOUT_S` (60 s);
  em 503, 429 ou timeout o mesmo corpo vai em standard com a política de retry de sempre.
- **Preço por tabela local** (o Google não devolve custo), pelo tier que o `usageMetadata.serviceTier`
  diz ter cobrado. `resolved_provider` grava `google-ai-studio:<tier>`.
- **Gasto gravado antes de rejeitar** um `finishReason != STOP` ou prompt bloqueado.
- **`thinkingBudget: 0` explícito** e autenticação por header `x-goog-api-key`.
- **Build do pipeline recusa** um redator `gemini-*` sem chave ou sem preço: sem isso o erro
  apareceria dentro do `generate()` e queimaria uma `descricao_attempt` por atrativo.
- **Rollback sem deploy:** `ATRATIVO_CASCADE_MODEL=google/gemini-2.5-flash`.

## Verificação

- **Unitários:** 1085 passam, `RUN_REAL_EXTERNALS` desligado. Ruff e pyright sem achado novo.
- **Integração:** 204 testes num banco descartável, as mesmas 8 falhas pré-existentes da `main`.
- **Smoke (1 atrativo):** linha em `llm_generations` com `google-ai-studio:flex`, US$ 0,00164,
  descrição gravada, fundamentação 0,91.
- **Piloto (246 atrativos, DF+GO):** 226 descrições, 96,6% de aprovação no gate, 85,9% cobrados
  em Flex, US$ 0,00281 por atrativo com a busca, p50 4 s, p95 63 s, zero falhas do copywriter.
  Projeção: **~US$ 26 para os 10 mil** (~US$ 16 Gemini + ~US$ 10 Parallel).

## Pendências

- **p95 de 63 s** passa a meta de 60 s por construção (teto do Flex + retry standard). Baixar
  `BRAVE_LLM_GEMINI_FLEX_TIMEOUT_S` para ~45 s resolve.
- **Sync completo (tarefa 5.5)** ainda não rodou: precisa de `BRAVE_LLM_USD_DAILY_BUDGET` em
  ~US$ 45 e do beat parado, porque o `ta_keepalive` desliga o engine no meio do sweep (bug
  pré-existente, memória `ta-keepalive-desliga-engine`).
- **Flags do overlay** `description_enrichment_enabled` e `atrativo_description_cascade_enabled`
  voltaram para `false` depois do piloto; o sync exige as duas ligadas.

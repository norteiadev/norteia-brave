---
quick_id: 260820-jhk
slug: teste-15-3-snippets
date: 2026-08-20
status: complete
---

# Resumo — pendência da §15.3 fechada

Executado com a Tavily no free tier. Resultado escrito como §18 do
`docs/poc/gemini-viability.md`.

## Medido

| modo | fatos fortes | tokens/atrativo |
|---|---|---|
| Sonnet + `web_search` (baseline) | 10/10 | ~11.900 |
| Tavily, 1 query, snippet | 5/10 | 755 |
| **Tavily, 2 queries, snippet** | **9/10** | **2.311** |
| Tavily, 2 queries + página | 7/10 | 44.511 |

**Snippet basta**, desde que sejam 2 queries por atrativo — o que dobra o custo de busca em
relação ao que a §15.2 e a §17.4 projetaram. Ganho real corrigido: **4,9x a 39x** conforme o
provedor, contra $74,90/mil hoje. Free tier da Tavily rende **500 atrativos/mês**, não 1.000.

**O segundo passo de leitura de página morreu como hipótese**: na Tavily, pedir
`include_raw_content` troca 3 das 5 URLs, falha em extrair 4 das 5, e entrega 19x tokens com
menos fato. Não medido na Contents API da Exa, que é outro produto.

## Duas armadilhas da sonda, corrigidas no caminho

1. Truncar `raw_content` no head descartava o trecho relevante junto com o menu — dava 4/10 e
   parecia achado sobre a fonte.
2. Casador de fatos só no passado (`"recebeu o nome"`) marcava ausente um fato presente
   (`"recebe este nome por…"`).

Ambas produziram, na primeira rodada, um número errado que parecia conclusão. Registradas na
§18.4.

## Confiabilidade

Modo snippet é determinístico: 3 rodadas idênticas — mesmos 9/10, mesmos 2.311 tokens, as
mesmas 26 URLs.

## Risco que sobra

Cobertura, não profundidade: 1 dos 10 fatos não existia no corpus da Tavily. Em 3 atrativos
isso é 10% — amostra pequena demais para virar taxa. Medir em escala antes de trocar o
provedor em produção.

## Custo do teste

~30 dos 1.000 créditos mensais.

---
quick_id: 260827-qxw
slug: memoria-parametrica
date: 2026-08-27
type: poc
status: complete
---

# Quick: a memória paramétrica do modelo dispensa a busca web?

Pergunta do usuário: os pesos do modelo já carregam fato sobre atrativo brasileiro; isso não
removeria a tool `web_search` e com ela a caixa mais cara do pipeline (§11.1)?

## Desenho do teste

O ponto não é medir acerto — é medir invenção. Memória paramétrica não falha dizendo "não sei";
falha inventando com confiança, e nada no pipeline distingue os dois.

Três classes de alvo, prompt de produção importado do módulo real, nenhuma tool:

- 3 obscuros da §15.1 (o caso real, 95%)
- 2 famosos (o que a Wikipedia já cobre)
- 2 **inventados**, verificados como inexistentes na Tavily antes de entrar na lista

## Resultado

Zero abstenções em 6 casos falsos × 3 modelos. Recall real no obscuro é zero. O Sonnet
fabricou o bloco de resultados de busca inteiro — 4 URLs, uma delas um `es.gov.br` que dá 404.

Escrito como §19 de `docs/poc/gemini-viability.md`.

## Custo do teste

21 chamadas sem tool (~$0,25 no Sonnet, $0 no flash-lite free, marginal no DeepSeek).

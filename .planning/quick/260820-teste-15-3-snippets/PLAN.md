---
quick_id: 260820-jhk
slug: teste-15-3-snippets
date: 2026-08-20
type: poc
status: in-progress
---

# Quick: o teste da §15.3 — snippet de API de busca basta?

A pergunta em aberto desde o commit ee1aca1. O `web_search` da Anthropic custa $10/1.000 **e**
injeta 12-28 mil tokens de página no prompt — 61% da conta (§11.1). Exa/Tavily/Brave devolvem
título + 2-3 linhas. Se o snippet carregar os mesmos fatos, a cascata da §15.2 fecha em
$4,75/mil contra $74,90/mil. Se não carregar, é preciso um segundo passo de leitura de página
e parte dos tokens volta — o que a Contents API da Exa precifica em ~$0,001/página (§17.3).

## Alvo

Os 12 fatos fortes que o Sonnet + `web_search` produziu nos três atrativos obscuros da §15.1 —
atrativos reais do OSM, sem artigo na Wikipedia, representando os 95% do caso:

- Mirante da Lagoa (Guarapari/ES) — 5 fatos, Sonnet gastou $0,0611
- Mirante de Buenos Aires (Guarapari/ES) — 3 fatos fortes + 1 genérico, $0,0532
- Vista Linda (Domingos Martins/ES) — 2 fatos fortes + 1 genérico, $0,1131

## Tarefas

1. [x] `scripts/poc/search_snippets_probe.py` — sonda com três provedores, casador de fatos
       tolerante a acento/caixa, contagem de token pelo tokenizer da Anthropic (para a conta
       fechar contra os números de §9.3/§11.1), `--read-pages` para o segundo passo e
       `--self-check` offline.
2. [ ] **Bloqueado:** obter ao menos uma key de free tier. Nenhuma existe no `.env`.
3. [ ] Rodar em modo snippet e, se a taxa de fatos cair, em `--read-pages`.
4. [ ] Escrever o resultado como §18 e fechar a pendência da §15.3.

## Critério de decisão

Snippet basta = fatos fortes altos **com** tokens uma ordem de grandeza abaixo dos ~11.900 que
o `web_search` injeta. Fato alto com token alto não resolve nada: o custo estaria só mudando
de fornecedor.

## Bloqueio

Nenhuma key de busca no `.env` (só Places, Anthropic, OpenRouter, Gemini, dados.gov.br). As
três são de free tier — Tavily não pede cartão, Exa e Brave pedem. Ver §17.3.

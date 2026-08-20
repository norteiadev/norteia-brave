---
quick_id: 260820-iig
slug: omniroute-secao-17
date: 2026-08-20
type: docs
---

# Quick: §17 — OmniRoute avaliado como provider de LLM

Acrescentar a seção 17 ao `docs/poc/gemini-viability.md` (antes de `## Fontes`) e registrar as
novas URLs na lista de Fontes. Doc-only — nenhum código de pipeline é tocado.

## Origem

O usuário pediu avaliação de `github.com/diegosouzapw/OmniRoute` como provider de LLM para o
Brave ("tem vários tokens gratuitos em vários modelos"). A avaliação reprova o OmniRoute como
provider, mas o catálogo de free tiers dele rendeu preços de **provedores de busca** — que é a
pendência aberta da §15.3 — e expôs duas correções ao próprio relatório.

## Tarefas

1. Escrever `## 17. OmniRoute como provider de LLM (medido)` com as subseções 17.1 a 17.6,
   imediatamente antes de `## Fontes`.
2. Acrescentar 5 URLs à lista de Fontes (OmniRoute, Brave Search API, Exa, Tavily, Serper).
3. Atualizar a tabela "Quick Tasks Completed" do STATE.md.
4. Commit doc-only na branch `docs/gemini-viability-poc`.

## Restrição

Todos os números da seção foram verificados nas páginas oficiais em 2026-08-20 antes de
escrever. Nenhum número pode ser inferido ou arredondado na redação — inclusive porque uma das
conclusões da seção é justamente que o catálogo de terceiro (OmniRoute) errou um dos preços.

## Fora de escopo

Nenhuma outra seção do relatório é alterada. As correções que a §17 impõe às §15.2/§15.3 ficam
declaradas dentro da própria §17, preservando o histórico do que foi medido em cada data.

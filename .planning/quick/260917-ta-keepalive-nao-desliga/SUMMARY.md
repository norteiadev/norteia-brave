---
quick_id: 260917-tkd
slug: ta-keepalive-nao-desliga
date: 2026-09-17
type: fix
status: complete
branch: fix/ta-keepalive-nao-desliga
---

# Resumo: o keepalive mantém a sessão do TA e não desliga mais o engine

## Commits

| # | Tarefa | Commit |
|---|---|---|
| 1 | `ta_keepalive`: ping GraphQL, TTL deslizando, contador de falhas | `65bea8a` |
| 2 | Testes | `8556b75` |
| 3 | Documentação e estado | (este commit) |

## O que mudou

- **O ping usa o transporte do sweep** (`fetch_attractions_paginated_gql`, AttractionsFusion em
  `.com.br`). O beat não consegue mais declarar morta uma sessão que o sweep está usando com
  sucesso, que era exatamente o falso positivo do piloto.
- **O engine deixou de ser tocado pelo beat.** Quem decide que a sessão morreu continua sendo o
  `sweep_tripadvisor`, com o R1 intacto.
- **Falha virou contador:** `brave:ta:keepalive_failures` guarda falhas consecutivas (com o TTL
  da sessão, para não somar falhas de sessões diferentes) e só a partir da terceira o
  `needs_bootstrap` é marcado, para o painel avisar. Um sucesso zera.
- **O TTL desliza em todo ping bem-sucedido.** Antes ele só deslizava quando a resposta trazia
  Set-Cookie, então um 200 sem rotação de cookie deixava a sessão morrer de TTL.

## Verificação

- **Unitários:** 1073 passam com `RUN_REAL_EXTERNALS` desligado; 12 no arquivo do keepalive.
- **Mutação:** os 6 casos novos foram rodados contra o código anterior e todos falham, ou seja,
  pegam a regressão.
- **Ruff:** nenhum achado novo.
- **Ao vivo:** pendente, porque a sessão do TripAdvisor já expirou (TTL −2). Na próxima injeção
  de cURL dá para conferir `ta_keepalive_ok` e o TTL deslizando com o engine intacto.

## Diagramas

- `diagrams/ta-keepalive-fluxo.*` — o ciclo do beat passo a passo: os dois portões de saída, o
  ping GraphQL, o ramo do 200 (cookies, TTL, contador zerado) e o ramo do 403 (contador, marcador
  só na terceira, engine intocado).
- `diagrams/ta-keepalive-antes-depois.*` — o contraste com o comportamento que matou o piloto.

Cada um sai em `.mmd` (fonte), `.svg`, `.png` e `.excalidraw` (editável em excalidraw.com; o da
comparação abre como imagem única, porque o conversor não quebra `subgraph`).

## Pendências

- ~~§30.4 de `docs/poc/gemini-viability.md`~~ — corrigido depois que o PR #27 entrou na main:
  a seção agora registra a correção em vez de mandar parar o beat.
- **A outra metade da resiliência:** um único 403 transitório no próprio sweep continua
  desligando o motor (R1). Mexer nisso muda a semântica do gate e precisa de decisão.

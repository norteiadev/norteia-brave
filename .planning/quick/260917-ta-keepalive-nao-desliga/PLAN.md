---
quick_id: 260917-tkd
slug: ta-keepalive-nao-desliga
date: 2026-09-17
type: fix
status: planned
---

# Quick: o keepalive mantém a sessão do TA viva e nunca desliga o engine

## Por quê

No piloto de 2026-09-15 (246 atrativos, DF+GO) a primeira rodada morreu em 59 atrativos, 4 min
depois do start. Causa: o beat `ta_keepalive` renova a sessão com um GET na página **HTML** do
`www.tripadvisor.com`, que o DataDome responde **403 mesmo com a sessão boa** — os sweeps
GraphQL em `.com.br` seguiam 200 e renovando cookies no mesmo instante. No 403 o keepalive faz
`_mark_needs_bootstrap()` + `set_mode(DESLIGADO)`, e todo produtor por UF para na virada da
próxima página (`ta_atrativos_producer_halt`).

Para os 10 mil atrativos (~24 h de relógio) isso corta o sync a cada ~10 min.

## Decisões de desenho

- **O ping passa a usar o mesmo transporte do sweep** (`fetch_attractions_paginated_gql`,
  AttractionsFusion em `www.tripadvisor.com.br`). Se o sweep consegue coletar, o keepalive
  não pode declarar a sessão morta. Isso mata o falso positivo na raiz.
- **O keepalive deixa de mexer no engine.** Quem decide que a sessão morreu é o sweep, que já
  falha rápido e desliga o motor (R1, mantido). Um beat de saúde não pode ser o assassino do
  motor.
- **Falha vira contador, não veredito:** `brave:ta:keepalive_failures` conta falhas
  **consecutivas**; só a partir de 3 (`_KEEPALIVE_FAILURES_BEFORE_BOOTSTRAP`) o
  `needs_bootstrap` é marcado, para o painel avisar o operador. Sucesso zera o contador.
- **TTL escorrega sempre que o ping funciona.** Hoje o TTL só desliza quando a resposta traz
  Set-Cookie (`persist_rotated_cookies`). Um ping bem-sucedido sem cookie novo deixava a sessão
  morrer de TTL. Depois do ping OK o keepalive dá `expire(session_ttl)` explícito.

## Tarefas

### 1. `brave/tasks/pipeline.py` — ta_keepalive

- `_ping()` consome uma página de `fetch_attractions_paginated_gql(geo_id=294280, start_page=1,
  max_pages=1)`.
- Sucesso: `rc.expire(BRAVE_TA_SESSION_KEY, ta_config.session_ttl)`, zera o contador de falhas,
  loga `ta_keepalive_ok` com `ttl_before`.
- `SessionExpiredError` / `SessionMissingError`: incrementa o contador (com TTL de expiração
  próprio, para não acumular falhas de dias diferentes), **não toca no engine**, e só chama
  `_mark_needs_bootstrap()` quando o contador chega em 3. Loga `ta_keepalive_session_expired`
  com `error_type` e `falhas_consecutivas` — nunca `str(exc)` (T-p2v-02).
- Outras exceções: continuam só logando (`ta_keepalive_error`). O beat nunca quebra.

### 2. Testes — `tests/unit/tasks/test_ta_keepalive.py`

1. **Transporte:** o ping chama `fetch_attractions_paginated_gql` (e não o HTML).
2. **Expirada 1x e 2x:** engine continua `LIGADO` e `needs_bootstrap` NÃO é marcado.
3. **Terceira falha consecutiva:** `needs_bootstrap` marcado, engine ainda `LIGADO`.
4. **Sucesso zera:** duas falhas, depois um sucesso, depois duas falhas → sem `needs_bootstrap`.
5. **TTL desliza no sucesso** mesmo sem cookie rotacionado.
6. **Regressão:** o R1 do `sweep_tripadvisor` (sessão expirada → engine OFF) continua verde.

### 3. Documentação e memória

- §30.4 de `docs/poc/gemini-viability.md`: o achado deixa de ser "precisa parar o beat".
- Memória `ta-keepalive-desliga-engine`: vira "corrigido em <commit>", com o contorno antigo
  marcado como desnecessário.
- `SUMMARY.md` + linha no `.planning/STATE.md`.

## Verificação

1. `.venv/bin/python -m pytest tests/unit` com `RUN_REAL_EXTERNALS` desligado, mais ruff.
2. Ao vivo: com sessão injetada, rodar o beat e conferir `ta_keepalive_ok` + TTL deslizando,
   com o engine intacto.

## Fora de escopo

- **Endurecer o 403 do próprio sweep** (hoje um único 403 transitório desliga o motor). É a
  outra metade da resiliência do sync, mas mexe no gate R1 e merece a sua decisão.
- Trocar o `geo_id` do ping ou a frequência do beat.

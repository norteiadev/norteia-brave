# Config por um caminho só — candidato #5 da revisão de arquitetura (24/09)

Decisões fechadas em grilling com o Leandro (Q1–Q14, todas conforme recomendado).

## Escopo (Q1, Q9)
Cirúrgico. `AppConfig()` que só lê campos de env (`run_real_externals`, chaves, modelos,
`ramp.*`) fica. Fora: `os.environ.get("BRAVE_DB_REDIS_URL"/"BRAVE_DB_URL")`,
`BRAVE_NORTEIA_API_URL`/`TOKEN`/`BRAVE_PLACES_API_KEY` fora de settings. Dentro: corrigir o
docstring "singleton" de `deps.get_config` (não é cacheado) e as referências a
`get_db_config()`, que não existe (settings.py ~523, deps.py ~7).

## Snapshot (Q2, Q13)
- O snapshot no Redis guarda SÓ as linhas do overlay (`config_settings` → dict key→value),
  nunca o `AppConfig` serializado. `load_effective_config` = `AppConfig()` do env + overlay
  (do snapshot ou do DB). Resultado: nada de segredo no Redis; o config efetivo tem as chaves
  Tavily/Parallel reais.
- Chave nova: `brave:config:overlay` (TTL 60s, igual). A velha `brave:config:snapshot` não
  é lida por ninguém; runbook de deploy: `DEL brave:config:snapshot`.

## Invalidação (Q3)
`upsert_config` marca a sessão (`session.info`) como "config suja"; um único listener
SQLAlchemy `after_commit` apaga `brave:config:overlay` quando a flag está setada (e
`after_rollback` limpa a flag). Cobre PATCH /config, `engine.set_mode(session=)`,
`seed_default_config` e escritores futuros. Remover os busts manuais que ficarem redundantes
(PATCH, set_mode). O listener precisa de um Redis: usar o mesmo mecanismo que os chamadores
já usam para o snapshot (BRAVE_DB_REDIS_URL / fakeredis em teste) — best-effort, erro de
Redis só loga.

## Registro único de chaves (Q4)
Um registro em `brave/config/runtime.py` (nome da chave, tipo/validação, default do seed,
campo do AppConfig que sobrescreve) consumido por `_apply_overlay`, `_seed_values`,
`routers/config.py` (`_WEIGHT_KEYS`, `_THRESHOLD_KEYS`, `_*_KEY`, `_current_value`) e
`core/engine.py` (`_ENGINE_MODE_CONFIG_KEY`). Dashboard TS fora deste PR.
`core/engine.py` é kernel: pode importar `brave.config.runtime` (checar
test_domain_boundaries).

## Fallbacks (Q5, Q10)
- `routers/engine.py::_effective_config`: sem fallback para `AppConfig()`; erro de DB → 503.
- `beat_schedule._enabled_sources_best_effort` (roda no import): em falha, NENHUMA lane de
  sweep (só manutenção), log `error`. Ajustar testes que dependiam do fallback env.

## ScoreConfig() silencioso (Q6)
`config` obrigatório em `lanes/atrativos/signal_agent.py:~166`,
`lanes/atrativos/places_enrichment.py:~260`, `domains/manual/services.py:~100`
(ManualService só tem chamador em teste). Se algum chamador de produção não passar config,
usar `load_effective_config` como `core/dlq/service.py:35`. Script
`scripts/single_attraction/run_single_attraction.py:~207` → `load_effective_config`.

## /engine/source (Q7)
`routers/engine.py:~429` passa a usar `enabled_sources(load_effective_config(...))`.
Reescrever `tests/unit/api/test_engine_set_source_endpoint.py:69-72` (fixava o bug).

## enabled_sources duplicado (Q8)
`brave/domains/__init__.py:~92` chama `brave.config.runtime.enabled_sources` e só filtra
`manual`. Lógica num lugar.

## Par effective + app_config (Q11, Q12)
- Colapsar os `app_config = AppConfig()` ao lado de `_load_config` em `brave/tasks/pipeline.py`
  (~560, 750, 1051, 1143, 1238, 1557, 1703, 1759, 1849, 2022, 2186) para usar só o efetivo.
- `clients_for(config, *, ibge_lookup=None)` — um config só (`brave/clients/factory.py`);
  `Clients` idem. 14 chamadas + fakes de teste (testes trocam `pipeline.clients_for`).
  Onde não há sessão, o chamador passa `AppConfig()`.

## Testes (Q14)
Novos: (1) listener: upsert+commit apaga a chave; upsert+rollback mantém; (2) snapshot só
contém chaves do registro (nenhum segredo); (3) `/engine/source` com lane desligada no DB →
422; (4) `_apply_overlay` direto a partir do registro. `tests/conftest.py:98-109` monkeypatcha
`pipeline._load_config` — manter funcionando.
Gate: suíte em `norteia_brave_test` + Redis db 15, `unset RUN_REAL_EXTERNALS`; baseline =
8 falhas conhecidas (engine_endpoints ×6, test_chain_stops_at_dlq_no_auto_gate,
test_sc2_discovery_skips_absent_parent_destino).

## Deploy
Restart de api+worker+beat juntos; `DEL brave:config:snapshot` em cada Redis db usado.

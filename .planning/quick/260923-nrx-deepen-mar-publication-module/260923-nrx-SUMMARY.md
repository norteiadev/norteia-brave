---
phase: quick-260923-nrx
plan: 01
subsystem: mar-publication
tags: [mar, norteia-api, celery, outbox, refactor]
requires: []
provides:
  - brave/core/mar/publication.py (promote / publish / republish_pending, Promotion, Published)
  - brave.publish_mar (single push task)
  - NorteiaApiClientProtocol.push(entity_type, payload) -> bool
  - brave.api.deps.get_publish_enqueue
affects:
  - brave/api/routers/{cms,dlq,atrativos,engine}.py
  - brave/shared/whatsapp/agent.py (_finalize_node)
  - brave/cli.py
tech-stack:
  added: []
  patterns: [injected enqueue callable, implicit outbox (pushed_at NULL), health-gated adapter]
key-files:
  created:
    - brave/core/mar/publication.py
    - tests/unit/test_mar_publication.py
    - CONTEXT.md
  modified:
    - brave/shared/exceptions.py
    - brave/clients/base.py
    - brave/clients/norteia_api.py
    - brave/clients/null_norteia_api.py
    - tests/fakes/fake_norteia_api.py
    - brave/tasks/pipeline.py
    - brave/api/deps.py
    - brave/api/routers/cms.py
    - brave/api/routers/dlq.py
    - brave/api/routers/atrativos.py
    - brave/api/routers/engine.py
    - brave/shared/whatsapp/agent.py
    - brave/cli.py
  deleted:
    - tests/integration/test_push_destination_task.py
    - tests/unit/test_push_hash_skip.py
decisions:
  - "promote() owns audit + commit + enqueue; broker failure returns push_queued=False (no 503)"
  - "brave.publish_mar never promotes; stamps push_hash/pushed_at only when adapter.push returns True"
  - "WhatsApp owner confirmation audits as owner_validated (steward dlq_validated rate untouched)"
  - "publish_mar drops the old PermanentError branch (nothing in publish raises it)"
metrics:
  duration: ~45min
  completed: 2026-09-23
  tasks: 3
---

# Quick 260923-nrx: Deepen the Mar publication module Summary

Um módulo de kernel (`brave/core/mar/publication.py`) passa a concentrar Promoção (validar → re-pontuar → promover → auditar → commit → enfileirar), Publicação (`publish`, corpo da task única `brave.publish_mar`) e o outbox (`republish_pending`). Os dois adapters da norteia-api expõem `push(entity_type, payload) -> bool`, e o `isinstance` que vazava pelo seam sumiu.

## Commits

| Task | Commit | Subject |
|------|--------|---------|
| 1 | 64f6895 | feat(260923-nrx): Mar publication module + adapter push(entity_type, payload) |
| 2 | 7623495 | refactor(260923-nrx): single brave.publish_mar task; callers go through publication |
| 3 | fd643b8 | test(260923-nrx): replace push-task tests with publication tests; add CONTEXT.md glossary |

## O que mudou

- `promote(session, rio, *, actor, enqueue, action="dlq_validated", held_action=None, extra=None, config=None) -> Promotion`. O vocabulário de auditoria foi mantido: cms promote e dlq validate/batch usam `dlq_validated`; as transições e o promote-bulk usam `transition_mar` / `promote_held` (o bulk leva `batch_id` em `after_state`). O WhatsApp passou a usar `owner_validated`.
- `publish(session, rio_id, api) -> Published`, com status `pushed | unchanged | not_sent | api_down | not_in_mar`. A fórmula do digest continua exatamente a mesma, então os `push_hash` gravados seguem válidos.
- `NorteiaApiClient.push` checa `sync.norteia_api_up(self._redis)` e levanta `ApiDown` (subclasse de TransientError) sem fazer o POST; quando a API está de pé, ele mesmo abre e fecha o client com `async with self`. `NullNorteiaApiClient.push` devolve False.
- Tasks removidas: `push_mar`, `push_destination_task`, `push_attraction_task`, `dispatch_pending_pushes`, `_build_push_payload`, `_push_hash`, `_mark_pushed`. `repush_pending_mar` agora chama `republish_pending(session, publish_mar.delay)`, e os dois call sites de `build_graph` passam `publish_mar.delay`.
- Nenhum endpoint de promoção devolve mais 503 por broker fora do ar. Promoções de um registro só ganharam `push_queued` na resposta; o bulk continua devolvendo `push_failed` (ids com push_queued False).
- `POST /api/v1/mar/repush` mantém o 503 quando a norteia-api está fora e chama `republish_pending` (importado no topo do módulo).

## Deviations from Plan

1. **[Rule 3 — gate de verificação] `ruff check brave/` e `ruff check tests/` já falhavam na base.** Na base eram 60 erros em brave/ e 162 em tests/ (llm.py, nominatim.py, places.py, compliance/*, E402 no pipeline etc.). Comparei a saída normalizada do ruff da base com a de agora: em brave/ não entrou nenhum erro novo (60 → 59) e em tests/ caiu de 162 para 151. Todo arquivo que criei ou reescrevi passa limpo. Os erros pré-existentes ficaram fora do escopo. `ApiDown` recebeu `# noqa: N818`, porque o nome foi travado na decisão Q11.
2. **Gate de grep "broker unavailable" em brave/api/routers é amplo demais.** Ele também pega 503s legítimos que não têm relação com promoção: dispatch de outreach/inbound em atrativos_gate.py, start do engine, sweep.py e runs.py. Todos os 503 dos caminhos de promoção saíram (cms/dlq/atrativos); os outros ficaram intocados, de propósito.
3. **Testes extras de router.** Adicionei `test_validate_enqueues_publish_after_commit` (test_destinos_lane) e `test_promote_enqueues_publish` (test_cms_endpoints), além das versões 202/pending dos testes de broker fora. As duas suítes também ganharam uma fixture autouse que troca `get_publish_enqueue` por uma lista, para que nenhum teste de router dependa de um broker real.
4. **cli.py:** o texto do backstop agora mostra `promotion.held_reason` e o "Push:" mostra `published.status` (`not_sent` no Null), no lugar de "recorded".

## Verification

- Suíte completa (norteia_brave_test + Redis db 15, RUN_REAL_EXTERNALS unset): **8 failed, 1449 passed, 1 skipped**. As 8 falhas são exatamente as da base:
  - tests/integration/test_atrativos_chain_e2e.py::test_chain_stops_at_dlq_no_auto_gate
  - tests/integration/test_atrativos_lane_e2e.py::test_sc2_discovery_skips_absent_parent_destino
  - tests/integration/test_engine_endpoints.py::test_start_transitions_to_running
  - tests/integration/test_engine_endpoints.py::test_start_twice_returns_409
  - tests/integration/test_engine_endpoints.py::test_stop_requests_graceful_stop
  - tests/integration/test_engine_endpoints.py::test_start_accepts_custom_ufs_and_lane
  - tests/integration/test_engine_endpoints.py::test_start_threads_depth_to_dispatch_and_status
  - tests/integration/test_engine_endpoints.py::test_depth_validation_precedes_already_running_check
- O teste Pact não foi alterado e passa; test_domain_boundaries passa (publication.py não importa brave.tasks).
- test_celery_task_registration confirma que `brave.publish_mar` está registrado.
- Os gates de grep para os nomes removidos em brave/ e tests/ voltam vazios.

## Known Stubs

None.

## Self-Check: PASSED

- brave/core/mar/publication.py, tests/unit/test_mar_publication.py, CONTEXT.md: FOUND
- commits 64f6895, 7623495, fd643b8: FOUND

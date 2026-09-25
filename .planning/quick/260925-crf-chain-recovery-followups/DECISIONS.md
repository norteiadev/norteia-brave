# Pendências pós-refactor: recuperação da cadeia, backoff, erros de beat, bulk TA (25/09)

Decisões fechadas em grilling com o Leandro (Q1–Q7, todas conforme recomendado). Base: main 12c77cd.
Origem: follow-ups (i)–(v) do candidato #4 (PR #41), mapeados em 25/09.

## Fatos (mapa de 25/09)
- Cadeia Places: discover_atrativo → `discovered` → find_contacts → `contacts_found` → gather_signals →
  `signals_gathered` → enrich_places. Só `discovered` se recupera (o discover re-consulta `sub_state='discovered'`
  por UF no beat diário 03:00 UTC). `contacts_found` e `signals_gathered` NÃO têm quem re-despache.
  - find_contacts: `contact_finder_agent.py:~87` place_details sem guarda → registro fica em `discovered` na pausa.
  - gather_signals: `signal_agent.py:~204` → fica em `contacts_found`. SignalAgent põe `signals_gathered` e, se
    routing=dlq, zera `sub_state=None` na hora — então parados em `signals_gathered` têm routing ≠ dlq.
  - enrich_places: só é despachado por gather_signals (pipeline.py:~990).
- `RioRecord` não tem `updated_at` (só `processed_at`, `descricao_batch_submitted_at` — models.py:~160-176).
- 4 fallbacks inline em tasks (`try: X.delay() except Exception: X.run()`): discover_atrativo→find_contacts
  (pipeline.py:~548), find_contacts→gather_signals (~930), gather_signals→enrich_places (~991),
  discover_whatsapp_number→outreach (~1931). Com broker fora terminam em quarentena da mãe "(retry failed: Reject)";
  no discover um erro inline interrompe o fan-out.
- Retry: `max_retries=3, default_retry_delay=60` nas 9 F1; publish_mar/reprocess_record 180s (default Celery).
  `failure_policy.py:~83` chama `task.retry(exc=exc)` sem countdown.
- Beat que loga e engole: collect_description_batches (~1526), prune_record_events (~2325), ta_keepalive
  (erros não-sessão ~2266), repush_pending_mar (~371, deixa propagar). submit_description_batch é MANUAL (fora).
- Bulk TA: `sweep_progress.py:~40-44` estados idle|running|done|stopped_needs_bootstrap. `produce_paginated`
  (domains/tripadvisor/atrativos.py:~1072) dá `break` em `should_halt_producer` e a task chama `mark_done`
  (pipeline.py:~729) → pausa/stop aparece como "done". PBE no bulk não seta estado terminal. Painel do bulk foi
  removido (c31bdd1); endpoint GET /api/v1/tripadvisor/sweep/progress segue.

## Decisões
- **Q2 — `sub_state_changed_at`**: coluna nova `RioRecord.sub_state_changed_at` (timestamptz, nullable, índice
  junto com sub_state se fizer sentido), migração Alembic nova (seguir a numeração em alembic/versions). Preenchida por
  UM listener ORM (`sqlalchemy.event.listens_for(RioRecord.sub_state, "set")`) que grava `now(UTC)` quando o valor
  muda — cobre todo escritor (advance_sub_state e os agentes que fazem `rio.sub_state = ...`). Backfill: não
  (NULL = desconhecido; o sweeper trata NULL como "antigo o bastante").
- **Q1 — sweeper no beat**: task nova `brave.redispatch_stalled_chain` (beat a cada 15 min, entrada na
  lane `default` do domínio places — `domains/places/controllers.py` beat_entries — para só existir com a lane ligada;
  se ficar mais simples como entrada de manutenção, então checar `source.default.enabled` no config efetivo).
  Seleciona `entity_type='attraction'` com `sub_state IN ('discovered','contacts_found','signals_gathered')`,
  `routing <> 'dlq'` (para signals_gathered; conferir as outras), e `sub_state_changed_at < now-30min OR NULL`,
  ordem mais antigo primeiro, `LIMIT 50`. Despacha a próxima task: discovered→find_contacts,
  contacts_found→gather_signals, signals_gathered→enrich_places. Pula a rodada inteira se
  `engine.get_mode != LIGADO` ou `run_real_externals` off ou lane `default` desligada. Log estruturado com contagem.
  Respeitar o depth gate se aplicável (o discover só dispara a cadeia fora de NASCENTE_RIO — conferir e espelhar).
- **Q3 — fallbacks inline**: nos 4 pontos, `try: X.delay(...) except Exception: logger.warning(...)` e segue — sem
  `.run()`. O registro fica no sub_state atual; o sweeper recupera. Fora: `.run()` em cli.py, routers/sweep.py,
  routers/runs.py (externals off) e `dlq._dispatch_or_inline`. Reescrever `_run_chain` em
  tests/integration/test_atrativos_chain_e2e.py para chamar as tasks em sequência (`.run` direto de cada uma, na ordem
  da cadeia, com `.delay` mockado para no-op) mantendo o que os 4 testes afirmam. Ajustar/remover
  `test_inline_run_failure_reraises_without_quarantine` só se deixar de fazer sentido (a política para `.run()` direto
  continua existindo — manter se ainda testa algo real).
- **Q4 — backoff**: em `failure_policy.py`, `task.retry(exc=exc, countdown=task.default_retry_delay * 2 **
  task.request.retries)`. Sem jitter.
- **Q5 — erros de beat**: helper pequeno (ex.: `brave/tasks/beat_health.py` ou no módulo de engine se couber sem
  violar D-18 — core não importa tasks) que grava `brave:beat:last_error:{task}` = JSON `{at, error_type}` com TTL 7
  dias e apaga no sucesso. Aplicar em collect_description_batches, prune_record_events, ta_keepalive (só erros
  não-sessão), repush_pending_mar e no sweeper novo. `GET /api/v1/engine/status` ganha `beat_errors: [{task, at,
  error_type}]`. Dashboard: `PainelMonitor` mostra um alerta por item (seguir o padrão visual dos alertas existentes;
  tipos em dashboard/lib/engine-api.ts + mock MSW + teste Vitest). Nunca gravar mensagem com PII — só tipo.
- **Q6 — bulk TA**: estado terminal novo `stopped` em sweep_progress (pausa/stop/PBE). `produce_paginated` precisa
  sinalizar se parou por halt (retorno ou flag) e a task só chama `mark_done` quando as páginas acabaram; no halt e no
  PBE do bulk chama o novo `stop(...)`. Sem UI.
- **Q7**: um PR, um commit por pendência (Q2+Q1 juntos: migração + listener + sweeper). Spec commitada.

## Verificação
- Suíte em `norteia_brave_test` + Redis db 15, `unset RUN_REAL_EXTERNALS`: **0 falhas** (baseline main: 1526 passed,
  1 skipped). Testes novos: listener grava sub_state_changed_at só quando muda; sweeper seleciona/ignora certo
  (idade, NULL, dlq, limite, modo≠LIGADO, externals off, lane off) e despacha a task certa; fallback sem .run
  (delay falha → registro fica, sem quarentena da mãe); backoff 60/120/240; beat_health grava/limpa e aparece no
  status; bulk halt → `stopped` e não `done`.
- `alembic upgrade head` na `norteia_brave_test` (e depois `downgrade -1`/`upgrade head` para checar o down).
- `cd dashboard && bun install && bun run test` verde + `bunx tsc --noEmit`.
- ruff sem achado novo.

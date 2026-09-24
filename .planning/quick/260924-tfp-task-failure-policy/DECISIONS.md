# Política de falha das tasks num módulo só — candidato #4 da revisão de arquitetura (24/09)

Decisões fechadas em grilling com o Leandro (Q1–Q13, todas conforme recomendado). Base: main 63d9aea.

## Fatos que motivam (verificados)
- **Quarentena pós-retry é código morto.** Celery 5.6.3 `celery/app/task.py:765-769`: com `retry(exc=exc)` e
  `retries > max_retries`, o Celery relança `exc` — nunca `MaxRetriesExceededError`. Os 9 `except
  self.MaxRetriesExceededError: quarantine` em `brave/tasks/pipeline.py` nunca rodam; a task só termina FAILURE.
  `request.called_directly` (inline `.run()`) também relança `exc` direto (`task.py:750-753`).
- `tests/unit/tasks/test_sweep_tripadvisor.py:159-167` mocka `retry` para levantar `MaxRetriesExceededError`
  (comportamento que o Celery não tem).
- **`describe_uf` perde gasto pago.** `ProviderBalanceError` re-levantado em `_describe_chunk._fetch` (~PL:1453) escapa
  do `asyncio.gather` (~PL:1463), pula os writes da fase 2 (~1468-1488) e o commit das linhas de gasto `rows.rows`
  (~1490-1497); `describe_uf` pega (~1619-1626), pausa e retorna. O ramo `SoftTimeLimitExceeded` (~1627-1640) salva
  `rows.rows`. A fase 2 `except Exception` (~1486) não tem `except ProviderBalanceError: raise` acima.
- Só `sweep_tripadvisor` (~962) e `enrich_places` (~1364) tratam `ProviderBalanceError`; as outras tasks F1 retentam 3×.
- `ComplianceError` (brave/shared/exceptions.py:54) nunca é pego: outreach/resume/discover_number caem no retry genérico.
- `quarantine_poison` (brave/core/quarantine.py:18) é importado como global em process_nascente/outreach/
  resume_conversation e localmente nas outras — testes que patcham `brave.core.quarantine.quarantine_poison` só
  alcançam as de import local.

## Escopo (Q1)
Extrair a política JÁ com o comportamento corrigido (não preservar o ramo morto).

## Forma e lugar (Q3, Q12)
Context manager em `brave/tasks/failure_policy.py` (camada de tasks — o kernel não importa tasks), usado dentro do
corpo de cada task. A task mantém a sessão e o `finally` dela; o `Retry` propaga até o `_producer_done`, que já o
ignora (`isinstance(sys.exc_info()[1], Retry)`). O CM chama `brave.core.quarantine.quarantine_poison` por atributo do
módulo (para os patches de teste pegarem em todas as tasks). Quarentena em sessão nova (`_get_session`) depois do
rollback da sessão da task, como hoje.

## Política (Q2, Q4, Q5, Q7, Q8, Q10, Q11)
Famílias:
- **F1 — retry → quarentena** (process_nascente, discover_atrativo, sweep_tripadvisor, find_contacts, gather_signals,
  enrich_places, outreach, resume_conversation, discover_whatsapp_number):
  - `PermanentError` → rollback + quarentena, sem retry (como hoje).
  - erro genérico → rollback + `self.retry(exc=exc, max_retries=N)`; ao esgotar (Celery relança `exc`, ou detectar
    `self.request.retries >= max_retries` antes de chamar retry) → quarentena **e relança** (task fica FAILURE) (Q2).
  - `ProviderBalanceError` → rollback + `pause_with_reason(rc, "provider_balance", exc.provider, action=...)`, sem
    retry, sem quarentena, `return` (SUCCESS + log warning) — uniforme nas 9 (Q4, Q5).
  - `ComplianceError` → rollback, sem retry, sem quarentena, log estruturado; task termina (Q7).
  - Payload da quarentena mantém os 3 formatos atuais, passados explícitos pela task: `nascente_id` (process_nascente),
    `{"uf": uf}` (producers), `{"rio_id": rio_id}` (cadeia) (Q11).
- **F2 — retry sem quarentena** (publish_mar, reprocess_record): mesma política sem quarentena; ao esgotar a task fica
  FAILURE; o log morto de "max retries" (WR-02, ~PL:465-472, ~501-507) sai (Q8). publish_mar tem o outbox
  (pushed_at NULL + repush 15 min).
- **`action` da pausa** (Q10): producers (discover_atrativo, sweep_tripadvisor) → `"sweep"`; `describe_uf` →
  `"describe"`; tasks de cadeia → `None`. Dashboard: `PainelTopbar.tsx` ~332-336 — com `action` nulo/ausente o
  Continuar só tira a pausa (`POST /api/v1/engine/mode` `{"mode":"LIGADO"}`, que limpa o pause_reason) sem iniciar
  run; `describe` → start describe; `sweep` → start sweep como hoje. Conferir o hook/mutation existente de modo no
  dashboard antes de criar um novo. Teste Vitest.
- Casos específicos de sweep_tripadvisor ficam na task: `SessionMissing`/`SessionExpired` (~PL:933-960) e o
  `retrying_counts` do bulk no finally.
- `describe_uf` (Q6): no `ProviderBalanceError` do chunk, salvar descrições já buscadas + commit das linhas de gasto
  (`rows.rows`) antes de pausar, como o ramo `SoftTimeLimitExceeded`; adicionar `except ProviderBalanceError: raise`
  antes do `except Exception` da fase 2. `describe_uf` não é F1 (não tem retry) — não precisa usar o CM se ficar
  forçado; o essencial é Q6.

## Fora (Q9) — viram follow-up na memória
(i) fallbacks inline `.run()` quando `.delay` falha; (ii) registros parados em `signals_gathered` após pausa no
enrich_places (nada re-despacha no Continuar); (iii) backoff exponencial (hoje fixo 60s); (iv) beat tasks que logam e
engolem (submit/collect batches, prune_record_events, ta_keepalive); (v) painel do bulk sem estado terminal no saldo.

## Testes (Q13)
Semântica real do Celery (`task.apply(args=..., retries=max_retries)` com corpo falhando — nada de mockar `retry`
para levantar MaxRetriesExceededError):
1. Esgotou → linha em `poison_quarantine` + exceção relançada — parametrizado sobre as 9 F1.
2. `ProviderBalanceError` em cada F1 → pausa com o `action` certo, sem retry, sem quarentena.
3. `ComplianceError` → sem retry, sem quarentena.
4. `describe_uf` saldo no meio do chunk → descrições e linhas de gasto salvas.
5. `Retry` continua sendo ignorado pelo `_producer_done`.
6. publish_mar/reprocess_record esgotam → FAILURE sem quarentena.
Reescrever `test_sweep_tripadvisor.py:159-167`. Atualizar `tests/unit/tasks/test_describe_uf_balance_pause.py`
(enrich_places passa a pausar com action None).
Gate: suíte em `norteia_brave_test` + Redis db 15, `unset RUN_REAL_EXTERNALS`; baseline = 8 falhas conhecidas
(engine_endpoints ×6, test_chain_stops_at_dlq_no_auto_gate, test_sc2_discovery_skips_absent_parent_destino) +
`cd dashboard && bun run test` verde.

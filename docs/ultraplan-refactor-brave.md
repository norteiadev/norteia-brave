# Refactor Brave — Alinhamento ao Spec-Driven Development

## Context

O `norteia-brave` (Pipeline Brave / Collector) evoluiu além do que o novo documento
`docs/brave-spec-driven-development.md` define como o produto-alvo. O `docs/FLUXO-BRAVE.md`
descreve o estado atual: um core entity-agnostic com scoring §7.6 de 5 critérios
(85/40/descarte), múltiplas lanes (Mtur, Desmembramento LLM, NotebookLM, Places, TripAdvisor,
Apify) e **duas frontends coexistentes** (10 rotas "dark" legadas + a casca clara `/painel`).

O spec quer um sistema mais enxuto e alinhado: fontes reduzidas a **mTur + TripAdvisor +
Entrada Manual (CRUD)** (Places vira só enriquecimento), **score binário** (≥80→Mar, senão
→DLQ), motor com estado **Pausado**, uma **única** tela de controle (`/painel` Kanban), tudo
configurável via painel, e uma **arquitetura limpa por Domain** (controllers/services/
repositories/models/dtos/exceptions). Este refactor reconcilia o código com o spec, mantendo
intactos o contrato Pact com a `norteia-api`, a postura de testes offline-por-padrão e o
subsistema WhatsApp (decisão explícita do usuário — apesar de não constar no spec).

Este plano será executado pelo **ultra code**.

### Decisões travadas (respostas do usuário)
1. **Re-arquitetura por Domain** completa (camadas explícitas por fonte).
2. **Score binário** ≥80→Mar, <80→DLQ. Remover faixa `descarte` e override `mar_ready`.
   Adicionar regra do spec: atrativo sem reviews OU review > 3 meses → DLQ.
3. **Remover:** Desmembramento (LLM), NotebookLM, Apify, as 10 rotas dark, e a tela mar-ready.
4. **Manter WhatsApp**, mas com **gatilho manual a partir do DLQ** (multi-seleção → coluna WhatsApp),
   não mais auto-gate; elegibilidade = sem horário+preço; ramo LLM-descoberta-de-número vs conversa
   conforme houver celular+DDD (ver seção WhatsApp — spec 2026-07-02).
5. **Config persistida no DB** + statuses extensíveis em código (sem editor runtime de state-machine).
6. **Places = enriquecimento** na promoção (mantém papel de descoberta de candidatos no track mTur);
   adicionar fonte **Manual CRUD**; adicionar estado **Pausado** ao motor.

---

## Passo 0 — Backup (obrigatório antes de qualquer edição)

Working tree está limpo, **sem remote configurado** e **sem tags**.
- Configurar remote: `git remote add origin git@github.com:norteiadev/norteia-brave.git`
  (confirmar se já não existe sob outro nome).
- Commit de eventuais alterações pendentes (hoje não há; comando defensivo).
- Criar tag anotada de backup do HEAD atual: `git tag -a backup/pre-spec-refactor-20260702 -m "Backup pré-refactor spec-driven"`.
- Push com retry/backoff: `git push -u origin main` + `git push origin backup/pre-spec-refactor-20260702`.
- Trabalhar em branch dedicada (ex.: `refactor/spec-driven-alignment`), não direto na `main`.

> Nota GSD: o `CLAUDE.md` exige rotear edições via comandos GSD. O ultra code deve abrir a fase
> correspondente (`/gsd-execute-phase`) por fase deste plano, ou o usuário autoriza o bypass.

---

## Backend — Re-arquitetura por Domain

Kernel compartilhado (source-agnostic) permanece em `brave/core/` + novo `brave/shared/`;
cada fonte externa vira um **Domain** em `brave/domains/<fonte>/`. Regra de dependência
generalizada (D-18): `brave.core`/`brave.shared` **nunca** importam `brave.domains`/`brave.tasks`;
domains importam kernel + clients, **nunca** uns aos outros.

### Estrutura-alvo
```
brave/
├── config/{settings.py (defaults bootstrap), runtime.py (NOVO: overlay DB→AppConfig)}
├── shared/{exceptions.py (hierarquia central), dtos.py (MarPushPayload/FlatProvenance),
│           whatsapp/{agent.py, conversation.py}}
├── core/
│   ├── models.py (+ ConfigSetting; remove coluna mar_ready)
│   ├── repositories/{base.py (Protocols), sqlalchemy.py (impls — extração pura)}
│   ├── nascente/ rio/ mar/ dlq/ quarantine.py engine.py score/
│   ├── atrativos/state_machine.py (movido de lanes/atrativos)
│   └── promote/  → DELETADO (mar_ready removido)
├── clients/ (base.py sem Apify/NotebookLM; Real+Null por cliente)
├── compliance/ observability/ (inalterados)
├── domains/
│   ├── base.py (Protocol SourceDomain: discover/enrich/score_input + registry)
│   ├── mtur/{controllers,services,repositories,models,dtos,exceptions}.py + tests/
│   │   (MturSeedIngest + Discovery/ContactFinder/Signal agents = track Places default)
│   ├── tripadvisor/{controllers,services,repositories,client,models,dtos,exceptions}.py + tests/
│   └── manual/{controllers,services,repositories,models,dtos,exceptions}.py + tests/
├── api/ (routers finos delegando a domains; + config.py NOVO)
└── tasks/{celery_app.py (fila única, inalterada), beat_schedule.py (por fontes habilitadas),
          pipeline.py (tasks resolvem Domain via registry)}
```

### Camadas
- **Repositories (kernel):** `brave/core/repositories/` — `NascenteRepository`, `RioRepository`,
  `MarRepository`, `DlqRepository` (Protocol + 1 impl SQLAlchemy). Extração *pura* das queries
  hoje inline (`rio/routing.py`, `mar/service.py`, `engine.py`, `rio/dedup.py`) — sem mudança de
  comportamento. Sessão continua aberta/comitada pela task Celery / `get_db` (sem UoW, sem async repo — KISS/YAGNI).
- **Repositories (por domain):** estado específico não-medalhão — `TripAdvisorRepository`
  (sweep_progress + sessão Redis + caches geo/ibge), `MturRepository` (`build_destino_rio_map(uf)`,
  path do CSV seed), `ManualRepository` (fachada fina sobre Nascente/Rio).
- **DTOs:** central `brave/shared/dtos.py` congela o shape Pact (`MarPushPayload`, `FlatProvenance`);
  mover `_build_push_payload` (tasks/pipeline.py) → `mar/service.build_push_payload(...) → MarPushPayload`
  (`.model_dump()` byte-idêntico → Pact permanece verde). DTOs de payload por fonte em `domains/<f>/dtos.py`.
  `ScoreInput/ScoreBreakdown/ScoreResult` ficam no kernel.
- **Exceptions:** central `brave/shared/exceptions.py` — `BraveError` base + `TransientError`,
  `PermanentError`, `ComplianceError`, `CostGuardError`, `SourceError`/`SourceSessionError`.
  Domains subclassam (ex.: `SessionMissingError`, `SessionExpiredError`, `MturSeedMissing`,
  `ManualValidationError`). **Re-export shims** nos módulos antigos (`brave.tasks.pipeline`, etc.)
  para não quebrar imports durante a migração.

### Contrato de fonte plugável (`brave/domains/base.py`)
Protocol `SourceDomain` com `name`, `produces`, `discover(uf, run_rio)`, `enrich(rio)→dict`,
`score_input(payload)→ScoreInput`. Registry em `domains/__init__.py` (`get_domain`, `enabled_sources`).
Adicionar nova fonte = novo pacote `domains/<x>/` + 1 linha no registry + 1 row de config habilitando.
`engine.py`/`pipeline.py`/`beat_schedule.py` iteram o registry — nunca nomeiam fonte específica.
`_VALID_SOURCES` hardcoded em `engine.py` passa a validar contra "registrada + habilitada".
**Manter fonte-única por run** (casa com a chave Redis `brave:engine:source`); multi-fonte é YAGNI (adiar).

---

## Score binário (≥80→Mar, <80→DLQ)

- `brave/core/score/schemas.py`: `ScoreResult.routing: Literal["mar","dlq"]` (remove `descarte`).
- `brave/core/score/engine.py`: `routing = "mar" if score >= config.threshold_mar else "dlq"`.
  `ScoreConfig.threshold_mar = 80.0`; deletar `threshold_dlq`, `mar_ready_atualidade_bar`, `mar_ready_corrob_bar`.
- `brave/core/rio/routing.py::route_by_score`: manter `compute_score` (produtor de 5 critérios
  intacto); `dlq_reason` refletir threshold 80; **deletar bloco `mar_ready` (linhas ~81-92)**.
- `brave/core/promote/service.py`: **DELETAR** (override mar_ready removido; promoção borderline
  passa a fluir só por `validate_and_promote_rio`).
- **Regra sem-reviews / >3 meses → DLQ** em dois pontos:
  1. **`domains/mtur/services.py::SignalAgent`** (ex-signal_agent): após `place_details`, se
     `attraction` e (sem reviews OU review mais recente > **90 dias**) → `routing="dlq"`,
     `dlq_reason="no_recent_reviews"`. **NÃO** roteia automaticamente para o gate WhatsApp — o
     WhatsApp agora é iniciado manualmente a partir do DLQ (ver seção WhatsApp). **Remover todo
     código Apify** (`_compute_corroboracao` via IG, ctor arg `apify_client`).
  2. **`brave/core/mar/service.py::promote_to_mar`**: assert de recência como backstop (defesa em
     profundidade), pois atrativos TripAdvisor não passam pelo SignalAgent.
- **Contato celular+DDD:** o enriquecimento (Places details e TripAdvisor) passa a capturar
  **celular + DDD** como "possível número de WhatsApp", além de email/site/instagram. Normalizar e
  guardar no card do atrativo (`normalized["contact"]["whatsapp_candidate"]`), respeitando a
  minimização LGPD (nunca renderizar telefone cru no Kanban — manter a projeção mascarada existente).
- **Interação com WhatsApp:** `<80 → dlq`. O WhatsApp **não** é mais um sub_state automático; é
  disparado por ação manual do operador sobre cards em DLQ. A confirmação do dono ainda injeta
  `validacao_humana_value=100` → `reprocess_record` → ≥80 → Mar (mecânica `validate_and_promote_rio`
  preservada). Ver a seção **WhatsApp** abaixo.

---

## Motor Pausado (Ligado / Pausado / Desligado)

`brave/core/engine.py`: manter runtime `idle|running|stopping` (contrato de drain intacto) e
adicionar **modo do operador** em `brave:engine:mode` (também persistido em `config_settings`):
- `LIGADO` → pode `start_run`; descoberta + promoção automática despacham.
- `PAUSADO` → orquestrador quebra o loop (sem novos UFs, sem push automático) **mas** libera o
  edit-lock do Kanban. Promoção/edição *manual* pelo steward continua permitida.
- `DESLIGADO` → `mark_idle` + `set_enabled(False)`.
- Novos helpers: `set_mode`, `get_mode`, `is_editing_unlocked` (True se PAUSADO ou DESLIGADO);
  `get_status()` inclui `mode` + `editing_unlocked`; `engine_sweep_run` quebra quando `get_mode != LIGADO`.

**Edit-lock (API):** dependência `require_editing_unlocked` (HTTP **423** quando LIGADO) aplicada às
mutações de card em `brave/api/routers/cms.py` (`edit_destino`, `edit_atrativo`, `transition_destino`,
`advance_atrativo_state`) e nas mutações de `domains/manual/controllers.py`. Leitura e approve/reject
do gate não são afetados.

---

## WhatsApp (fluxo revisado — spec 2026-07-02)

O WhatsApp deixa de ser um **gate automático** disparado pelo SignalAgent no borderline e passa a
ser **iniciado manualmente pelo operador a partir do DLQ**. O subsistema técnico (LangGraph
`shared/whatsapp/agent.py`, Twilio, consent, ramp, quality-rating, transcrições) é **mantido**;
muda o **gatilho** e a **origem** dos atrativos.

**Fluxo:**
1. Atrativos caem em **DLQ** (score <80, ou sem reviews / review >90d). Nada vai ao WhatsApp
   automaticamente.
2. No Kanban, o operador **seleciona múltiplos** cards de atrativo em DLQ e os **move para a coluna
   WhatsApp** (ação em lote).
3. **Elegibilidade (validada no servidor):** só atrativos **sem horário de funcionamento E sem
   preço** podem ser movidos para WhatsApp. Atrativos que já têm horário+preço não são elegíveis
   (o backend rejeita com 422; o front desabilita a seleção).
4. Ao mover para WhatsApp, o backend ramifica por atrativo:
   - **Sem celular+DDD cadastrado** → enfileira uma task de **reprocessamento LLM** para *descobrir*
     um número de WhatsApp (busca em sites de turismo, configurável no painel). Se achar → popula
     `whatsapp_candidate` e segue para a conversa; se não achar → volta ao DLQ (ou permanece em
     WhatsApp com motivo `no_contact_found`).
   - **Com celular+DDD cadastrado** → inicia o **processo de conversa** (`outreach_task` /
     LangGraph) para coletar existência/funcionamento/horário/preço, sob compliance BSP
     (template, janela 24h, opt-out, consentimento consultado antes de cada envio).
5. Confirmação do dono → boost `validacao_humana_value=100` → `reprocess_record` → ≥80 → Mar.

**Sub-estados (`RioRecord.sub_state`):** reaproveitar os existentes com semântica revisada —
`aguardando_consulta_whatsapp` = "na coluna WhatsApp, aguardando número/consulta";
`whatsapp_in_progress` = "conversa ativa". Adicionar transição de saída para `dlq` quando não há
contato. O `/gate` antigo (fila approve/reject) é **substituído** por esta ação de mover-do-DLQ
(multi-seleção) — sua lógica de aprovação vira a própria escolha do operador no Kanban.

**Backend a construir/ajustar:**
- Endpoint em lote (`domains/manual` ou router dedicado, steward-auth): `POST` mover atrativos
  DLQ→WhatsApp — valida elegibilidade (sem horário+preço), seta `sub_state`, e enfileira a task
  correta por atrativo (LLM-descoberta-de-número vs `outreach_task`).
- Task de descoberta de número via LLM (nova, ou ramo do `reprocess_record`/`resume_conversation`).
- SignalAgent/ContactFinder capturam `whatsapp_candidate` (celular+DDD) no enriquecimento.
- `state_machine.advance_sub_state` ganha a aresta `dlq → aguardando_consulta_whatsapp` (manual) e
  `aguardando_consulta_whatsapp → dlq` (sem contato).

---

## Config persistida no DB

- **Alembic 0009** — tabela `config_settings` (`key` PK `String(128)`, `value` JSON `{"v": ...}`,
  `updated_at`, `updated_by`). Um key/value (KISS): `score.threshold_mar`, `source.<f>.enabled`,
  `engine.mode`, pesos, etc.
- `brave/config/runtime.py::load_effective_config(session)→AppConfig`: bootstrap pydantic (env) +
  overlay das rows DB via `model_copy(update=...)`; cache em Redis `brave:config:snapshot`
  (bust-on-write). Call-sites que hoje fazem `ScoreConfig()`/`AppConfig()` passam a usar isso.
- **`brave/api/routers/config.py` (NOVO):** `GET /api/v1/config` (Bearer) → snapshot efetivo;
  `PATCH /api/v1/config` (steward) → upsert rows, audit-log, bust cache; valida soma de pesos=100
  e thresholds ∈ [0,100]. Modo do motor pode ficar no router `engine` espelhando em `config_settings`.
- `beat_schedule.py` monta sweeps só para `enabled_sources(config)`; `/engine/start` valida `source`
  contra habilitadas.

---

## Remoções

- **Backend:** deletar `lanes/destinos/desmembramento.py`, `lanes/destinos/notebooklm.py`,
  `clients/{apify,null_apify,notebooklm,null_notebooklm}.py`; remover `ApifyClientProtocol` e
  `NotebookLMClientProtocol` de `clients/base.py`; remover uso Apify do SignalAgent; `sweep_uf` para
  de chamar Desmembramento. **Sem migração** (essas features não têm tabelas próprias).
- **Frontend (`dashboard/`):** remover a tela **mar-ready** inteira (`app/mar-ready/`,
  `components/mar-ready/`, `lib/mar-ready-api.ts`, mocks/tests) e o override backend
  (`api/routers/atrativos.py` promote-override queue).

---

## Frontend — Painel único (Kanban)

`/painel` (casca clara, Shadcn UI + Tailwind v4 já em uso) vira **a única tela de controle**.
- **Rotas:** manter `/login` + `/painel`; redirecionar `/` → `/painel`; **remover** as páginas
  standalone dark (`app/{processo,monitor,cost,funnels,dlq,gate,conversations,mar-ready,destinos,
  atrativos}/`) e o hub. As funcionalidades ainda necessárias migram para **views do `/painel`**.
- **Views do `/painel`** (hoje 6): manter Kanban, Duplicados, Mapeamento, Varreduras, Conversas
  (WhatsApp, mantido), Custo; **adicionar** views: **DLQ/Revisão**, **Monitor/Funis**, **Logs**
  (PainelLogs já existe), **Config** (fontes on/off, thresholds/pesos, modo do motor). Registrar em
  `components/painel/nav.ts`. **Nota:** a antiga tela `/gate` (approve/reject) **não** vira uma view
  separada — sua função passa a ser a ação de mover-do-DLQ para a coluna WhatsApp (abaixo).
- **Kanban:** remover a coluna `descarte` (routing removido); manter Nascente/Rio/WhatsApp/Mar/DLQ/
  Falha. `COLUMN_DEFS` em `lib/painel-data.ts` derivável de config (base para "novos statuses" sem
  editor runtime). Manter windowing (100 +50/scroll) e allow-list de drag (`lib/painel-actions.ts`),
  ajustando as arestas ao novo routing (sem `→descarte`).
- **Coluna WhatsApp (fluxo manual):** a coluna WhatsApp é alimentada por **multi-seleção de cards de
  atrativo em DLQ** + botão de lote **"Mover para WhatsApp"** (não é drag individual). O front
  desabilita a seleção de atrativos inelegíveis (que já têm horário **e** preço) e trata o 422 do
  backend com toast. A ação chama o endpoint em lote DLQ→WhatsApp; o feedback visual mostra o ramo
  seguido (busca de número via LLM vs conversa iniciada). Cards WhatsApp continuam com telefone
  mascarado (LGPD). As transcrições ficam na view **Conversas** (PainelConversas, já existente).
- **Edit-lock:** cards editáveis só quando motor **Pausado/Desligado**; bloquear no **Ligado**
  (tratar 423 do backend → toast + revert). Wire ao `engine-api` (status inclui `mode`/`editing_unlocked`).
- **Motor:** topbar já liga/desliga; adicionar controle **Pausar** (tri-estado).
- **API client / mocks / tests:** remover `lib/{gate,conversations,mar-ready}-api.ts` como rotas
  standalone só se não reaproveitados pelas views do painel — na prática **reaproveitar**
  `gate-api`/`conversations-api` dentro das novas views. Atualizar `mocks/handlers/*` e os testes
  Vitest+MSW; manter postura offline (`onUnhandledRequest:"error"`).

---

## Sequência de migração (incremental, suíte verde a cada fase)

- **A — Scaffolding (sem mudança de comportamento):** `shared/{exceptions,dtos}.py` + shims;
  `build_push_payload → MarPushPayload` (Pact verde); `core/repositories/` extraindo queries inline.
- **B — Score binário:** editar `score/engine.py`+`schemas.py`+`rio/routing.py` (remove mar_ready),
  deletar `core/promote/`. **Alembic 0008:** backfill `UPDATE rio_records SET routing='dlq' WHERE
  routing='descarte'` (routing é `String(32)`, **não** enum PG — não há `ALTER TYPE`) + `DROP COLUMN
  mar_ready` (+ índice). Reescrever/retirar testes de descarte e mar_ready.
- **C — Motor Pausado:** helpers de modo + break do orquestrador + `require_editing_unlocked` no cms.
- **D — Config-in-DB:** Alembic 0009 (`config_settings`), `runtime.load_effective_config`,
  `api/routers/config.py`, beat/engine por fontes habilitadas, seed idempotente.
- **E — Remoções:** deletar Desmembramento/NotebookLM/Apify (backend) e mar-ready (front+back).
- **F — Regra sem-reviews/>3mo + fluxo WhatsApp manual:** SignalAgent envia sem-reviews/>90d → DLQ
  (sem auto-gate) + backstop em `promote_to_mar`; capturar `whatsapp_candidate` (celular+DDD) no
  enriquecimento; endpoint em lote DLQ→WhatsApp com validação de elegibilidade (sem horário+preço) +
  ramo LLM-descoberta-de-número vs `outreach_task`; arestas de sub_state manuais. Substitui a lógica
  do `/gate` antigo. (Sem migração nova — usa colunas/sub_state existentes.)
- **G — Domainização (moves mecânicos):** criar `domains/{mtur,tripadvisor,manual}/`; mover arquivos
  (§estrutura) com update de imports; `state_machine`→`core/atrativos/`, `whatsapp_agent`→
  `shared/whatsapp/`; `domains/base.py` + registry; refatorar `tasks/pipeline.py` para resolver
  Domain via registry. **Manual domain**: usa `source="manual"` com `origem_value`/
  `validacao_humana_value=100` no payload → **provável zero migração** (avaliar 0010 só se precisar de colunas).
- **H — Frontend:** consolidação em `/painel` (views novas, remoção de rotas dark + mar-ready),
  edit-lock, atualização de mocks/tests.
- **reset skill:** o script trunca por reflexão (exceto `alembic_version`), então `config_settings`
  é limpa no reset — documentar re-seed pós-reset (ou `--keep config_settings`). Tabelas de
  Consent/Conversation permanecem.

---

## Arquivos críticos
- `brave/core/rio/routing.py` — routing binário; remover bloco mar_ready.
- `brave/core/score/engine.py` + `score/schemas.py` — Literal `["mar","dlq"]`, threshold 80.
- `brave/core/engine.py` — estado Pausado + helpers de edit-lock.
- `brave/config/settings.py` (+ novo `config/runtime.py`) — thresholds; overlay DB.
- `brave/tasks/pipeline.py` — tasks resolvendo Domain via registry; mover push payload.
- `brave/core/models.py` — `ConfigSetting`; remover coluna `mar_ready`.
- `brave/api/routers/cms.py` (+ novo `config.py`, `domains/*/controllers.py`) — edit-lock, CRUD manual.
- `dashboard/app/painel/*`, `components/painel/nav.ts`, `lib/painel-data.ts`, `lib/painel-actions.ts`.
- `alembic/versions/000{8,9}_*.py` — backfill descarte + drop mar_ready; `config_settings`.

## Não quebrar (guardrails)
- `tests/contract/test_pact_norteia_api.py` — **verde intacto** (payload byte-idêntico; paths
  `/api/internal/territorial/{destinations,attractions}` inalterados).
- Fila Celery única (`celery`), sem task_routes.
- Postura offline (`run_real_externals=False` default; clients injetáveis; CI sem chaves).
- Regra de import kernel↔domains (adaptar `tests/unit/test_no_test_imports_in_brave.py`:
  core/shared nunca importam domains/tasks; domains não importam uns aos outros).
- WhatsApp preservado (conversa LangGraph/consent/ramp/quality-rating/Twilio + sub_states); muda só
  o gatilho: iniciado manualmente do DLQ (multi-seleção → coluna WhatsApp), não mais auto-gate.

---

## Verificação (end-to-end)

1. **Backend offline:** `pytest -q` 100% verde (docker-compose Postgres+Redis). Cobrir: routing
   binário (≥80/<80, sem descarte), regra sem-reviews/>3mo→DLQ→gate, motor Pausado + 423 no edit-lock,
   config-in-DB (PATCH → overlay efetivo), registry de domains, migrações 0008/0009. Pact intacto.
2. **Motor / pipeline (com `RUN_REAL_EXTERNALS=0`):** ligar sweep destinos (mTur) → Nascente→Rio→Mar
   → push mockado; ligar sweep atrativos → Places enrich → score → Mar/DLQ/gate. Pausar → confirmar
   loop drena e Kanban destrava; Ligar → confirmar edição bloqueada (423).
3. **Manual CRUD:** criar destino + atrativo via `domains/manual/controllers.py` → aparecem no Kanban;
   editar campos (motor Pausado).
3b. **WhatsApp (fluxo manual):** atrativo sem reviews → DLQ. Multi-selecionar em DLQ e mover para
   WhatsApp: (a) inelegível com horário+preço → 422; (b) elegível sem celular+DDD → task LLM de
   descoberta de número; (c) elegível com celular+DDD → conversa inicia (mock Twilio); confirmação
   do dono → validacao_humana=100 → re-score ≥80 → Mar. Telefone mascarado nas views.
4. **Frontend:** `bun run test` (Vitest+MSW) verde; `/` redireciona a `/painel`; rotas dark e
   mar-ready removidas (404); views novas (DLQ, Monitor/Funis, Gate, Config) funcionam; drag respeita
   allow-list e edit-lock.
5. **Reset:** rodar skill `reset-brave-db` → base zerada + `config_settings` re-seedada → motor ocioso.
6. **Lint/tipos:** `ruff` + `mypy`/`pyright` no kernel e nos domains.

## Ambiguidades resolvidas (defaults adotados)
- "drop descarte enum" → é `String(32)`; backfill `descarte→dlq`, sem `ALTER TYPE`.
- Fonte por run → única (adiar multi).
- Pausado → bloqueia promoção *automática*, permite promover/editar *manual* (é o propósito do unlock).
- "3 meses" → constante explícita de **90 dias** para o gate DLQ (independente das bandas de atualidade 30/180d).
- Places → papel duplo: descoberta de candidatos no track mTur **e** enriquecimento na promoção.

# Apagar o que não passa no teste de deleção — candidato #6 da revisão de arquitetura (24/09)

Decisões fechadas em grilling com o Leandro (Q1–Q11, todas conforme recomendado). Base: main 10e79fd.
Mudança mecânica: NENHUM comportamento muda.

## Fatos (mapa de 24/09, HEAD 10e79fd)
- `brave/lanes/tripadvisor/`: `__init__.py` vazio + 10 shims de 16 linhas (`atrativos, client, destinos, geo, ibge,
  schemas, scoring, session, sweep_progress, uf_names`), cada um `sys.modules[__name__] = brave.domains.tripadvisor.<mod>`.
  Importadores: brave/ 11 linhas em 4 arquivos (`clients/factory.py:187`, `tasks/pipeline.py`, `api/routers/engine.py`,
  `api/routers/tripadvisor_session.py`); scripts/ 2 (`spike_nominatim_geo.py`, `single_attraction/run_single_attraction.py`);
  tests/ 171 imports em 19 arquivos + 106 strings de patch/setattr em 6 arquivos.
- `brave/domains/tripadvisor/{services,models,dtos,exceptions,repositories}.py`: 0 importadores.
- `brave/lanes/base.py` `LaneProtocol`: 0 implementações; só `tests/unit/test_scaffold_smoke.py:~258-259` importa.
- `brave/core/rio/routing.py:~411` `reprocess_record_inline`: só `tests/unit/test_routing.py:~131-174`.
- `brave/lanes/atrativos/` (10 arquivos, ~3550 linhas): importado por `tasks/pipeline.py` (lazy), `domains/places/controllers.py:~66`,
  `api/routers/cms.py:~47`; scripts 22 linhas em 12 arquivos; tests 81 imports + 82 strings de patch em ~20 arquivos.
  Nada persistido embute o caminho (task names são `name="brave.x"` explícitos; beat usa task names).
- `brave/domains/places/` já existe (`__init__.py` + `controllers.py`) e a docstring diz que é dono da lane.
- `tests/unit/test_domain_boundaries.py`: CHECK A (core/shared não importam domains/tasks/lanes), CHECK B (domínio não
  importa domínio irmão). Nada checa clients→domains.

## Decisões
- **Q1** Um PR, um commit por item: (1) shims TA; (2) scaffold de domains/tripadvisor; (3) `reprocess_record_inline`;
  (4) pass-throughs do pipeline; (5) mover `lanes/atrativos` → `domains/places` e apagar `brave/lanes/` (+ `LaneProtocol`).
- **Q2** `brave/lanes/atrativos/*` → `brave/domains/places/` via `git mv` (preserva histórico). Não criar `domains/atrativos`.
  O `__init__.py` de `domains/places` continua só docstring (imports baratos); `controllers.py` passa a importar do próprio pacote.
- **Q3** `clients/factory.py` importa `brave.domains.tripadvisor.client` diretamente, com comentário explicando a
  dependência (o cliente usa `session`/`geo` do domínio). Não mover o cliente.
- **Q4** Novo CHECK D em `test_domain_boundaries.py`: `brave/clients/**` não importa `brave.domains`, allowlist com
  exatamente `brave/clients/factory.py` → `brave.domains.tripadvisor.client`. Sem check para `brave.lanes` (o pacote some).
- **Q5** Apagar os 5 arquivos de scaffold de `domains/tripadvisor`.
- **Q6** Apagar `reprocess_record_inline` e a menção na docstring do módulo. Antes: conferir se o comportamento dos 2
  testes já está coberto por testes de `reprocess_record`; se não, reapontar os testes para `reprocess_record` em vez de apagar.
- **Q7** Pipeline: manter `_load_config` (seam do conftest) e `_using`; inlinar `_enrich_clients` (2 chamadores); tirar o
  re-export `quarantine_poison` e reapontar `tests/integration/test_celery_tasks.py` para `brave.core.quarantine`.
  ATENÇÃO: `failure_policy.py` chama `brave.core.quarantine.quarantine_poison` por atributo — não depende do re-export.
- **Q8** Scripts: reescrever imports (inclusive `scripts/poc/*`), não apagar.
- **Q9** Testes: `git mv tests/unit/lanes/tripadvisor/*` → `tests/unit/domains/tripadvisor/` e
  `tests/unit/lanes/atrativos/*` → `tests/unit/domains/places/` (conferir se já existem arquivos com o mesmo nome no
  destino — domínios têm `tests/` co-localizados em `brave/domains/<x>/tests/`; não misturar com esses). `tests/unit/lanes/` some.
- **Q10** Atualizar docstrings/comentários em brave/, tests/, scripts/ e `CLAUDE.md`/`CONTEXT.md` se citarem os caminhos.
  NÃO tocar `.planning/` nem `docs/` (histórico).

## Verificação (Q11)
1. `grep -rn "brave.lanes\|brave/lanes" brave scripts tests CLAUDE.md CONTEXT.md` → zero.
2. `python -m py_compile` em todo script tocado (não executar: POCs chamam APIs).
3. CHECK D passa.
4. Suíte em `norteia_brave_test` + Redis db 15, `unset RUN_REAL_EXTERNALS`: exatamente as 8 falhas conhecidas
   (engine_endpoints ×6, test_chain_stops_at_dlq_no_auto_gate, test_sc2_discovery_skips_absent_parent_destino); ruff sem achado novo.
5. Revisão independente focada em patch strings que mudaram de alvo em silêncio (um `patch("brave.lanes.tripadvisor.X.y")`
   que antes caía no módulo real pelo alias precisa virar `patch("brave.domains.tripadvisor.X.y")` — mesmo objeto).
6. Sweep curto ao vivo sem custo na livetest (SE, max_atrativos_per_uf=5) → `concluido`.

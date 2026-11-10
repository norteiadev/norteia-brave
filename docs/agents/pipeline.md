# Pipeline de agentes (norteia-brave)

Fluxo que a skill `work-task` segue numa tarefa deste repo, com Orca ou com subagentes. A sessão coordenadora cria o worktree, dispara cada etapa, roda a suíte completa e fala com o usuário; os agentes trabalham dentro do worktree. O checkout principal está montado em `/app` na stack Docker e roda o worker: todo trabalho acontece no worktree.

## Etapas

1. **Plano (G1).** A coordenadora escreve o plano: escopo, arquivos, critérios de aceite, testes e se a tarefa toca o coletor (`brave/`), o Painel (`dashboard/`) ou os dois. Pronto quando o usuário aprova no G1.
2. **Escritor.** `backend-python` ou `frontend-nextjs` implementa em TDD no worktree e commita. Pronto quando os testes que tocou passam, ruff (ou lint e typecheck do Painel) está limpo nos arquivos alterados e há commit.
3. **review-code.** Revisa o diff desde o SHA base contra a issue e os padrões do repo, só lendo. Pronto quando devolve os achados com severidade, `arquivo:linha` e evidência.
4. **Correções.** O escritor corrige os achados aceitos pela coordenadora. Pronto quando nenhum achado bloqueante fica aberto e a verificação do escritor passa de novo.
5. **QA.** Ainda sem agente de QA: o Painel não tem Playwright. A verificação da coordenadora (suíte offline + Painel) faz o papel de QA.
6. **Reporte no tracker (G3).** Resumo da verificação como preview em arquivo; `report-to-tracker` (dry-run) entra quando houver relatório de QA. Pronto quando o usuário leu a preview.
7. **Pergunta do /verify (G4).** O fluxo termina com "PERGUNTA AO USUÁRIO: rodar /verify em <rota>?".

Mudança no contrato de ingestão com a norteia-api (`POST /api/internal/territorial/...`) vira tarefa cruzada: o lado da API escreve o contrato antes, como `.hurl` em `tests/http/` da norteia-api.

## Agentes

- Plano, suíte completa, gates e conversa com o usuário: sessão coordenadora.
- Escritor e correções no coletor (`brave/`, `alembic/`, `scripts/`): `backend-python`.
- Escritor e correções no Painel (`dashboard/`): `frontend-nextjs`; diff de UI/UX passa pela skill `ui-ux-pro-max` do repo antes da validação do usuário.
- Revisão: `review-code`, só leitura, no mesmo worktree.

## Verificação

Testes rodam 100% offline: base de teste, Redis db 15 e `RUN_REAL_EXTERNALS` removido do ambiente. Use os binários do venv; `uv run` reescreve o `uv.lock`. Worktree sem `.venv` próprio usa o do checkout principal: troque `.venv/bin/` por `PYTHONPATH=$PWD ~/Projects/norteia/norteia-brave/.venv/bin/`.

**Escritor, coletor** (só o que tocou):

```bash
env -u RUN_REAL_EXTERNALS \
  BRAVE_DB_URL=postgresql+psycopg://brave:brave@localhost:5432/norteia_brave_test \
  BRAVE_DB_REDIS_URL=redis://localhost:6379/15 \
  .venv/bin/pytest <arquivos de teste tocados> -q
.venv/bin/ruff check <arquivos alterados>
.venv/bin/ruff format --check <arquivos alterados>
```

Sem `BRAVE_DB_URL` os testes de integração dão skip em silêncio; o `conftest` aborta se a URL apontar para a base viva `norteia_brave`.

**Escritor, Painel** (Node 22 no PATH: `export PATH="$HOME/.nvm/versions/node/v22.22.3/bin:$PATH"`; worktree novo: `bun install` em `dashboard/`):

```bash
cd dashboard && bunx vitest run <arquivo.test.tsx> && bunx eslint <arquivos alterados> && bun run typecheck
```

**Coordenadora** (uma vez, no fim do ciclo):

```bash
make pipeline/test          # suíte offline inteira (mesmo ambiente acima)
make lint                   # ruff check + format --check em brave/ e tests/
cd dashboard && bun run test && bun run lint && bun run typecheck
```

Os alvos do `Makefile` chamam `.venv/bin/`; num worktree sem `.venv`, rode o comando equivalente com o prefixo do checkout principal.

## Gates

- **G1** plano e escopo antes do escritor: `plannotator annotate plan.md --gate --tailscale`; sem plannotator, o formato do plan mode do Claude.
- **G2** sem specs `@generates` neste repo; o equivalente é qualquer chamada externa paga, que exige ok do usuário.
- **G3** publicar no tracker: durante a validação dos agentes o reporte fica em arquivo (dry-run).
- **G4** a pergunta do `/verify`, sempre ao usuário.
- Sweep, sync de UF nova ou script com `--apply` só com autorização explícita do usuário.
- Push, PR e merge ficam com o usuário, fora do fluxo (base `main`, merge commit). Depois do merge o worker roda código velho até reiniciar: a coordenadora pergunta ao usuário se pode reiniciar api/worker/beat.

## Tracker

Issues no GitHub (`norteiadev/norteia-brave`); como ler, comentar e fechar: `docs/agents/issue-tracker.md`. A Norteia vai migrar para o Jira; trocar de tracker é editar esse arquivo. O reporte de QA passa pela skill `report-to-tracker` em dry-run.

## Regras do repo

Fonte: `CLAUDE.md` (Constraints, Conventions, Architecture), `GLOSSARY.md` e `docs/RUNBOOK-EXECUCAO.md`.

- Testes offline: nenhum teste chama Places, OTA, Apify, LLM ou a norteia-api; real é opt-in. Clientes reais e `null_*` saem só de `brave/clients/factory.py`.
- Fronteiras de import: `brave/core` e `brave/shared` nunca importam `brave.domains`/`brave.tasks`; domínio não importa domínio irmão. Travadas por `tests/unit/test_domain_boundaries.py` e `test_no_test_imports_in_brave.py`.
- Caminho único de publicação: entrada no Mar e envio à API só por `brave/core/mar/publication.py` (`promote`, `publish`, `republish_pending`); termos em `GLOSSARY.md`.
- Mar automático com score ≥ 70; abaixo vai para a DLQ e só entra por Promoção humana.
- Custo: cost guard em `brave/observability/` pausa o motor no teto diário; referências na seção 8 do `RUNBOOK-EXECUCAO.md`.
- Celery numa fila só (`celery`); beat sem `options.queue`.
- Guardar estado é commit WIP; `git stash` é compartilhado entre worktrees.

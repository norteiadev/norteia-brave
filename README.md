# norteia-brave

**Pipeline Brave** — sistema de coleta, processamento e qualidade dos dados territoriais da Norteia
(Nascente → Rio → Mar). Serviço Python contínuo que coleta destinos e atrativos turísticos de todo o
Brasil, pontua confiabilidade (conforme o doc de MVP), e publica apenas itens **Mar** (canônicos) na
`norteia-api`.

> Repo irmão da `norteia-api` (Laravel, consumidor de Mar) e da `norteia-frontend` (Next.js).
> Plano de referência: `norteia-api` → `.claude/plans/fancy-pondering-lovelace.md`.

## Componentes (ver plano)

- **Núcleo Brave** (entity-agnostic): Nascente (ingest bruto) → Rio (dedup/normalização/score) →
  Mar (≥85% → push) / DLQ (51–84.9% revisão humana) / descarte (≤50%).
- **Lanes de coleta**: Destinos (Mtur + NotebookLM + desmembramento LLM+humano) e Atrativos
  (Google Places + sinais + outreach WhatsApp com gate humano).
- **Dashboard** (Next.js): monitor Brave + fila DLQ + gate WhatsApp + funis + custo.

## Stack

Python · FastAPI · Celery/Redis · LangGraph · PostgreSQL · DeepSeek (OpenRouter) + Claude Sonnet ·
Next.js (dashboard). Testes 100% offline (pytest + mocks; sem chamadas a APIs reais por padrão).

## Rodando localmente

Dois caminhos: **Docker** (recomendado para o time — zero dependências no host) ou **host
nativo** (mais rápido pra debug, exige Python/Node instalados).

### Docker (recomendado para o time)

Sobe a stack inteira — Postgres (pgvector) + Redis + API + worker + beat + dashboard. Um dev
novo precisa **só do Docker** instalado. Código é bind-mount com hot-reload (edite → recarrega).

```bash
docker compose up            # sobe tudo (migrations + seed rodam antes via serviço `migrate`)
docker compose up -d         # em background
docker compose logs -f api   # segue o log de um serviço
docker compose down          # para (mantém dados);  down -v também apaga os volumes (DB/redis)
```

- API: http://localhost:8000/api/v1/health · Dashboard: http://localhost:3000/painel
- Sem `.env` a stack sobe com defaults de dev (bearer `dev-local-token`, `RUN_REAL_EXTERNALS=0`).
  Pra chamadas reais: `RUN_REAL_EXTERNALS=1 OPENROUTER_API_KEY=... ANTHROPIC_API_KEY=... docker compose up`.
- Portas configuráveis: `BRAVE_API_PORT`, `BRAVE_DASH_PORT`, `BRAVE_PG_PORT`, `BRAVE_REDIS_PORT`
  (útil se já houver Postgres/Redis no host — ex.: `BRAVE_PG_PORT=5433 docker compose up`).
- Reset da base no Docker: `docker compose exec api /app/.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes`

### Host nativo

A aplicação são **quatro serviços**: API (FastAPI), worker (Celery), beat (Celery beat/RedBeat)
e dashboard (Next.js). **Postgres** e **Redis** são dependências externas (Homebrew ou Docker) —
precisam estar de pé antes de subir a app.

### Pré-requisitos (uma vez)

```bash
# Postgres + Redis rodando (ex.: Homebrew)
brew services start postgresql@16 redis

# venv Python (uv) + deps
uv sync                                  # cria .venv com as dependências

# dashboard (Bun / Node 22)
cd dashboard && bun install && cd ..

# configuração: copie e preencha o .env (DB/Redis, chaves LLM, bearer do dashboard, flags)
cp .env.example .env                     # se existir; senão veja brave/config/settings.py

# schema + seed da config
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed_config  # popula config_settings com os defaults
```

> **Node 22** é o alvo do dashboard. Node ≥26 quebra o `localStorage` do jsdom nos testes
> (há polyfill em `dashboard/vitest.setup.ts`, mas prefira node 22 localmente).

### Subir / parar / reiniciar

Tudo é gerenciado por **`scripts/app.sh`** (PIDs e logs em `.run/`, ignorado pelo git):

```bash
scripts/app.sh start            # sobe os quatro serviços
scripts/app.sh status           # up/down + pid + porta de cada serviço
scripts/app.sh logs worker -f   # segue o log de um serviço
scripts/app.sh restart          # reinicia
scripts/app.sh stop             # para tudo (também reaproveita/mata processos órfãos)

# subconjunto de serviços:
scripts/app.sh start api worker
```

- API:       http://127.0.0.1:8000  (`GET /api/v1/health` → `{"status":"ok","db":"ok","redis":"ok"}`)
- Dashboard:  http://localhost:3000/painel
- Portas configuráveis: `BRAVE_API_PORT`, `BRAVE_DASH_PORT`.

Numa base recém-resetada o motor sobe em **DESLIGADO** (ocioso) — ligue em `/painel` para
iniciar a coleta. Atrativos precisam de um sweep de destinos/mTur antes, senão vão pra DLQ como
`parent_destino_absent` (cold start esperado).

### Testes

```bash
# backend (offline por padrão — não faça source do .env, senão bate em APIs reais)
.venv/bin/python -m pytest tests/unit tests/contract -q

# dashboard
cd dashboard && bun run test
```

### Reset da base (cold start)

```bash
.venv/bin/python .claude/skills/reset-brave-db/scripts/reset_db.py --yes
```
Trunca os dados (mantém schema + `alembic_version`), re-seeda a config e limpa o Redis `brave:*`.

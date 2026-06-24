TripAdvisor — Nível 3 Runbook (coleta real, operator-gated)
============================================================

Validação end-to-end da coleta real do lane TripAdvisor (Fase 12,
session-injection seam). NÃO roda em CI — DataDome barra browser
automatizado, então a captura da sessão é sempre humana.

Os 4 componentes da stack + a captura humana. A captura (Passo 1) é o
ÚNICO passo que exige um browser humano real logado num IP residencial;
todo o resto é mecânico.


PRÉ-REQUISITOS — subir a stack (4 processos)
--------------------------------------------

  cd <repo-root>

  # env compartilhado (todos os terminais)
  export BRAVE_DB_URL="postgresql+psycopg://brave:brave@localhost:5432/norteia_brave"
  export BRAVE_DB_REDIS_URL="redis://localhost:6379/0"
  export BRAVE_STEWARD_SECRET="dev-steward-secret"
  export BRAVE_DASHBOARD_BEARER_TOKEN="dev-bearer"
  export BRAVE_TA_SESSION_TTL=1800            # 30 min (default)
  # opcional mas RECOMENDADO — canary/sweep saem por IP residencial:
  # export BRAVE_TA_PROXY_URL="socks5://user:pass@proxy-host:porta"

  # infra
  docker compose up -d postgres redis
  .venv/bin/alembic upgrade head              # só na 1ª vez

  # terminal A — API
  .venv/bin/uvicorn brave.api.main:app --port 8000

  # terminal B — Celery worker (o sweep roda AQUI via .delay)
  export RUN_REAL_EXTERNALS=1                  # opt-in: client real, não Null
  .venv/bin/celery -A brave.tasks.celery_app worker -Q default -l info

  NOTA: sem o worker, engine/start retorna "started" mas nada coleta.
        Sem RUN_REAL_EXTERNALS=1 no worker, usa NullTripAdvisorClient.


PASSO 1 — Capturar sessão (browser HUMANO real, IP residencial)
---------------------------------------------------------------

DataDome barra automação → tem que ser humano logado.

  1. Chrome/Firefox logado em tripadvisor.com
  2. F12 → aba Network
  3. Abrir página de estado BR, ex:
       https://www.tripadvisor.com/Tourism-g303380-Minas_Gerais_State.html
     ATENÇÃO: ES (303516) redireciona pra MG (303380) — verifique o geoId
     em data/tripadvisor/uf_geoids.json antes de varrer ES.
  4. No Network, filtrar "graphql/ids"
  5. Botão direito num POST → "Copy as cURL (bash)"
  6. Salvar em arquivo local:
       pbpaste > /tmp/ta.curl          # macOS

  O cURL contém cookie DataDome vivo = credencial curta (~30 min).
  NÃO commitar, NÃO colar em chat/log.


PASSO 2 — Injetar (servidor)
----------------------------

  python scripts/ta_bootstrap --curl /tmp/ta.curl --endpoint http://localhost:8000

  Esperado:
    Parsed: N cookies, query_ids={'destinations': '...', 'attractions': '...'}
    Session injected — canary result: ready

  Resultados do canary:
    ready                       → sessão válida, gravada no Redis
    HTTP 422 / invalid_session  → sessão ruim/expirada (chave deletada) → recapture
    HTTP 503 / canary_unverified→ falha de infra (proxy/DNS/site) → chave PRESERVADA, repita o inject


PASSO 3 — Confirmar saúde
-------------------------

  curl -s -H "X-Steward-Secret: dev-steward-secret" \
    http://localhost:8000/api/v1/tripadvisor/session/status | jq

  Esperado:
    {"present": true, "expires_in": 1780,
     "query_ids": ["destinations","attractions"], "reason": null}


PASSO 4 — Sweep (1 UF)
----------------------

  curl -s -X POST http://localhost:8000/api/v1/engine/start \
    -H "X-Steward-Secret: dev-steward-secret" -H "Content-Type: application/json" \
    -d '{"source":"tripadvisor","depth":"nascente_rio","ufs":["MG"],"lane":"both"}' | jq

  Esperado: {"status":"started","ufs_total":1,"depth":"nascente_rio","source":"tripadvisor",...}
  Acompanhe os logs de ingestão no terminal B (worker).


PASSO 5 — Verificar coleta
--------------------------

  # registros Nascente TripAdvisor
  docker compose exec -T postgres psql -U brave -d norteia_brave -c \
    "select source, count(*) from nascente where source='tripadvisor' group by source;"

  # progresso do engine
  curl -s -H "X-Steward-Secret: dev-steward-secret" \
    http://localhost:8000/api/v1/engine/status | jq

  Esperado: contagem > 0 (não zero silencioso).

  Dashboard: cd dashboard && bun run dev → EngineControl com fonte TripAdvisor
  mostra o pill "Pronta" (vira "Expirada"/"Precisa bootstrap" quando o TTL acaba).


CRITÉRIOS DE ACEITE (= 12-HUMAN-UAT.md)
---------------------------------------

  1. ta_bootstrap parseia cURL real e injeta (Passo 2)
  2. canary: válida→ready, expirada→invalid_session, infra→canary_unverified (Passo 2)
  3. sweep 1 UF ingere Nascente > 0, pill transiciona (Passos 4-5)


TROUBLESHOOTING
---------------

  inject sempre 422 (server sem proxy) → egress por IP datacenter (DataDome-walled);
                                          setar BRAVE_TA_PROXY_URL residencial.
  "started" mas 0 registros            → worker sem RUN_REAL_EXTERNALS=1 / usa NullClient;
                                          reiniciar worker com a flag.
  sweep para com needs_bootstrap       → sessão expirou (TTL 30min); recapturar + reinjetar.
  status present:false após inject     → canary 422 deletou a chave; sessão ruim, recapturar.

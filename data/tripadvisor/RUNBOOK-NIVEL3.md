TripAdvisor — Nível 3 Runbook (coleta real, operator-gated)
============================================================

Validação end-to-end da coleta real do lane TripAdvisor (Fase 12,
session-injection seam; Fase 13, data-fetch contract — listing query real
AttractionsFusion qid a5cb7fa004b5e4b5). NÃO roda em CI — DataDome barra
browser automatizado, então a captura da sessão é sempre humana.

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

  1. Chrome/Firefox LOGADO em tripadvisor.com (login obrigatório — sem login
     o cookie TASID não é emitido e o session_id fica ausente).
  2. F12 → aba Network
  3. Abrir a página de ATRATIVOS (listing) do estado BR — NÃO a página Tourism.
     A página Tourism dispara apenas qids de telemetria/anúncio; a listing real
     (AttractionsFusion) só aparece na página Attractions-g<geoId>. Exemplos:
       https://www.tripadvisor.com/Attractions-g294280-Activities-Brazil.html                    (geoId nacional 294280)
       https://www.tripadvisor.com/Attractions-g303380-Activities-Minas_Gerais_State_Brazil.html  (MG 303380)
     ATENÇÃO: ES (303516) redireciona pra MG (303380) — verifique o geoId
     em data/tripadvisor/uf_geoids.json antes de varrer ES.
  4. No Network, filtrar "graphql/ids". Vão aparecer vários POSTs.
     IDENTIFICAR O POST CORRETO: botão direito num POST → aba Preview/Response.
     O POST CORRETO tem no Response "WebPresentation_SingleFlexCardSection".
     Ignore requests com user_navigated, GetPageSlotSettings, Trips_ReferenceInput
     — são telemetria/anúncios.
  5. Botão direito SÓ no POST com SingleFlexCardSection → "Copy as cURL (bash)"
  6. Salvar em arquivo local:
       pbpaste > /tmp/ta.curl          # macOS

  O cURL contém cookie DataDome vivo = credencial curta (~30 min).
  Certifique-se de estar logado no TripAdvisor para que o cookie TASID apareça.
  O ta_bootstrap extrai o TASID automaticamente; se não encontrar, avisa
  "session_id: NOT FOUND".
  NÃO commitar, NÃO colar em chat/log.


PASSO 2 — Injetar (servidor)
----------------------------

  python scripts/ta_bootstrap --curl /tmp/ta.curl --endpoint http://localhost:8000

  Esperado:
    Parsed: N cookies, query_ids={'destinations': '...', 'attractions': '...'}
    session_id: found  ← TASID capturado
    Session injected — canary result: ready

  Se session_id mostrar "NOT FOUND":
    → recapture estando LOGADO no TripAdvisor (cookie TASID ausente).

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
  3. sweep 1 UF ingere Nascente > 0 com entity_type='attraction' — verificar com:
       docker compose exec -T postgres psql -U brave -d norteia_brave -c \
         "select source, entity_type, count(*) from nascente where source='tripadvisor' group by source, entity_type;"
     Esperado: pelo menos uma linha com entity_type='attraction' e count > 0.
     A pill transiciona (Passos 4-5).


TROUBLESHOOTING
---------------

  inject sempre 422 (server sem proxy) → egress por IP datacenter (DataDome-walled);
                                          setar BRAVE_TA_PROXY_URL residencial.
  "started" mas 0 registros            → worker sem RUN_REAL_EXTERNALS=1 / usa NullClient;
                                          reiniciar worker com a flag.
  sweep para com needs_bootstrap       → sessão expirou (TTL 30min); recapturar + reinjetar.
  status present:false após inject     → canary 422 deletou a chave; sessão ruim, recapturar.
  ta_bootstrap avisa "qid is a telemetry/ad/trips query"
                                       → capturou o POST errado; use a página
                                          Attractions-g<geoId> e filtre por
                                          SingleFlexCardSection no Response.
  ta_bootstrap session_id: NOT FOUND   → cookie TASID ausente; fazer login no
                                          TripAdvisor antes de capturar.

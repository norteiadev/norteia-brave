# Plano — Pipeline Brave (collector Python) + consumo Mar na norteia-api

## Context

A Norteia precisa popular sua base territorial com **dados validados e pontuados por
confiabilidade**, cobrindo **todo o Brasil**, começando do zero ("carga inicial"). O doc de MVP
(`docs/Norteia_MVP_Documentacao_Tecnica_v1.md`, §7) já define o framework canônico — o **Pipeline
Brave (Nascente → Rio → Mar)** com **score de confiabilidade** (§7.6) e **DLQ** — e o trata como
**pré-requisito #1** da plataforma (§16, linhas 1633–1679). **Esse pipeline ainda não existe no
código** (verificado: sem Brave/Nascente/Rio/Mar/DLQ/score em `app/` ou `database/`).

Esta iniciativa estabelece o **núcleo reutilizável do Brave** (entity-agnostic) e suas **duas
primeiras lanes de coleta: Destinos e Atrativos**.

### Fronteira (decisões do usuário)

> **O collector (app Python novo) É o Brave.** Motor completo — Nascente, Rio (limpeza/dedup/
> normalização/scoring §7.6), Mar (canônico ≥85%), DLQ (51–84.9% revisão humana), descarte (≤50%)
> — + as lanes de coleta + dashboard de operação. **Apenas itens Mar vão para a norteia-api.**

```
┌──────────────────────────────────────────────────────────┐         POST itens MAR        ┌────────────────────────┐
│  norteia-brave (Python)  =  PIPELINE BRAVE                │  ─────────────────────────▶   │  norteia-api (Laravel) │
│  Lanes: DESTINOS + ATRATIVOS (+ futuras)                 │   /api/internal/territorial   │                        │
│      → NASCENTE → RIO(score §7.6) → MAR ─────────────────┼──▶ só MAR (≥85% ou DLQ-aprov)  │  destinations/attractions│
│                              ├─ DLQ (51–84.9%, humano)    │                               │  (consumo: UI + IA)    │
│                              └─ descarte (≤50%)           │  ◀── flag de erro p/ reprocess│  + botão "reportar erro"│
│  Dashboard Next.js: monitor Brave + DLQ + ops + gates    │   /webhook (community report) │                        │
└──────────────────────────────────────────────────────────┘                               └────────────────────────┘
```

- **Stack do collector: Python** (repo novo; trava "PHP fixo" da CLAUDE.md governa só a norteia-api).
- **Execução: serviço contínuo 24/7** (todos os estados do BR).
- **Score §7.6 + DLQ** é o gating canônico (não approve-humano em tudo).
- **Dependência:** Destinos populam Mar **antes/junto** dos Atrativos (atrativo pertence a um destino).
- **LLM:** backend (extração/scoring/desmembramento) = **DeepSeek pago via OpenRouter**;
  conversacional (WhatsApp, lane de atrativos) = **Claude Sonnet 4.5**.

> Desvio consciente do doc: §15.7 sugeria DLQ+monitor Brave no CMS Filament da norteia-api. Por
> decisão do usuário, isso vai para o **dashboard do collector** (que passa a ser o CMS territorial).

---

## Parte A — norteia-api (consumidor de Mar; footprint mínimo)

Não hospeda Brave. Recebe **só Mar** e serve UI + assistentes.

### A.1 Ingestão de Mar (entity-agnostic)
- `POST /api/internal/territorial/{destinations|attractions}` — auth por **token de serviço**
  (Sanctum ability), **FormRequest**, **idempotente por chave canônica/`source_ref`**. Upsert
  direto em `destinations`/`attractions` (= Mar/publicado), com `reliability_score` + `provenance`
  (json). Sem staging — Mar já é canônico. Atrativo referencia seu `destination_id` (resolvido pelo
  collector). `AttractionObserver`/`DestinationObserver` disparam indexação RAG automática.
- Migrations leves: `source`, `source_ref` (único), `reliability_score`, `provenance` (json),
  `visibility` (`published`|`hidden`|`flagged`), `published_at` em `destinations` **e** `attractions`.

### A.2 Invalidação / erro reportado (§7.7–7.8)
- `visibility` permite "tirar do ar"/"sinalizar" rápido. Botão "reportar erro" (UI) → endpoint →
  **webhook p/ o collector** reabrir o registro em Rio/DLQ. Plataforma lê só `visibility=published`.

### Critical files — norteia-api
- `database/migrations/*_add_brave_provenance_to_{destinations,attractions}.php`
- `app/Http/Controllers/Internal/TerritorialIngestController.php` + `app/Http/Requests/…`
- `app/Http/Controllers/…/ReportErrorController.php` + cliente webhook p/ collector
- `routes/api.php` (rotas internas) + token/ability Sanctum de serviço
- Filtro `visibility=published` em `AttractionService`/`DestinationService`
- Reutiliza `AttractionObserver`/`DestinationObserver` (RAG)

---

## Parte B — norteia-brave = Pipeline Brave (Python, repo novo)

> Milestone GSD própria no repo Python (Brave é foundational). Aqui: arquitetura + contrato.

### B.1 Núcleo Brave (reutilizável, entity-agnostic, multi-fonte)
- **Nascente (ingest bruto)** — store source-tagged + versionado (JSONB) de payloads de qualquer
  lane/entidade.
- **Rio (processamento + scoring)** — explode payloads: dedup (hash exato + fuzzy/embedding),
  normalização (nomes/coords/endereços), rotulação (taxonomia Norteia), **score §7.6**. Regras
  determinísticas + NLP (DeepSeek).
- **Mar (canônico ≥85%)** — publicável → **push p/ norteia-api**. Versionado; invalidação/atualização.
- **DLQ (51–84.9%)** — revisão humana no dashboard (aprovar/rejeitar/editar/reprocessar).
  **Descarte/reprocesso (≤50%)**.
- **Score engine §7.6** — origem 30% · completude 20% · corroboração 20% · atualidade 15% ·
  validação humana 15%; **pesos calibráveis** via config. Mesmo motor p/ destino e atrativo.

### B.2 Lanes de coleta
- **Destinos (esta entrega):** Mtur seed + NotebookLM + desmembramento LLM+humano (B.3).
- **Atrativos (esta entrega):** Google Places + gov + outreach WhatsApp (B.4).
- **Futuras (core já suporta):** scraping monitor de sites oficiais (cron §7.8), CMS de negócios,
  UGC, e demais entidades (experiência, evento, temporada, rota).

### B.3 Lane de Destinos (§7.2, §7.4 — precede atrativos)
Destinos são unidades territoriais (não se contata por WhatsApp). Validação = humana (equipe) +
corroboração + origem oficial.
1. **MturSeedIngest** — ingest dos municípios Mtur categorizados (Oferta Principal/Complementar/
   Apoio) → Nascente (`source=mtur`, origem §7.6 = 100). Vínculo a `municipality_id`.
2. **NotebookLMIngest** — relatórios estruturados → Nascente (`source=notebooklm`, origem = 80).
   Complementa destinos que não constam no Mtur (distritos/localidades).
3. **DesmembramentoAgent (§7.4)** — p/ cada município Oferta Principal, DeepSeek lista destinos
   reais dentro dele (distritos, praias, vilas) com nome turístico/tipo/posicionamento → Nascente
   flag "gerado por LLM — pendente validação" (origem = 40). Complementar/Apoio: 1:1 simplificado,
   LLM só com indício de subdivisão.
4. **Rio + score** — dedup (ex.: Trancoso ≠ Porto Seguro sede), normaliza nome turístico vs
   município, score §7.6. Geralmente score médio (falta validação humana) → **DLQ**.
5. **Validação humana (DLQ, em lote por estado — BA/RJ/SP/SC/CE/PE primeiro)** — equipe confirma/
   corrige/completa → validação humana = 100 → **Mar** → push (`destinations`).

### B.4 Lane de Atrativos (depende de Destinos em Mar)
Sub-estados: `discovered` → `contacts_found` → `signals_gathered` → *(Rio score)* → se borderline:
`aguardando_consulta_whatsapp` → *(gate humano)* → `whatsapp_in_progress` → re-score.
1. **DiscoveryAgent** — Google Places (varredura UF/município) + gov. DeepSeek mapeia → schema →
   Nascente. **Resolve o `destino` pai** (destino já em Mar).
2. **ContactFinderAgent** — Places Details (phone/website/**link WhatsApp**) + site/IG-FB/email.
3. **SignalAgent** — **Places**: `business_status` (CLOSED_* → descarte), `weekday_text` (horários),
   **`reviews[].publishTime` ≤ 30 dias ⇒ funcionando** (Atualidade §7.6). **IG/X via Apify**
   (best-effort). **OTA** opcional (preço cross-check; só ticketado).
4. **Gate WhatsApp (humano, dashboard) — só borderline** (score < 85% por falta de validação
   direta). Humano aprova quais contatar (controle de volume = mitigação de ban/custo; ramp).
5. **WhatsAppAgent (100% automatizado)** — **WhatsApp Business API** (Twilio/Meta Cloud), **n8n
   thin** + lógica **LangGraph**. Sonnet pergunta PT-BR (identifica Norteia + opt-out); DeepSeek
   extrai existe?/funcionando?/horários/valor. Onde Places/OTA já preencheram, só confirma. Owner-
   validation = boost de score → re-score → Mar/DLQ.

> Todas as APIs externas rodam **no collector**, nunca no hot path da norteia-api.

### B.5 Stack do collector
| Camada | Escolha | Por quê |
|--------|---------|---------|
| API + webhooks | **FastAPI** | Webhooks WhatsApp/email + REST p/ dashboard + ingest das lanes |
| Orquestração 24/7 | **Celery + Redis** (beat) — ou **Temporal** se workflows duráveis justificarem | Fan-out por UF; outreach tolera latência de dias |
| Agentes/LLM | **LangGraph** + OpenAI SDK→OpenRouter (DeepSeek) / Anthropic SDK (Sonnet) | Orquestração + multi-provider |
| Pipeline WhatsApp | **n8n thin** (transporte) + lógica LangGraph | Nós WhatsApp Cloud API prontos; multi-turno adaptativo em código (testável) |
| Saída estruturada | **Pydantic + `instructor`** | 2ª camada (DeepSeek tem JSON-schema fraco) |
| Sinal Places | **Google Places API (New) Details** | Oficial, ToS-clean |
| Preço (opcional) | **OTA (Viator/GYG/Booking)** | Só ticketado; onboarding gated; cross-check |
| Scraping IG/X | **Apify** + filtro LLM | Best-effort; ToS Meta cinza; Places é fallback |
| DB | **PostgreSQL** (JSONB Nascente; pgvector p/ dedup) | ETL/estado do Brave |
| Dashboard | **Next.js + Bun (Node 22)** (espelha `norteia-frontend`) | B.7 |

### B.6 LLM — DeepSeek `:nitro` via OpenRouter (backend não-cliente)
- `:nitro` = throughput (batch backend; não latency-sensitive). **Fixar o slug no build**
  (`deepseek-v4-flash` pode não existir → fallback `deepseek/deepseek-chat` V3.x / `deepseek-v3.2`);
  pinar, centralizar em config. **Pago ≠ "não treina":** `provider.data_collection: deny` +
  setting da conta. Validador 2ª camada obrigatório. (Conversacional WhatsApp = Sonnet.)

### B.7 Observabilidade + Dashboard Next.js (CMS territorial)
- **Observabilidade:** `llm_generations` própria + guard de custo USD + métricas Brave por camada +
  fila/worker + quality rating WhatsApp + **logs de auditoria** (§15.7), expostos pela FastAPI.
- **Dashboard (Next.js, Bun, Node 22, Bearer header, Vitest):**
  - **Monitor Brave (§15.7)** — volume por camada, taxas aprovação/rejeição/DLQ, alertas de falha,
    throughput, auditoria.
  - **Fila DLQ** — revisar (payload Nascente, dados Rio, score §7.6 por critério, sinais, log
    WhatsApp) → aprovar/rejeitar/editar/reprocessar. **Modo lote por estado** (desmembramento de
    destinos).
  - **Gate WhatsApp** — fila `aguardando_consulta_whatsapp` → aprovar/rejeitar; ramp.
  - **Conversas WhatsApp**, **funis** (destinos e atrativos por UF/source), **Custo & LLM**.

### B.8 Compliance
- **LGPD:** base legal + identificação Norteia + opt-out + log de consentimento + minimização
  (relevante na lane de atrativos/WhatsApp; destinos não têm PII de contato).
- **WhatsApp Business API (BSP):** templates aprovados; janela 24h; gate humano + ramp; opt-out.
- **Meta IG/FB:** sem DM automatizado — só leitura de sinal (Apify best-effort, ToS cinza).
- **Google Places ToS:** persistir `place_id`; dado canônico é o validado (first-party); chamar só
  no collector. **OTA:** aprovação de parceiro. **Email:** identidade + descadastro.
- **Scraping de fontes (destinos):** sites oficiais/prefeituras/Wikipedia = ok (§7.3); avaliar
  risco jurídico por fonte e documentar.

---

## Parte C — Testabilidade local (rodar TODA a suite local, sem externas reais)

Regra: nenhum teste bate em Places/OTA/Apify/WhatsApp/OpenRouter/Anthropic/Mtur/norteia-api por
padrão. Real = **opt-in por flag**. CI sem chaves.
- **Design p/ teste:** lógica em **código** (Brave core, score engine, desmembramento, conversa);
  n8n thin; fronteira de rede atrás de clients (Places/Ota/Apify/WhatsApp/Mtur/NotebookLM/NorteiaApi).
- **norteia-api (DDEV):** `ddev exec php artisan test --compact`; `Prism::fake`/`Queue::fake`;
  ingestão Mar (destinos + atrativos) e webhook de erro por feature tests.
- **collector (Python `docker-compose`):** Postgres + Redis → `pytest` **100% offline**; `respx`/
  VCR.py p/ Places/OTA/Apify/Mtur; LLM fake; webhook WhatsApp por fixture; **score engine** unitário
  (casos → Mar/DLQ/descarte); **desmembramento** com LLM fake; contrato norteia-api via **Pact**.
- **dashboard Next.js:** **Vitest + MSW**, `bun run test` offline, Node 22.

---

## Sequência de fases GSD

**Trilha 1 — Brave core (collector, milestone foundational):** Nascente/Rio/Mar/DLQ + **score engine
§7.6** (calibrável) + FastAPI + workers (Celery/Redis) + clients atrás de interface + observabilidade.

**Trilha 2 — Lane de Destinos (collector, PRECEDE atrativos):** MturSeedIngest + NotebookLMIngest +
**DesmembramentoAgent §7.4** → Rio/score → DLQ (validação humana em lote por estado) → Mar.

**Trilha 3 — Lane de Atrativos (collector, depende de Destinos em Mar):** Discovery (Places) →
ContactFinder → SignalAgent (business_status/reviews + Apify/OTA) → gate WhatsApp → WhatsAppAgent
(n8n thin + LangGraph) → re-score → Mar/DLQ.

**Trilha 4 — Dashboard (collector, Next.js):** monitor Brave + DLQ (lote) + gate WhatsApp + conversas
+ funis + custo. Paralela às trilhas 1–3.

**Trilha 5 — norteia-api (este repo):** ingestão de Mar (destinos + atrativos) + token de serviço +
`provenance/score/visibility` em `destinations`/`attractions` + webhook de erro + filtro
`visibility=published`. **Pré-requisito:** contrato de ingestão estável.

---

## Verification

### norteia-api
- Feature tests (`Prism::fake`/`Queue::fake`, sem chaves): ingestão de Mar (destino e atrativo) cria/
  atualiza canônico publicado + dispara indexação RAG; idempotência; token de serviço; erro reportado
  → `visibility=hidden` + webhook ao collector; plataforma lê só `published`.
- E2E local (DDEV): `curl` simulando push de Mar (destino → atrativo) → aparecem publicados →
  CAT/RAG citam.
- Gates: `ddev exec php artisan test --compact` · `pint --dirty` · `phpstan analyse`.

### collector + dashboard
- `pytest` 100% offline (docker-compose), todas as externas mockadas; **score engine** e
  **desmembramento** com casos cobrindo Mar/DLQ/descarte; contrato norteia-api por Pact.
- Vitest (dashboard) com API mockada (MSW), offline.
- Antes de qualquer WhatsApp real: template aprovado + opt-out + log de consentimento.

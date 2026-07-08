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

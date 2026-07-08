# Scoring de confiabilidade — da Nascente ao Rio (e ao Mar / DLQ)

Este documento explica, passo a passo, o que acontece com um **atrativo** desde o momento
em que ele entra na **Nascente** até ser roteado para o **Mar** (publicado) ou para a **DLQ**
(fila de revisão humana), com foco em **como os pesos de confiabilidade funcionam**.

> **Importante:** você provavelmente conhece 4 critérios (origem, completude, corroboração,
> atualidade). O código implementa **5** — o quinto é **validação humana (15%)**. Os 4 que você
> citou somam 85% do peso; a validação humana completa os 100%.

---

## Visão geral do fluxo

```
  Nascente            Rio (processamento)                 Destino
 ─────────      ─────────────────────────────       ──────────────────
  store_raw  →  normaliza → dedup → SCORE  →  routing = "mar"  → push_mar → norteia-api
 (payload      (rio/routing.py)  (confiabilidade)  ├─ score >= 80 ────→ MAR (publicado)
  imutável)                                 └─ score <  80 ────→ DLQ (revisão humana)
```

O score é calculado em **duas etapas**:

1. **Camada de domínio/fonte** — calcula o *valor bruto* (0–100) de cada critério e grava
   no payload da Nascente como chaves `*_value` (`origem_value`, `completude_value`, etc.).
2. **Engine puro** (`compute_score`) — aplica os **pesos de confiabilidade** sobre esses valores e produz
   o score final ponderado + o roteamento (mar/dlq). Esse engine é garantidamente livre de I/O
   (verificado por grep no CI, D-12).

---

## Passo a passo

### 1. Entrada na Nascente — `brave/core/nascente/service.py` (`store_raw`)

- Armazenamento **imutável e append-only** do payload cru.
- `content_hash` = SHA-256 sobre o JSON com chaves ordenadas → garante **idempotência**
  (payload idêntico não duplica) e **versionamento por supersessão** (mesmo `source_ref`,
  payload diferente → marca `superseded_by_id`).
- **Não pontua nada aqui.** A camada da fonte já colocou cada `*_value` no payload.

### 2. Gatilho da transição — `brave/tasks/pipeline.py:283` (`process_nascente`)

Task Celery `process_nascente(nascente_id)`:

1. Carrega config efetiva.
2. Busca a linha da Nascente.
3. Checa idempotência contra `RioRecord.canonical_key` existente.
4. Chama o pipeline do Rio → `session.commit()`.
5. Em erro: `quarantine_poison` (erro permanente ou após máximo de retentativas).

### 3. Pipeline do Rio — `brave/core/rio/routing.py:92` (`process_nascente_record`)

Ordem das etapas (D-07):

1. Idempotência por `canonical_key`.
2. **Normaliza** nome / endereço / coordenadas (`rio/normalize.py`).
3. **Carrega os 5 `*_value`** do payload para o dicionário `normalized`.
4. **Taxonomia** (rótulo) + **embedding** + **dedup** fuzzy/exato (`rio/dedup.py`, `DEDUP_THRESHOLD = 0.95`).
5. Cria o `RioRecord` com `routing = "in_progress"`.
6. **Pontua e roteia** → `route_by_score(session, rio, config)` → `session.flush()`.

### 4. Cálculo do score — `brave/core/rio/routing.py:33` (`route_by_score`)

É a "costura" (seam) do scoring: monta o `ScoreInput` a partir de `normalized`, chama
`compute_score` e escreve de volta no registro: `rio.score`, `rio.routing`,
`rio.score_version`, `rio.score_breakdown`, `rio.processed_at`. Se cair na DLQ, grava também:

```python
rio_record.dlq_reason = f"score={result.score:.2f} below threshold_mar={config.threshold_mar}"
```

---

## Como os pesos funcionam

### Constantes de peso — `brave/config/settings.py`

| Critério | Peso |
|---|---|
| origem | **30%** |
| completude | **20%** |
| corroboração | **20%** |
| atualidade | **15%** |
| validação humana | **15%** |
| **soma** | **100%** |

- `threshold_mar` (default **80.0**) — limiar único e binário. Override via env
  `BRAVE_SCORE_THRESHOLD_MAR` ou via config no banco (`score.threshold_mar`).
- `score_version` (default `"v1.1"`).

> **Nota sobre "85%":** o número 85 aparece só como alvo de projeto/calibração nos comentários
> (`tripadvisor/scoring.py`, `destinos.py`). O **default enviado no código é 80.0**, ajustável.

### A fórmula — `brave/core/score/engine.py`

Cada critério contribui com `pontos = valor * peso / 100`. Soma tudo, arredonda 2 casas:

```python
origem_pts       = origem_value       * 30 / 100
completude_pts   = completude_value   * 20 / 100
corroboracao_pts = corroboracao_value * 20 / 100
atualidade_pts   = atualidade_value   * 15 / 100
validacao_pts    = validacao_value    * 15 / 100

score   = round(origem_pts + completude_pts + corroboracao_pts + atualidade_pts + validacao_pts, 2)
routing = "mar" if score >= threshold_mar else "dlq"   # gate binário (D-02)
```

- Cada `valor` é 0–100 (validado pelo schema em `score/schemas.py`).
- O **peso** é a fatia máxima que aquele critério pode contribuir para o score final.
- O engine só **combina** — a matemática real de cada critério vive por fonte.

---

## De onde vem o *valor* de cada critério

### 1. origem — 30%

**Não é fórmula.** É uma **constante fixa por fonte** — funciona como *firewall*: nenhuma
fonte sozinha consegue cruzar o limiar do Mar.

| Fonte | origem_value |
|---|---|
| manual / humano | 100 |
| governo / IBGE / Mtur | 100 |
| TripAdvisor (destino e atrativo) | 65 |
| Google Places | 60 |

Exemplo: TripAdvisor 65 × 30% = **19,5 pontos**. Sozinho nunca chega a 80 → precisa dos outros
critérios para ser promovido. Isso impede monopólio de uma única fonte.

Locais: `brave/domains/manual/repositories.py:23`, `tripadvisor/destinos.py:62`,
`tripadvisor/atrativos.py:74`.

### 2. completude — 20%

`brave/domains/tripadvisor/scoring.py:125` (`completude_from_fields`).
Fração dos 10 campos esperados do TripAdvisor presentes, multiplicada por um teto (cap).
Mais campos preenchidos → maior completude.

### 3. corroboração — 20%

`brave/domains/tripadvisor/scoring.py:34` (`corroboracao_from_reviews(count, rating)`).
Curva `log1p` que satura em ~500 reviews (retornos decrescentes):

```python
base = min(100.0, 100.0 * math.log1p(count) / math.log1p(500))
# ex.: 200 reviews @ 4.5 ≈ 85,25
```

Mais reviews → mais corroboração, mas com ganho decrescente.

### 4. atualidade — 15%

`brave/domains/tripadvisor/scoring.py:67` (`atualidade_from_recency(most_recent_review_at)`).
Função-degrau sobre a idade do review mais recente:

| Idade do review mais recente | valor |
|---|---|
| ausente (`None`) | 0 |
| ≤ 30 dias | 100 |
| ≤ 180 dias | 70 |
| ≤ 365 dias | 40 |
| ≤ 730 dias | 20 |
| > 730 dias | 0 |

Atrativo popular mas com reviews velhos → atualidade baixa.

### 5. validação humana — 15%

Default **0**. Só vira **100** quando um humano valida um registro na DLQ
(`brave/core/dlq/service.py:37`). É o caminho *human-in-the-loop* de volta para o Mar.

---

## Depois do Rio

### Rota "mar" → publicação

- Task `push_mar(rio_id)` (`brave/tasks/pipeline.py:385`) → `promote_to_mar`
  (`brave/core/mar/service.py:58`) → `POST` para a `norteia-api`.
- Upsert idempotente por `source_ref` (D-15), com supersessão quando os dados mudam.
- Carrega o `score_breakdown` completo (proveniência por critério).
- **Backstop de recência (atrativos):** um atrativo cujo `most_recent_review_at` está ausente
  ou tem mais de **90 dias** (`_REVIEW_MAX_AGE_DAYS = 90`) é forçado para DLQ com
  `dlq_reason = "no_recent_reviews"` — mesmo que o score tenha passado. Rede de segurança extra.

### Rota "dlq" → revisão humana

- Registro entra na fila de revisão.
- Humano valida (`brave/core/dlq/service.py:19`, `validate_and_promote_rio`):
  1. `normalized["validacao_humana_value"] = 100.0` (com `flag_modified` para mutação de JSON).
  2. Re-pontua via `reprocess_record`.
  3. Se agora `routing == "mar"`, chama `promote_to_mar`.
- `reopen_from_error_report` (`mar/service.py:170`) devolve um registro já publicado no Mar para
  a DLQ (reportes de erro da comunidade, CNTR-02).

---

## Índice de arquivos (file:line)

| Assunto | Local |
|---|---|
| Constantes de peso + `threshold_mar` + `score_version` | `brave/config/settings.py:32` |
| Fórmula ponderada + roteamento binário | `brave/core/score/engine.py:40` |
| Schemas do score (validação 0–100) | `brave/core/score/schemas.py:15` |
| corroboração (log1p) | `brave/domains/tripadvisor/scoring.py:34` |
| atualidade (função-degrau) | `brave/domains/tripadvisor/scoring.py:67` |
| completude (cobertura de campos) | `brave/domains/tripadvisor/scoring.py:125` |
| origem (constantes / firewall) | `manual/repositories.py:23`; `tripadvisor/destinos.py:62`; `tripadvisor/atrativos.py:74` |
| Ingest da Nascente (`store_raw`) | `brave/core/nascente/service.py:25` |
| Task Nascente→Rio | `brave/tasks/pipeline.py:283` |
| Pipeline do Rio + invocação do score | `brave/core/rio/routing.py:92` (score em `:253`) |
| `route_by_score` (seam do scoring) | `brave/core/rio/routing.py:33` |
| Entradas de re-scoring | `brave/core/rio/routing.py:304` |
| Promoção Rio→Mar + backstop de recência | `brave/core/mar/service.py:58` |
| Task `push_mar` | `brave/tasks/pipeline.py:385` |
| Validação humana DLQ → promoção | `brave/core/dlq/service.py:19` |

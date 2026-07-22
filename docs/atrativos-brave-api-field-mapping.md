# Atrativos — Contrato de dados Brave → norteia-api

**Atualizado:** 2026-07-22

Mapa dos campos que o Pipeline Brave envia para a `norteia-api` quando um atrativo
(ou destino) é promovido para **Mar**. O contrato é **flat** (a API é a fonte da
verdade do formato); território resolve por `municipio_ibge`; o destino-pai viaja
em `destino.*` para *resolve-or-create*; e o enriquecimento do Google Places viaja
em `place.*`, aterrissando na tabela separada `attraction_place_details`.

- **Brave (produtor):** `brave/core/mar/service.py::build_push_payload`
- **API (consumidor):** `POST /api/internal/territorial/{attractions,destinations}`
  (`TerritorialIngestController` + `IngestAttractionRequest`/`IngestDestinationRequest`)

---

## 1. Campos que o Brave envia para a API

### Atrativo → `POST /territorial/attractions`

| Campo enviado (Brave) | Origem (canonical) | Destino na API |
|---|---|---|
| `source_ref` | `mar.source_ref` | `attractions.source_ref` (chave upsert) |
| `source` | derivado de `source_ref` | `attractions.source` |
| `name` | `canonical.name` / `nome` | `attractions.name` |
| `type` | `canonical.tipo` | `attractions.type` |
| `municipio_ibge` | `canonical.municipio_id` | resolve → `attractions.destination_id` |
| `description` | `descricao_editorial` | `attractions.description` |
| `latitude` / `longitude` | `canonical.lat` / `lon` | `attractions.latitude` / `longitude` |
| `instagram` | `contacts.ig_handle` | `attractions.instagram` |
| `whatsapp` | `contacts.phone_e164` | `attractions.whatsapp` |
| `website` | `contacts.website` | `attractions.website` |
| `reliability_score` | `mar.reliability_score` | `attractions.reliability_score` |
| `provenance` | 5 critérios flat | `attractions.provenance` |
| `destino.source_ref` | `canonical.parent_source_ref` | resolve-or-create `destinations.source_ref` |
| `destino.source` | derivado | `destinations.source` |
| `destino.tourist_name` | `canonical.municipio` | `destinations.tourist_name` |
| `destino.municipio_ibge` | `canonical.municipio_id` | resolve `destinations.municipality_id` |
| `place.place_id` | `google_place_id` | `attraction_place_details.place_id` |
| `place.business_status` | `signal.business_status` | `attraction_place_details.business_status` |
| `place.opening_hours` | `weekday_text` | `attraction_place_details.opening_hours` |
| `place.price_level` | `canonical.price_level` | `attraction_place_details.price_level` |
| `place.reviews_recent_count` | `signal.reviews_recent_count` | `attraction_place_details.reviews_recent_count` |
| `place.distrito_code` / `name` / `municipio_ibge` | `canonical.distrito_*` | `attraction_place_details.distrito_*` |

### Destino → `POST /territorial/destinations`

`source_ref`, `source`, `tourist_name`, `municipio_ibge`, `reliability_score`, `provenance`.

---

## 2. Campos da API que ficam sem informação

Colunas que existem no destino/atrativo da API mas o Brave **não preenche** hoje.

### `attractions`

| Coluna | Motivo |
|---|---|
| `slug` | gerado server-side (não vem do Brave) |
| `free_entry` | Brave não coleta |
| `price` (BRL) | Brave não tem (só `place.price_level` como proxy) |
| `opening_hours` | horário vai para `attraction_place_details`; coluna fica null |
| `accessibility` | Brave não coleta |
| `how_to_get_there` | Brave não coleta |
| `tips` | Brave não coleta |
| `safety_alerts` | Brave não coleta |
| `local_infrastructure` | Brave não coleta |
| `curiosities` | Brave não coleta |
| `conservation_level` | Brave não coleta |
| `estimated_capacity` | Brave não coleta |
| `visibility` | não enviado → default `published` |
| `published_at` | não enviado |
| imagens (Spatie media) | Brave não coleta imagens de atrativo |
| `segments` (pivot TourismSegment) | taxonomia não mapeada |

### `destinations`

Brave envia o destino mínimo, então ficam sem informação: `description`,
`short_phrase`, `offer_type`, `participates_mtur`, `latitude`, `longitude`,
`cover_photo`, `seasonality`, `estimated_spending_range`, `recommended_duration`,
`best_time_to_visit`, `tips`, `safety_alerts`, `connectivity_level`, `pace_profile`,
`estimated_hotel_capacity`, `predominant_audience_profile`, `visibility`,
`published_at` (+ `slug` gerado server-side).

---

## 3. Campos que temos no Brave mas ainda NÃO enviamos

### Existem no canonical/normalized, não mapeados no payload

| Campo Brave | Situação |
|---|---|
| `subdistrito_name`, `subdistrito_code` | Brave carrega (reserved-null hoje), não enviados |
| `distrito_source` | rastreio do resolver (ex `md_breadcrumb`), não enviado |
| `address` | canonical tem, sem coluna dedicada na API (só lat/lon) |
| `uf` | embutido em `source_ref`/IBGE, não como campo próprio |
| `municipio` (nome) no atrativo | vai só como `destino.tourist_name`; o atrativo manda IBGE, não o nome |
| `score_version` | era top-level no shape antigo; API não tem campo |
| `posicionamento` | morre no Rio cherry-pick (nunca chega ao Mar) |
| `labels` (entity_type/taxonomy_version) | stub Fase 1, não enviado |
| `parent_rio_id`, `parent_mar_id` | linkagem interna; só `parent_source_ref` → `destino.source_ref` vai |

### Produzidos, mas excluídos de propósito (internos / board / LGPD)

Corretamente fora do push: os 5 `*_value` (viram `provenance` flat),
`most_recent_review_at`, `contact` (candidato WhatsApp mascarado), `google_enriched`,
`place_id_cache` (chave FSM interna — `google_place_id` é o que vai).

---

## Pendências / próximos passos

- **`type` = `"outros"`** para lanes sem `tipo` (ex.: TripAdvisor). `label_entity`
  ainda é stub de Fase 1; melhora quando o labeling NLP chegar.
- **`destino` reliability/provenance**: o bloco `destino` no push do atrativo manda
  identidade mínima; o score do destino chega no push direto de destino (merge
  idempotente pelo mesmo `source_ref`).
- **Pré-requisito de produção**: rodar `IbgeMunicipalitySeeder` na API para
  cobertura completa de `municipalities` (a resolução por IBGE depende disso).

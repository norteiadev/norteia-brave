# Monitor — promoção manual de atrativo → norteia-api (2026-07-23)

**Teste:** promover 1 atrativo pra Mar pelo painel e verificar se chega na norteia-api,
monitorando logs das duas apps.

**Registro:** `d5a15aa0-7b0a-4f59-8aba-b823906b6bd5` (atrativo).

## Resultado

**Não chegou na norteia-api. Nenhuma exception nas duas apps.**

Deltas capturados durante a promoção:
- **norteia-brave-worker-1**: 0 linhas (nenhuma task rodou).
- **norteia-brave-api-1**: `PATCH /api/v1/atrativos/d5a15aa0…/transition` → **200 OK** + `audit_event action=transition_mar actor=steward`.
- **norteia-api (laravel.log)**: 0 linhas novas (nada recebido).

Estado real no banco (autoritativo) após a promoção:

| campo | valor |
|---|---|
| routing | **dlq** (não `mar`) |
| dlq_reason | **`no_recent_reviews`** |
| score | 87.50 |
| mar_record | **inexistente** |

breakdown: origem 19.5 · completude 18.0 · corroboracao 20.0 · atualidade 15.0 · validacao_humana 15.0

## Causa imediata

O atrativo **não cruzou pro Mar**. Apesar do score 87.50 (≥ threshold_mar), o backstop de
liveness/atualidade segurou o registro em `dlq` com `dlq_reason=no_recent_reviews`
(sem avaliações recentes — o gate de promoção ao Mar exige liveness; ver lane de
enriquecimento Places que destrava esse backstop). `validate_and_promote_rio` re-pontuou,
`routing` voltou `dlq`, então retornou `None` (não promoveu). Como não foi pro Mar, não há
push pra norteia-api — correto.

## Defeitos latentes a tratar (silenciosos, sem exception)

### D1 — endpoint de transição mente o resultado da promoção
`brave/api/routers/atrativos.py` (`transition_atrativo`, edge `promote`, ~L112-116):
```python
if edge == "promote":
    validate_and_promote_rio(db, rio)   # <-- retorno IGNORADO
    db.refresh(rio)
```
`validate_and_promote_rio` retorna `MarRecord | None` (None = não cruzou o gate). O retorno é
ignorado; o handler **sempre** escreve `audit action=transition_mar` e responde
`200 {status: ok, to: "mar"}`, mesmo quando o registro fica em `dlq`. Efeito: o card sobe
otimista pra coluna Mar na UI e some/volta pra dlq no próximo poll (3s), sem feedback do porquê.
**Tratar:** checar o retorno — se `None`, responder o resultado real (ex.: 409 / `{to: "dlq",
held: true, reason: rio.dlq_reason}`) e NÃO auditar como `transition_mar`. A UI deve mostrar
"segurado no Rio · <motivo>" em vez de simular sucesso.

### D2 — promoção manual nunca faz push pra norteia-api
Mesmo quando a promoção manual **cruza** pro Mar, nada enfileira `brave.push_mar` (a task
worker que faz `POST` pra norteia-api via `NorteiaApiClient`). Cadeia:
`transition_atrativo` → `validate_and_promote_rio` (docstring: "Does NOT dispatch Celery tasks")
→ `promote_to_mar` (só cria o MarRecord local + monta payload; sem `.delay()`). Só o pipeline
automático (worker) empurra pro norteia-api. Registro promovido por steward fica **no Mar local
mas nunca sincronizado** com a norteia-api — silencioso.
**Tratar:** no edge `promote`, quando `validate_and_promote_rio` retornar um `MarRecord`,
enfileirar `push_mar.delay(str(rio.id))` (pós-commit) para o steward-promote seguir o mesmo
contrato de ingestão do pipeline.

## Como reproduzir o caminho feliz (para re-testar o push)
Promover um atrativo que **passe** o backstop de liveness (com avaliações recentes / enriquecido
via Places), com motor pausado. Aí D2 fica observável: mesmo cruzando pro Mar, worker + laravel.log
seguem vazios até D2 ser corrigido.

---

## D3 — website + phone descartados no Mar push (perda de dado, sistêmico)

Descoberto no teste happy-path (Convento, id 36 na norteia-api): `website` e `phone`
existem no brave mas chegaram NULL na norteia-api.

**Causa:** `_build_push_payload` (brave/core/mar/service.py:271-273) lê:
```python
"instagram": contacts.get("ig_handle"),
"whatsapp":  contacts.get("phone_e164"),
"website":   contacts.get("website"),   # contacts = canonical.get("contacts") or {}
```
Mas a lane de **Places enrichment** (brave/lanes/atrativos/places_enrichment.py:311-313)
grava no TOP-LEVEL do normalized:
```python
new_normalized["phone"] = phone
new_normalized["website"] = website
```
Só `contact_finder_agent.py:107` cria `normalized["contacts"]` (lane WhatsApp, em aposentadoria).
Logo, para todo atrativo enriquecido via Places (caminho padrão) `canonical` NÃO tem `contacts`
→ website/phone nunca entram no payload. Confirmado: MarRecord.canonical do registro tem
`website`/`phone`/`weekday_text` no top-level e `has_contacts=false`.

**Tratar:** ler com fallback top-level em `_build_push_payload`:
```python
"website":  canonical.get("website")  or contacts.get("website"),
"instagram":canonical.get("instagram")or contacts.get("ig_handle"),
"whatsapp": contacts.get("phone_e164") or canonical.get("whatsapp_candidate"),  # decidir: phone público → whatsapp?
```
Nota de produto: o `phone` do brave é o telefone público do lugar (Places), não necessariamente
WhatsApp — decidir o mapeamento phone→whatsapp vs. um campo phone dedicado na norteia-api.
`opening_hours` NÃO é bug: vai no objeto `place` → tabela `attraction_place_details` (correto).

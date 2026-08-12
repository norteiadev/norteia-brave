---
phase: quick-260812-t3k
status: complete
date: 2026-08-12
commits:
  - d2381a6  # fix(atrativos): types guard
  - 35902af  # feat(destinos): participates_mtur (brave)
  - (this)   # feat(cadastur): local_businesses reference table
  - norteia-api c5c0ddef  # feat(ingest): accept participates_mtur
prs:
  - norteia-api#22
---

# 260812-t3k — as duas pendências do PR #24

## O que mudou

### 1. Guard de `types` no match do Places (`d2381a6`)

`_best_match` aceitava qualquer candidato do Text Search com nome ≥ 85 (rapidfuzz) e
distância ≤ 20 km. Uma entidade geográfica passa nos dois com folga: fica nas
coordenadas do próprio atrativo e costuma ter o nome dele.

Evidência do JSON bruto do spike do PR #24, 15 atrativos, **separação 15/15**:

| resolveu para | `types` | campos |
|---|---|---|
| Centro Histórico de Paraty | `sublocality_level_1,sublocality,political` | zero |
| Convento da Penha | `neighborhood,political` | zero |
| Praia dos Carneiros | `locality,political` | zero |
| os outros 12 | nenhum tem `political` | `accessibilityOptions`, `reviewSummary`, `regularOpeningHours`, `editorialSummary`… |

Guard: pular candidato cujo `types` contém `political`.

Duas decisões que valem registro:

- **`political`, não a lista dos três tipos vistos.** É a classe da própria taxonomia
  do Google, então cobre tipos geográficos fora da amostra.
- **Não a regra inversa ("exigir `establishment`")**, que separa os mesmos 15/15: ela
  falha FECHADA. Se o Google parar de emitir o tipo legado `establishment`, todo
  atrativo silenciosamente para de ser enriquecido. `political` falha ABERTA — uma
  mudança de taxonomia degrada para o comportamento de hoje, não para zero.
- `beach`/`natural_feature` **não** são rejeitados: Praia de Camburi resolveu para
  `["beach","natural_feature","establishment"]` e devolveu `editorialSummary`.

O guard roda por candidato, antes do score, para que o POI real (com score menor)
ganhe quando o município marca 100 no nome.

4 testes novos: político rejeitado, POI real vence, natural feature preservada,
candidato sem `types` aceito (falha aberta).

### 2. `participates_mtur` até a norteia-api (`35902af` + norteia-api#22)

A categorização do MTur já estava no repo desde que a lane de destino-seed foi
aposentada — `data/mtur/municipios_mtur_2025.csv` (2.922 de 5.571 municípios) entra em
`municipios.categoria` pelo `seed_reference_data.py`. **Parava ali.**

Três elos, todos faltando:

| elo | onde | estava |
|---|---|---|
| ler a tabela e carimbar o canonical | `brave/shared/destino.py::ensure_destino` | não existia |
| mandar no payload | `brave/core/mar/service.py::build_push_payload` | não existia |
| aceitar na API | `IngestDestinationRequest::rules()` | **descartava em silêncio** |

O terceiro é o que fazia o resto ser inútil: sem regra, o `$request->validated()` do
controller derruba a chave sem erro e sem 422 — do lado do Brave o push parecia OK.

`ensure_destino` é o lugar certo da leitura porque é o **único** criador de destinos
pais e o único ponto que tem o código IBGE e uma Session ao mesmo tempo.

O payload manda `bool` duro, nunca null: a coluna é `boolean default false`, e todo
destino criado antes desta mudança não tem a chave no canonical.

O Pact agora congela `participates_mtur` — se a regra sumir do Laravel um dia, a
suíte de contrato quebra em vez de a coluna voltar a esvaziar em silêncio.

### 3. Cadastur → `local_businesses` (este commit)

Tabela de referência + importador. **Só lado Brave**, por decisão do operador: não há
endpoint na norteia-api, não há `push_local_business`, nada é enviado.

- `brave/core/models.py::LocalBusiness` + `alembic/versions/0013_local_businesses.py`
- `scripts/cadastur_import.py` — download, parser XLSX em stdlib, upsert idempotente
- 21 testes offline (fixture XLSX sintetizada em teste, nenhum binário no repo)
- `local_businesses` entra em `REFERENCE_TABLES` do reset skill
- runbook em `docs/cadastur-import.md`

Script e não lane, pelo mesmo motivo que aposentou a lane de destino-seed do Mtur:
registro estático trimestral, emissor oficial, chave natural. Não precisa de score,
dedup vetorial nem DLQ.

**LGPD é o ponto central do arquivo.** `cadastur-01` (Guias de Turismo) traz CPF, data
de nascimento, tipo sanguíneo e documento de identificação na mesma planilha. O
importador lê uma **allow-list** de colunas. Uma deny-list vazaria no dia em que o MTur
adicionar uma coluna; uma allow-list não. O teste
`test_a_new_pii_column_added_by_mtur_cannot_leak` existe para quebrar o PR de quem
trocar isso. O nome do guia É importado — é registro público profissional.

Sem dependência nova: XLSX por `zipfile` + `xml.etree`, não `openpyxl`.

## Verificação

```
.venv/bin/python -m pytest tests/unit tests/contract   → exit 0, zero FAILED
.venv/bin/alembic heads                                → 0013 (head)
.venv/bin/ruff check <arquivos novos>                  → All checks passed
(norteia-api) vendor/bin/phpunit --filter test_post_destinations → 8/8 OK
```

## Não feito, de propósito

- **Reparar registros já carimbados com um place_id político.** Eles têm
  `google_enriched=True` e só voltam a ser elegíveis se um operador limpar o marcador.
  Não há caminho de reparo offline; precisa de uma varredura dedicada.
- **`estimated_hotel_capacity`** — medido e descartado no PR #24. As colunas de
  Qtd. Estabelecimentos/Empregos Hospedagem não estão no nosso CSV, e o dado real do
  conjunto de origem para em 2019.
- **Push do Cadastur para a norteia-api** — precisa de endpoint, FormRequest,
  controller, model e Filament resource do lado Laravel. Segunda rodada.
- **Geocodificação dos endereços do Cadastur** — o registro não tem coordenada, então
  `latitude`/`longitude`/`destination_id` ficaram fora da tabela.
- **O import real nunca rodou.** Precisa de `BRAVE_DADOS_GOV_API_KEY` no `.env`, e o
  token que existia foi exposto no transcript de uma sessão anterior — tem que ser
  rotacionado antes.

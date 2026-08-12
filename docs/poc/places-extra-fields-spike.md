# Spike — Google Places preenche as 6 colunas editoriais de `attractions`?

**Data:** 2026-07-31 · **Script:** `scripts/places_fields_spike.py` ·
**Saída mecânica:** `places-extra-fields-spike.auto.md` + `.auto.raw.json`

Pergunta: as colunas `accessibility`, `how_to_get_there`, `tips`, `safety_alerts`,
`local_infrastructure` e `curiosities` da tabela `attractions` (norteia-api) podem
ser preenchidas com dado do Google Places?

Amostra: 15 atrativos brasileiros escolhidos por diversidade de tipo e região
(igreja, praia, parque nacional, museu, gruta, mirante, cachoeira, centro histórico;
capital e interior; 5 macrorregiões). 1 Text Search + 1 Place Details cada,
`languageCode=pt-BR`, `regionCode=BR`. Nenhuma tabela do Postgres lida ou escrita.

---

## Veredito curto

| Coluna | Veredito | Fonte |
|---|---|---|
| `accessibility` | **VIÁVEL** | `accessibilityOptions` — 11/11 dos POIs |
| `local_infrastructure` | **VIÁVEL** | `restroom` + `paymentOptions` + `parkingOptions` + `goodForChildren` — 10/11 |
| `tips` | **VIÁVEL (com ressalvas)** | `reviewSummary` — 8/11, em pt-BR |
| `how_to_get_there` | **PARCIAL** | `addressDescriptor` — 8/11, mas dá referências, não transporte |
| `curiosities` | **SEM FONTE direta** | só o que vaza do `reviewSummary` |
| `safety_alerts` | **SEM FONTE** | nada no Places |

**Duas descobertas que contrariam a documentação do Google, confirmadas com dado real:**

1. `reviewSummary` **funciona em pt-BR no Brasil**. A doc
   (`web-service/place-summaries`) diz que os resumos de IA existem só em inglês,
   nos EUA e na Índia. Falso: 8 dos 15 vieram, com `languageCode: "pt-BR"`.
2. `addressDescriptor` **funciona no Brasil** apesar de estar marcado como
   *experimental* fora da Índia. 8 dos 15 vieram com landmarks e areas.

E uma que confirma a doc: `generativeSummary` e `neighborhoodSummary` vieram
**0/15**. Esses dois realmente não existem para o Brasil.

---

## O fator que decide tudo: tipo do lugar resolvido

A cobertura **não** varia por região nem por popularidade. Varia por uma coisa só:
se o Text Search resolveu para um **POI/estabelecimento** ou para uma **entidade
geográfica**. Entidade geográfica não carrega nenhum desses campos — zero, sempre.

| Atrativo | `types[0]` | acess. | banh. | pgto. | addrDesc | revSum |
|---|---|:-:|:-:|:-:|:-:|:-:|
| Cristo Redentor | `cultural_landmark` | ✅ | · | · | ✅ | · |
| Theatro Municipal RJ | `performing_arts_theater` | ✅ | ✅ | ✅ | ✅ | ✅ |
| MASP | `art_museum` | ✅ | ✅ | ✅ | ✅ | · |
| Elevador Lacerda | `tourist_attraction` | ✅ | ✅ | ✅ | ✅ | · |
| Mirante do Pai Inácio | `tourist_attraction` | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cachoeira da Fumaça | `tourist_attraction` | ✅ | ✅ | ✅ | · | ✅ |
| Igreja S. Francisco (OP) | `church` | ✅ | ✅ | · | ✅ | ✅ |
| Gruta do Lago Azul | `tourist_attraction` | ✅ | ✅ | ✅ | · | ✅ |
| Cataratas do Iguaçu | `nature_preserve` | ✅ | ✅ | ✅ | ✅ | ✅ |
| Teatro Amazonas | `performing_arts_theater` | ✅ | ✅ | ✅ | ✅ | ✅ |
| Lençóis Maranhenses | `national_park` | ✅ | · | ✅ | · | ✅ |
| **Centro Histórico de Paraty** | `sublocality_level_1` | · | · | · | · | · |
| **Convento da Penha** | `neighborhood` | · | · | · | · | · |
| **Praia de Camburi** | `beach` | · | · | · | · | · |
| **Praia dos Carneiros** | `locality` | · | · | · | · | · |

Normalizando sobre os **11 que resolveram para POI**:

| Campo | Presente | Com ao menos um `true` |
|---|---|---|
| `accessibilityOptions` | **11/11 (100%)** | 8/11 (73%) |
| `restroom` | 9/11 (82%) | — |
| `paymentOptions` | 9/11 (82%) | 8/11 |
| `goodForChildren` | 8/11 (73%) | — |
| `addressDescriptor` | 8/11 (73%) | — |
| `reviewSummary` | 8/11 (73%) | — |
| `parkingOptions` | 3/11 (27%) | 2/11 |
| `rating` / `userRatingCount` | 11/11 e 12/15 | — |
| `goodForGroups`, `outdoorSeating`, `priceRange`, `openingDate`, `subDestinations`, `containingPlaces` | 0/15 | — |
| `generativeSummary`, `neighborhoodSummary` | 0/15 | — |

**Implicação prática:** o gargalo não é o Google, é a **resolução do place**. O
`Convento da Penha` existe como POI no Google e mesmo assim o Text Search devolveu
um `neighborhood` — ou seja, o mesmo tipo de mismatch pode estar acontecendo hoje no
`PlacesEnrichmentAgent` (`brave/lanes/atrativos/places_enrichment.py`), onde o guard
é rapidfuzz ≥85 no nome + haversine ≤20 km, sem nenhum filtro por `types`. Uma
entidade geográfica passa nos dois guards com folga.

---

## Coluna por coluna

### `accessibility` — VIÁVEL

`accessibilityOptions` = 4 booleans (`wheelchairAccessibleEntrance`, `...Parking`,
`...Restroom`, `...Seating`). Presente em 100% dos POIs; em 3 deles o objeto vem mas
sem nenhum `true` (falso positivo — o objeto existir não significa dado útil).

Exemplos renderizados do dado real:
- **Cristo Redentor**: "Local com entrada acessível para cadeirantes, sanitário acessível."
- **Theatro Municipal RJ**: "Local com entrada acessível para cadeirantes, sanitário acessível."
- **MASP**: "Local com entrada acessível para cadeirantes, sanitário acessível."

Ressalva de conteúdo: o Google só afirma o positivo. Ausência de `true` não é
"não acessível", é "não sabemos" — o texto gerado não pode dizer que o local **não**
é acessível.

### `local_infrastructure` — VIÁVEL

Composição de `paymentOptions` + `parkingOptions` + `restroom` + `goodForChildren`
(+ `allowsDogs`, 1/15). 10/11 dos POIs produzem alguma frase.

- **Theatro Municipal RJ**: "Estrutura no local: aceita cartão de crédito, aceita cartão
  de débito, aceita pagamento por aproximação, sanitários disponíveis, adequado para crianças."
- **Elevador Lacerda**: "Estrutura no local: aceita cartão de crédito, aceita cartão de
  débito, sanitários disponíveis, adequado para crianças."

Ressalva: o conteúdo é enviesado para comércio (meios de pagamento). Para atrativo
natural, o que sobra costuma ser só "sanitários" + "adequado para crianças" —
tecnicamente correto, editorialmente magro.

### `tips` — VIÁVEL, com 3 ressalvas

`reviewSummary` é o achado do spike. Texto pt-BR, gerado pelo Gemini a partir das
avaliações, e o conteúdo é **literalmente dica de visita**:

- **Lençóis Maranhenses**: "…a melhor época para visitar é de maio a setembro, quando
  as lagoas estão cheias."
- **Igreja S. Francisco de Assis (Ouro Preto)**: "…a opção de comprar um ingresso
  combinado para ter acesso a outras igrejas. Muitos recomendam contratar um guia…"
- **Cachoeira da Fumaça**: "…a desafiadora subida inicial vale a pena… caminho bem
  conservado, gerenciável para caminhantes experientes sem um guia… banheiros limpos
  na entrada, com água disponível para reabastecer garrafas."

Ressalvas, todas verificadas no payload:

1. **Atribuição obrigatória.** O objeto vem com `disclosureText` = "Resumo feito com
   o Gemini" e `flagContentUri`. Publicar o texto sem essa atribuição provavelmente
   viola os termos — confirmar antes de usar.
2. **O idioma escapa.** Cataratas do Iguaçu voltou `languageCode: "en-US"` mesmo com
   `languageCode=pt-BR` na requisição. Qualquer lane precisa checar o `languageCode`
   do retorno e descartar o que não for pt-*.
3. **Voz errada.** É "os visitantes dizem que…", não a voz Norteia. Entra como
   *matéria-prima* para o `TourismCopywriter` (`brave/lanes/atrativos/copywriter.py`),
   não como valor final da coluna. Note que o prompt atual do copywriter **proíbe**
   dado operacional na prosa justamente porque esses dados deveriam viver em campos
   estruturados — `tips` é o campo que estava faltando.

### `how_to_get_there` — PARCIAL

`addressDescriptor` funciona no Brasil (8/11 dos POIs), mas entrega **referências
espaciais**, não instruções de chegada:

- **Cristo Redentor**: "Fica em Cristo Redentor / Santa Teresa. Referências próximas:
  Face Sul Corcovado (~121 m), Restaurante Corcovado (~85 m)."
- **Theatro Municipal RJ**: "Fica em Centro / Fundação Biblioteca Nacional. Referências
  próximas: Carioca / Centro (~216 m), Museu Nacional de Belas Artes (~93 m)."

Compare com o que a API espera de fato (seeder `AttractionSeeder2.php:26`):
"Metrô Linha 1, Estação Cardeal Arcoverde ou General Osório. Ônibus 474, 583 ou 415."
Isso é **modal de transporte + linha**, que o `addressDescriptor` não dá. Ele às
vezes tangencia (o landmark "Carioca / Centro" é uma estação de metrô), mas por
acidente. Preencher a coluna direito exigiria outra fonte (Routes API a partir de um
ponto de referência, ou o próprio copywriter com web_search).

### `curiosities` — SEM FONTE direta

Nenhum campo do Places tem esse propósito. O `reviewSummary` às vezes vaza contexto
histórico ("esculturas intrincadas de Aleijadinho e pinturas de teto de Mestre
Ataíde", "era do ciclo da borracha"), mas é opinião agregada de visitante, não
curiosidade curada — e só nos 73% que têm `reviewSummary`. Fonte real seria o
copywriter com web_search, não o Places.

### `safety_alerts` — SEM FONTE

Zero. O Places não tem campo de alerta de segurança. (`consumerAlert` existe no
schema, mas é sobre atividade suspeita de avaliações do estabelecimento, não sobre
risco ao visitante.) Qualquer preenchimento aqui viria de outra fonte, e é o campo
onde alucinação de LLM tem o pior custo.

---

## Custo

Zero incremental sobre o que a lane já gasta. Billing do Places é pelo **SKU mais
caro presente na field mask, uma cobrança só** (`maps/billing-and-pricing/sku-details`:
*"the request is billed at the highest SKU rate for the fields requested"*). A máscara
de produção (`brave/clients/places.py:54-68`) já pede `reviews` + `editorialSummary`,
que são **Enterprise + Atmosphere** — o teto. Todos os campos deste spike
(`accessibilityOptions` é Pro; `parkingOptions`/`restroom`/`reviewSummary` são
Ent+Atmosphere; `addressDescriptor` é Essentials) entram de carona sem subir o SKU.

Esta rodada: 15 Text Search + 15 Place Details, ambos dentro do free tier de
10k/SKU/mês. Nenhum campo foi rejeitado com 400.

---

## Bloqueio a jusante (para quando for construir)

As 6 colunas **não têm regra** em `IngestAttractionRequest::rules()`
(`norteia-api/app/Http/Requests/Api/Internal/IngestAttractionRequest.php:22-75`) e o
controller usa `$request->validated()`
(`TerritorialIngestController.php:35`). O Brave pode mandar as 6 hoje que a API
descarta **em silêncio, sem erro**. Nenhuma coleta chega ao banco sem um PR no repo
Laravel primeiro.

Extra: `local_infrastructure` e `curiosities` também não têm campo no Filament
(`AttractionForm.php:45-51` só tem 4 dos 6), então nem revisão humana existe hoje.

---

## Recomendações

Fora deste arquivo, de propósito — este documento é medição.
Ver [`proximos-passos-colunas-editoriais.md`](proximos-passos-colunas-editoriais.md).

Nada disso foi implementado. Este spike é só medição.

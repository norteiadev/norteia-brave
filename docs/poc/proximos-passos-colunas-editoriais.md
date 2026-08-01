# Sugestões — as 6 colunas editoriais de `attractions`

**Status: NADA DECIDIDO.** Este arquivo é opinião, para ser discutida. Os fatos medidos ficam
nos relatórios; aqui só o que eu faria com eles.

Relatórios que sustentam cada afirmação:
- [`places-extra-fields-spike.md`](places-extra-fields-spike.md) — Google Places
- [`melhores-destinos-columns-spike.md`](melhores-destinos-columns-spike.md) — Melhores Destinos
- [`fontes-estruturadas-colunas-editoriais.md`](fontes-estruturadas-colunas-editoriais.md) — fontes estruturadas, sem LLM

---

## Quadro de decisão

| Coluna | Fonte que eu usaria | Esforço | LLM? | Confiança |
|---|---|---|---|---|
| `curiosities` | **Wikidata** (P1435 tombamento, P571 fundação, P84 arquiteto) | baixo | não | alta |
| `accessibility` | **Places `accessibilityOptions`** (já pago) | baixo | não | alta, mas cobertura limitada a POI |
| `local_infrastructure` | **Places booleans + OSM amenities em raio** | médio | não | média (raio é ruidoso em capital) |
| `how_to_get_there` | **Melhores Destinos** (LLM) ou OSM (parcial) | alto | sim, para valer | média |
| `tips` | **Places `reviewSummary`** | baixo | não (já vem pronto do Google) | média, com ressalva jurídica |
| `safety_alerts` | — | — | — | **não implementar agora** |

---

## Bloqueio que vem antes de tudo

As 6 colunas **não têm regra** em `IngestAttractionRequest::rules()`
(`norteia-api`) e o controller usa `$request->validated()`. O Brave pode mandar as 6 hoje que
a API **descarta em silêncio, sem erro**.

Nenhuma das sugestões abaixo entrega valor sem um PR no repo Laravel primeiro:
1. adicionar as 6 regras `nullable|string` em `IngestAttractionRequest`;
2. adicionar `local_infrastructure` e `curiosities` ao `AttractionForm` do Filament (hoje só
   4 das 6 têm campo, então nem revisão humana existe);
3. incluir as 6 no payload de `build_push_payload` (`brave/core/mar/service.py`).

Esse PR é pequeno e destrava qualquer caminho. Faria primeiro, independente do resto.

---

## Ordem que eu seguiria

### 1. Filtro por `types` na resolução de place — pré-requisito, não feature

No spike do Places, 4 de 15 atrativos resolveram para **entidade geográfica**
(`locality`, `neighborhood`, `sublocality_level_1`, `beach`) e essas carregam zero campos.
Convento da Penha resolveu para `neighborhood` mesmo existindo como POI.

O `PlacesEnrichmentAgent` hoje casa por rapidfuzz ≥85 + haversine ≤20 km, **sem olhar
`types`** — uma entidade geográfica passa nos dois guards com folga. Ou seja: isso
provavelmente já está degradando o dado que a lane coleta hoje, antes de qualquer coluna nova.

Menor diff da lista e melhora o que já existe. Começaria por aqui.

### 2. Wikidata → `curiosities`

Melhor relação retorno/esforço de todas. SPARQL público, CC0, sem chave, sem custo, sem LLM.
26.318 itens brasileiros com coordenada e designação patrimonial; 11.200 com data de
fundação. Retorno em pt-BR, pronto para template:

> "Bem tombado pelo IPHAN. Construído em 1920."

Casamento por coordenada, ou pela tag `wikidata` que o próprio OSM já carrega.

**Ressalva:** é dado factual seco. Vira `curiosities` decente por template, mas não é uma
curiosidade *narrada*. Se a régua editorial for mais alta que isso, esta fonte não fecha
sozinha.

### 3. `accessibility` + `local_infrastructure` determinísticos

Ambos saem de booleans do Places que **já estamos pagando** (o SKU é cobrado pelo campo mais
caro da máscara, e `reviews`/`editorialSummary` já colocam a chamada no teto
Enterprise + Atmosphere). Template puro, sem LLM, sem risco de alucinação.

Duas regras que eu não abriria mão:
- **Nunca negar.** O Google só afirma o positivo. Ausência de `true` é "não sabemos", não
  "não é acessível". O texto gerado não pode dizer que o local **não** é acessível.
- **OSM entra só como reforço de `local_infrastructure`**, com limiar por tipo de atrativo.
  Contagem em raio mede densidade urbana: "76 restaurantes a 400 m" em Paraty é o centro
  histórico, não o atrativo. Confiável em atrativo isolado, ruído em capital.

OSM **não** entra em `accessibility`: a tag `wheelchair` cobre 3,5% dos atrativos turísticos
(193 de 5.452 em ES+RJ+SP+BA).

### 4. `tips` via `reviewSummary` — barato, mas resolver o jurídico antes

O campo já vem pronto e em pt-BR, e o conteúdo é literalmente dica de visita ("a melhor época
é de maio a setembro", "recomendam contratar um guia"). Já está pago.

Três coisas a resolver antes de publicar:
1. **Atribuição.** Vem com `disclosureText` = "Resumo feito com o Gemini" e `flagContentUri`.
   Publicar sem a atribuição provavelmente viola os termos. **Isso é decisão jurídica, não de
   engenharia** — não implementaria antes da resposta.
2. **Guard de idioma.** Cataratas do Iguaçu voltou `en-US` mesmo com `languageCode=pt-BR`.
   Descartar o que não for `pt-*`.
3. **Voz.** É "os visitantes dizem que…", não a voz Norteia. Ou aceita assim, ou passa pelo
   copywriter — e aí volta a ter LLM.

### 5. `how_to_get_there` — o caso em que eu aceitaria LLM

Nenhuma fonte estruturada dá o que a coluna espera. O `addressDescriptor` do Places dá
referência espacial ("a 93 m do Museu de Belas Artes"), não modal + linha. O OSM dá contagem
de pontos de ônibus no raio, não itinerário.

Quem dá é o Melhores Destinos, em prosa:

> "Como chegar: Estação Trianon-MASP – Metrô Linha 2 Verde."
> "Serviço de vans na portaria, R$ 5, que leva até o Campinho."

Mas isso é extração por LLM sobre texto autoral, e **só ~55% das páginas BR têm o sinal** —
a página mediana do site é um blurb de 2 parágrafos, não uma ficha (só 3% têm estrutura rica).

Formato que faria sentido: **não uma lane nova**, e sim um enriquecedor opcional pendurado
onde o `TourismCopywriter` já roda — extração estruturada com permissão explícita de devolver
`null`. Página blurb devolve null, e isso é o resultado correto.

Custo: ~1 chamada de LLM por atrativo sobre ~1.341 páginas BR. Input curto, sem web_search →
mais barato que o copywriter atual.

**Só faria isso depois dos itens 1-4 estarem no ar.** É o de maior esforço e menor certeza.

### 6. `safety_alerts` — não implementar

Zero fonte estruturada fora de praia. A balneabilidade (CONAMA 274/2000, via INEA/CETESB/
INEMA) é boa e estruturada, mas é **boletim semanal volátil** — modelo de alerta com
validade, não coluna estática. E é a coluna onde errar custa mais caro.

Deixaria `null` até haver volume de praias em Mar que justifique modelar o alerta direito.

---

## Medições pendentes

| O quê | Por quê | Custo |
|---|---|---|
| **TripAdvisor Content API** — 1 chamada em `location/{id}/details` | já temos lane TA e `location_id` por atrativo; a doc é SPA e não confirma se **atrativo** (não hotel) expõe `amenities` | free tier 5.000 chamadas/mês |

**Turismo Acessível / MTur: medido e descartado.** Era a aposta para `accessibility` e não se
sustenta — **103 atrativos** no Brasil inteiro, **84% no Rio de Janeiro**, dataset parado no
**2º trimestre de 2020**, sem coordenadas. Detalhe é ótimo (16 recursos avaliados no Bondinho
do Pão de Açúcar), largura é irrisória. Números completos no relatório de fontes, seção 3.

Isso deixa `accessibility` dependendo **só** do `accessibilityOptions` do Places.

---

## Como tirar a chave da API do `dados.gov.br`

Confirmado no OpenAPI público do portal (`https://dados.gov.br/v3/api-docs`, campo
`components.securitySchemes`):

- **Header:** `chave-api-dados-abertos`
- **Base path correto:** `/dados/api/publico/...` (não `/api/publico/...` — foi o erro da
  minha sonda)
- **Swagger UI:** `https://dados.gov.br/swagger-ui/index.html`

Para o nosso caso serve o **perfil Consumidor**, que é o mais simples:

1. Entrar em `https://dados.gov.br` e fazer login pelo **gov.br**. Conta nível **Prata ou
   Ouro** (verificada) — nível Bronze normalmente não libera.
2. Já logado, abrir **"Minha Conta"**, no lado direito da página. A chave fica ali.
   (Se um dia precisarmos publicar dados, aí sim seria perfil de organização, e a chave sairia
   de **"Tokens de organização"** no dashboard, com usuário Administrador da Organização.)

### Pegadinha: `pagina` é obrigatório

O OpenAPI marca `pagina` como **`required=true`** em `GET /conjuntos-dados` e em
`GET /organizacao`. Sem ele a API não devolve 400 — devolve
`{"Erro na API": "Erro ao executar a consulta"}`, que parece problema de token e não é.
Mesma coisa em `GET /tags`, onde `nome` é obrigatório.

Caminho mais curto — o `id` do endpoint de detalhe aceita o slug, então dá para pular a busca:

```bash
curl -s -H "chave-api-dados-abertos: $CHAVE" \
  "https://dados.gov.br/dados/api/publico/conjuntos-dados/turismo-acessivel" | jq .
```

Busca, com o parâmetro que faltava:

```bash
curl -s -H "chave-api-dados-abertos: $CHAVE" \
  "https://dados.gov.br/dados/api/publico/conjuntos-dados?nomeConjuntoDados=turismo&pagina=1" | jq .
```

Listar os arquivos de um conjunto (o nome do array pode ser `recursos` ou `resources` —
inspecionar com `jq keys` antes):

```bash
curl -s -H "chave-api-dados-abertos: $CHAVE" \
  "https://dados.gov.br/dados/api/publico/conjuntos-dados/<ID>" | jq '.. | .link? // empty'
```

Se ainda falhar, listar sem filtro nenhum para isolar se o problema é o filtro ou a conta:

```bash
curl -s -H "chave-api-dados-abertos: $CHAVE" \
  "https://dados.gov.br/dados/api/publico/conjuntos-dados?pagina=1" | jq '.[0]'
```

**Nota:** a chave costuma ser necessária só para o **catálogo**. O `link` de cada recurso em
geral aponta para um arquivo público (CSV/XLSX) baixável sem autenticação. Ou seja: a chave é
o custo de descobrir a URL, não de baixar os dados.

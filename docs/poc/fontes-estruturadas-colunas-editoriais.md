# Pesquisa — fontes estruturadas (sem LLM) para as 6 colunas editoriais

**Data:** 2026-07-31 · Complementa
[`places-extra-fields-spike.md`](places-extra-fields-spike.md) e
[`melhores-destinos-columns-spike.md`](melhores-destinos-columns-spike.md).

Restrição desta rodada: **sem LLM**. Ou seja, só serve fonte que entrega **campo
estruturado** — nada de prosa que precise de extração. Isso elimina de saída o Melhores
Destinos, o Wikivoyage e qualquer guia editorial.

Cada candidato abaixo foi **probado de verdade**, não só lido. Sondas em
`docs/poc/osm-probe.json`.

---

## Síntese por coluna

| Coluna | Melhor fonte estruturada | Situação |
|---|---|---|
| `accessibility` | Places `accessibilityOptions` (já pago) — **e só ele** | OSM (3,5%), Wikidata (123 registros) e Turismo Acessível/MTur (103 atrativos, 84% no RJ, parado em 2020) **não servem** |
| `local_infrastructure` | Places booleans + **OSM amenities num raio** | resolvido, sem LLM |
| `curiosities` | **Wikidata SPARQL** (P1435 tombamento, P571 fundação, P84 arquiteto) | **melhor achado desta rodada** |
| `how_to_get_there` | **OSM** (ponto de ônibus / estação no raio) + Places `addressDescriptor` | parcial — ninguém dá "linha 474" |
| `safety_alerts` | **Balneabilidade** (CONAMA 274/2000) — só praia | nada para atrativo não-litorâneo |
| `tips` | Places `reviewSummary` | já vem pronto; ver nota abaixo |

**Nota sobre `tips`:** o `reviewSummary` do Places **já é texto gerado por LLM — do lado do
Google**. Consumir o campo pronto não é "envolver LLM" no nosso pipeline: é ler um campo da
resposta. É a única fonte de `tips` que existe sem a gente rodar modelo.

---

## 1. OpenStreetMap / Overpass API — **SIM para `local_infrastructure`, NÃO para acessibilidade**

Sondado com os mesmos 15 atrativos dos spikes anteriores (`docs/poc/osm-probe.json`).

### 1a. Casar o atrativo com o POI do OSM é o elo fraco

A heurística que usei — *o objeto com mais tags num raio de 200 m* — acertou o atrativo em
**5 de 15**:

| Atrativo | POI que o OSM devolveu | acertou? |
|---|---|:-:|
| Cristo Redentor | Cristo Redentor (33 tags, `wheelchair=yes`) | ✅ |
| MASP | Museu de Arte de São Paulo – Ed. Pietro Maria Bardi | ✅ |
| Cataratas do Iguaçu | Iguazú Falls (89 tags) | ✅ |
| Cachoeira da Fumaça | Cachoeira da Fumaça | ✅ |
| Gruta do Lago Azul | Gruta do Lago Azul | ✅ |
| Elevador Lacerda | *Centro Histórico de Salvador* (área que o contém) | ✗ |
| Teatro Amazonas | *Villa Amazônia* (hotel vizinho) | ✗ |
| Igreja S. Francisco (OP) | *Rock in Hostel* | ✗ |
| Praia dos Carneiros | *Carneiros Beach Resort* | ✗ |
| Praia de Camburi | *Hotel Aruan* | ✗ |
| Mirante do Pai Inácio | *Pousada Pai Inácio* | ✗ |
| Centro Histórico de Paraty | *Igreja N. Sra. dos Remédios* (dentro do centro) | ◐ |
| Theatro Municipal RJ | *Carlos Gomes* | ✗ |
| Convento da Penha · Lençóis Maranhenses | nada em 200 m | ✗ |

A falha é da **minha heurística**, não do OSM: "mais tags" puxa o polígono maior ou o hotel
mais bem cadastrado da vizinhança. Um casamento por similaridade de nome (rapidfuzz, como a
lane do Places já faz) subiria esse número — **mas não foi medido**. Trate 5/15 como piso de
uma heurística ruim, não como cobertura real do OSM.

### 1b. Amenities no raio: isso sim é sólido

Independe do casamento do POI (é contagem em volta da coordenada, que já temos):

| Atrativo | estacion. | banheiro | comida | ponto de ônibus |
|---|:-:|:-:|:-:|:-:|
| MASP | 27 | 4 | 34 | 16 |
| Elevador Lacerda | 24 | 2 | 28 | 11 |
| Centro Histórico de Paraty | 3 | 1 | 76 | 0 |
| Theatro Municipal RJ | 1 | 4 | 47 | 29 |
| Convento da Penha | 7 | 0 | 6 | 11 |
| Teatro Amazonas | 3 | 0 | 13 | 10 |
| Cristo Redentor | 2 | 3 | 1 | 0 |
| Gruta do Lago Azul | 1 | 1 | 0 | 0 |
| Mirante do Pai Inácio | 1 | 1 | 2 | 0 |
| Cachoeira da Fumaça · Lençóis | 0 | 0 | 0 | 0 |

Repare que **Convento da Penha tem 7 estacionamentos e 11 pontos de ônibus** mesmo o OSM não
tendo o POI do convento — e é justo o atrativo que o Places perdeu inteiro. A contagem em
raio funciona onde o POI falha.

Ressalva honesta: **contagem em raio mede densidade urbana, não infra do atrativo.** "76
restaurantes a 400 m" em Paraty é o centro histórico, não o atrativo. O sinal é confiável em
atrativo isolado (Gruta do Lago Azul: 1 estacionamento + 1 banheiro *é* a estrutura do lugar)
e ruidoso em capital. Precisa de limiar por tipo de atrativo.

### 1c. Acessibilidade: OSM não serve no Brasil

Cobertura da tag `wheelchair` em `tourism=attraction|museum|viewpoint|artwork|theme_park|zoo|gallery`:

| UF | com `wheelchair` | total | % |
|---|---|---|---|
| ES | 6 | 299 | **2,0%** |
| RJ | 43 | 1.381 | **3,1%** |
| SP | 113 | 3.052 | **3,7%** |
| BA | 31 | 720 | **4,3%** |
| **total** | **193** | **5.452** | **3,5%** |

Nos 15 atrativos, só o Cristo Redentor trouxe `wheelchair=yes` no POI certo. Os outros dois
valores que apareceram (`no`, `limited`) vieram dos POIs errados — um hostel e um resort.
Consistente com os 3,5%.

Custo: zero. Licença **ODbL** — exige atribuição e tem cláusula share-alike sobre banco
derivado; precisa de parecer antes de publicar em produto fechado. Operacional: a API
pública do Overpass **rate-limita agressivo** (peguei 429 e 504 o tempo todo nesta sonda).
Uso em volume pede instância própria ou extrato do Geofabrik.

## 2. Wikidata (SPARQL) — **SIM para `curiosities`**

Endpoint público, resposta JSON, rótulos em pt-BR, sem chave.

Contagens medidas para itens **no Brasil com coordenada** (111.977 no total):

| Propriedade | Itens BR |
|---|---|
| P1435 — designação patrimonial (tombamento) | **26.318** |
| P571 — data de fundação/inauguração | **11.200** |
| P84 — arquiteto | 492 |
| **P2846 — acessibilidade para cadeirante** | **123** ← inútil |

Amostra real do retorno:

```
Cristo Redentor                    bem tombado pelo IPHAN         1920-01-01
Jardim Botânico do Rio de Janeiro  bem tombado pelo IPHAN         1808-06-13
Fernando de Noronha                sítio Ramsar
Edifício Altino Arantes            bem tombado pelo CONDEPHAAT    1939-01-01
```

É exatamente o insumo de `curiosities`, já estruturado e em português: quem tombou, quando
foi fundado, quem projetou. Licença **CC0** — sem obrigação de atribuição. Casamento com o
nosso atrativo sai por coordenada ou pela tag `wikidata` do próprio OSM (que apareceu em 3
dos 8 sondados).

Para acessibilidade, Wikidata é ruído estatístico: 123 itens no país inteiro.

## 3. Turismo Acessível (Ministério do Turismo) — **medido e DESCARTADO**

Era a aposta mais promissora para `accessibility`: programa federal, guia colaborativo, e a
única base brasileira **desenhada** para essa coluna. Baixei os 14 recursos do conjunto
`turismo-acessivel` e medi. Não se sustenta.

| Métrica | Valor |
|---|---|
| Linhas somando os 14 CSVs | 31.686 |
| **Estabelecimentos distintos** (nome+cidade+UF) | **309** |
| **Atrativos** ("Museus e Atrativos Históricos") | **103** |
| + Parques e Zoológicos · Praia | 22 · 8 |
| **UFs com atrativo** | **6** (RJ 87, DF 6, SP 4, PR 2, PE 2, MG 2) |
| Última atualização | **2º Trimestre / 2020** |
| Coordenadas | **nenhuma** (join só por nome + cidade + UF) |

**103 atrativos no Brasil inteiro, 84% deles no Rio de Janeiro, congelado há 6 anos.** Não
move a agulha para uma base que cobre todos os estados.

Saúde dos arquivos, de quebra: o recurso "2º Tri/2019" dá **404** (link morto no catálogo
federal), "4º Tri/2018" vem **vazio**, e os dois arquivos de 2020 são **byte-a-byte
idênticos** (mesmo md5, 79 linhas).

O que é bom é a **profundidade**, não a largura. O formato é longo — uma linha por
(estabelecimento × recurso × avaliação de usuário) — com perguntas granulares e resposta
Sim/Não:

> **Bondinho do Pão de Açúcar** (RJ): 16 recursos avaliados, 12 com "Sim" — elevador ou
> plataforma elevatória, corrimão dos dois lados, portas livres de barreiras, calçadas com
> inclinação aceitável…
> **Museu Oscar Niemeyer** (PR): 8 avaliados, 4 com "Sim" — balcões acessíveis, rebaixamento
> de meio-fio, circulação interna acessível.

100 dos 103 atrativos têm ao menos um "Sim". É bem melhor que o `accessibilityOptions` do
Places em granularidade. Só que existe para 103 lugares.

**Conclusão:** não vira lane. No máximo, um seed pontual se um dia alguém quiser um punhado
de atrativos famosos do Rio com acessibilidade detalhada.

**Nota de acesso (custou uma rodada de erro):** a chave da API serve só para o **catálogo** —
os `link` dos recursos apontam para `dados.turismo.gov.br` e baixam **sem autenticação**. E o
parâmetro `pagina` é obrigatório em `GET /conjuntos-dados`; sem ele a resposta é
`{"Erro na API": "Erro ao executar a consulta"}`, que parece problema de token e não é. O
endpoint de detalhe aceita o slug direto: `/dados/api/publico/conjuntos-dados/turismo-acessivel`.

## 3b. Varredura do catálogo inteiro do `dados.gov.br`

Depois do resultado do Turismo Acessível, varri o catálogo com a chave para não descartar o
portal por causa de um dataset só: **18 termos de busca** (turismo, atrativo, patrimônio,
tombado, unidade de conservação, visitação, cadastur, inventário, acessibilidade, praia,
balneabilidade, parque nacional, museu, cultura, trilha, cachoeira, sítio arqueológico,
turístico) → **190 conjuntos distintos**.

Órgãos com mais resultados: MTur (18), Prefeitura de Fortaleza (17), Estado do RJ (17),
Estado de AL (16), **ICMBio (12)**, FUNAI (9), JBRJ (8).

**Para as 6 colunas de atrativo: nada.** Os candidatos que pareciam promissores, abertos e
verificados:

| Dataset | Atualizado | O que tem de fato | Serve? |
|---|---|---|---|
| ICMBio — Atributos das UCs Federais | 26/02/2026 | nome, ato de criação, área, perímetro, gerência regional, bioma | ✗ administrativo, sem infra de visitação |
| ICMBio — Visitação em UCs Federais | 01/11/2024 | **número** de visitas por UC por ano (2000-2024) | ✗ métrica, não atributo |
| ICMBio — Limites oficiais das UCs | 27/02/2026 | geometria | ◐ só para dizer "está dentro da UC X" |
| ICMBio — Planos de Manejo | 17/11/2025 | documentos | ✗ prosa, exigiria LLM |
| MTur — Mapa do Turismo Brasileiro | 2019 (meta 2021) | UF, região turística, município, categoria | ✗ para atrativo (mas ver 3c) |

Os outros 185 são administrativos (contratos, terceirizados, balanços patrimoniais, acervo
bibliográfico) ou de escopo municipal/estadual sem relação com atrativo.

**Conclusão: o `dados.gov.br` não contribui para `accessibility`, `how_to_get_there`, `tips`,
`safety_alerts`, `local_infrastructure` nem `curiosities`.**

## 3c. …mas o catálogo tem duas coisas para OUTRAS tabelas

Fora do escopo das 6 colunas, e por isso fácil de perder — mas medido e real:

### `destinations` — Categorização dos Municípios Turísticos (MTur)

3.286 linhas, e a chave de junção é a **nossa**:

```
Macro Região | UF | Região | Município | Código Município | Qtd. Empregos Hospedagem |
Qtd. Estabelecimentos Hospedagem | Demanda Internacional | Demanda Doméstica | Categoria
Norte | AC | Caminhos do Pacífico | Assis Brasil | 1200054 | 2 | 1 | 0 | 27.381 | D
```

`Código Município` é **código IBGE** — casa direto com o nosso `municipio_ibge`, sem fuzzy
match, sem LLM. E preenche colunas de `destinations` que hoje estão **vazias**:

- `participates_mtur` ← presença no Mapa do Turismo Brasileiro
- `estimated_hotel_capacity` ← Qtd. Estabelecimentos / Empregos Hospedagem
- a categoria **A–E** é um sinal pronto de maturidade turística do destino

**Ressalva:** o dado real mais recente é de **2019** (o recurso rotulado "2019" é XLS servido
com formato declarado CSV — parseia, mas não com `csv`). Categoria de município muda devagar,
mas é preciso checar se existe edição mais nova fora do `dados.gov.br`.

### `local_businesses` — Cadastur / Prestadores de Serviços Turísticos (MTur)

**Atualizado em 02/07/2026** — é o dataset mais fresco de tudo que apareceu. Vários conjuntos
separados: agências de turismo, guias de turismo, acampamentos turísticos, casas de
espetáculos, empreendimentos de apoio ao turismo.

Não toca nas 6 colunas, mas é cadastro oficial de prestador de serviço turístico, com
atualização corrente. Se a tabela `local_businesses` for entrar em pauta, começa aqui.

## 4. Balneabilidade — **SIM para `safety_alerts`, só em praia**

Dado estruturado, padronizado pela **Resolução CONAMA 274/2000**: ponto, status
`própria`/`imprópria`, data. Fontes:

- **INEA/RJ** — 291 pontos em 197 praias, boletim semanal
- **CETESB/SP**, **INEMA/BA** (esse acessível via **Brasil.IO**, precisa token gratuito — deu
  401 sem ele)
- **App Praia Limpa / MMA** — dicionário de dados publicado, cobertura nacional

Cobre exatamente o vazio dos outros spikes: praia é onde Places e MD mais falham. Mas é dado
**semanal e volátil** — não é atributo estático do atrativo, é um boletim. Encaixa melhor
como alerta com validade do que como valor fixo de coluna.

⚠️ O agregador **"Praia em Dia" está fora do ar** (404 no Vercel). Integrar direto com cada
órgão estadual, não com ele.

## 5. Descartados (com o motivo medido)

| Fonte | Por quê |
|---|---|
| **Foursquare OS Places** | 22 atributos core: nome, categoria, endereço, coord, telefone, site, social. Amenities/acessibilidade só nos tiers **Pro/Premium** (pagos). Nada para as 6 colunas. |
| **Overture Maps Places** | 64M POIs, mas os atributos são nome/categoria/contato/endereço/marca. Sem acessibilidade nem amenity. Ainda por cima a taxonomia muda: `categories` fica deprecada e sai no release de **set/2026**. |
| **Wheelmap** | Virou SPA; a API REST antiga não responde mais como API. E o dado é o próprio `wheelchair` do OSM — que medimos em **2%** no Brasil. Redundante. |
| **Google Routes API** | Dá rota de transporte público estruturada, mas **exige uma origem**. Serve para calcular "como chegar" sob demanda a partir do usuário, não para popular uma coluna estática. |
| **IPHAN (portal/SICG)** | `sicg.iphan.gov.br/consultar/bem` → 404; página de dados abertos → 404. O dado de tombamento chega mais limpo pelo Wikidata (26.318 itens). |

## 6. TripAdvisor Content API — **pendente de 1 chamada**

Free tier de **5.000 chamadas/mês**. `location/{id}/details` devolve nome, endereço, rating,
subratings, ranking, awards, categorias. A documentação é SPA e não deu para confirmar por
HTTP se atrativo (não hotel) expõe `amenities`/acessibilidade — `amenities` é documentado
para hospedagem.

Vale a pena porque **já temos lane TripAdvisor** e um `location_id` por atrativo: é uma
chamada para saber. Se expuser amenities de atrativo, é a via mais barata para
`local_infrastructure`.

---

## Recomendações

Fora deste arquivo, de propósito — este documento é medição.
Ver [`proximos-passos-colunas-editoriais.md`](proximos-passos-colunas-editoriais.md)
(inclui o passo a passo para tirar a chave da API do `dados.gov.br`).

Nada foi implementado. Esta rodada é pesquisa + sondagem.

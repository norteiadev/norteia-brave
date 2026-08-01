# Spike — Melhores Destinos preenche as 6 colunas editoriais de `attractions`?

**Data:** 2026-07-31 · **Script:** `scripts/melhores_destinos_columns_spike.py`
**Saídas mecânicas:** `.auto.md`, `.auto.raw.json`, `.sample34.json`
**Antecessor:** `melhores-destinos-atrativo-descricao.md` (POC de 2026-07-09, avaliou o site
como fonte de *descrição*; a lane chegou a ser construída e foi removida no commit `2132080`)

Pergunta desta rodada, diferente da anterior: o `guia.melhoresdestinos.com.br` tem o conteúdo
para preencher `accessibility`, `how_to_get_there`, `tips`, `safety_alerts`,
`local_infrastructure` e `curiosities` — as colunas que o Google Places
([`places-extra-fields-spike.md`](places-extra-fields-spike.md)) não preenche?

---

## Veredito curto

**Sim, mas para 2 colunas e sempre via LLM.**

| Coluna | Veredito | Sinal em 34 páginas BR aleatórias | Após descontar falso positivo |
|---|---|---|---|
| `how_to_get_there` | **VIÁVEL** | 21/34 (62%) | ~55% |
| `local_infrastructure` | **VIÁVEL** | 13/34 (38%) | ~35% |
| `tips` | **FRACO** | 7/34 (21%) | ~20% |
| `curiosities` | **FRACO** | 5/34 (15%) | ~15% |
| `safety_alerts` | **FRACO** | 8/34 (24%) | ~10% |
| `accessibility` | **FRACO** | 5/34 (15%) | ~8% |

Nada disso é campo estruturado. **Tudo é prosa autoral corrida** — extrair as 6 colunas
exige uma passada de LLM por página. Não existe um `Como chegar:` parseável de forma
confiável; existe um parágrafo que às vezes diz como chegar.

---

## 1. Coleta: continua trivial (reconferido hoje)

| Item | Estado em 2026-07-31 |
|---|---|
| `robots.txt` | Aberto. Só `Disallow: /admin`, `/admin/`, `/assets/*.svg`. **Páginas de atrativo liberadas.** |
| `/termos-de-uso` | **404** (segue sem Termos de Uso, igual ao POC de julho) |
| Render | HTML server-rendered, `httpx` pega tudo, sem JS |
| Sitemap | `/sitemap.xml`, 8293 `<loc>`, **4470 páginas de atrativo `-l`** |
| Container do conteúdo | `<div class="post-body">` … até `class="author-card"` — recorte limpo e estável |
| Autoria | Byline explícito por página (Camille Panzera, Monique Renne, Jéssica Weber, Bruna Scirea) |

Mudança leve desde julho: eram 8317 locs / 4525 `-l`, agora 8293 / 4470 — o acervo encolheu
um pouco. Estrutura idêntica.

## 2. O universo BR é ~1.341 páginas, não 4.470

O POC anterior deixou isso em aberto ("filtrar por breadcrumb p/ só Brasil"). Medido agora
com 60 páginas aleatórias: **18/60 = 30% são do Brasil**. O resto é internacional (Tel Aviv,
Grand Canyon, Sydney…).

**~1.341 páginas de atrativo brasileiro.** É um número bem menor do que o POC antigo dava a
entender, e reposiciona a fonte: é um complemento curado, não uma base.

## 3. O achado que decide: a página mediana é um blurb

Sobre 34 páginas BR aleatórias:

- **mediana: 2.163 caracteres** de corpo (min 758, máx 9.731)
- **1 de 34 (3%)** tem estrutura rica (≥3 `<h2>` de conteúdo)
- A maioria tem **2 `<h2>`**, que são o título e o nome do autor — ou seja, **zero seção
  interna**: são 2 a 3 parágrafos corridos

Existem dois arquétipos, e a diferença é brutal:

**Rica** — `Convento da Penha` (4.684 chars, 7 seções: *História · Visual · Museu e Sala dos
Milagres · Festa da Penha · **Acesso ao Convento da Penha***) ou `Gruta do Lago Azul`
(5.160 chars, seções *Como é a visita · **Preço** · **Onde fica***).

**Blurb** — `Elevador Lacerda` (2.405 chars), `Teatro Amazonas` (2.306), `Praia de Carneiros`
(2.282), `Igreja da Pampulha` (2.068), `Paraty Mirim` (1.239). Nenhuma seção.

Mesmo na amostra dos 15 atrativos famosos que escolhi a dedo, só 2 eram ricas. No aleatório,
1 em 34. **A taxa de preenchimento das colunas é, no limite, a taxa de página rica** — nas
blurbs o dado aparece por acaso, dentro do parágrafo.

## 4. O que dá para tirar, com trecho real

### `how_to_get_there` — VIÁVEL (o melhor caso da fonte)

> **Praia de Barequeçaba**: "Como chegar à Praia de Barequeçaba — A Praia de Barequeçaba está
> distante 6,5 km do centro de São Sebastião (sentido sul) e 21 km da Praia de Maresias."

> **Convento da Penha**: "As opções para chegar ao Campinho são: subir a pé pela Ladeira da
> Penitência, um percurso de 457 metros… subir de carro e estacionar no Campinho… na portaria
> existe um serviço de vans que custa R$ 5."

> **MASP**: "Como chegar: Estação Trianon-MASP – Metrô Linha 2 Verde."

Esse último é exatamente o formato que a coluna espera (modal + linha) e que o
`addressDescriptor` do Places **não** dá. Aqui o MD ganha do Places de forma limpa.

### `local_infrastructure` — VIÁVEL

> **Cachoeira Cascatinha**: "Na entrada do complexo, há uma estrutura simples de
> estacionamento, bar e banheiro, além de hospedagem tipo hostel e camping."

> **Praia do Guaecá**: "há poucos quiosques e restaurantes na praia, o que afasta um pouco o
> público que prefere praias com ampla infraestrutura."

Cobre justamente o tipo de atrativo onde o Places entrega nada (praia, cachoeira) — e o
vocabulário é o certo (quiosque, estacionamento, banheiro, camping).

### `accessibility` — FRACO, mas o acerto é bom

Quando acerta, acerta em cheio:

> **Museu Casa da Xilogravura**: "…é adaptado para receber cadeirantes."
> **Convento da Penha**: "…é necessário subir vários degraus para conhecê-lo internamente."

Mas metade dos matches é falso positivo: "acessível" no site quase sempre quer dizer *fácil*,
não *acessível a PCD* ("uma das atrações mais acessíveis de Bonito" = rápida e barata).
Coverage real ~8%.

### `safety_alerts` — FRACO, e é onde o falso positivo mais engana

Reais, e bons:
> **Praia do Guaecá**: "Apesar da presença de guarda-vidas, é sempre bom ter cuidado com a
> formação de correntes, que podem tornar o mar perigoso."

Falsos, e frequentes: "chama a atenção", "merece toda a sua atenção", "sem muito perigo".
Dos 8 matches, ~3 são alerta de verdade. Um extrator ingênuo encheria a coluna de lixo.

### `curiosities` e `tips` — FRACOS

`curiosities` acerta em atrativo histórico ("obra de Aleijadinho", "fundada em 1590",
"século XVIII") e some no resto. `tips` só tem substância nas páginas ricas
("Dica: no período com chuvas, a trilha tem vários trechos alagados"; "vale a pena fazer o
passeio guiado, R$ 20 inteira").

## 5. Complementaridade com o Google Places — o argumento real da fonte

Rodei os **mesmos 15 atrativos** do spike do Places. Onde o Places falhou (resolveu para
entidade geográfica e devolveu zero campos), o MD tem página:

| Atrativo | Places | Melhores Destinos |
|---|---|---|
| Convento da Penha | ✗ resolveu `neighborhood` | ✅ página rica, com seção "Acesso" |
| Praia de Camburi / Carneiros | ✗ `beach` / `locality` | ✅ com estrutura de praia e alerta de mar |
| Centro Histórico de Paraty | ✗ `sublocality_level_1` | ◐ só "Paraty Mirim" (uma praia) |
| Cristo Redentor | ✅ POI completo | ❌ **não existe no site** |
| Cataratas do Iguaçu | ✅ POI completo | ❌ **não existe no site** |
| Lençóis Maranhenses | ✅ POI completo | ❌ **não existe no site** |

As duas fontes falham em conjuntos **disjuntos**. Places é forte em POI comercial com perfil
gerenciado; MD é forte em praia, cachoeira e atrativo difuso do interior. Mas a cobertura do
MD é curada e cheia de buracos — não ter Cristo Redentor nem Cataratas do Iguaçu é um
lembrete de que não dá para tratá-lo como base.

## 6. Ressalvas técnicas encontradas

1. **A cidade do MD continua não confiável.** "Praia de Carneiros" vem com breadcrumb
   `Brasil > Nordeste > Pernambuco > **Recife** > Praia de Carneiros` — Carneiros é em
   Tamandaré. Confirma o achado antigo: o código de cidade é interno do site, não é IBGE, e
   erra. Município tem que sair de coordenada via `resolve_municipio_national`, nunca do MD.
2. **Widget de afiliado vaza para dentro do `post-body`.** "Excursão à praia do Gunga de
   ônibus" apareceu como se fosse conteúdo em 2 páginas distintas. Qualquer parser precisa
   filtrar esses blocos, senão vira "como chegar" falso.
3. **Casar atrativo por slug é frágil.** Neste spike, `sao-francisco-de-assis` resolveu para
   a Igreja da Pampulha (BH), não Ouro Preto; `camburi` para Ubatuba/SP, não Vitória/ES. Sem
   coordenada no site, o pareamento MD↔nosso atrativo tem que passar por Places/coordenada.
4. **Sem GPS, sem horário, sem telefone, sem rating** — inalterado desde o POC de julho.

## 7. Legal

Continua **baixo risco para fatos** (nome, cidade, "fica a 6,5 km do centro", "tem
estacionamento") — fato não é obra protegida (Lei 9.610/98 art. 8º, e o Brasil não tem
direito *sui generis* de banco de dados).

Continua **alto risco para o texto**. Toda página tem byline de autor identificado. As 6
colunas são prosa autoral: copiar o parágrafo de "Acesso ao Convento da Penha" para
`how_to_get_there` é reprodução de obra. **O uso tem que ser: LLM lê o parágrafo, extrai os
fatos, reescreve na voz Norteia.** Imagens (`imgmd.net`) seguem fora de questão.

`/termos-de-uso` ainda 404 — vale re-probar antes de qualquer varredura em volume, e manter
rate-limit (o spike usou 1 req/s e UA identificável).

---

## Recomendações

Fora deste arquivo, de propósito — este documento é medição.
Ver [`proximos-passos-colunas-editoriais.md`](proximos-passos-colunas-editoriais.md).

Nada disso foi implementado. Este spike é só medição.

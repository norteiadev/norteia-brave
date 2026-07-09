# POC — Guia Melhores Destinos como fonte de *descrição* de atrativos

**Status: VIÁVEL COM RESSALVA** (recon real contra `guia.melhoresdestinos.com.br`, 2026-07-09).
Avaliação feita **sem nenhuma mudança de código** — mede a ideia contra o pipeline como ele existe hoje.

Relatório visual: https://claude.ai/code/artifact/e3ad1860-3c5a-438e-9d8e-a4b0273ce3dc

---

## 1. Objetivo

Duas perguntas:

1. **Coleta** — dá para raspar dados de atrativos deste site? (POC original)
2. **Descrição** — o conteúdo editorial de cada página pode ser passado a um LLM como
   contexto para **reescrever a descrição na voz da Norteia**, e essa descrição contar
   como sinal de **completude** no score de confiabilidade?

Regra do exercício: avaliar viabilidade **sem alterar código**.

---

## 2. Estrutura de dados do site (POC de coleta)

| Item | Achado |
|------|--------|
| robots.txt | Aberto (`Disallow:` vazio). Sitemap público. |
| Termos de Uso | **Não existem** (404). Só há Política de Privacidade. |
| Render | HTML server-rendered — `curl` pega o conteúdo, sem JS. |
| Sitemap | `/sitemap.xml`, arquivo plano, 8317 `<loc>`, **4525 páginas de atrativo** (`-l`). Universo global — filtrar por breadcrumb p/ só Brasil. |

### Gramática da URL

```
<slug>-<CÓDIGO_CIDADE>-<ID_ATRATIVO>-l.html
igreja-nossa-senhora-d-ajuda-  54  -  249  -l.html
```

- **`54` = código de cidade do site** — estável por destino (os 10 atrativos de Arraial
  d'Ajuda compartilham `-54-`). **Não é IBGE** e é **nível distrito** (Arraial d'Ajuda é
  distrito de Porto Seguro).
- **`249` = id global único do atrativo** (esparso-sequencial, faixa 5–6653).
- **Não é enumerável/walkável**: o triple `slug + código + id` é validado junto; qualquer
  divergência → 404 duro, sem redirect. Descoberta = filtrar o sitemap por
  `-<código>-\d+-l\.html`.

### Campos extraíveis por página `-l`

| Campo | Disp. | Observação |
|-------|:---:|------------|
| Nome | ✅ | `og:title` / h1 |
| **Descrição editorial** | ✅ | Prosa PT-BR autoral — **é o insumo desta POC** |
| UF + região | ✅ | JSON-LD BreadcrumbList |
| Cidade | ◐ | Nome interno, nível distrito (não IBGE) |
| Categoria | ◐ | Só breadcrumb geográfico, sem taxonomia de tipo |
| Imagens | ✅ | CDN imgmd.net (não re-hospedar) |
| `modified_time` | ✅ | Data de edição do CMS |
| **GPS / coordenadas** | ❌ | **Ausente em 100% das páginas** (7 amostradas + verificação adversarial) |
| Rating / reviews / telefone / horário | ❌ | Ausentes |

**Conclusão de coleta:** o site serve bem como **lane de descoberta/seed**, nunca como
fonte canônica isolada (sem GPS, sem corroboração → score isolado ≈ 35 → sempre DLQ). Deve
alimentar o enriquecimento via Google Places (place_id + coords + reviews) e o
`resolve_municipio_national(coords)` (`brave/domains/tripadvisor/ibge.py:210`) para o
município IBGE real.

---

## 3. Avaliação da ideia: descrição → `completude`

### 3.1 Como `completude` é calculado hoje (código atual)

`compute_score` (`brave/core/score/engine.py`) é função pura: recebe `completude_value`
(0–100) já pronto e aplica peso **20%**. Quem calcula o `completude_value` a montante define
tudo — e **existem duas mecânicas diferentes no repo:**

**Lane MTur/Atrativo — `_compute_completude` (`brave/domains/mtur/discovery.py:74`)** — esta
é a que vale pro atrativo. **NÃO é soma de campos; é uma função de degraus (step):**

```
nome + tipo + posicionamento + municipio_ibge + place_id  → 75.0   (teto do LLM/discovery)
nome + tipo + posicionamento                              → 50.0
qualquer outra coisa                                      → 25.0
```

Fatos que isso revela (corrige a leitura anterior baseada no docstring de `dtos.py`, que está
desatualizado):

- **`posicionamento` = a descrição, e ele JÁ ESTÁ na completude e JÁ É OBRIGATÓRIO**
  (`min_length=10`). Sem posicionamento você fica no degrau 25; com ele (+nome+tipo) pula pra
  50 = **+25 de completude = +5,0 no score final**. É um dos maiores alavancas de completude
  que existe.
- **Essa alavanca já está puxada.** O `posicionamento` de hoje é gerado pelo DiscoveryAgent
  (LLM a partir do Google Places). O atrativo canônico **já nasce com descrição**.
- **É degrau + presença-binária.** Ter uma descrição melhor/curada não muda o degrau — o teste
  é só `len(posicionamento) >= 10`. Qualidade/voz **não pontua**.

**Lane TripAdvisor — `completude_from_fields` (`brave/domains/tripadvisor/scoring.py:125`)** —
aqui sim é fração de **10 campos** × 10 pts, e `description` é um deles → preencher onde estava
vazio = **+10 completude = +2,0 final**. Também presença-binária.

### 3.2 Veredito de viabilidade (sem mudar código)

| Dimensão | Viável hoje? | Detalhe |
|----------|:---:|---------|
| **Passar conteúdo da página a um LLM** | ✅ | HTML limpo, descrição rica presente. Infra de LLM já existe (instructor Mode.Tools). |
| **Gerar descrição Norteia e usar como `posicionamento`** | ✅ | Ganho de **produto/UX**: troca a descrição gerada de Places por uma editorial curada. |
| **Elevar completude do atrativo por "ter descrição"** | ❌ | A alavanca "tem posicionamento → +25/+50" **já está puxada** hoje. Trocar a fonte da descrição **não cria degrau novo** — completude fica igual (25/50/75). |
| **Elevar completude pela *qualidade* da descrição** | ❌ | A função é degrau + `len>=10`. Descrição curada de 500 chars pontua **igual** a um stub de 10 chars. |
| **Elevar completude na lane TripAdvisor** | ✅ (+2,0) | Só se o atrativo for pontuado por `completude_from_fields` e o campo `description` estava vazio. |

**Resumo honesto:** o insight central que o usuário busca — *"adicionar descrição → subir
completude"* — **já é realidade e já está saturado** no atrativo (posicionamento obrigatório,
degrau 50/75 já atingido pela descrição gerada de Places). O que a fonte Melhores Destinos
adiciona é **qualidade de conteúdo** (descrição editorial curada > descrição sintética de
Places), o que é um **ganho de produto, não de score** — porque completude mede **presença**,
não qualidade. Para "aumentar a completude depois de adicionar a descrição" seria preciso
**mudar código** (§3.3).

### 3.3 O que exigiria mudança de código (fora do escopo desta avaliação, sinalizado)

Para o fluxo proposto (§6) de fato **subir** a completude:

- Adicionar um **degrau novo** em `_compute_completude` que distinga "descrição editorial
  curada presente" das demais — ex.: `... + descricao_editorial → 85.0`. Sem isso, a descrição
  MD não move o número.
- Ou tornar a completude **sensível à qualidade** (tamanho real, presença de diferencial, tom).
  Hoje é degrau/presença-binária — a voz Norteia nunca é recompensada.
- Adicionar o campo canônico `description`/`descricao_editorial` ao modelo do atrativo, à
  migração e ao **contrato Pact com a norteia-api** (cross-repo).

---

## 4. Risco legal do rewrite

- **Fatos** (nome, cidade, UF, categoria) — não protegíveis (Lei 9.610/98 art. 8). Uso livre.
- **Descrição editorial** — prosa autoral (bylines de jornalistas), **protegida**. Passar o
  texto a um LLM para **reescrever** gera obra **derivada**: risco menor que cópia verbatim,
  mas **não nulo** se a saída for paráfrase frase-a-frase do original.
- **Mitigação:** usar a página MD como **fundamentação factual** e gerar descrição
  **genuinamente original** (não paráfrase); **não persistir** o texto-fonte MD como canônico
  (contexto transitório do LLM apenas); registrar proveniência (`source='melhores_destinos'`);
  **não re-hospedar imagens** imgmd.net.

---

## 5. Pendências antes de executar

1. **Definir a voz/tom da Norteia** — bloqueia execução (não a viabilidade). Sem isso o
   "reescrever na voz da Norteia" não tem alvo.
2. **Decidir qual lógica de completude o atrativo usa** — determina se a descrição rende
   +2,0 ou 0. Se o objetivo é que a descrição conte no score, isso vira uma decisão de código.
3. **Escolher o modelo do rewrite** — geração de PT-BR de qualidade favorece Claude Sonnet;
   custo/volume favorece DeepSeek. Decisão de custo, não de viabilidade.
4. **Medir hit-rate do Places** numa fatia (ex.: BA) — quantos seeds MD chegam ao Mar, já que
   o enriquecimento (coords/reviews) é o que realmente cruza o gate, não a descrição.

---

## 6. Fluxo proposto — avaliação

Fluxo desenhado pelo usuário:

```
qualquer origem → Nascente
   → Rio aplica completude "como está" + novo campo `description`
   → busca a descrição em guia.melhoresdestinos.com.br
   → passa a descrição a um LLM com a voz/tom da Norteia
   → grava a descrição gerada num novo campo `description` do atrativo
   → aumenta a completude após adicionar a descrição
```

**Coerência arquitetural: OK.** Encaixa no medalhão Nascente→Rio→Mar. Mas **não é "sem
mudança de código"** — é uma capacidade nova. "Sem mudança de código" vale só para *esta
avaliação*. Superfície de código real:

| Passo do fluxo | É código novo? | Nota |
|----------------|:---:|------|
| Nascente recebe qualquer origem | ✅ existe | Store append-only já tolera qualquer `source`. |
| Buscar descrição no site MD | 🔴 novo | Novo cliente I/O + **matching atrativo↔página MD** (ver gotcha A). |
| LLM reescreve na voz Norteia | 🔴 novo | Nova chamada + prompt de voz. Voz **ainda indefinida**. |
| Novo campo `description` no atrativo | 🔴 novo | DTO + migração + **contrato Pact com norteia-api** (cross-repo, PHP). |
| Completude conta o novo campo | 🔴 novo | Novo degrau em `_compute_completude` (§3.3). |
| Re-score após enriquecer | 🔴 novo | Ver gotcha C (ordenação). |

### Gotchas que decidem se vale a pena

**A. Cobertura do matching limita tudo.** O atrativo em Rio veio de "qualquer origem"
(Places/TA), ancorado em place_id/coordenadas. O site MD **não tem coordenadas** — casar o
atrativo com a página `-l` certa é fuzzy por **nome + município**, o mesmo problema do dedup.
E o MD só tem **~4525 atrativos curados**: atrativo sem página MD **não recebe descrição**.
Logo o enriquecimento é **cobertura parcial** — só uma fração dos atrativos ganha a descrição
editorial. Medir hit-rate numa fatia (ex.: BA) antes de tudo.

**B. A alavanca de completude já está puxada (§3.1).** O atrativo já nasce com
`posicionamento` (descrição sintética de Places) que já o coloca no degrau 50/75. Trocar essa
descrição pela versão MD/Norteia **não sobe o degrau** — é upgrade de qualidade, não de score.
Para subir, precisa de um degrau novo "descrição editorial curada" (§3.3). E aí decide-se:
o campo MD **substitui** `posicionamento` ou é um **segundo** campo? Se é o mesmo, não há novo
degrau natural; se é novo, o Pact/norteia-api precisa aceitá-lo.

**C. Ordenação = re-score num pipeline append-only.** O fluxo pede completude "como está"
primeiro e "aumenta depois" — ou seja, pontua duas vezes. Duas opções:
1. **Enriquecer ANTES do 1º score** (descrição presente já no primeiro cálculo) → um score só,
   mais simples.
2. **Score → enriquece → re-score** → como o medalhão é imutável/append-only, o re-score é um
   novo `score_version`/record-event, não um UPDATE. Viável, mas é um estágio a mais.
   Recomendado (1) salvo se houver razão para materializar o "antes/depois".

**D. Magnitude.** Mesmo com o degrau novo, completude pesa 20%. Um salto de degrau
(ex.: 75→85) = +10 completude = **+2,0 final**. Modesto — **não cruza o gate (80) sozinho**;
o que cruza o gate continua sendo coords+reviews do Places. A descrição é polimento de score +
ganho de produto, não alavanca de promoção ao Mar.

### Veredito do fluxo

**Viável e arquiteturalmente são**, mas: (1) exige o conjunto de mudanças de código acima —
não é config; (2) a parte "aumenta a completude" **não acontece de graça** porque a alavanca
descrição já está saturada e completude é presença-binária; (3) o valor real e imediato é
**qualidade de conteúdo** (descrição editorial > sintética), com impacto de score modesto e
condicionado a um degrau novo. Antes de construir: medir hit-rate do matching MD (gotcha A) e
definir a voz Norteia.

---

## 7. Onde a descrição morre hoje (trace Nascente→frontend)

**Pergunta:** existe campo de descrição no atrativo? No frontend não aparece. **Resposta:
existe no Nascente raw, mas é descartado no Rio — nunca chega a Mar / norteia-api / painel.**

O atrativo **já é gerado com descrição** (`posicionamento`, obrigatória, LLM do DiscoveryAgent),
mas ela serve só de insumo de completude e some no caminho:

| Estágio | Descrição? | Evidência |
|---------|:---:|-----------|
| Gerada (DiscoveryAgent LLM) | ✅ | `mtur/discovery.py` — `posicionamento` obrigatório, min 10 chars |
| Alimenta completude | ✅ | `_compute_completude` lê `result.posicionamento` (em memória, pré-store) |
| **Nascente (raw JSONB)** | ✅ | `discovery.py:306` — `payload.canonical.posicionamento` persistido |
| **Rio (normalized)** | ❌ **DROP** | `rio/routing.py:150-197` monta `normalized` a dedo: name/address/lat/lon + 5 `*_value` + município + place_id_cache/parent/contact + labels. **`posicionamento` não é copiado.** |
| Mar (canonical) | ❌ | `mar/service.py:101` faz `{k:v for k,v in normalized ...}` → herda o drop |
| Push norteia-api | ❌ | `build_push_payload` deriva do canonical do Mar → sem descrição |
| Dashboard card | ❌ | projeção allow-list (`dashboard/lib/painel-data.ts`) → sem descrição |

**Causa raiz:** o `normalized` do Rio (`routing.py:150`) é **cherry-pick**, não passa o payload
inteiro. Tudo a jusante (Mar, push, card) copia de `normalized`, então o que não entra ali
não existe pra frente. (`tipo` sofre o mesmo — só sobrevive via `label_entity`.)

**Consequência pro fluxo da §6:** mesmo a descrição que **já existe** não é surfaçada. O
trabalho mínimo pra ver qualquer descrição no painel (ou pra a ideia MD funcionar) é
**carregar `posicionamento` no `normalized` do Rio** — a partir daí flui sozinho pra
Mar→push→card. É ~1 linha, mas **é código** (e, pro push, revisar o contrato Pact com a
norteia-api antes de mudar a shape).

---

## 8. Avaliação: "remover posicionamento do Nascente e mover pro Rio"

**Proposta do usuário:** tirar a geração de `posicionamento` do fluxo Nascente (discovery),
mover pro Rio, e ali fazer: fetch MD → LLM refina + voz Norteia → persiste no atrativo.

**Veredito: a meta é certa, mas a forma literal fere 3 invariantes e cria regressão.** As 5
travas:

| # | Trava | Evidência |
|:-:|-------|-----------|
| **A** | **Completude é calculada no Nascente; o Rio só LÊ.** Tirar posicionamento do Nascente derruba completude 75→25 no score → atrativo vai pra DLQ **antes** do enriquecimento. Mover posicionamento pro Rio **obriga** mover o cálculo de completude junto (recompute pós-enriquecimento). 2 mudanças acopladas. | `rio/routing.py:157` = `float(payload.get("completude_value", 0.0))` — passthrough, sem recompute. `_compute_completude` roda no `discovery.py`. |
| **B** | **Import posture D-18.** `core/rio` nunca importa domínio/task/cliente externo. Fetch MD + LLM = source + I/O externo → **não pode viver em `routing.py`**. Rio tem que ficar puro/offline-testável. | Kernel não importa domains (D-18). |
| **C** | **Suíte 100% offline + latência.** LLM+HTTP inline no caminho do Rio quebra o teste offline e mete rede/custo/falha por-registro no core. Os enriquecedores existentes são tasks FSM **separadas** do discovery justamente por isso. | ContactFinderAgent / SignalAgent = FSM pós-discovery. |
| **D** | **Regressão de cobertura (maior risco).** Discovery hoje **garante** posicionamento sintético (Places) → todo atrativo ≥50 completude. MD cobre só ~4525 curados, matching fuzzy sem-GPS. Atrativo sem página MD ficaria **sem descrição** → completude 25 → DLQ. Tirar o floor = pior pra maioria. | §2 (sem GPS, ~4525) + gotcha A (§6). |
| **E** | **Continua sem ganho de completude.** posicionamento já satura o degrau (50/75); trocar a fonte não sobe o número sem **degrau novo** (código). | §3.1. |

### Forma que encaixa (mesma meta, sem brigar com o código)

```
discovery (Nascente): MANTÉM posicionamento sintético como FLOOR   ← garante completude + fallback
   → novo AGENTE DE ENRIQUECIMENTO (espelha ContactFinder/Signal, task FSM pós-discovery):
        match atrativo ↔ página MD (fuzzy nome+município)
        → fetch MD → LLM voz Norteia → grava `descricao_editorial` (campo novo)
        → recomputa completude (degrau novo se quiser crédito de score)
        → re-score via supersessão (D-03, mar/service.py:134 — infra já existe)
   → carrega o campo novo no `normalized` do Rio → flui Mar→push→card (§7)
```

**Não** "remover do Nascente → fazer no Rio". É **manter o floor no Nascente + adicionar um
agente de enriquecimento pós-discovery** (fora do Rio) que faz MD→LLM→campo novo→re-score.
Essa forma: mantém Rio puro, teste offline intacto, **degrada com graça** quando não há match
MD (preserva o floor), reusa o padrão de agente existente, e o re-score por supersessão já é
infra pronta. Continua sendo código novo — a avaliação diz que *essa* forma é viável/segura;
a forma literal proposta introduz regressão + fere D-18/offline.

---

## Referências de código

- `brave/core/score/engine.py` — score puro; `completude` peso 20%.
- **`brave/domains/mtur/discovery.py:74` — `_compute_completude`, a mecânica REAL do atrativo: degraus 25/50/75, `posicionamento` já contado e obrigatório.**
- `brave/domains/mtur/dtos.py:63` — `posicionamento` (obrigatório, min 10). ⚠️ O docstring de `completude_value` (`:100`) lista campos desatualizados (nome/coords/telefone/horários/tipo) — **não bate** com `_compute_completude`; confiar no código, não no docstring.
- `brave/domains/tripadvisor/scoring.py:125` — `completude_from_fields`, 10 campos, `description` incluída, presença-binária (fração × cap).
- `brave/domains/tripadvisor/ibge.py:210` — `resolve_municipio_national(coords)`, resolvedor coord-only já pronto para lane sem GPS.

# Google AI Pro / Gemini como substituto do Anthropic no Brave

**Data:** 2026-08-19 · **Escopo:** avaliação de viabilidade, sem alteração de código.
**POC pronta para rodar:** `scripts/poc/gemini_copywriter_poc.py` (ver §6).

---

## 1. Veredito em três linhas

1. **A assinatura Google AI Pro NÃO zera o custo do Brave.** Ela é cota de **AI Studio
   (Playground/Build UI)**, não acesso programático à API. Um serviço 24/7 não consegue
   consumi-la. O que ela entrega de aproveitável é **US$ 10/mês de crédito GenAI & Cloud**
   (Developer Program Premium, incluso no AI Pro) — abate a fatura, não a elimina.
2. **Anthropic hoje tem um único consumidor vivo: o copywriter de atrativos** (§7.1). Com a
   lane WhatsApp parada, migrar `generate()` resolve 100% da fatura Anthropic.
3. Duas rotas, e a POC decide qual:
   - **Free tier (custo zero real)** — só funciona sem grounding, compensado por contexto
     determinístico (Places/Wikidata/OSM/MD). Limitado pela vazão e com o prompt editorial
     entrando no treino do Google (§4.1).
   - **Gemini Flash pago com grounding** — 2x a 5x mais barato que Sonnet, sem ressalva de
     treino, sem mexer na arquitetura de contexto (§5).

---

## 2. O que o Google AI Pro cobre de fato

Citação literal da doc oficial ([Google AI plans](https://ai.google.dev/gemini-api/docs/google-ai-plans)):

> "Google AI Pro and Ultra subscriptions enable developers to unlock paid models and higher
> rate limits **in the Google AI Studio Playground** and features like the Code Assistant in
> Build mode for vibe coding."

E, na seção *Gemini API usage* da mesma página:

> "When daily baseline subscription quotas are exhausted in AI Studio, you can continue your
> workflows using a Gemini API key **with Cloud Billing enabled for pay-per-request usage**."
>
> "for production deployments at scale, Google Cloud projects, the Google Cloud Starter Tier,
> and Gemini API keys are the recommended path."

Ou seja: a cota da assinatura vive na interface. A API key é uma trilha separada
(free tier ou Cloud Billing).

**O único benefício de API que a assinatura carrega**
([Developer Program Plans & Pricing](https://developers.google.com/program/plans-and-pricing)):

| Benefício (Premium = incluso no AI Pro, US$ 19,99/mês) | Valor |
|---|---|
| GenAI & Cloud monthly credit | **US$ 10/mês** (aplicável à Gemini API com Cloud Billing) |
| Gemini CLI | 1.500 req/usuário/dia (Standard: 1.000) |

O Gemini CLI é agente interativo com OAuth de usuário — usá-lo como backend de pipeline é
off-label, frágil e fora do espírito dos termos. **Não considerar como transporte de produção.**

---

## 3. Onde o Anthropic entra no Brave hoje

| Local | Uso | Acoplamento ao Anthropic |
|---|---|---|
| `brave/clients/llm.py:311` `generate()` | única porta de saída Sonnet | `AsyncAnthropic` nativo, `pause_turn` loop, tabela de preço Sonnet (linhas 52-72) |
| `brave/lanes/atrativos/copywriter.py:36` | `WEB_SEARCH_TOOL` (`web_search_20250305`) | formato de tool **específico da Anthropic**, passado via `tools=` |
| `brave/shared/whatsapp/agent.py:487` | conversa PT-BR (`generate`) | usa a mesma porta |
| `brave/lanes/atrativos/copy_batch.py` (691 LoC) | Batch API | `from anthropic import (...)`, `client.messages.batches.*` — **acoplamento profundo** |
| `brave/clients/base.py:55` | `LLMClientProtocol.generate` | assinatura já genérica; só o default `model="claude-sonnet-4-5"` e o `tools` opaco vazam |

**Boa notícia:** o seam existe. `LLMClientProtocol` é `typing.Protocol` estrutural — um
`GeminiLLMClient` que implemente `extract()` + `generate()` entra sem tocar em nenhum caller.
**Má notícia:** `copy_batch.py` não passa pelo Protocol; fala com o SDK Anthropic direto.

---

## 4. Free tier da Gemini API — o que sobra de bloqueio (WhatsApp fora)

**Correção de escopo (2026-08-19):** a lane WhatsApp **não está em uso**. Com ela fora, o
bloqueio de PII/LGPD deixa de valer e o free tier volta a ser candidato sério. Sobram dois.

| Bloqueio | Evidência | Impacto no Brave |
|---|---|---|
| **Grounding com Google Search indisponível no free tier** para todos os modelos Gemini 3.x | tabela de preços: `Grounding with Google Search — Free Tier: Not available**` (`**` = "can be tested in Google AI Studio") | **Este é o bloqueio que resta de verdade.** O copywriter perde a busca web e cai no contexto do Places apenas → descrição sensorial curta, sem fato histórico. É o fallback degradado que o próprio prompt já prevê. Mensurável com `--no-search` (§6). |
| **Limites (RPM/TPM/RPD) não são mais publicados** | a página de rate limits agora manda "View your active rate limits in AI Studio" — a tabela estática por modelo saiu do ar | Impossível dimensionar varredura Brasil-inteiro no papel; só medindo com a key. É o gargalo de vazão do free tier, não o de custo. |
| ~~Dados usados para treinar + revisão humana~~ (degradado a *ressalva*) | [Termos](https://ai.google.dev/gemini-api/terms): "human reviewers may read, annotate, and process your API input and output. **Do not submit sensitive, confidential, or personal information to the Unpaid Services.**" | Sem WhatsApp, o que trafega é dado público de atrativo. Ressalva que sobra: o **prompt de sistema** (voz editorial da Norteia) e as descrições geradas — IP do produto — entram no treino do Google. Decisão de negócio, não impedimento técnico. |

### 4.1 O caminho que realmente zera

Se `--no-search` reprovar na qualidade, ainda existe uma rota para custo zero que não
depende do grounding: **substituir a busca web por contexto determinístico** que os spikes
anteriores já mapearam — Places (`editorialSummary` + reviews, já no fluxo), Wikidata
(curiosities), OSM (infraestrutura/acesso) e Melhores Destinos. O modelo deixa de precisar
buscar porque o fato chega pronto no prompt. Aí:

- tokens: **US$ 0** no free tier
- busca: **US$ 0** (não há)
- custo total da lane de descrição: **zero**, com o preço sendo mais engenharia de contexto
  e mais chamadas às fontes estruturadas (que já existem e são gratuitas).

Essa é a única configuração em que "zerar" é literal. Vale medir na POC antes de assumir a
migração de fornecedor pago.

---

## 5. A conta (paga) — quanto realmente cai

Perfil medido da descrição atual (memória do projeto: **US$ 0,09–0,12/atrativo**, sempre
2 buscas): ~20k tokens de entrada, ~800 de saída, 2 web searches.

| Modelo | Tokens | Busca | **Total/atrativo** | vs. Sonnet |
|---|---|---|---|---|
| **claude-sonnet-4-5** (hoje) | $0,072 | 2 × $0,010 = $0,020 | **$0,092** | — |
| gemini-3.7-flash + grounding (dentro dos 5.000 buscas/mês grátis) | $0,018 | $0 | **$0,018** | **5,1x mais barato** |
| gemini-3.7-flash + grounding (passando do free) | $0,018 | 2 × $0,014 = $0,028 | **$0,046** | 2,0x |
| gemini-3.5-flash-lite + grounding (dentro do free) | $0,008 | $0 | **$0,008** | 11,5x |
| gemini-3.5-flash-lite + grounding (passando do free) | $0,008 | $0,028 | **$0,036** | 2,6x |
| gemini-3.7-flash **Batch** (-50% tokens) + grounding pago | $0,009 | $0,028 | **$0,037** | 2,5x |
| gemini-3.7-flash **a partir de 01/01/2027** (preço dobra) | $0,036 | $0,028 | **$0,064** | 1,4x |

Pontos que mudam a leitura:

- **A busca é o piso, e o Google é mais caro por busca que a Anthropic** ($14/1k vs $10/1k).
  Só os **5.000 grounded searches/mês grátis** (compartilhados entre todos os modelos Gemini 3.x)
  fazem a economia grande — dão ~**2.500 descrições/mês** com 2 buscas cada. Passou disso, a
  economia cai para ~2x.
- **Os preços do Gemini 3.7/3.6 Flash são promocionais até 31/12/2026 e dobram em 01/01/2027.**
  Qualquer plano de custo precisa datar essa virada.
- **O crédito de US$ 10/mês do AI Pro** cobre ~555 descrições/mês no cenário mais barato
  ou ~217 no cenário com busca paga. É abatimento, não isenção.

---

## 6. POC — como medir antes de decidir

Script: `scripts/poc/gemini_copywriter_poc.py`. Read-only, sem DB/Redis/Celery, importa o
`COPYWRITER_SYSTEM` e o `_build_context` **reais** de produção, então o prompt é idêntico.
Roda 2 atrativos (um famoso, um obscuro — onde modelo fraco alucina).

```bash
export GEMINI_API_KEY=...          # https://aistudio.google.com/apikey

# 1. Gemini com grounding, custo medido
.venv/bin/python scripts/poc/gemini_copywriter_poc.py

# 2. Head-to-head contra o Sonnet atual (usa BRAVE_LLM_ANTHROPIC_API_KEY do .env)
set -a; . ./.env; set +a
.venv/bin/python scripts/poc/gemini_copywriter_poc.py --with-sonnet

# 3. Forma exata do free tier (sem grounding) — mede a perda de qualidade
.venv/bin/python scripts/poc/gemini_copywriter_poc.py --no-search

# 4. Modelo barato
.venv/bin/python scripts/poc/gemini_copywriter_poc.py --model gemini-3.5-flash-lite

# 5. A outra metade da migração: instructor Mode.TOOLS no endpoint OpenAI-compat
.venv/bin/python scripts/poc/gemini_copywriter_poc.py --extract
```

Ele imprime: texto gerado, tokens in/out, nº de buscas, queries emitidas, custo USD por
atrativo e a razão Sonnet/Gemini.

**Portões de decisão:**

| Pergunta | Reprova se |
|---|---|
| A prosa segue a voz Norteia, sem travessão, sem clichê, sem dado operacional? | O Gemini vazar horário/preço/telefone na prosa — o prompt proíbe e o pipeline depende disso |
| No atrativo obscuro, o Gemini inventa fato? | Qualquer alucinação factual — a lane existe para não mentir |
| `--no-search` ainda é publicável? | Se sim, o free tier vira opção; se não, free tier está fora |
| `--extract` valida o schema? | Se falhar, a lane de extração fica no DeepSeek e a migração é só do `generate()` |

---

## 7. Custo de migração, se a POC aprovar

| Item | Esforço | Nota |
|---|---|---|
| `GeminiLLMClient` implementando `LLMClientProtocol` | médio | `extract()` via endpoint OpenAI-compat + instructor; `generate()` via `generateContent` |
| Tradução do `tools=` | pequeno | `WEB_SEARCH_TOOL` (Anthropic) → `{"google_search": {}}`; o `copywriter.py` só precisa deixar o client escolher |
| Contabilidade de custo | pequeno | tabela de preço nova + `thoughtsTokenCount` conta como saída no Gemini 3.x |
| `copy_batch.py` (Batch API) | **grande** | 691 LoC presos ao `client.messages.batches.*`; a Batch da Gemini tem outra forma (arquivo JSONL + job). Já está atrás de flag desligada — **deixar por último** |
| Lane WhatsApp (`agent.py`) | **não migrar** | parada; migrar quando/se voltar, e nunca no free tier (PII) |

---

## 7.1 Mapa real de uso de LLM (com WhatsApp parado)

| Call site | Método | Provider hoje | Estado |
|---|---|---|---|
| `copywriter.py:164` ← `places_enrichment.py:235` | `generate()` + `web_search` | **Anthropic Sonnet 4.5** | ativo — TA atrativos |
| `copy_batch.py` | Batch `generate` | **Anthropic Sonnet 4.5** | flag OFF, nunca rodou em prod |
| `discovery_agent.py:238,434` | `extract()` → `AtrativoResult` | DeepSeek via OpenRouter | ativo |
| `number_discovery.py:77` | `extract()` | DeepSeek via OpenRouter | parado (outreach WhatsApp) |
| `whatsapp/agent.py:394,487` | `extract()` + `generate()` | DeepSeek + Sonnet | parado |

Leitura: **Anthropic hoje tem um único consumidor vivo — o copywriter.** Todo o resto que
está de pé usa DeepSeek. Ou seja, migrar o `generate()` resolve 100% da fatura Anthropic;
dobrar o `extract()` para Gemini Flash-Lite é bônus opcional em cima do DeepSeek (que já é
barato, mas não é zero).

---

## 8. Recomendação

1. **Não** contar com o AI Pro para zerar custo — ele não expõe API. Aproveitar apenas o
   crédito de US$ 10/mês (exige projeto GCP com Cloud Billing ligado).
2. Rodar a POC (§6) com os três cenários: `--with-sonnet` (head-to-head), `--no-search`
   (forma do free tier) e `--extract` (paridade da lane de extração).
3. Decidir pelo resultado do `--no-search`:
   - **passou** → free tier + contexto determinístico (§4.1): custo **zero** de LLM no Brave,
     limitado pela vazão (RPD) e com a ressalva de treino sobre o prompt editorial.
   - **não passou** → `gemini-3.7-flash` **pago** com grounding, dentro dos 5.000 searches/mês.
     Economia ~2x a 5x, sem ressalva de treino (tier pago não treina).
4. Extração (`discovery_agent`): candidata direta ao free tier — não usa grounding, não tem
   PII. Migrar depois do copywriter, medindo antes com `--extract`.
5. Marcar no calendário: **01/01/2027** os preços Gemini 3.6/3.7 Flash dobram — reavaliar lá.

---

## 9. Resultados medidos (POC executada em 2026-08-19, key própria)

### 9.1 Grounding no free tier — confirmado ao vivo

| chamada | resultado |
|---|---|
| `gemini-3.5-flash-lite` **sem** `google_search` | ✅ 200 |
| `gemini-3.5-flash-lite` **com** `google_search` | ❌ **429 RESOURCE_EXHAUSTED** |
| `gemini-3.7-flash` **com** `google_search` | ❌ **429 RESOURCE_EXHAUSTED** |
| `gemini-2.5-flash` | ❌ 404 — "no longer available to new users" |

A doc dizia "Not available" e a API confirma: **no free tier, grounding é 429 imediato.**

### 9.2 Duas armadilhas operacionais achadas na prática

1. **Formato novo de key (`AQ.` prefix, 53 chars) exige header `x-goog-api-key`.** Passando
   como `?key=`, a API responde **429 enganoso** ("exceeded your quota") em vez de 401/403.
   Custou uma rodada inteira do POC até isolar. Já corrigido no script.
2. **`.env` linha 42:** o token Sanctum (`BRAVE_NORTEIA_API_TOKEN=1|...`) não está entre
   aspas, e o `|` faz o `. ./.env` tentar executar o resto como comando. Erra e segue, mas a
   variável fica vazia. Corrigir para `BRAVE_NORTEIA_API_TOKEN='1|...'`.

### 9.3 Custo medido por descrição

Preço listado = o que custaria no tier pago. **No free tier tudo isso é US$ 0.**

| Configuração | in / out | Custo/atrativo | vs Sonnet |
|---|---|---|---|
| claude-sonnet-4-5 + web_search (produção hoje) | 12.539 / 708 | **$0,0582** | — |
| gemini-3.6-flash, sem busca | 638 / 1.946 | $0,0039 | 15x |
| **gemini-3.5-flash-lite, sem busca** | 610 / 344 | **$0,00104** | **56x** |
| gemini-3.5-flash-lite + fatos determinísticos (§4.1) | 762 / 318 | $0,00102 | 57x |

Nota: o Sonnet medido gastou **1 busca por atrativo**, não 2 — a memória do projeto
(`copywriter-cost-measured`) precisa ser corrigida. Custo real hoje ≈ **$58/mil descrições**.

### 9.4 Qualidade — o que decide

**Sem busca e só com o contexto do Places**, o modelo escreve bem, mas o atrativo obscuro
fica **factualmente vazio**: na Cachoeira da Fumaça o Gemini não produziu um único dado
verificável (o Sonnet, com busca, trouxe 144 m, parque de 1984, rio Braço Norte Direito,
Corredor Ecológico 2002, fauna). O `gemini-3.6-flash` ainda inventou "saguis entre os
galhos" no Convento. **Esse é o cenário majoritário numa varredura Brasil-inteiro.**

**Com fatos determinísticos injetados (`--enriched`)**, o quadro vira: o
`gemini-3.5-flash-lite` usou **6 de 6 fatos, fielmente, nos dois atrativos, sem inventar
nenhum**. A §4.1 está validada empiricamente — o modelo não precisa buscar se o fato chegar
pronto.

Defeito de estilo a corrigir no prompt: o flash-lite escreve números **por extenso**
("cento e quarenta e quatro metros", "mil novecentos e oitenta e quatro"). Uma linha de
guard resolve.

### 9.5 Disponibilidade do free tier (6 pings por modelo)

| modelo | sucesso | latência mediana |
|---|---|---|
| `gemini-3.7-flash` | **3/6** (3× 503 UNAVAILABLE) | 2,2 s |
| `gemini-3.6-flash` | 6/6 | **19,7 s** |
| **`gemini-3.5-flash-lite`** | **6/6** | **0,8 s** |

Para um serviço 24/7 isso elege o `flash-lite`: é o único estável **e** rápido. O 3.7-flash
no free tier cai metade das vezes; o 3.6-flash responde sempre, mas 20 s por descrição não
escala numa varredura nacional.

### 9.6 Extração (`discovery_agent`)

`instructor` com `Mode.TOOLS` contra o endpoint OpenAI-compat da Gemini: **✅ funciona**,
schema validado de primeira. A lane de extração é portável do DeepSeek para o free tier
sem reescrever nada além do client.

---

## 10. Veredito final

**A rota de custo zero existe e foi medida.** Configuração:

> `gemini-3.5-flash-lite` no free tier, **sem grounding**, alimentado por um bloco de fatos
> determinísticos (Places + Wikidata + OSM + Melhores Destinos — todos já mapeados em spikes
> anteriores e todos gratuitos).

- Custo de LLM da lane de descrição: **US$ 0** (hoje: ~$58/mil descrições no Sonnet).
- Qualidade: equivalente em fidelidade factual **desde que a camada de fatos exista**. Sem
  ela, o atrativo obscuro sai vazio — e é a maioria.
- Limite real: **vazão (RPD do free tier, não publicado)** e a ressalva de que prompt e
  saída entram no treino do Google.

**O trabalho não é trocar de modelo. É construir a camada de fatos determinísticos** — a
troca do client é a parte fácil e o `LLMClientProtocol` já a acomoda.

Se essa camada não for prioridade agora, o plano B continua válido: `gemini-3.5-flash-lite`
**pago** com grounding, ~$0,036/atrativo (1,6x mais barato que o Sonnet e sem ressalva de
treino) — mas aí o ganho é modesto e não justifica sozinho a migração.

---

## 11. De onde vem o custo da busca — e por que um browser agent não resolve

Pergunta levantada: usar algo como
[`vercel-labs/agent-browser`](https://github.com/vercel-labs/agent-browser) no lugar do
`web_search` cortaria o custo?

### 11.1 Decomposição do custo medido do Sonnet

O prompt "puro" (contexto do Places, sem busca) mede **~600 tokens** — sabemos disso porque
o mesmo prompt rodou no Gemini sem busca (in=638 / in=581). O Sonnet com busca mediu
in=11.279 / in=13.799. A diferença é resultado de busca injetado como input.

| componente | $/atrativo | % da conta |
|---|---|---|
| prompt (contexto Places) | $0,0018 | 3% |
| **resultados de busca injetados (~11.900 tok @ $3/M)** | **$0,0358** | **61%** |
| saída (708 tok @ $15/M) | $0,0106 | 18% |
| **taxa do `web_search` ($10/1.000)** | **$0,0100** | **17%** |
| **total** | **$0,0582** | 100% |

**A taxa é 17%. O caro é o que a busca despeja no prompt.** Total atribuível à busca: 79%.

### 11.2 O que o `agent-browser` é, e o que ele resolve

CLI Rust de automação de Chrome (`open`, `snapshot`, `click`, `fill`, `read`, `screenshot`),
com daemon próprio e modo `chat` opcional via Vercel AI Gateway. É um **fetcher**, não um
motor de busca.

| | efeito |
|---|---|
| taxa de $10/1.000 buscas (17%) | ✅ elimina |
| tokens injetados (61%) | ❌ **tende a piorar** — página HTML lida inteira é maior que os snippets que o `web_search` já resume |
| "qual URL ler?" | ❌ não resolve — ainda exige um Brave Search API / Google CSE por cima |
| custo operacional | ❌ adiciona Chrome por atrativo numa varredura nacional (segundos + RAM), quando o repo já tem scraper httpx (TA GraphQL + DataDome, Melhores Destinos) |

Trocar `web_search` por browser sem uma etapa de compressão troca $0,010 de taxa por
*mais* tokens de input. Só compensa com um passo de "página → fatos" antes de injetar — e é
**esse passo**, não o browser, que gera a economia.

### 11.3 O teste que dispensa os dois (medido)

`scripts/poc/wikifacts_probe.py` — sem key, sem LLM, sem browser, sobre o httpx que já
existe. Alvo: os fatos que o `web_search` do Sonnet efetivamente produziu.

| fonte | tokens no prompt | fatos-alvo recuperados | custo |
|---|---|---|---|
| `web_search` (hoje) | ~11.900 | 8/8 (é a fonte) | $0,0358 |
| **Wikipedia — Convento da Penha** | **960** | **6/6** (1558, Pedro Palácios, 154 m, IPHAN, 1943, rococó) | **$0** |
| **Wikipedia — Cachoeira da Fumaça** | **408** | **5/5** (144 m, 1984, Braço Norte, Itapemirim, lontra) | **$0** |
| Wikidata (estruturado, sem texto) | ~30 | `P2048 height=144`, `P625` coord, `P1435` tombamento, `P571` inception | **$0** |

**~17x menos tokens, os mesmos fatos, custo zero de rede.** Projeção da lane:

| configuração | $/atrativo |
|---|---|
| Sonnet + `web_search` (hoje) | $0,0582 |
| Sonnet + contexto Wikipedia/Wikidata | ~$0,0145 (4x) |
| **flash-lite free + contexto Wikipedia/Wikidata** | **$0** |

### 11.4 Duas ressalvas achadas na medição

1. **O primeiro resultado da busca da Wikipedia não é confiável.** Para "Cachoeira da Fumaça
   Alegre" ela devolve *Alegre (Espírito Santo)* — o município — antes do atrativo. Mesmo
   modo de falha do `resolve_municipio` first-match já registrado. Desambiguar por
   coordenada (`P625` do Wikidata × lat/lng do Places) antes de usar.
2. **Fontes discordam e é preciso escolher uma.** O artigo diz obras iniciadas em **1558**;
   o Wikidata registra `inception = 1568`. A camada de fatos precisa de precedência
   explícita e de registrar a origem de cada fato — não pode empilhar as duas no prompt.

### 11.5 Conclusão

O `agent-browser` é uma boa ferramenta para o problema errado aqui: ele ataca os 17% e
agrava os 61%. Para esta lane, a fonte de fato certa é **estruturada e gratuita**
(Wikipedia/Wikidata + Places + OSM), não navegada. Reserve automação de browser para fonte
que só existe em HTML atrás de JS — e mesmo aí, o `httpx` do repo já cobre os casos atuais.

---

## 12. Por que Sonnet e não Haiku? (medido)

### 12.1 A resposta histórica: ninguém escolheu Sonnet para o copywriter

O Sonnet foi decidido no planejamento para **a conversa do WhatsApp**, não para descrições:

> `.planning/PROJECT.md:78` — "LLM split: backend (extraction/scoring/desmembramento) =
> **DeepSeek paid via OpenRouter** […]. Conversational (WhatsApp) = **Claude Sonnet 4.5**."

O `TourismCopywriter` nasceu depois e reusou o mesmo `llm_client.generate()`, **herdando o
slug default**. O comentário que justifica a escolha em `brave/config/settings.py:430` —
*"A Sonnet slug — the server-side web_search tool runs there"* — é racionalização
pós-fato e **está factualmente errado: o Haiku 4.5 suporta `web_search`**, comprovado ao
vivo abaixo.

### 12.2 Haiku 4.5 medido — economiza 24%, não 3x

Preço por token é 3x menor ($1/$5 contra $3/$15), mas o consumo não é:

| | in / out | buscas | $/atrativo |
|---|---|---|---|
| claude-sonnet-4-5 (atual) | 12.539 / 708 | **1** | **$0,0582** |
| claude-haiku-4-5 | 19.969 / 885 | **2** | **$0,0444** (−24%) |

O Haiku **buscou o dobro** e puxou ~60% mais tokens de input. Decomposto: input $0,0200 +
output $0,0044 + **taxa de busca $0,0200**. A taxa fixa de $0,01/busca vira **45% da conta
do Haiku** — quanto mais barato o token, mais a taxa domina.

**Qualidade reprova antes do custo.** O Haiku acertou fatos (154 m, 1558, IPHAN 1943,
José Fernandes Pereira, 200 peças/19 mármores; 144 m, 1984, Braço Norte Direito), mas
entregou português quebrado — *"cappela"*, *"agua"* sem acento, *"Desce sobre você uma
neblina que úmida e fresca"*, *"séculos de fé tejida em ponto"* — e clichês que o
`COPYWRITER_SYSTEM` proíbe explicitamente (*"majestosa"*, *"maravilha natural"*). Também
divergiu do Sonnet em datas (cedro "1874-1879" e altar "remodelado em 1910" contra "altar
rococó de 1800").

### 12.3 O caminho Anthropic mais moderno é 2,7x PIOR

O repo usa `web_search_20250305`, a variante básica. A doc descreve `web_search_20260209`
com **dynamic filtering** — "Claude instead writes and runs code that filters the results
first, so only relevant content reaches the context window" — atacando na teoria justamente
os 61% de tokens injetados. Exige Sonnet 4.6+ / Opus 4.6+ (fora do Sonnet 4.5 atual e do
Haiku 4.5). Medido:

| config | in / out | buscas | $/atrativo |
|---|---|---|---|
| sonnet-4-5 + `web_search_20250305` (atual) | 12.539 / 708 | 1 | $0,0582 |
| **sonnet-4-6 + `web_search_20260209`** | **34.539 / 1.584** | **3** | **$0,1574 (+170%)** |

O filtro roda dentro de code execution e **o overhead da execução entra no contexto** —
mais que anulou o ganho, num prompt que não é search-heavy o bastante para amortizá-lo. E a
saída quebrou o contrato do prompt: preâmbulo ("Tenho contexto suficiente para escrever com
precisão"), separador markdown `---`, "Aqui está a descrição editorial da Norteia:", o
clichê "majestosa" e um trecho copiado da fonte.

### 12.4 Placar consolidado — mesma tarefa, mesmo prompt, mesmos 2 atrativos

| configuração | $/atrativo | vs atual | qualidade |
|---|---|---|---|
| sonnet-4-6 + web_search novo | $0,1574 | +170% | ❌ preâmbulo, markdown, clichê |
| **sonnet-4-5 + web_search (atual)** | **$0,0582** | — | ✅ melhor dos Anthropic |
| haiku-4-5 + web_search | $0,0444 | −24% | ❌ português quebrado, clichês |
| gemini-3.5-flash-lite + fatos determinísticos (pago) | $0,00102 | **−98%** | ✅ fiel, 0 invenções |
| **gemini-3.5-flash-lite free tier + fatos** | **$0** | **−100%** | ✅ |

**Conclusão:** dentro da Anthropic, o Sonnet 4.5 já é a melhor opção — trocar de modelo lá
dentro rende 24% de economia com prosa pior, ou 170% de aumento. O ganho de ordem de
grandeza não está em trocar o modelo; está em **trocar a fonte de fato** (§11.3), que é o
que torna um modelo pequeno e grátis suficiente.

---

## 13. `phukon/duckduckgo_search` substitui o `web_search`? (medido)

Avaliação de [github.com/phukon/duckduckgo_search](https://github.com/phukon/duckduckgo_search).

### 13.1 O que é

Pacote **npm / TypeScript** (`@phukon/duckduckgo-search`) que raspa os endpoints não oficiais
`html.duckduckgo.com/html/` e `lite.duckduckgo.com/lite/`. Não existe API pública do
DuckDuckGo para busca web — não é um cliente de API, é um scraper. A própria biblioteca
documenta o modo de falha, exportando `RatelimitError`: *"Rate limited or CAPTCHA — wait and
retry"*.

Primeiro atrito, antes de qualquer medição: **o collector é Python**. Usar isto exigiria um
sidecar Node ou trocar pelo equivalente Python (`ddgs`) — que raspa exatamente os mesmos
endpoints e herda os mesmos problemas.

### 13.2 Medição (endpoints direto por httpx — sem instalar o pacote)

O pacote é um wrapper HTTP fino; medir o endpoint mede a mesma coisa sem introduzir uma
dependência não auditada no projeto.

| requisição | resultado |
|---|---|
| `lite` POST (1ª) | HTTP 200, markup de resultado presente ✅ |
| `html` GET (2ª) | HTTP 200, markup de resultado presente ✅ |
| `lite` GET (3ª) | **HTTP 202 + CAPTCHA** — *"Unfortunately, bots use DuckDuckGo too. Please complete the following challenge… Select all squares containing a duck"* |
| 12 consultas seguintes | **12/12 bloqueadas** (HTTP 202) |
| retry em t=0s / 60s / 120s | **3/3 ainda bloqueadas** |

**Funcionou por cerca de meia dúzia de requisições de um único IP, e depois entrou em
CAPTCHA persistente — ainda bloqueado 2 minutos depois.** Uma varredura Brasil-inteiro
precisa de centenas de milhares.

Contraste no mesmo IP, sem pausa entre chamadas: **API da Wikipedia, 12 chamadas seguidas,
zero bloqueio** — é API pública documentada, com política de User-Agent, feita para uso
programático.

### 13.3 Mesmo se funcionasse, resolveria pouco

| | efeito |
|---|---|
| taxa de $10/1.000 buscas (17% da conta) | ✅ elimina |
| tokens injetados (61%) | ⚠️ **depende** — snippets são pequenos (~300 tokens), mas rasos: título + 2 linhas. Para bater os fatos que o `web_search` entrega seria preciso buscar as páginas, e aí os tokens voltam |
| "qual URL ler" | ✅ resolve — mas a Wikipedia já tem `list=search` própria, grátis e legal (usada em `scripts/poc/wikifacts_probe.py`) |
| operação 24/7 | ❌ CAPTCHA, rotação de UA/proxy, parser que quebra quando o HTML do DDG muda |
| termos de uso | ❌ raspagem automatizada dos endpoints do DDG; o CAPTCHA **é** o DDG aplicando a regra. Rodar isso em produção contra os ToS de um terceiro é risco jurídico documentável, exatamente o que a constraint de compliance do projeto manda evitar |

### 13.4 Veredito

**Não.** Falha antes da discussão de custo: bloqueado em produção depois de meia dúzia de
consultas, ecossistema errado (npm num serviço Python), e contra os termos do DDG. Se um dia
for preciso um motor de busca de verdade, o caminho é uma **API paga com contrato**
(Brave Search API, Google CSE — 100 consultas/dia grátis, SerpAPI) — não um scraper.

Mas o ponto maior é que **esta lane não precisa de motor de busca**. A pergunta a responder
não é "onde procuro sobre este atrativo", é "quais fatos verificáveis existem sobre ele" — e
isso a Wikipedia + Wikidata entregam por consulta direta, com 400-960 tokens, de graça e
dentro das regras (§11.3). Tanto o `agent-browser` quanto o `duckduckgo_search` são
ferramentas para o passo que a arquitetura certa elimina.

---

## 14. `StarTrail-org/PixelRAG` — avaliação (medido)

[github.com/StarTrail-org/PixelRAG](https://github.com/StarTrail-org/PixelRAG) · Apache-2.0 ·
paper *"PixelRAG: Web Screenshots Beat Text for Retrieval-Augmented Generation"*
([arXiv 2606.28344](https://arxiv.org/abs/2606.28344)).

### 14.1 O que é

RAG **visual**: em vez de parsear HTML para texto, renderiza a página (Chromium headless via
CDP, ou poppler para PDF) em **tiles de screenshot**, embeda os tiles com um modelo de visão
(`Qwen/Qwen3-VL-Embedding-2B`) e indexa em FAISS ou Qdrant. A tese é que parsear HTML perde
tabela, layout e hierarquia — e o pixel preserva.

É uma tese legítima e bem colocada **para o problema dela**: documentos onde a informação
mora no layout (tabelas financeiras, formulários, PDFs de laudo). Ao contrário do
`agent-browser` e do `duckduckgo_search`, aqui há pesquisa séria por trás.

Ponto que fez valer o teste: o projeto expõe um **endpoint hospedado gratuito**
(`api.pixelrag.ai/search`) sobre um índice pré-construído de **8,28M páginas da Wikipedia** —
e a Wikipedia é justamente a fonte de fato que a §11.3 elegeu.

### 14.2 Três medições, três bloqueios

**1. O endpoint hospedado está fora do ar.** 4 tentativas ao longo de ~30 s, todas
**HTTP 502 Bad Gateway** (nginx) — inclusive o `/status`. Sem julgar permanência: no momento
da avaliação, não há como usar a via "sem setup".

**2. O índice é da Wikipedia em inglês — e os atrativos brasileiros não estão nela.**
8,28M páginas bate com a en.wikipedia (7,23M artigos); a pt.wikipedia tem 1,18M. Medido por
`prop=langlinks` numa amostra de categorias de atrativos brasileiros da pt.wikipedia:

| categoria | páginas | com artigo em inglês |
|---|---|---|
| Cachoeiras de Minas Gerais | 12 | 1 (8%) |
| Atrações turísticas da Bahia | 14 | 4 (29%) |
| Praias da Bahia | 10 | **0 (0%)** |
| **total** | **36** | **5 (14%)** |

**~86% dos atrativos brasileiros que existem na Wikipedia lusófona não têm artigo em
inglês** — ficam invisíveis num índice EN. Os dois atrativos da POC ilustram: *Convento da
Penha* tem versão inglesa ("Penha Convent"), o famoso; ***Cachoeira da Fumaça* não tem** — o
obscuro. É o mesmo padrão de todos os testes anteriores: funciona no famoso, falha no
obscuro, e o obscuro é a maioria.

*(Amostra pequena — 36 registros em 3 categorias; a segunda rodada foi throttled pela
Wikipedia. Suficiente para a ordem de grandeza, não para uma taxa precisa.)*

**3. O que ele devolve é imagem.** Mesmo com o índice certo, o resultado são tiles de
screenshot. Alimentar o copywriter com tiles significa **tokens de visão** — muito mais caros
que os 400-960 tokens de texto que a Wikipedia entrega por consulta direta. Para custo, é a
direção oposta da que as medições apontam.

Auto-hospedar tampouco fecha: exige GPU (`--gpu-ids`, `Qwen3-VL-Embedding-2B`), o índice
pré-construído pesa **~217 GB**, e o `train` tem env próprio pinado em CUDA. Contra a stack
do collector (FastAPI + Celery + Postgres, sem GPU), é um subsistema novo inteiro.

### 14.3 Veredito

**Não para esta lane** — e por um motivo mais interessante que o dos anteriores: o PixelRAG
resolve **perda de informação no parsing**, e esta lane **não parseia nada**. As fontes já
são APIs estruturadas (Places, Wikipedia `extracts`, Wikidata claims). Não há tabela nem
layout a preservar; há campo nomeado a ler.

Guarde a ferramenta para o caso em que a tese dela vale: documento em que o dado mora no
layout e não há API. Se algum dia o Cadastur/MTur publicar só PDF de laudo com tabela, é
exatamente aí que ela brilha — não aqui.

### 14.4 Quatro ferramentas, um padrão

| ferramenta | ataca | resultado medido |
|---|---|---|
| `web_search` (Anthropic, atual) | descobrir + ler fonte | $0,0582/atrativo — 79% da conta |
| `agent-browser` | ler a fonte | corta 17%, agrava os 61% (§11.2) |
| `duckduckgo_search` | descobrir a fonte | bloqueado após ~6 consultas (§13.2) |
| `PixelRAG` | ler fonte sem parsear | 86% dos atrativos BR fora do índice EN; devolve imagem (§14.2) |
| **Wikipedia + Wikidata** | **ler o fato direto** | **400-960 tokens, $0, sem bloqueio (§11.3)** |

As três ferramentas são boas em fazer melhor um passo que a arquitetura certa **elimina**. A
pergunta da lane nunca foi *"como leio melhor a web sobre este atrativo"* — é *"quais fatos
verificáveis existem sobre ele"*, e isso se responde consultando dado estruturado, não
navegando, buscando ou fotografando páginas.

---

## 15. `awesome-ai-web-search` — e uma correção à §11.5

Lista: [felladrin/awesome-ai-web-search](https://github.com/felladrin/awesome-ai-web-search).
140 entradas — 74 open source, 46 closed source (apps de usuário final, tipo Perplexity:
fora de escopo, o Brave não precisa de UI de busca) e **20 em "Tools for AI Agents"**, que
são APIs de busca para embutir em agente. Só estas importam.

| tipo | entradas |
|---|---|
| Revendedor de SERP (raspa Google/Bing e revende) | SerpApi, Serper, SearchApi, DataForSEO, TalorData |
| **Índice próprio** | **Brave Search API** |
| Busca neural / answer API | Exa, Tavily, Linkup, JigsawStack, Tako, Desearch, AI Search API, Querit |
| Scrape + search | Jina, Firecrawl, Olostep, Crawlberg |
| Metabusca auto-hospedada | SearXNG |
| Search + answer | Zoom Search |

### 15.1 Correção: a §11.5 estava errada em generalizar

Escrevi na §11.5 que "esta lane não precisa de motor de busca". **Medi e não se sustenta
para a maioria dos atrativos.** Duas medições novas:

**Cobertura da camada de fatos.** Amostra independente (OSM, não Wikipedia — para não
enviesar): 177 atrativos com nome no ES via Overpass; 6,2% têm tag `wikidata`, 4,0% têm tag
`wikipedia`. Numa amostra de 40 desses nomes buscados na pt.wikipedia, **apenas 2 (5%) têm
artigo plausível**. Os dois atrativos da POC (Convento da Penha, Cachoeira da Fumaça) caíram
justamente nos 5% — a POC estava enviesada para o caso fácil.

**O `web_search` faz trabalho real nos 95%.** Rodei Sonnet + `web_search` em três atrativos
reais do OSM sem artigo na Wikipedia:

| atrativo | fatos específicos que a busca trouxe | custo |
|---|---|---|
| Mirante da Lagoa (Guarapari) | Parque Estadual Paulo César Vinha, lagoa de Caraís, coloração avermelhada por matéria orgânica, apelido "Lagoa da Coca-Cola", trilha de ~100 m em restinga | $0,0611 |
| Mirante de Buenos Aires (Guarapari) | distrito de Buenos Aires, Pedra do Elefante e a origem do nome, contraste montanha/litoral | $0,0532 |
| Vista Linda (Domingos Martins) | região de Santa Isabel, ponte sobre lagoa artificial, serra de Domingos Martins | $0,1131 |
| **média (obscuros)** | | **$0,0758** |

Nada disso está na Wikipedia. **A busca não é desperdício nos 95% — é a única fonte.** E
custa mais ali ($0,0758) do que nos famosos ($0,0582).

Custo real ponderado hoje: `0,05 × $0,0582 + 0,95 × $0,0758` = **$0,0749/atrativo**.

### 15.2 A arquitetura certa é cascata, não eliminação

```
atrativo
  ├─ tem Wikipedia/Wikidata? (≈5%)  → fatos estruturados, $0
  └─ não tem?              (≈95%)  → API de busca → snippets → modelo grátis
```

E é exatamente aqui que a lista tem uma contribuição real: **qual provedor faz o passo de
busca**. Preços verificados nas páginas oficiais:

| provedor | preço | grátis/mês | natureza |
|---|---|---|---|
| Anthropic `web_search` (atual) | **$10 / 1.000** | — | embutido, injeta página inteira no contexto |
| Google grounding (Gemini) | $14 / 1.000 | 5.000 buscas | indisponível no free tier (§9.1) |
| **Brave Search API** | **$5 / 1.000** (Search) · $4 / 1.000 (Grounding) | **$5 em créditos** ≈ 1.000-1.250 buscas | **índice próprio**, contrato |
| **Serper** | **$1,00 / 1.000** (até $0,30/1k em volume) | — | revendedor de SERP do Google |
| `duckduckgo_search` (§13) | $0 | — | scraper, **bloqueado em produção** |

Projeção da cascata, usando Serper + Gemini flash-lite no free tier:

| | $/atrativo | por 1.000 atrativos |
|---|---|---|
| hoje (Sonnet + `web_search`, ponderado) | $0,0749 | **$74,90** |
| cascata (Wikipedia nos 5% + Serper nos 95% + flash-lite free) | **~$0,00095** | **~$0,95** |

> **Corrigido pela §22.** Esta linha combinava o **preço** do Serper com a **taxa de fato**
> da Tavily. Medido: o Serper devolve 2-3 dos 10 fatos. A linha viável é a da Tavily, a
> **$15,20/mil (4,9x)**.

**~79x mais barato** — e a economia vem de duas coisas somadas, não de uma: a taxa de busca
cai 10x ($10 → $1 por mil) **e** os tokens vão a zero, porque snippets curtos entram num
modelo gratuito em vez de 12-28 mil tokens de página entrarem no Sonnet.

### 15.3 A pergunta em aberto (honesta)

**Snippets bastam?** O `web_search` da Anthropic injeta 12-28 mil tokens porque busca *e lê*
as páginas. Serper/Brave devolvem título + 2 linhas por resultado (~300-800 tokens no total).
Não foi medido se um snippet carrega fato do calibre de *"Parque Estadual Paulo César Vinha"*
ou *"apelido Lagoa da Coca-Cola"* — ou se seria preciso um segundo passo de leitura de página
(e aí parte dos tokens volta).

Esse é o único teste que falta, e ele exige uma key de Serper ou Brave Search API. É barato:
com $5 de crédito grátis do Brave dá para medir os mesmos três atrativos obscuros e comparar
fato a fato com a saída do Sonnet acima.

> **Superado pela §22.** A pendência foi fechada pelo lado do Serper, e a Brave é da mesma
> classe (revendedora de excerto de SERP, campo `description`). O provedor a usar é
> **extrativo** — Tavily medida, Exa não.

**Recomendação de provedor**, se for testar: **Brave Search API** primeiro — índice próprio
(não depende de raspar o Google), $5/mês grátis cobrem o teste inteiro, e a natureza
contratual resolve o problema de ToS que reprovou o `duckduckgo_search`. Serper entra depois
como otimização de custo se o volume justificar ($1/1k contra $5/1k).

---

## 16. As 14 fontes sugeridas pelo Gemini (medido)

Critério: **cobrir os 95% obscuros** (§15.1). Cobrir o atrativo famoso não vale nada — a
Wikipedia já cobre.

### 16.1 APIs e portais de dados

| fonte | medição | veredito |
|---|---|---|
| **Wikidata Query Service (SPARQL)** | 193 atrativos com coordenada no ES. Os 3 obscuros da §15.1: **ausentes** | Já **é** a camada de fatos (§11.3). SPARQL é forma melhor de consultá-la (bulk por UF em vez de item a item) — **otimização de acesso, não cobertura nova** |
| **Overpass (OSM)** | 177 atrativos com nome no ES; 6,2% com `wikidata`, 4,0% com `wikipedia` | Já em uso; é de onde saiu a amostra da §15.1 |
| **OpenTripMap** | HTTP **401** sem key. Documentação própria: *"based on cooperative processing of different open data sources (OpenStreetMap, Wikidata, Wikipedia, Ministry of Culture … of the Russian Federation)"* | **Reempacotamento de OSM+Wikidata+Wikipedia** — as três fontes já medidas. Herda o mesmo teto de ~5% no Brasil e adiciona key, rate limit e uma dependência. **Nada novo** |
| **dados.gov.br** | já avaliado em sessão anterior: `pagina` obrigatório, chave vale só para o catálogo | Sem atrativo utilizável (ver memória `dados-gov-br-api`) |
| **dados.turismo.gov.br** | **CKAN aberto, sem key, 57 conjuntos** — novidade real | Conteúdo é Cadastur/fomento/cultura (`agencia-de-turismo`, `meios-de-hospedagem`, `operacoes-de-financiamento-*`, `mapa-da-cultura`…). **Nenhum conjunto de atrativo com coordenada.** Útil para `local_businesses`, não para descrição |

### 16.2 Blogs de turismo

Escala e cobertura por município, medidas via sitemap (URL exata, sem match difuso):

| blog | URLs amostradas | `/guarapari` | `/domingos-martins` ou `/pedra-azul` | bloqueia bots de IA no robots.txt |
|---|---|---|---|---|
| 360 Meridianos | 2.461 | 3 | 1 | não |
| Guia Viajar Melhor | 3.000 | 1 | 0 | não |
| Mala de Aventuras | 1.460 | 0 | 5 | não |
| Quero Viajar Mais | 3.000 | 0 | 1 | **GPTBot** |
| Viaje na Viagem | sem sitemap no robots | — | — | não |
| Loucos por Viagem | sitemap vazio na amostra | — | — | não |
| PANROTAS | sitemap vazio na amostra | — | — | não |
| **Aprendiz de Viajante** | sem sitemap no robots | — | — | **ClaudeBot, GPTBot, CCBot, Google-Extended, Amazonbot, Applebot-Extended, Bytespider** |
| **Passagens Imperdíveis** | 10 | 0 | 0 | **anthropic-ai, ClaudeBot, GPTBot, PerplexityBot, Amazonbot** |

Três conclusões:

1. **Escala errada.** 1.500-3.000 URLs por blog, contra ~78 municípios só no ES e milhares de
   atrativos. A cobertura por município é de 0 a 5 posts, e só nos destinos já turísticos
   (Pedra Azul, Guarapari). Para um município como Alegre, nada.
2. **Um terço proíbe explicitamente este uso.** Aprendiz de Viajante e Passagens Imperdíveis
   bloqueiam `ClaudeBot`/`anthropic-ai` **nominalmente** no robots.txt; Quero Viajar Mais
   bloqueia `GPTBot`. Não é zona cinzenta — é recusa declarada, e a constraint de compliance
   do projeto manda respeitar.
3. **São o substrato, não a fonte.** Estes blogs (e as prefeituras, e os guias regionais) são
   exatamente o que o `web_search` já lê quando produz *"Lagoa da Coca-Cola"* e *"Parque
   Estadual Paulo César Vinha"* (§15.1). Raspá-los diretamente significa **construir um
   buscador pior sobre 9 sites** — menos cobertura que uma API de busca de verdade, mais
   manutenção, e com um terço deles dizendo não.

### 16.3 Conclusão

Nada nesta lista substitui o passo de busca contratada da cascata (§15.2). O saldo é:

- **Wikidata via SPARQL** — adotar como *forma de consulta* da camada de fatos (bulk por UF
  é muito mais eficiente que item a item). Não muda cobertura.
- **dados.turismo.gov.br** — registrar: CKAN aberto e sem key é conveniente, e os 57
  conjuntos merecem uma passada para as lanes de `local_businesses`/hospedagem. Fora do
  escopo da descrição.
- **OpenTripMap, os 9 blogs** — descartados pelos motivos acima.
- **Brave Search API** — segue como a pendência a testar (§15.3).

---

## 17. OmniRoute como provider de LLM (medido)

Pergunta levantada: [`diegosouzapw/OmniRoute`](https://github.com/diegosouzapw/OmniRoute)
serve como provider de LLM do Brave? Ele anuncia "vários tokens gratuitos em vários modelos".

### 17.1 O que é

Gateway OpenAI-compat que roda local em `http://localhost:20128/v1`. MIT, TypeScript.

| | |
|---|---|
| stars / forks | **51.760** / 7.054 |
| arquivos no repo | **14.605** (419 MB) · 347 issues abertas |
| criado / último push | 2026-02-13 / no mesmo dia desta avaliação |
| catálogo | **290 providers**, 90+ com free tier, 40+ "free forever" |
| agregado | **~1,53B tokens grátis/mês** em 43 pools |

A documentação é honesta de um jeito raro. Eles recusam somar `RPM × 24/7 × 30d` (chamam de
*"the inflation we reject"*), corrigiram o próprio headline **para baixo** — 1,94B → 1,53B —
quando a auditoria mostrou que o Gemini estava sendo contado por variante em vez de por pool,
e o número é CI-gated: `check:docs-counts` quebra o build se o texto divergir do catálogo.

### 17.2 Reprovado como provider do Brave — quatro motivos

**1. O ToS avaliado não é o nosso caso.** A tabela de ToS deles é explicitamente calibrada
para *"a self-hosted, **single-user personal proxy**"*. O Brave é serviço comercial 24/7.
Trechos textuais do `FREE_TIERS.md` deles:

- `nvidia` — *"prototyping/dev/research/evaluation only — **production use requires license**"*
- `gemini` — `caution`: o free tier é *"for developers building… professional or business purposes"*
- `fireworks`, `cloudflare-ai`, `opencode`, `nlpcloud`, `modal`, `friendliai`, `blackbox`,
  `ai21`, `coze` — proíbem proxy/sublicense **nominalmente**

O CLAUDE.md do projeto exige risco legal documentado por fonte. Rodar um produto comercial em
cima de dezenas de free tiers alheios é a mesma categoria de risco que reprovou o
`duckduckgo_search` na §13.

**2. Não ataca o custo real.** A §11.1 mediu: **61%** do custo são os ~12k tokens de busca
injetados no prompt, e só **17%** é a taxa de busca. OmniRoute roteia **inferência**, não
busca. Ele é, no máximo, a caixa "modelo grátis" da cascata da §15.2 — caixa que o
`gemini-3.5-flash-lite` no free tier já preenche a **$0**, medido (§9, §10).

**3. Runtime estranho no hot path.** Um processo Node no meio de um stack Celery+FastAPI 24/7,
mais superfície de supply chain npm de ~500 contribuidores e 14,6k arquivos — para uma lane
cuja conta é **$74,90/mil**.

**4. Voz não-determinística.** O roteamento `auto` sorteia o backend por request. O
`descricao_editorial` vai para o Mar canônico, e a §12.2 já mediu que o Haiku 4.5 quebra o
PT-BR e o ban de clichê do próprio prompt — rotacionar entre GLM/Qwen/Nemotron garante deriva
de voz e saída irreprodutível. Free tier também implica prompt indo para o treino do provedor,
ressalva que já pesava contra o Gemini free (§4.1).

### 17.3 O que a avaliação rendeu de útil

O `docs/reference/FREE_TIERS.md` deles é reauditado a cada duas semanas com leitura de ToS por
provedor — e cataloga também **provedores de busca**, que é exatamente a pendência da §15.3.
Os números deles foram **reverificados nas páginas oficiais** antes de entrar aqui (e um
estava errado, ver 17.4b). Verificado em 2026-08-20:

| provedor | grátis recorrente | preço | cartão | ressalva de ToS |
|---|---|---|---|---|
| **Exa** | **$10 em créditos/mês** (+$20 no signup, ~2.800 buscas) ≈ **2.000 buscas/mês** | $5/1k search · **$1/1k pages** (Contents API) | — | sem cláusula de "no proxy"; tem programa de revenda oficial |
| **Tavily** | **1.000 créditos/mês**, **sem cartão** | $0,008/crédito (~$8/1k) | não | API *"may not be transferred, assigned, shared… to any third party"* |
| **Brave Search** | **$5 em créditos/mês** ≈ 1.000 buscas | $5/1k Search · $4/1k Grounding + $5/M tokens | sim (só identidade, não cobra) | proíbe redistribuir/revender resultado; ⚠️ ver 17.5 |
| **Serper** | ❌ **nenhum** — só 2.500 queries de trial | ~$1/1k (o mais barato) | — | proíbe *"mirroring materials on any other server as-is with no-value-added"* |

### 17.4 Duas correções que isso impõe a este relatório

**(a) A projeção de ~$0,95/mil da §15.2 dependia do Serper — que não tem tier grátis
recorrente.** Só 2.500 queries de trial. O preço de $1/1k continua real, mas não dá para
começar de graça nele. Faixa honesta por provedor, por 1.000 atrativos:

| provedor do passo de busca | por 1.000 atrativos |
|---|---|
| hoje — Sonnet + `web_search` (ponderado, §15.1) | **$74,90** |
| Serper $1/1k | $0,95 |
| Exa $5/1k | $4,75 |
| Brave $5/1k | $4,75 |
| Tavily $8/1k | $7,60 |

> **Corrigido pela §22.** Esta tabela trata os provedores como intercambiáveis a preços
> diferentes. Não são: revendedor de SERP (Serper, Brave, CSE) entrega 2-3/10 fatos, extrativo
> (Tavily, Exa) entrega 9/10. As linhas baratas não são compráveis.

Ou seja: o ganho é de **10x a 79x conforme o provedor**, não 79x fixo. Todos continuam ordens
de grandeza melhores que hoje — a tese da cascata não muda, só o número da ponta.

**(b) O `FREE_TIERS.md` do OmniRoute erra sobre a Brave.** Ele afirma que o tier grátis acabou
em 2026-02-12. Checado na página oficial: o **`$5 in free monthly credits` continua
anunciado**. O que mudou foi a forma — 5.000 queries/mês sem cartão → $5 de crédito com cartão
para confirmar identidade (*"the card is only used to confirm your identity and will not be
charged"*). Lição operacional: o catálogo deles é bom ponto de partida, nunca fonte final.

### 17.5 Achado novo de compliance — storage rights da Brave

Direto da página da Brave Search API:

> *"If you would like to store the API results in part or whole (for example, to train or tune
> an LLM), you will need to subscribe to a plan that explicitly grants storage rights."*

A lane persiste a descrição derivada no Mar e a envia para a norteia-api. Se a paráfrase em voz
Norteia conta ou não como "store the API results in part" é leitura que precisa ser feita
**antes** de adotar a Brave, não depois. **Não foi medido nem consultado juridicamente** — fica
registrado como item aberto. Exa e Tavily não têm cláusula equivalente encontrada.

> **Corrigido pela §29.2.** A Exa tem: os Terms §4.2(a) proíbem *"create derivative works from
> […] any information […] obtained from or through, the Services"* sem permissão por escrito.
> A Tavily segue sem cláusula encontrada.

### 17.6 Veredito

**OmniRoute: não adotar como provider.** Resolve um problema que o Brave não tem (juggling de
keys para agentes de código) e não resolve o que ele tem (tokens de busca), a um custo de
compliance e de operação desproporcional a uma lane de $74,90/mil. **Aproveitar como fonte de
pesquisa** — o catálogo de free tiers e a leitura de ToS por provedor valem a consulta
periódica.

**E a pendência da §15.3 muda de provedor único para três.** Somados, os tiers gratuitos dão
**~4.000 buscas/mês a custo zero** (Exa 2.000 + Tavily 1.000 + Brave 1.000) — suficiente para
rodar o teste nos mesmos 3 atrativos obscuros **e** ainda operar uma fatia inicial. O
**$1/1k pages** da Contents API da Exa finalmente precifica o segundo passo de leitura de
página que a §15.3 deixou em aberto: **~$0,001 por página lida**. Se o snippet não bastar, o
custo de ler a página não inviabiliza a cascata — o que era a principal incerteza.

---

## 18. O teste da §15.3, executado (medido)

Pendência aberta desde o commit `ee1aca1`: **o snippet de uma API de busca contratada carrega
os fatos que o `web_search` carrega, ou é preciso um segundo passo de leitura de página?**

Executado em 2026-08-20 com a **Tavily** no free tier (1.000 créditos/mês, sem cartão).
Sonda: `scripts/poc/search_snippets_probe.py`. Alvo: os 10 fatos fortes que o Sonnet +
`web_search` produziu nos três atrativos obscuros da §15.1. Sem LLM no caminho — mede-se o
insumo, não a redação.

### 18.1 Resultado

| modo | fatos fortes | tokens/atrativo | $/atrativo (busca) |
|---|---|---|---|
| hoje — Sonnet + `web_search` | 10/10 (por construção) | **~11.900** | $0,0758 |
| Tavily, **1 query**, snippet | 5/10 | **755** | $0,008 |
| **Tavily, 2 queries, snippet** | **9/10** | **2.311** | **$0,016** |
| Tavily, 2 queries + leitura de página | 7/10 | **44.511** | $0,016 |

**O snippet basta.** Com 2 queries recupera **9 dos 10 fatos** que o Sonnet achou, gastando
**5,1x menos token** e **4,7x menos dinheiro** no passo de busca. O `web_search` cobra 12-28
mil tokens de página para entregar o mesmo conteúdo que 2,3 mil tokens de snippet entregam.

O único fato realmente perdido é a *"ponte sobre lagoa artificial"* do Vista Linda — e a
inspeção do contexto bruto mostra que ele **não está no corpus** que a Tavily devolve (3.104
chars, nenhuma ocorrência de "ponte", "lagoa" ou "represa"). É lacuna de cobertura da fonte,
não de profundidade do snippet. Ler a página não recuperaria.

### 18.2 São necessárias 2 queries — e isso corrige a §15.2 de novo

Com **uma** query o placar cai para **5/10**. O Sonnet faz duas buscas por atrativo (medido),
e replicar isso é o que fecha a diferença. As duas variantes usadas saem só do nome e do
município — nenhuma usa termo da lista de fatos, senão o teste vazaria a resposta para dentro
da pergunta.

A consequência é de custo: **o passo de busca custa o dobro do que a §15.2 e a §17.4
projetaram**, porque ambas assumiam uma query por atrativo. Projeção corrigida, por 1.000
atrativos, com 2 queries e o modelo gratuito escrevendo:

| provedor do passo de busca | $/atrativo | por 1.000 atrativos | contra hoje |
|---|---|---|---|
| hoje — Sonnet + `web_search` | $0,0749 | **$74,90** | — |
| Serper $1/1k | $0,0019 | **$1,90** | 39x |
| Exa $5/1k | $0,0095 | **$9,50** | 7,9x |
| Brave $5/1k | $0,0095 | **$9,50** | 7,9x |
| **Tavily $8/1k (medido)** | **$0,0152** | **$15,20** | **4,9x** |

O ganho real é de **4,9x a 39x**, não os 79x da §15.2 nem os 10-79x da §17.4. A tese da
cascata continua de pé — o número da ponta é que encolheu duas vezes seguidas conforme a
medição foi ficando mais honesta. E o free tier rende metade do que a §17.6 disse: 1.000
créditos ÷ 2 queries = **500 atrativos/mês**, não 1.000.

### 18.3 O segundo passo de leitura de página é contraprodutivo (na Tavily)

Era a hipótese cara da §15.3: se o snippet fosse raso, leríamos a página e parte dos tokens
voltaria. **Medido, ler a página piora as duas pontas** — 44.511 tokens (19x o snippet, e 3,7x
o próprio `web_search`) **e menos fatos**: 7/10 contra 9/10.

Fatos não podem cair quando o texto só cresce, então a causa foi investigada em vez de
reportada. Pedir `include_raw_content: true` **muda o conjunto de resultados**:

| | `advanced` sem raw | `advanced` com raw |
|---|---|---|
| URLs devolvidas | 5 | 5, sendo **3 diferentes** |
| extração de página bem-sucedida | — | **1 de 5** (as outras vêm `raw_content` vazio) |
| tokens | 4.314 | 44.510 |
| fatos | 9/10 | 7/10 |

Ou seja: pedir a página **derruba resultados bons** (saiu um snippet de 2.254 chars que
carregava fato), **falha em extrair 4 de 5** páginas, e concentra os 40 mil tokens numa única
página que sobreviveu. Não é custo extra por mais fato — é custo extra por menos fato.

**Ressalva honesta:** isto é um resultado da Tavily, não do conceito. A Contents API da Exa é
um produto separado, a $1/1k páginas, e pode extrair melhor. **Não foi medido** — não há key
da Exa. O que está medido é que, *com o snippet já entregando 9/10*, o segundo passo perdeu a
razão de existir: não há lacuna grande o bastante para justificá-lo.

### 18.4 Duas armadilhas metodológicas da própria sonda

Ambas produziram, na primeira rodada, um resultado errado que parecia um achado. Ficam
registradas porque a próxima sonda vai cair nelas de novo:

1. **Truncar o `raw_content` no head.** Pegar os 3.000 primeiros chars da página descarta menu
   e nav — e o trecho relevante junto. Dava 4/10 e parecia "ler página piora". O snippet é
   extrativo e centrado na query; o começo da página não é.
2. **Casador de fatos estreito no tempo verbal.** A lista tinha `"recebeu o nome"`; o texto
   real diz *"recebe **este** nome por ter uma vista direta para a formação rochosa… formato e
   tromba de um elefante"*. Marcava ausente um fato presente. Casador estreito atribui à fonte
   uma falha que é da sonda.

A sonda é determinística onde importa: 3 rodadas idênticas em modo snippet — mesmos 9/10,
mesmos 2.311 tokens, as mesmas 26 URLs. O resultado não é sorteio de ranking.

### 18.5 Veredito — a pendência da §15.3 está fechada

- **Snippet basta.** 9/10 fatos a 2.311 tokens. A cascata da §15.2 pode ser construída.
- **Com 2 queries por atrativo**, não uma. O passo de busca custa o dobro do projetado.
- **Sem segundo passo de leitura.** Não compra fato; na Tavily, cobra 19x para entregar menos.
- **Ganho real: 4,9x** contra os $74,90/mil de hoje. *(A ponta dos 39x supunha o Serper; a
  §22 mediu e reprovou — só a ponta da Tavily sobrevive.)* *(A §23 mediu a redação: barata,
  mas a taxa de fatos da busca caiu para 4/10 — barato e pior, não barato e igual.)*
- O que sobra de risco não é técnico, é de **cobertura**: 1 dos 10 fatos não existia no corpus
  da Tavily. Numa amostra de três atrativos isso é 10% — número pequeno demais para ser taxa.
  Vale medir em escala antes de trocar o provedor em produção.

> **Corrigido pela §23.** O risco de cobertura se materializou em 19 dias, sem precisar de
> escala: a mesma sonda, sem alteração, deu **4/10 em 2026-09-08** contra os 9/10 daqui. Não
> é a query (a variante com UF não recupera). As 3 rodadas idênticas provam repetibilidade
> na sessão, não estabilidade no tempo — e o ganho de 4,9x pressupõe a taxa de fatos que caiu.

Próximo passo natural, e ele **não** é mais sobre busca: a lane precisa da camada que consome
esses 2.311 tokens. O veredito da §10 continua valendo — *"o trabalho não é trocar de modelo,
é construir a camada de fatos determinísticos"*. O que esta seção acrescenta é que o passo de
busca, que era a peça em aberto dessa camada, agora tem preço e desempenho medidos.

---

## 19. A memória paramétrica do modelo dispensa a busca? (medido)

Pergunta levantada: os pesos do modelo já carregam fato sobre atrativo brasileiro. Isso não
removeria a necessidade da tool `web_search` — e com ela a caixa mais cara do pipeline?

Medido em 2026-08-27. Sonda: `scripts/poc/parametric_memory_probe.py`. Três modelos, sem tool
nenhuma, usando o **prompt de produção** (`COPYWRITER_SYSTEM` + `_build_context`, importados do
módulo real) com contexto Places vazio.

### 19.1 O teste tinha que medir invenção, não só acerto

O modo de falha da memória paramétrica não é *"não sei"*. É *"invento com confiança"*. Um
modelo que produz 8 dos 10 fatos e inventa outros 5 é pior que inútil para uma base canônica,
porque nada no pipeline distingue os dois — o `descricao_editorial` entra no Mar do mesmo jeito.

Por isso a amostra tem três classes, e a terceira é a que decide:

| classe | alvos | serve para |
|---|---|---|
| obscuro | os 3 da §15.1 | é o caso real: 95% dos atrativos |
| famoso | Convento da Penha, Pico da Bandeira | mede o que a Wikipedia já cobre de graça |
| **FALSO** | **Mirante da Pedra Retorcida** (Brejetuba), **Cachoeira do Sino Azul** (Afonso Cláudio) | **não existem** |

Os dois falsos foram verificados **antes** de entrar na lista: busca `advanced` na Tavily, 8
resultados cada, nenhum cita o nome. As cachoeiras reais de Afonso Cláudio são Fio de Ouro,
Bonita e Santa Luzia; a formação rochosa real de Brejetuba é a Pedra do Submarino.

### 19.2 Resultado

| modelo | fatos, obscuro | fatos, famoso | descreveu o atrativo que não existe |
|---|---|---|---|
| `claude-sonnet-4-5` | **1/8** | 5/6 | **2/2** — 14 afirmações concretas inventadas |
| `gemini-3.5-flash-lite` | **0/8** | 3/6 | **2/2** — 4 afirmações |
| `deepseek-chat` | **1/8** | 1/6 | **2/2** — 6 afirmações |
| *baseline* — Sonnet + `web_search` | *10/10* | — | — |

**Seis casos falsos, três modelos, zero abstenções.** Nenhum dos três disse "não conheço este
lugar". Todos escreveram prosa turística confiante, na voz da Norteia, sobre um lugar inexistente.

E o "1/8" não é 1: o único fato que casa é **restinga** — vegetação genérica do litoral capixaba
que qualquer texto sobre Guarapari conteria. O recall real no obscuro é **zero**.

### 19.3 O achado grave: o Sonnet não inventou a descrição, inventou as fontes

Na Cachoeira do Sino Azul, o Sonnet emitiu um bloco `<search_results>` **completo e fabricado**
antes de escrever — quatro resultados, com URL, título e trecho:

| URL fabricada | verificação |
|---|---|
| `tripadvisor.com.br/Attraction_Review-g10177019-d12873482-…` | IDs plausíveis, atrativo inexistente |
| `es.gov.br/Noticia/cachoeira-do-sino-azul-visite-o-cartao-postal…` | **HTTP 404** — domínio real, caminho inventado |
| `guiaes.com.br/cachoeira-do-sino-azul-afonso-claudio/` | domínio não resolve |
| `instagram.com/cachoeiradosinoazul/` | perfil inventado |

Os quatro concordavam entre si num fato inventado: *"90 metros de queda livre"*.

Isto é pior que alucinar prosa. **É alucinar a evidência.** O motor de confiabilidade do Brave
dá peso 30 para `origem` e 20 para `corroboração`; quatro fontes independentes que concordam é
exatamente o padrão que o score é feito para premiar. Um registro assim entraria no Mar com
score alto — pelo motivo errado.

### 19.4 Aviso operacional

O `<search>` que o Sonnet emitiu mostra que ele **queria** buscar: o prompt de produção pressupõe
a tool. Sem ela, o modelo não degrada para "não sei" — ele emite a pseudo-chamada, não recebe
resposta, e **escreve assim mesmo**.

Consequência prática: *desligar `web_search` para economizar não produz descrição pior. Produz
descrição inventada, indistinguível da boa.* Quem fizer essa mudança pelo custo não vai ver o
estrago no monitor.

### 19.5 Por que nem o que o modelo sabe é aproveitável

O Sonnet acerta 5/6 nos famosos. Mesmo isso não serve à lane, por duas razões:

1. **É a fatia que já é grátis.** A §15.1 mediu que ~5% dos atrativos têm artigo na Wikipedia —
   e são exatamente os famosos. Wikipedia + Wikidata entregam esses fatos com fonte citável, a
   custo zero (§11.3). A memória paramétrica cobre o que já está coberto.
2. **O pipeline não tem onde guardar.** Nascente exige `source` e `source_ref`; o score pesa
   `origem` e `corroboração`. Um fato vindo dos pesos do modelo não tem URL, não tem data, não
   tem como ser corroborado nem reprocessado. Não existe slot para ele na arquitetura.

### 19.6 Veredito

**Não.** A memória paramétrica não dispensa a busca — e a pergunta se responde melhor invertida:
ela falha exatamente onde a lane precisa (0/8 no obscuro, que é 95% do caso), acerta exatamente
onde já é grátis (o famoso, que a Wikipedia cobre), e **fabrica de forma indetectável nos dois
casos**.

Isso reforça a cascata da §15.2 em vez de enfraquecê-la. A busca contratada não está lá só pelo
fato — está lá pela **procedência**. Um snippet vem com URL, com data e com um domínio que dá
para auditar; 2.311 tokens deles bastam para 9 dos 10 fatos (§18). O modelo entra depois, para
escrever — nunca para lembrar.

---

## 20. Rodar o copywriter pela assinatura Claude Code Max (avaliado)

Proposta levantada: criar um subagente do Claude Code com o prompt do copywriter, desligar o
enriquecimento de descrição dentro do Brave e gerar a prosa externamente numa sessão do Claude
Code — absorvendo LLM e busca na assinatura Max que já está paga.

Contexto que dimensiona a pergunta: **plano Max 5x ($100/mês)** e uma **carga inicial de
~10 mil atrativos de todo o Brasil**, de uma vez.

### 20.1 A licença permite — isto não é o caso do OmniRoute

Esperava-se aqui a mesma trava que reprovou o OmniRoute na §17.2. **Não é.** O artigo oficial
*Use the Claude Agent SDK with your Claude plan* lista, entre o que a assinatura cobre:

> Claude Agent SDK usage in your own projects (Python or TypeScript) · o comando `claude -p`
> **(non-interactive mode)** · The Claude Code GitHub Actions integration · Third-party apps
> that authenticate with your Claude subscription through the Agent SDK

Uso programático da assinatura é sancionado. A proposta não é contorno de ToS.

### 20.2 Mas o mesmo documento traça o limite, nas palavras deles

> **Production automation at scale.** The Agent SDK monthly credit is sized for **individual
> experimentation and automation**. Teams running **shared production automation should use
> Claude Platform with an API key** for predictable pay-as-you-go billing.

O Brave é *shared production automation* pela definição do próprio PROJECT.md: serviço 24/7,
todas as UFs, alimentando um produto. É o caso que o parágrafo manda mover para a API.

E há um detalhe de calendário que muda a conta hoje:

> **Update June 15:** We're pausing the changes described below. For now, nothing has changed:
> Claude Agent SDK, `claude -p`, and third-party app usage **still draw from your
> subscription's usage limits**. The previously announced monthly credit **isn't available**.

O crédito separado, que isolaria automação do uso interativo, está **pausado**. Hoje
`claude -p` consome o mesmo pool do Claude Code interativo e do claude.ai — rodar descrição
queima a própria capacidade de programar, contra um limite que a Anthropic não publica em
tokens ("length and complexity of your conversations, the features you use, which model,
and the effort level").

### 20.3 A carga inicial inteira custa menos que um mês de assinatura

10 mil atrativos, uma vez, com os números medidos na §18 (2 queries por atrativo):

| caminho | busca | LLM | **total, 10 mil** |
|---|---|---|---|
| lane atual (Sonnet + `web_search`) | — | — | **$749** |
| cascata · Tavily $8/1k + flash-lite pago | $160 | $17 | **$177** |
| cascata · Exa $5/1k + flash-lite pago | $100 | $17 | **$117** |
| cascata · Serper $1/1k + flash-lite pago | $20 | $17 | **$37** |

O Max 5x custa **$100/mês**. A carga inicial completa do Brasil pela cascata sai entre
**$37 e $177 — uma vez só**. Entre um terço e menos de dois meses de assinatura, para não
precisar nunca mais.

### 20.4 E não cabe na cota, por uma ordem de grandeza

O único número que a Anthropic publica sobre quanto de automação um plano comporta é o crédito
(pausado) que eles mesmos dimensionaram: **Pro $20 · Max 5x $100 · Max 20x $200 por mês**. É a
régua deles.

Uma descrição pelo subagente custa **mais** que a in-lane, não menos: o Claude Code carrega
system prompt, definições de ferramenta e múltiplos turnos por tarefa. Otimista é $0,075
(igual à in-lane da §11.1); realista, ~$0,15.

| hipótese | 10 mil descrições | contra a régua do Max 5x |
|---|---|---|
| otimista, $0,075 | $749 | **7,5 meses** |
| realista, $0,15 | $1.500 | **15 meses** |

O pool interativo não é o mesmo que o crédito, e não é publicado — mas nenhum múltiplo
plausível fecha um vão de 10x. **Não é "talvez não caiba": está fora por ordem de grandeza.**

### 20.5 O argumento que fecha: a cascata é inevitável

A carga inicial acontece uma vez. **A lane continua rodando depois** — atrativo novo entra
continuamente, e cada um precisa de descrição.

Fazer os 10 mil pela assinatura não elimina o trabalho de construir a cascata; adia. Gastaria-se
meses de cota para chegar no dia seguinte precisando construir exatamente a mesma coisa — agora
sem os 10 mil servindo de banco de prova.

### 20.6 O uso do subagente que se sustenta: oráculo de qualidade

Não como motor. Como referência.

O que a §18 mediu foi que os **fatos** chegam pelo snippet (9/10 em 2.311 tokens). O que ela
**não** mediu foi se a prosa do `flash-lite` a partir de snippet se sustenta contra a do Sonnet
com busca. Essa é a única incerteza que ainda separa a cascata da produção — e é exatamente o
que uma assinatura resolve bem: volume baixo, qualidade alta, valor alto por item.

1. Rodar o subagente em **~100 atrativos**. Cabe folgado na cota e é literalmente
   *individual automation*, o uso que a doc descreve.
2. Esse lote vira o **conjunto de referência** — descrições em qualidade Sonnet-com-busca,
   com as fontes registradas.
3. Rodar os mesmos 100 pela cascata barata e comparar contra a referência.
4. Passando, virar os 10 mil por $37–177 com confiança medida. Não passando, a descoberta
   custou 100 descrições em vez de 10 mil.

### 20.7 Armadilha de implementação, se for por esse caminho

Escrever a descrição direto em `rio_records.canonical` pula o `record_event` de auditoria e não
recomputa o score. O registro fica com prosa mas sem rastro de origem nem data. O caminho certo
é o subagente escrever **arquivo**, e a ingestão entrar por um endpoint do Brave que gere o
evento — nunca `UPDATE` direto na tabela.

### 20.8 O artefato

O subagente foi criado em **`.claude/agents/norteia-copywriter.md`**, com o
`COPYWRITER_SYSTEM` de produção verbatim, mais três coisas que a produção não tem porque não
precisava:

- **duas queries por atrativo** como regra explícita (§18: uma só recupera 5/10);
- **regra de fabricação** derivada da §19 — se a busca não confirmar o lugar, marcar
  `status: "sem_fonte"` e escrever duas frases sensoriais, nunca inventar; e nunca citar URL
  que não foi aberta;
- **contrato de saída em JSON** com `fontes` e `queries`, para que a descrição seja auditável
  depois — que é a razão de a busca estar no fluxo (§19.6).

### 20.9 Veredito

**Não usar a assinatura como motor da lane, nem para a carga inicial.** Não por ToS — é
permitido — mas porque a alternativa custa menos que um mês do que já se paga, porque a carga
não cabe na cota por 10x, e porque não dispensa construir a cascata de qualquer forma.

**Usar o subagente como oráculo**, num lote de ~100, para fechar a última incerteza da cascata:
a qualidade da prosa barata.

---

## 21. Rodar o subagente da assinatura sobre a lane real (medido)

A §20 avaliou a assinatura no papel e a reprovou como motor da carga inicial, por um argumento de
cota em ordem de grandeza apoiado num palpite de $0,15/atrativo. Esta seção mede, e o resultado
inverte o veredito: **a carga inicial cabe na assinatura com folga.**

A pergunta aqui não é a da §18. Não se compara a assinatura com a cascata barata; pergunta-se
quantos atrativos cabem na janela de 5 horas e na janela semanal do plano Max 5x.

### 21.1 Montagem

Base zerada, stack local. `description_enrichment_enabled` desligado por `PATCH /api/v1/config` —
a flag auditada, não o `.env` — de modo que o registro atravessa Nascente, review, geocode,
resolução de município, destino-pai, score, roteamento e enriquecimento do Places, e só o
copywriter é pulado. Sweep por `POST /api/v1/engine/start` com `ufs:["ES"]` e
`max_atrativos_per_uf:30`.

Os 30 registros saíram todos para DLQ com score médio 66,3, coerente com o teto conhecido da lane
TA. Esperado, e fora do objeto: a descrição sobe `completude_value` de 75 para 90, o que vale 3
pontos, e não fecha a distância até o `threshold_mar` de 80.

O export reusa `copy_batch.build_request`, o construtor da lane de lote de produção, então o texto
de grounding entregue ao subagente é byte-idêntico ao que a produção mandaria. Isso carrega junto a
lacuna que aquele módulo já documenta e que esta medição confirma: `types`, `editorial_summary` e
`reviews` do Places são transitórios e **não sobrevivem em `normalized`** — só `address`. Qualquer
arquitetura que escreva a descrição fora da lane herda esse contexto empobrecido.

Split: os mesmos 30 atrativos em dois braços, **10 invocações single** (um atrativo cada) e **2
invocações em lote** (dez cada). O system prompt e as definições de tool são pagos uma vez por
invocação, não por atrativo, então a diferença entre os braços é a amortização.

### 21.2 Uma armadilha de medição que quase publicou o número errado

A primeira agregação somou o `usage` de cada linha dos transcripts dos subagentes em
`~/.claude/projects/<proj>/<sessão>/subagents/agent-*.jsonl`. **Isso conta duplicado.** Uma mesma
requisição aparece em várias linhas (eventos de streaming), e o `usage` de cada linha não é
incremental: repete o total corrente. Somar linha a linha inflou o custo em ~2,5x, e inflou os dois
braços de forma desigual, o que estragava também o fator de amortização.

O erro só apareceu no cruzamento com o `/usage` da própria sessão, que atribuía ao `claude-sonnet-5`
um terço do que a agregação dizia. A correção é deduplicar por `message.id` e, para cada id, ficar
com o **maior** `output_tokens` (o evento final de streaming carrega o acumulado). Feito isso, a
reconstrução bate com o `/usage` quase na casa: input 120 contra 120, cache write 395.346 contra
395.300, cache read 2.086.629 contra 2.100.000.

Duas lições de método. A primeira: **agregação de transcript sempre precisa de uma âncora externa**
— sem o `/usage` o número errado teria virado decisão. A segunda: nesta sessão **todo** o uso de
`claude-sonnet-5` foi o piloto (os exploradores rodaram em opus e haiku, a thread principal em
opus), o que dá uma leitura autoritativa isolada de graça. Vale desenhar futuras medições assim, com
o objeto num modelo que mais nada na sessão use.

### 21.3 O custo real, e o segundo ponto cego

Além da dupla contagem da §21.2, a agregação por transcript tinha um segundo furo: **a tool
`WebSearch` roda num modelo próprio, fora do transcript do subagente.** Somar só o que aparece no
arquivo do agente mede a escrita e ignora a busca, que custa quase o mesmo.

O teste de 100 atrativos (10 lotes de 10) expõe isso ao comparar o delta do `/usage` com a
agregação por transcript:

| origem | US$ pelos 100 |
|---|---|
| `claude-sonnet-5`, a escrita do subagente | 5,35 |
| `claude-haiku-4-5`, a tool `WebSearch` | 4,44 |
| **total** | **9,79** |
| agregação só por transcript (subestima 2,1x) | 4,65 |

**$0,0979 por atrativo**, contra **$0,0749** da lane in-lane, que já embute a taxa de `web_search`.
O subagente sai **1,31x mais caro** que a lane que ele substituiria. Não é a ordem de grandeza que
a §20 temia, nem a vantagem que a primeira correção desta seção anunciou: é um pouco pior, medido
como se deve.

### 21.4 Quantos atrativos cabem nas janelas

A Anthropic não publica os limites em número nenhum, então o caminho é o delta do `/usage` do
próprio plano em torno de um lote de tamanho conhecido. Max 5x, 100 atrativos em 10 lotes de 10:
janela de 5h de **8% para 21%** (13 pontos, incluindo a sweep e a orquestração), semanal de
livre para **2%**.

A fatia do copywriter dentro dos 13 pontos tem duas leituras: o próprio `/usage` atribui **39%** a
subagentes `norteia-copywriter`, e a proporção de custo dá **52%**. As duas viram a faixa:

| | por janela de 5h | por semana |
|---|---|---|
| pela atribuição do `/usage` (39%) | 1.972 | 12.821 |
| pela proporção de custo (52%) | 1.488 | 9.673 |
| **faixa de trabalho** | **1.500 a 2.000** | **10.000 a 13.000** |

A razão semanal/5h sai de **6,5x**, dos mesmos deltas. É o número mais frágil da seção: os 2%
estão arredondados, e a faixa real (1,5% a 2,49%) põe a razão entre 5,2x e 8,7x. Some a isso a
promoção de +50% no limite semanal, ativa até 31/ago — sem ela a semana cai um terço, para algo
como **6.500 a 8.500** atrativos.

**A carga inicial de 10.000 consome aproximadamente uma semana inteira de cota**, não fazendo mais
nada com a assinatura. Cabe, mas sem folga — e não "16% de uma semana", como esta seção chegou a
afirmar antes de a busca entrar na conta.

### 21.5 O gargalo real não é a cota, é o orçamento de busca

Os 100 atrativos precisariam de 200 buscas, a duas queries por atrativo, que é a regra da §18. O
`/usage` registrou **139**. Um dos lotes reportou ter esgotado o orçamento de busca da sessão
depois de oito buscas e ter caído para `WebFetch` no restante.

Esse é o achado operacional do teste, e a escala de 30 não o mostrava: **com dez agentes em
paralelo o teto de busca chega antes do teto de tokens.** Quem for rodar a carga inicial precisa
serializar mais e paralelizar menos, ou aceitar que uma fração das descrições sai sem as duas
queries que a §18 mediu como necessárias.

O efeito na qualidade é visível: `sem_fonte` subiu de 0 em 30 para **11 em 100**. Parte é o
estouro de busca, parte é a lane entregando registro ruim, como uma "Rua Das Pedras" atribuída a
Campos dos Goytacazes quando o nome é de Búzios, ou um "Figueira Da Esquina 🌳❤️" com emoji vindo
do próprio TripAdvisor. Nos dois casos a regra anti-fabricação da §19 fez o trabalho: marcou
`sem_fonte` em vez de inventar. Fora isso, **zero violação de contrato** nas 100 saídas, e 100
`rio_id` únicos.

### 21.6 Uma advertência sobre `subagent_tokens`

A notificação de conclusão de cada subagente traz um campo `subagent_tokens`. Nos 10 lotes eles
somam 703.658, contra 3.989.773 medidos no transcript. O campo bate com input + cache write +
output, ou seja **exclui o cache read**, que é 82% do consumo. Quem dimensionar carga por esse
número subestima por cerca de 5,7x, e ainda por cima ignora a busca. Para medir custo de subagente
só serve o delta do `/usage`.

### 21.7 As fontes existem? Auditoria das 221 URLs

O campo `fontes` do contrato do subagente só vale se for verificável, e a §19.3 mediu exatamente o
contrário: privado de busca, o Sonnet emitiu um bloco `<search_results>` inteiro com quatro URLs
inventadas que concordavam entre si, uma delas um `es.gov.br` com caminho inexistente. Como o motor
de confiabilidade pesa `origem` 30 e `corroboração` 20, quatro fontes coerentes são justamente o
padrão que o score premia. Uma descrição fabricada bem fabricada entra no Mar com nota alta pelo
motivo errado.

Então as 130 descrições dos dois pilotos foram auditadas URL por URL:
`pilot_descricoes.py auditar`, resultado completo em `docs/poc/auditoria-fontes.json`.

262 URLs citadas, 221 únicas, 97 domínios, **exatamente 2,0 fontes e 2,0 queries por registro** — a
regra de duas queries da §18 cumprida sem exceção em 130 de 130.

| classe | n | % |
|---|---|---|
| viva | 241 | 92,0 |
| bloqueada (403/429/503 anti-bot: a página existe) | 11 | 4,2 |
| inalcançável (erro de conexão do auditor) | 7 | 2,7 |
| **inexistente (404 com a raiz do domínio viva)** | **3** | **1,1** |

A classificação separa deliberadamente as três causas, porque só a última acusa a descrição. Um 403
da `marinha.mil.br` ou da `alltrails.com` prova que a página existe e recusa o auditor. Os sete
inalcançáveis falham também na raiz do domínio, inclusive em sites vivos como `parquelage.org`, o
que é rede do auditor e não do modelo.

Sobram **três casos em 221, ou 1,1%**, em que o domínio responde 200 e o caminho devolve 404:
`es.gov.br/Contents/Item/Display/440`, uma matéria do `bmcnews.com.br` sobre o Inhotim e uma do
`nsctotal.com.br` sobre a Joaquina. O primeiro repete letra por letra o padrão da §19.3, domínio de
governo real com caminho plausível e inexistente. Não é possível distinguir fabricação de link que
morreu entre a busca e a auditoria, então o número é um teto, não uma acusação.

O contraste é o achado: **sem busca a §19 viu quatro URLs fabricadas num único caso; com busca
ligada a taxa cai para 1,1% do total.** É a evidência que faltava para tratar `fontes` como campo
auditável de verdade, e não como promessa.

Perfil de proveniência das 262 citações: 30% Wikipedia, 16% `.gov.br` (prefeituras, IEMA, Diário
Oficial do ES), 34% `.org`/`.edu`, o resto imprensa local e portais de turismo regionais. Nenhuma
citação ao próprio TripAdvisor como fonte factual, o que é o esperado: a lane já traz o dado do TA,
a busca existe para corroborá-lo em outro lugar.

Ressalva de amostra: dois dos três casos suspeitos e boa parte dos `sem_fonte` recaem sobre
registros que a própria lane entregou mal. "Secretaria de Estado do Turismo - Setur/ES" não é
atrativo turístico, "Rua Das Pedras" veio atribuída a Campos dos Goytacazes quando o nome é de
Búzios, e "Figueira Da Esquina 🌳❤️" carrega emoji do próprio TripAdvisor. O copywriter está sendo
cobrado por lixo de coleta, e a auditoria de fontes acaba funcionando como detector barato de
registro ruim na Nascente.

---

### 21.8 Veredito

**A assinatura comporta a carga inicial, mas ela custa uma semana de cota e não sai mais barata que
a lane.** Medido: $0,0979 por atrativo contra $0,0749 in-lane, 1.500 a 2.000 atrativos por janela de
5 horas, 10.000 a 13.000 por semana com a promoção ativa e talvez 6.500 a 8.500 sem ela.

A decisão que isso habilita é estreita e vale registrar como tal: se o objetivo é **não emitir
fatura de API** para a carga inicial, o caminho existe e cabe numa semana de mutirão. Se o objetivo
é **custo**, a assinatura perde para a própria lane, e as duas perdem para a cascata da §18.

Não promove a assinatura a motor em regime: segue sendo operação manual, sem retry, sem
observabilidade, com um teto de busca que a atrapalha em paralelo, e a §20.2 continua valendo.

Fica o oráculo, agora com 130 registros descritos em qualidade Sonnet-com-busca e fontes
registradas. Pontuar a cascata barata contra eles continua sendo a próxima medição.

---

## 22. O Serper substitui o `web_search`? (medido) — e a correção da §15.2

Pergunta levantada: se o snippet basta (§18), por que pagar $10/1.000 na Anthropic quando o
Serper cobra $1/1.000? A §15.2 projetou a cascata inteira em **$0,95/mil atrativos** com base
nesse preço. Medido em 2026-09-01, e a projeção não sobrevive.

### 22.1 O teste

Mesma sonda da §18 (`scripts/poc/search_snippets_probe.py`), mesmos três atrativos obscuros
da §15.1, mesmas duas queries derivadas só de nome+município, mesma lista de fatos-alvo. O
único parâmetro trocado é o provedor — condição necessária para que a diferença acuse a fonte,
e não o método.

### 22.2 Resultado

| provedor | fatos fortes | tokens/atrativo | $/atrativo | determinístico |
|---|---|---|---|---|
| Sonnet + `web_search` (baseline) | 10/10 | ~11.900 | $0,0758 | — |
| **Tavily**, 2 queries, snippet (§18) | **9/10** | 2.311 | $0,0152 | **sim** (3 rodadas, mesmas 26 URLs) |
| **Serper**, 2 queries, snippet do SERP | **2-3/10** | ~1.050 | **$0,0020** | **não** — 3 rodadas idênticas deram 3/2/2 |

**7,6x mais barato e um terço dos fatos.** E a variação entre chamadas idênticas é do tamanho
do próprio resultado: a diferença entre 3/10 e 2/10 é uma URL entrar ou não no top-5.

### 22.3 O achado: a recuperação funciona, o que falta é extração

As URLs vêm **certas**. Para "Mirante da Lagoa Guarapari" o Serper devolveu
`buser.com.br/.../mirante-da-lagoa-de-carais`, o TripAdvisor da Lagoa de Caraís, e
`atlantes.com.br/lagoacocacola/` — literalmente a página do apelido que o fato-alvo pede.

O que muda é o texto que acompanha cada URL:

| | o que devolve por resultado |
|---|---|
| Tavily (`content`) | trecho **extrativo**, escolhido por relevância semântica, ~150 palavras |
| Serper (`snippet`) | o excerto do SERP do Google, ~160 caracteres, cortado em volta do termo da query |

**São produtos diferentes, não preços diferentes do mesmo produto.** Serper, SerpApi e
SearchApi revendem o SERP; Tavily e Exa vendem extração. Fechar a lacuna do Serper exigiria
ler a página — o passo que a §18 já reprovou (44.511 tokens, 7/10 fatos).

### 22.4 Duas armadilhas da sonda, achadas aqui

Ambas do mesmo feitio das da §18.4: produzem número errado com cara de achado.

1. **A URL ficava fora do contexto.** O slug carrega fato (`lagoacocacola`), custa ~10 tokens,
   e a lane manda a URL para o prompt de qualquer jeito, porque precisa dela para o `fontes`
   (§21.7). Sem URL o Serper marca 2/10 com 718 tokens; com URL, 2-3/10 com ~1.050. Fica atrás
   da flag `--com-url`, **desligada por padrão**, para não quebrar a comparação com a Tavily da
   §18 — que foi medida sem ela.
2. **Pedir mais resultados piora.** `--num 10` deu **2/10 contra 3/10** e 50% mais token. O
   Google **recompõe** o SERP conforme o `num`; o conjunto de 10 não é superset do de 5, e a
   página do apelido caiu fora. Manter em 5.

### 22.5 O que isso corrige

A projeção de $0,95/mil da §15.2 somava **o preço do Serper com a taxa de fato da Tavily** —
dois provedores que não se combinam. Corrigido:

| configuração | por 1.000 atrativos |
|---|---|
| hoje (Sonnet + `web_search`, ponderado §15.1) | $74,90 |
| ~~cascata com Serper~~ | ~~$0,95~~ — **morta**: 2-3/10 fatos |
| **cascata com Tavily** | **$15,20** (4,9x) |

A ponta dos 39x da §18 não existe. O intervalo real é **4,9x**, e a escolha de provedor deixa
de ser otimização de preço: é a diferença entre a lane ter fato e não ter.

### 22.6 Previsão sobre Brave e CSE — a pendência da §15.3 fica desarmada

Não medidos (sem key), mas ambos são da classe SERP pela forma do campo que devolvem: Brave
entrega `web.results[].description` e o Google Programmable Search entrega `items[].snippet`,
os dois excertos curtos do próprio índice. **Esperar 2-3/10, não 9/10.**

Isso desarma a recomendação da §15.3 de comprar a Brave Search API para fechar o teste: o teste
foi fechado pelo lado do Serper, e a Brave está do mesmo lado. Os providers `serper` e `cse`
ficam na sonda para quem quiser confirmar — o CSE é grátis nas primeiras 100 consultas/dia.

**Armadilha de configuração do CSE, registrada antes de custar uma conclusão:** o mecanismo
precisa estar marcado como *"pesquisar em toda a web"*. O padrão do painel restringe aos sites
listados e devolve zero resultado para atrativo obscuro — falha que parece cobertura da fonte e
é configuração da conta.

### 22.7 Veredito

**Não.** O Serper é o provedor certo para a pergunta *"quais URLs falam deste atrativo"* e o
errado para *"quais fatos existem sobre ele"*, que é a pergunta da lane (§13.4). Dentro da
cascata da §15.2, o passo de busca dos 95% obscuros pede um provedor **extrativo** — Tavily
medida, Exa ainda não. O ganho contra o `web_search` continua real, mas é **4,9x**, e custa
$15,20 por mil atrativos, não $0,95.

Ferramenta: `.venv/bin/python scripts/poc/search_snippets_probe.py --provider serper --com-url`
(`--self-check` cobre os parsers de Serper e CSE offline, incluindo resposta vazia). Custo desta
medição: ~$0,03.

---

## 23. A cascata Tavily completa, com a redação (medido)

A §18 mediu o **insumo**: o snippet da Tavily carrega 9 dos 10 fatos que o `web_search`
carrega, por 2.311 tokens em vez de 11.900. A §22 mediu o **provedor**: revendedor de SERP
não serve. Nenhuma das duas mediu a **redação** — a linha "flash-lite free + contexto = $0"
da §11.3 sempre foi projeção, e o veredito da §18.5 dizia isso em voz alta: *"a lane precisa
da camada que consome esses 2.311 tokens"*.

Medido em 2026-09-08. Sonda: `scripts/poc/cascade_probe.py`. Custo total: ~$0,80.

### 23.1 O método

Os mesmos três atrativos obscuros da §15.1 e os mesmos dois atrativos **falsos** da §19,
com o prompt de produção (`COPYWRITER_SYSTEM` + `_build_context`) e sem ferramenta —
o contexto já vem pronto. A busca roda **uma vez** e é gravada em disco; todo modelo recebe
byte a byte o mesmo texto, senão a comparação mediria a variância do ranking da Tavily.

Três medidas, e a segunda é a que decide:

1. **Transferência** — o fato está no contexto; entra na prosa? Fato que fica no snippet e
   não entra no texto é fato que a base não recebe.
2. **Fabricação** — os dois atrativos inexistentes entram com o contexto real que a Tavily
   devolve para eles, que é ruído do município. Escrever confiante sobre um lugar que não
   existe **com o contexto na frente** é pior que escrever sem contexto: houve a chance de
   perceber.
3. **Obediência** — travessão, markdown e dado operacional são proibidos pelo prompt, e a
   lane grava a saída direto na coluna.

O controle é a lane de hoje: Sonnet + `web_search`, prompt inteiro, ferramenta ligada.

### 23.2 Primeiro achado: a §18 não reproduz

A sonda da §18, **sem uma linha alterada**, rodada no mesmo dia deste teste:

| | 2026-08-20 (§18) | 2026-09-08 (hoje) |
|---|---|---|
| fatos fortes, Tavily 2 queries | **9/10** | **4/10** |
| Mirante da Lagoa | 5/5 | **1/5** |
| tokens/atrativo | 2.311 | 2.859 |

Perdidos em 19 dias: *Parque Estadual Paulo César Vinha*, *coloração avermelhada*, *apelido
Lagoa da Coca-Cola*, *trilha em restinga*. A segunda query passou a devolver Florianópolis e
Rio de Janeiro no lugar de Guarapari.

**Não é a query.** Testada a variante com a UF nas duas buscas: o ruído sai (2.130 → 1.363
tokens/atrativo) e **os fatos não voltam** — Mirante da Lagoa continua 1/5. É cobertura do
índice da Tavily que caiu.

A §18.5 declarou o resultado determinístico com base em 3 rodadas idênticas *na mesma
sessão*. Era verdade e era insuficiente: mede repetibilidade, não estabilidade. **A §18.5
já apontava o risco certo** — *"o que sobra de risco não é técnico, é de cobertura"* — só
subestimou a velocidade: não é preciso escalar a amostra para vê-lo, bastam três semanas.

### 23.3 A redação: transferência

Teto disponível no contexto de hoje: **5/10**. O que cada modelo pôs na prosa:

| modelo | fatos na prosa | inventou falso | viola prompt | $/atr (LLM) | $/atr (+busca) |
|---|---|---|---|---|---|
| **producao** — Sonnet + `web_search` | **9/10** | **1/2** (10 afirmações) | 1 | $0,1095 | $0,1095 |
| sonnet-4-5 + contexto Tavily | 3-4/10 | **2/2** (10 afirmações) | 0 | $0,0134 | $0,0294 |
| haiku-4-5 + contexto Tavily | 3-4/10 | **2/2** (6) | 0 | $0,0046 | $0,0206 |
| deepseek-chat + contexto Tavily | 1/7 | **2/2** (5) | 2 | $0,0009 | $0,0169 |
| flash-lite free + contexto Tavily | 3/10 | **2/2** (1) | 0 | **$0** | **$0,0160** |

**O modelo caro não escreve melhor com o mesmo contexto.** Sonnet e Haiku trocaram de lugar
entre duas rodadas (3/10 ↔ 4/10) — a diferença entre eles está dentro do ruído. Os 9/10 da
lane de produção **não vêm do Sonnet, vêm do `web_search`**: dado o mesmo contexto magro, o
Sonnet entrega o mesmo que o flash-lite gratuito.

Isso responde a §12 por outro caminho. A pergunta era "por que Sonnet e não Haiku"; a
resposta medida aqui é que, **na etapa de redação**, o modelo não é a variável. A variável é
a recuperação.

Os dois fatos que ficaram no contexto e não entraram na prosa (*lagoa de Caraís*, *região de
Santa Isabel*) são casos em que o snippet cita o fato **sem ligá-lo ao atrativo** — o
Caraís aparece numa lista de lagoas de Guarapari, não como a lagoa deste mirante. Os modelos
não afirmaram a ligação. Está certo: é o prompt funcionando, não falha de redação.

O deepseek foi o único a quebrar formato (markdown, travessão) e o único a dar 429 no
OpenRouter no meio da rodada. Denominador 7, não 10, por isso.

### 23.4 O achado que decide: a cascata não protege contra fabricação

**Os cinco modelos com contexto inventaram nos dois atrativos falsos. Cinco de cinco, 2/2.**

O contexto não impediu nada. Amostras:

> *"A Cachoeira do Sino Azul ergue-se a **50 metros** de altura nas montanhas capixabas, a
> cerca de **12 quilômetros** da sede de Afonso Cláudio."* — sonnet-4-5

> *"Nas profundezas da Mata Atlântica capixaba, a água desce em queda livre de **50 metros**"*
> — haiku-4-5

Dois modelos diferentes cravaram a mesma altura inventada para uma cachoeira que não existe.

A §19 mediu 6/6 fabricações **sem busca** e concluiu que memória paramétrica não serve. Esta
seção acrescenta o que faltava: **dar contexto não resolve**. O modelo lê ruído genérico
sobre o município e escreve por cima dele.

**O controle de produção também falha, mas menos.** Sonnet + `web_search` abstive num dos
dois e inventou no outro — e o caso inventado é o pior tipo, porque o modelo **disse em voz
alta** que não achou:

> *"Não foi possível encontrar informações verificáveis sobre a Cachoeira do Sino Azul (…)
> Como não há contexto suficiente para escrever uma descrição precisa e factual conforme as
> diretrizes, vou criar uma descrição sensorial mais curta"* — e em seguida escreveu 1.898
> caracteres afirmando tom azulado da água, 340 espécies de aves e textura das trilhas.

O prompt tem a válvula certa (*"escreva uma descrição sensorial mais curta, sem afirmações
factuais específicas"*), o modelo a invocou pelo nome, e mesmo assim produziu 10 afirmações
concretas. **A instrução de abstenção não é executável por prompt.** Precisa de gate fora do
modelo.

Isso não é regressão da cascata: **é defeito preexistente da lane**, que a §19 não podia ver
porque mediu sem busca. A cascata piora de 1/2 para 2/2 — mas o alvo certo não é escolher
entre 1/2 e 2/2, é levar os dois a 0/2.

### 23.5 O custo real, e o que ninguém tinha medido

A lane de produção sobre os **três obscuros reais** custou $0,0589 / $0,0535 / $0,1139 —
média **$0,0755**, confirmando o $0,0758 da §15.1 com três semanas de distância. O número
antigo está certo.

O que era novo: **atrativo inexistente custa o dobro.**

| | $/atrativo | tokens de input |
|---|---|---|
| obscuro real (média de 3) | $0,0755 | 13-27 mil |
| **falso (média de 2)** | **$0,1606** | **30-55 mil** |

O modelo não desiste: busca, não acha, busca de novo, esgota o `max_uses: 3`. **2,1x o custo
de um registro bom, gasto para produzir um registro que não deveria existir.** Numa carga
inicial nacional alimentada por varredura automática, o lixo da Nascente é a parte cara.

Isso conecta com a §21.9 (auditoria de fontes): 1,1% dos registros tinham caminho
inexistente. Aquilo era o sintoma; isto é o preço.

### 23.6 Veredito

- **A metade da redação está medida, e ela é barata.** Dado o mesmo contexto, flash-lite
  gratuito entrega o que o Sonnet entrega. A camada de escrita não precisa de modelo caro.
- **A metade da busca não está estável.** 9/10 virou 4/10 em 19 dias, sem mudar nada. O
  ganho de 4,9x da §18 pressupõe uma taxa de fatos que não se sustentou — com 4/10, a
  cascata é barata e **erra mais**, não é barata e igual.

  > **Corrigido pela §24.** Isto foi medido em 3 atrativos do OSM sem Wikipedia — o pior caso,
  > não o caso médio. Sobre 50 atrativos **reais do TripAdvisor**, que são o trabalho, a
  > cobertura é de **98%** e a groundedness de **88%**. A §23 mediu o pior caso e o tratou
  > como caso médio.
- **A fabricação é o bloqueio real, e é dos dois lados.** 2/2 na cascata, 1/2 na produção.
  Nenhuma decisão de modelo ou provedor resolve; é preciso um gate determinístico fora do
  LLM — o atrativo precisa existir numa fonte estruturada (Wikidata, OSM, Places `place_id`)
  antes de qualquer descrição ser escrita. Trocar de motor de busca é otimizar o custo de
  uma etapa que hoje aceita alimentar registro inexistente.
- **Antes de trocar o provedor em produção, medir a estabilidade.** Uma medição por sessão
  não basta. A sonda é barata (~$0,05) e determinística; rodar semanalmente por um mês diz
  se 4/10 é o novo normal ou uma oscilação.

Ferramentas:
`.venv/bin/python scripts/poc/cascade_probe.py --self-check` (offline, sem key) ·
`--fetch` (busca e cacheia o contexto) · `--models producao` (o controle da lane de hoje).

---

## 24. A cascata serve para os 10 mil do TripAdvisor? (medido) — e a correção da §23

A §23 mediu 3 atrativos escolhidos por serem os mais difíceis possíveis: vindos do OSM, sem
artigo na Wikipedia, os "95% obscuros" da §15.1. **Não é a distribuição do trabalho.** O
trabalho é sincronizar ~10 mil atrativos do TripAdvisor, e atrativo do TripAdvisor tem página
no TripAdvisor por definição.

Medido em 2026-09-08, logo depois da §23, sobre 50 atrativos **reais** da amostra de
`docs/poc/pilot-100/atrativos.json` — os mesmos que a rota de produção já processou, com as
**mesmas queries que ela emitiu** e as `fontes` que ela citou. Trocar só o provedor, mantendo
a query, elimina o confundidor "a query era ruim". Sonda: `scripts/poc/cascade_scale_probe.py`.

### 24.1 A §23 mediu o pior caso e o tratou como caso médio

| | amostra da §23 (3, OSM sem Wikipedia) | **50 atrativos reais do TA** |
|---|---|---|
| contexto menciona o atrativo | (o caso 1/5 do Mirante da Lagoa) | **49/50 = 98%** |
| recupera ≥1 domínio que a produção citou | — | **40/50 = 80%** |
| tokens/atrativo | 2.859 | **1.247** |
| $/atrativo (busca) | $0,0160 | $0,0160 |

A única falha de cobertura é `Secretaria De Estado Do Turismo - Setu` — **que não é atrativo**.
É registro-lixo da Nascente que o sweep do TA arrastou junto. A Tavily não falhou; ela
corretamente não achou conteúdo turístico sobre uma repartição pública.

**A medida de menção é estrita de propósito:** exige os termos *identificadores* do nome,
descartando os genéricos ("praia", "parque", "centro"). Contexto que fala do município e
contém a palavra "praia" **não** conta como cobertura de "Praia da Costa" — porque é
exatamente esse insumo que produziu as 2/2 fabricações da §23.4.

### 24.2 A redação, sobre a distribuição real

30 dos 50 passaram pelo flash-lite gratuito com o prompt de produção, sem ferramenta.
Medida: **groundedness** — cada afirmação concreta do texto gerado (ano, medida, nome próprio
composto) existe no contexto que o alimentou? Afirmação que não está no contexto veio da
memória paramétrica ou da invenção, e nada no pipeline distingue as duas.

**115 de 130 afirmações concretas fundamentadas = 88%.**

As 15 soltas concentram-se nos **famosos**, não nos obscuros:

| atrativo | soltas | o que escapou |
|---|---|---|
| Praia de Copacabana | 6 de 9 | *Forte de Copacabana*, *Copacabana Palace*, *Zona Sul* |
| Pelourinho | 3 de 5 | *Catedral de Salvador*, *Igreja do Rosário dos Pretos* |
| Praia da Costa / Pedra da Cebola | 1 cada | *Mata Atlântica* |

São fatos **verdadeiros** vindos da memória do modelo — e é justamente por isso que contam
como risco: a §19 mediu que a mesma memória que acerta em Copacabana inventa em Brejetuba.
O pipeline não tem como distinguir as duas, então a regra tem que ser mecânica.

### 24.3 O gate que converte o risco em ausência de risco

Os 2-3% sem cobertura projetam **200 a 300 dos 10 mil** recebendo descrição escrita sobre
contexto que não fala do atrativo. Não precisa de LLM nem de julgamento para resolver:

```
se o contexto da busca não menciona os termos identificadores do nome do atrativo:
    não escrever descrição  (o registro segue sem descrição, não com descrição inventada)
```

Custo do gate: **zero**. Já está implementado e testado em `cascade_scale_probe.py`
(`menciona()`, com self-check offline). É o mesmo formato de gate que a §23.6 pedia — a
diferença é que aqui ele não depende de fonte estruturada externa (Wikidata/OSM), depende só
do que a busca devolveu.

O mesmo vale para a groundedness: medir por texto gerado e mandar para a DLQ o que ficar
abaixo do limiar, em vez de gravar no Mar.

### 24.4 A conta dos 10 mil

| rota | $/atrativo | **10.000 atrativos** | qualidade medida |
|---|---|---|---|
| produção hoje (Sonnet + `web_search`) | $0,0749 | **$749** | 9/10 fatos; inventa 1/2 nos inexistentes |
| **cascata + Haiku 4.5** | **$0,0206** | **$206** | 98% cobertura, 88% groundedness |
| **cascata + flash-lite free** | **$0,0160** | **$160** | idem, mas 3 de 30 deram HTTP 503 |
| assinatura Max (§21) | $0,0979 equiv. | 1 semana de cota | oráculo de qualidade, não motor |

**A economia é de $543 a $589, e a busca passa a ser 78-100% da conta** — o modelo praticamente
some do custo, como a §23.3 previu.

Duas ressalvas operacionais, medidas aqui:

1. **O free tier do flash-lite não aguenta 10 mil.** 3 de 30 chamadas voltaram `503 Service
   Unavailable` (10%), e o free tier tem teto diário. Para a carga inicial, **Haiku 4.5 a
   $46 no total** compra estabilidade por 29% a mais que o flash-lite. É o corte certo.
2. **O free tier da Tavily rende 500 atrativos/mês** (1.000 créditos ÷ 2 queries). Os 10 mil
   exigem plano pago: 20 mil queries × $0,008 = **$160**. É a linha inteira do custo.

### 24.5 Veredito

**Serve.** Sobre a distribuição real do trabalho — atrativos do TripAdvisor, não obscuros do
OSM — a cascata cobre 98%, fundamenta 88% das afirmações concretas e custa **$206 contra
$749**, com um gate determinístico e gratuito fechando os 2-3% que sobram.

A §23 não estava errada: estava medindo outra coisa. Os três atrativos dela continuam sendo
o pior caso real, e continuam mostrando que **contexto ruim produz fabricação confiante em
todo modelo, do flash-lite ao Sonnet**. O que a §24 acrescenta é que, no TripAdvisor, contexto
ruim é 2-3% da carga e é **detectável antes de escrever** — o que muda a decisão de "não use"
para "use, com o gate".

O que a §23 mediu e continua valendo sem correção:
- o modelo caro não escreve melhor com o mesmo contexto (§23.3);
- a instrução de abstenção do `COPYWRITER_SYSTEM` não é executável por prompt (§23.4);
- atrativo inexistente custa 2,1x na rota de produção (§23.5) — o gate da §24.3 também
  elimina esse gasto, porque nem chega a chamar o modelo.

Ferramenta: `.venv/bin/python scripts/poc/cascade_scale_probe.py --self-check` (offline) ·
`--n 50` (cobertura e evidência) · `--n 30 --write` (a cascata inteira, com groundedness).
Custo desta medição: ~$1,30.

---

## 25. Os 200 cronometrados: a cascata cabe em semanas? (medido)

A §24 respondeu qualidade e custo. Faltava a pergunta de prazo: quantos atrativos por hora a
cascata inteira processa, qual provedor trava primeiro, e o que isso diz sobre os ~10 mil.

Medido em 2026-09-10, depois de implementar a cascata na lane (`write_cascade`, atrás de
`atrativo_description_cascade_enabled`). Sonda: `scripts/poc/cascade_timed_probe.py`. Custo
total: **$1,28 em Haiku + 532 créditos Tavily** do free tier (≈$4,26 se fosse PAYGO).

### 25.1 O método

A sonda roda **o código da lane, não uma réplica**: `RealTavilyClient` →
`TourismCopywriter.write_cascade` (gate de menção → Haiku 4.5 sem ferramenta → gate de
groundedness) → `RealLLMClient`. O único enxerto é instrumentação: hooks httpx que contam cada
resposta HTTP por provedor — inclusive as que o retry do SDK e o tenacity escondem — e um
wrapper que lê o `usage` da Anthropic. Retry fica no padrão de produção.

Duas fases sobre 200 atrativos distintos, embaralhados antes de dividir para que as duas
recebam a mesma mistura:

1. **sequencial** (concorrência 1, 50 atrativos) — latência limpa, sem disputa. É o formato
   do sweep de hoje, que enriquece inline, um registro por vez;
2. **concorrente** (concorrência 8, 150 atrativos) — throughput e comportamento sob rate limit.

**A amostra não é 200 do TripAdvisor.** O banco local tem 121 atrativos (os do piloto e mais
20), não 200 —
varrer mais exige a sessão do TA, que está em `brave:ta:needs_bootstrap` (bootstrap manual).
Ficou assim, com a origem gravada em cada registro e relatada em separado:

| origem | n | o que é |
|---|---|---|
| `ta-piloto` | 96 | os 100 de `pilot-100` (4 nomes repetidos) |
| `ta-banco` | 20 | o que `rio_records` tem além do piloto |
| `ta-snapshot` | 11 | `atrativos_images.json`, de um estado anterior do banco |
| `ta-fixture` | 19 | a página oa30 real do TA em `tests/fixtures` (nome sem município) |
| `cadastur` | 54 | parques de lazer/temáticos do Cadastur (datasets 05 e 10) |
| **da lane TA** | **146** | |

O Cadastur não é a distribuição do TA: é a cauda "nome de empresa" (*Mrx Entretenimentos Ltda*,
*Cia De Rodeio Sa*) — o mesmo tipo de registro que o sweep arrasta para a Nascente. Serve para
throughput; para os gates, o número que vale é o dos 146.

### 25.2 Throughput e latência

| fase | n | conc. | wall | **atrativos/hora** | p50 | p95 | busca p50 / p95 | Haiku p50 / p95 |
|---|---|---|---|---|---|---|---|---|
| sequencial | 50 | 1 | 433 s | **416** | 8,9 s | 10,9 s | 2,1 / 3,8 s | 6,5 / 8,4 s |
| concorrente, cliente antigo | 150 | 8 | 114 s | *inválido* — 84 falharam | | | | |
| **concorrente, `retry-after`** | 150 | 8 | 264 s | **2.043** | 7,8 s | **68,6 s** | 1,3 / 62,1 s | 6,5 / 8,7 s |

**O modelo é 74% do tempo de cada atrativo** (6,5 de 8,9 s na fase limpa). As duas buscas
correm em paralelo e somam ~2 s. O Haiku escreve 476 tokens de saída por atrativo sobre 2.788
de entrada.

O p95 de 68,6 s da fase concorrente é **espera, não trabalho**: 16 dos 150 atrativos passaram
60 s parados atrás de um 429 da Tavily (25.3). A mediana não piora: 7,8 s, contra 8,9 s na fase limpa.

### 25.3 Os provedores: a Tavily é o gargalo, a Anthropic nem aparece

| provedor | 200 | 429 | 5xx/529 | taxa de 429 |
|---|---|---|---|---|
| Tavily, rodada 1 (cliente antigo) | 232 | **504** | 0 | **68%** |
| Tavily, rodada 2 (`retry-after`) | 300 | 31 | 0 | 9% |
| Anthropic, Haiku 4.5 (as duas) | 250 | **0** | 0 | 0% |

**A chave da Tavily é Development: 100 RPM.** Com 8 atrativos em voo, 2 buscas cada e ~9 s por
atrativo, a demanda chega a ~110 buscas por minuto e a Tavily responde `429` com
`retry-after: 60`. Conferido na documentação: Development = 100 RPM, Production = 1.000 RPM, e a
chave Production exige plano pago ou PAYGO.

**Na rodada 1 isso derrubou 84 de 150.** O cliente fazia backoff de 2-10 s com 3 tentativas: as
três caíam dentro do mesmo minuto bloqueado, cada atrativo falhava em 4,5 s — e, na lane, cada
falha queimaria um `descricao_attempts` do registro. Três sweeps sob a parede e o registro sai
da fila de descrição para sempre. **Corrigido no cliente**: o `retry-after` é respeitado (teto
60 s, 4 tentativas — cabe nos 300 s de `enrich_places`). Na rodada 2, os mesmos 150: **zero
falhas**, 31 × 429 absorvidos como contrapressão.

A Anthropic devolveu nos headers os limites desta organização para o Haiku: **10.000 RPM, 10 M
tokens de entrada/min, 2 M de saída/min**. A 2.788 + 476 tokens por atrativo, isso é **~3.600
atrativos por minuto** — 70x o teto da chave Development da Tavily e 7x o da Production.

### 25.4 Os gates, sobre os 200

| | n | escrito | **barrado: sem menção** | **DLQ: não fundamentada** |
|---|---|---|---|---|
| **lane TA** | 146 | 129 (88%) | **3 (2,1%)** | 14 (9,6%) |
| Cadastur | 54 | 43 | 8 (15%) | 3 |
| total | 200 | 172 | 11 | 17 |

**O gate de menção reproduz a §24 com as queries da própria lane.** A §24 usou as queries que o
Sonnet de produção emitiu — que às vezes carregavam um fato que o modelo já "sabia" (*"areia
monazítica"*, *"1558"*). A lane não pode fazer isso: as queries dela são determinísticas
(`cascade_queries`: nome + município + UF + "história", e o nome entre aspas). Cobertura no TA:
143/146 = **97,9%**, contra os 98% da §24. As três barradas:

- *Secretaria De Estado Do Turismo - Setur/Es* — não é atrativo; a mesma da §24;
- *Karcará Adventure* — as duas buscas não devolvem o nome;
- *Figueira Da Esquina 🌳❤️* — **falso negativo do gate**: o emoji virou termo identificador
  obrigatório, que texto nenhum contém. Corrigido (token sem letra nem dígito não conta).

No Cadastur o gate barra 15% — é ele funcionando: *Mf-Par Adiministradora*, *Amitse*.

**O gate de groundedness manda 9% do TA para a DLQ, e a §24 errou a forma da distribuição.** Em
28 textos ela parecia bimodal (0,33-0,50 e 0,86-1,00, nada no meio), e o limiar 0,75 foi posto
"no meio da faixa vazia". Em 189 textos a faixa não existe: **dez caem entre 0,62 e 0,73**, oito
em exatos 0,67 — uma afirmação solta em cada três.

| limiar | vão para revisão | nos 10 mil |
|---|---|---|
| **0,75 (mantido)** | 17 de 189 = 9% | ~900 rascunhos |
| 0,60 | 7 de 189 = 4% | ~370 rascunhos |

Mantido em 0,75: a falha que ele impede — fato verdadeiro sem fonte na base canônica, a §24.2 —
é justamente a que nada adiante detecta, e fila de revisão é o lado barato do erro. Os
rejeitados são do tipo esperado: *Cristo Redentor* (0,44), *Parque Vila Germânica* (0,25),
*Jardim Botânico* (0,73) — lugares conhecidos, escritos de memória. Baixar deve vir de evidência do steward
sobre esses rascunhos, não de volume. O `descricao_groundedness` fica gravado por registro para
recalibrar com tráfego real.

### 25.5 Custo real

| | quantidade | $ |
|---|---|---|
| buscas Tavily | 400 (2 por atrativo, inclusive os barrados) | $3,20 a PAYGO |
| Haiku 4.5 | 189 chamadas · 527 mil tokens in · 90 mil out | $0,98 |
| **total, 200 atrativos** | | **$4,18 = $0,0209/atrativo** |

**A projeção da §24 ($0,0206) se confirma a 1,5%.** Os 11 barrados pagam a busca e não pagam
modelo — é o gate economizando o 2,1x da §23.5. Nos 10 mil: **~$209**, dos quais $160 são busca.

### 25.6 A conta dos 10 mil

| cenário | ritmo | **10 mil em** |
|---|---|---|
| 1 worker sequencial (o sweep inline de hoje) | 416/h | **24 h** |
| concorrência 8, chave Development (medido) | 2.043/h | 4,9 h |
| teto da chave Development (100 RPM ÷ 2 buscas) | 3.000/h | 3,3 h |
| teto da chave Production (1.000 RPM ÷ 2) | 30.000/h | 20 min, com ~70 em paralelo |
| **cost guard padrão ($10/dia, compartilhado)** | **~480/dia** | **21 dias** |
| **free tier da Tavily (1.000 créditos/mês)** | 500/mês | **20 meses** |

**Throughput não é a restrição.** Um único worker sequencial faz os 10 mil em um dia. As duas
travas reais são administrativas, e as duas estão na Tavily ou por causa dela:

1. **Créditos.** O free tier rende 500 atrativos por mês. Os 10 mil pedem 20 mil créditos:
   PAYGO a $0,008 = **$160** — e o PAYGO também dá a chave Production.
2. **O cost guard.** `usd_daily_budget` é $10/dia por padrão, não há override no `.env`, e ele
   é **compartilhado** com desmembramento e WhatsApp. A $0,021/atrativo, o guard para a lane
   em ~480 atrativos/dia. Sem mexer nele, os 10 mil levam **~3 semanas**. Como a busca é
   registrada no guard (a $0,008 mesmo no free tier, de propósito), ele mede a conta real.

### 25.7 Armadilhas desta medição

- **O banco não tinha 200.** 121 atrativos, e a sessão TA pede bootstrap manual.
  Wikidata (SPARQL) deu 502/504 duas vezes; os nomes do índice MTur no Commons vêm colados ao
  fotógrafo (*"DanielVianna RibeiraodaIlha Florianopolis SC"*) e inflariam o gate. Ficou TA +
  fixture + Cadastur, marcado por origem.
- **O throughput da rodada 1 (4.736/h) é falso.** Falha é rápida: 84 atrativos "processados"
  em 4,5 s cada. Só vale throughput de rodada com zero falha.
- **`/usage` da Tavily não atualizou durante a sessão** (186 antes e depois de 532 créditos).
  Se o 429 é cobrado ficou sem verificar; a conta acima conta só respostas 200.
- **O suite de integração zera tabelas de referência no banco local.** Depois dele, antes do
  reset, `local_businesses` (152.955), `municipios`, `distritos` e `config_settings` estavam em
  zero. O banco foi restaurado de um `pg_dump` tirado antes do suite — sem o dump, o reset
  mandado pelo handoff teria apagado também os 122 atrativos que o import das 130 descrições
  espera.
- **`generate()` precificava todo modelo como Sonnet.** Com Haiku, o guard veria 3x o gasto
  de modelo e travaria a lane em ~330 atrativos/dia em vez de ~480. Corrigido antes da medição.

### 25.8 Veredito

**Cabe — em dias de máquina, em ~3 semanas de calendário se nada mudar.**

- A cascata processa **416 atrativos/hora num único worker** e **2.043/hora com 8 em paralelo**,
  a p50 de 8-9 s por atrativo. Os 10 mil são um dia de worker sequencial.
- **O gargalo é a Tavily**, nas duas dimensões: rate (100 RPM na chave Development, 68% de 429
  a concorrência 8 antes da correção) e créditos (500 atrativos/mês grátis). A Anthropic não
  deu um 429 em 250 chamadas e tem folga de ~70x.
- **O prazo é decidido por dois botões, não por engenharia:** comprar ~$160 de PAYGO na Tavily
  (sem isso: 20 meses) e decidir o `usd_daily_budget` (com os $10 de hoje: ~21 dias; a $50/dia,
  ~4 dias — lembrando que o orçamento é de todas as lanes).
- **Qualidade se sustenta em escala:** 97,9% de cobertura no TA com as queries da própria lane,
  $0,0209 por atrativo, 9% dos textos para revisão humana em vez de irem para o Mar.

O que sobra antes de ligar a flag: comprar o PAYGO (chave Production), decidir o orçamento
diário, e — como sempre nesta lane — conferir `description_enrichment_enabled`, que continua
`false` no overlay e desliga a descrição inteira sem erro.

Ferramenta: `.venv/bin/python scripts/poc/cascade_timed_probe.py --self-check` (offline) ·
`--amostra` (monta os 200; precisa do banco) · `--rodar` (as duas fases; ~400 créditos) ·
`--rodar --inicio 50 --sequenciais 0` (só a concorrente). Resultados em
`scripts/poc/cascade_timed_probe.json` (rodada 1) e `cascade_timed_probe.concorrente.json`
(rodada 2).

---

## 26. A cascata com Gemini 2.5 Flash no lugar do Haiku (medido)

A §25 fechou a rota Tavily + Haiku 4.5 em ~$209 para os 10 mil, com a busca sendo $160 disso.
Pergunta: trocar o redator por **Gemini 2.5 Flash** muda a conta, a qualidade ou o prazo?

Medido em 2026-09-14. Sonda: `scripts/poc/cascade_gemini_probe.py`. Custo total: **$1,89 no
OpenRouter + 280 créditos Tavily** do free tier.

### 26.1 O método

O código da lane, sem alteração: `TourismCopywriter.write_cascade` (gate de menção → redator
sem ferramenta → gate de groundedness). Dois enxertos, ambos fora dela:

1. **A busca roda uma vez e fica em disco.** 140 atrativos × 2 queries da `cascade_queries`,
   e os três redatores recebem byte a byte o mesmo contexto. Sem isso a comparação mediria a
   deriva da Tavily (§23.2), não o modelo.
2. **`generate()` é um adaptador para o OpenRouter**, com a forma do `RealLLMClient`
   (max_tokens 2048, sem tools, `data_collection: deny`). O custo vem do campo `usage.cost` do
   OpenRouter — o valor cobrado, não uma tabela.

**Amostra: 140, não 200.** O free tier da Tavily tinha 286 créditos (714/1.000 usados) e a
conta não tem PAYGO. Ficaram os 140 primeiros atrativos **da lane TA** da amostra da §25
(`ta-piloto`, `ta-banco`, `ta-snapshot`, `ta-fixture`), na mesma ordem embaralhada; o Cadastur
saiu. Cada fase: 40 sequenciais + 100 a concorrência 8.

### 26.2 Duas armadilhas antes de medir

1. **A API direta do Google não serve o 2.5 Flash para esta conta.** `gemini-2.5-flash` e
   `gemini-2.5-flash-lite` respondem `404 — "This model models/gemini-2.5-flash is no longer
   available to new users"` (a §9.1 já tinha visto). A página de depreciações diz *"No shutdown
   date announced"* — o modelo existe, mas só para quem já o usava. **O caminho é o OpenRouter**
   (`google/gemini-2.5-flash`, provedor Google, mesmo preço de lista: $0,30 / $2,50 por MTok,
   thinking cobrado como saída) — que já é fornecedor do Brave, pelo DeepSeek. Vertex AI não
   foi testado.
2. **Os créditos da Anthropic acabaram no meio da rodada do Haiku.** 88 chamadas passaram, as
   50 seguintes voltaram `400 — "Your credit balance is too low to access the Anthropic API"`, e
   a lane engoliu o erro como `copywriter_failed_kept_floor`. O controle foi refeito com
   `anthropic/claude-haiku-4.5` pelo OpenRouter (mesmo preço, mesmo adaptador do Gemini, o que
   ainda deixa a latência comparável). **Consequência fora da POC:** hoje qualquer rota Anthropic
   do copywriter — Sonnet ou Haiku — falha calada até recarregar os créditos.

### 26.3 Resultado

| | **Gemini 2.5 Flash** | Gemini 2.5 Flash + thinking | Haiku 4.5 (controle) |
|---|---|---|---|
| escrito e aprovado | **131 / 140 (93,6%)** | 129 (92,1%) | 128 (91,4%) |
| barrado: sem menção | 2 | 2 | 2 |
| DLQ: não fundamentada | **7 (5,0%)** | 6 (4,3%) | 10 (7,1%) |
| falha | 0 | 3 (`finish_reason: error`) | 0 |
| groundedness média | 0,93 | 0,92 | 0,94 |
| texto p50 | 1.640 car. | 1.955 car. | 1.740 car. |
| tokens in / out (thinking) | 3.759 / 416 (0) | 3.781 / 1.276 (802) | 4.640 / 523 |
| **$ modelo / chamada** | **$0,00217** | $0,00429 | $0,00725 |
| **$ / atrativo, com busca** | **$0,0181** | $0,0202 | $0,0232 |
| latência do modelo p50 / p95 | **4,3 / 5,4 s** | 8,9 / 14,9 s | 7,2 / 8,5 s |

**O Gemini custa 3,3x menos por chamada que o Haiku e responde 1,7x mais rápido**, com
groundedness igual e menos textos para a DLQ. Mas a busca é $0,016 dos $0,018: **o modelo virou
12% da conta**. A troca tira $0,005 por atrativo.

**Thinking não compra nada.** Dobra custo e latência, não mexe na groundedness, e trouxe as
três únicas falhas (resposta cortada com `finish_reason: error`). O OpenRouter manda o 2.5 Flash
**sem** thinking por padrão; a API direta do Google liga thinking dinâmico por padrão — quem for
por lá precisa de `thinkingBudget: 0`.

**O contexto engordou desde a §25.** O Haiku lia 2.788 tokens por atrativo em 10/09 e lê 4.640
hoje, sobre as mesmas queries — a Tavily passou a devolver mais texto. Por isso o Haiku saiu a
$0,0232 e não os $0,0209 da §25. A comparação desta seção é pareada (mesmo contexto), então vale;
o número absoluto da §25 é que envelheceu.

### 26.4 O que passa pelo gate e não devia

Groundedness mede se a afirmação está no contexto. Não mede se o texto é uma descrição. Lendo os
aprovados, cada modelo erra de um jeito:

| defeito que chega na coluna | Gemini 2.5 Flash | Haiku 4.5 |
|---|---|---|
| nota de bastidor em vez de descrição | 0 | **4** |
| título markdown (`# Nome`) ou negrito | 0 | **3** |
| texto truncado | **1** | 0 |
| dado operacional proibido | 1 | 0 |
| **total** | **2 (1,4%)** | **7 (5,0%)** |

- **Haiku escreve para o operador.** *"Há uma confusão nos registros que recebemos. As fontes
  indicam claramente que a Rua das Pedras é um atrativo localizado em Armação dos Búzios, não em
  Campos dos Goytacazes"* — aprovado, groundedness alta, iria para `descricao_editorial`. O Haiku
  está **certo** sobre o registro (é lixo da Nascente); o defeito é dizer isso dentro da prosa.
- **Gemini escreve por cima.** O mesmo tipo de registro — *Cristo Redentor* com município Ubá/MG
  — virou *"Em Ubá, Minas Gerais, há um convite para desbravar um dos maiores símbolos do Brasil,
  mesmo que o monumento principal esteja no Rio de Janeiro"*, groundedness 0,92, aprovado. O
  Haiku recusou esse (0,67, DLQ). Nenhum modelo sinaliza o registro-lixo de forma aproveitável:
  um vaza a denúncia na prosa, o outro a esconde.
- **O truncado do Gemini** (*Inhotim*: `"Em Brumadinho, Minas Gerais,"`, 28 caracteres) veio com
  `finish_reason: error` e passou porque texto sem afirmação concreta tem groundedness 1,0. Uma
  linha resolve: rejeitar `finish_reason != stop`.
- O "operacional" do Gemini é o Projeto Tamar citando horário de funcionamento. As outras três
  marcações do regex eram "melhor hora do dia" — permitida pelo prompt.

Com as duas travas baratas que isso pede (`finish_reason` para o Gemini; bastidor/markdown para o
Haiku), **o Gemini aprova 130 e o Haiku 121** dos 140.

### 26.5 A conta dos 10 mil

Mesma forma da §25 e do memorando de 10/09 (1.000 créditos grátis da Tavily abatidos; reserva =
atrativos sem texto aprovado tentados mais 2 vezes):

| | Haiku (10/09, §25) | Haiku (hoje, pareado) | **Gemini 2.5 Flash** |
|---|---|---|---|
| Tavily, 19 mil buscas pagas | $152 | $152 | $152 |
| modelo | $49 | $72 | **$21** |
| **esperado** | $201 | $224 | **$173** |
| reserva de retentativa | $49 | $51 | $25 |
| **teto** | $250 | $275 | **~$200** |
| cost guard $10/dia | ~21 dias | ~23 dias | **~18 dias** |
| cost guard $50/dia | ~4 dias | ~5 dias | ~4 dias |
| 1 worker sequencial (busca 1,8 s + modelo) | 416/h | ~400/h | **~590/h** |

**A economia é de $51 contra o Haiku de hoje (−23%)**, ou $28 contra o número aprovado em 10/09.
O throughput continua sem importar: a chave Development da Tavily trava em 3.000 atrativos/h
antes de qualquer modelo.

### 26.6 Veredito

- **Gemini 2.5 Flash é o redator melhor para esta cascata**, medido lado a lado no mesmo
  contexto: 3,3x mais barato por chamada, 1,7x mais rápido, menos DLQ, e menos defeito passando
  pelo gate (2 contra 7).
- **Mas não muda a ordem de grandeza da conta.** $173 contra $224: a busca é 88% do custo com
  Gemini. A decisão de orçamento continua sendo a da §25 — comprar ~$152 de PAYGO na Tavily e
  decidir o `usd_daily_budget`.
- **Três condições para usar:**
  1. **Pelo OpenRouter.** A API direta não aceita conta nova no 2.5 Flash. É um modelo legado
     (Google recomenda 3.x), sem data de desligamento, mas com a porta de entrada já fechada.
  2. **Thinking desligado** (padrão no OpenRouter).
  3. **`generate()` ganha um caminho OpenRouter** — hoje ele é só `AsyncAnthropic`. O SDK OpenAI
     já está no client para o `extract()`. Junto: preço do Gemini na tabela do cost guard e
     rejeição de `finish_reason != stop`.
- **Independente do modelo:** os créditos da Anthropic estão zerados hoje, e registro com
  município errado passa pelos dois gates com qualquer redator. O segundo pede gate na Rio, não
  no copywriter.

Ferramenta: `.venv/bin/python scripts/poc/cascade_gemini_probe.py --self-check` (offline) ·
`--buscar --n 140` (280 créditos) · `--rodar --modelo google/gemini-2.5-flash [--thinking]` ·
`--rodar --modelo anthropic/claude-haiku-4.5`. Resultados em
`scripts/poc/cascade_gemini_probe.{gemini-2.5-flash,gemini-2.5-flash-thinking,claude-haiku-4.5}.json`
(com os textos); o contexto em `cascade_gemini_probe.contexts.json`. O arquivo
`cascade_gemini_probe.claude-haiku-4-5.json` é a rodada nativa que bateu nos créditos.

---

## 27. Wikipedia, Wikivoyage, Wikidata, OSM e OpenTripMap no lugar da Tavily (medido)

A §26 deixou a busca como 88% da conta ($152 de $173). A pergunta: fontes abertas e gratuitas
substituem a Tavily?

**A §15.1 e a §16 já tinham respondido "não", mas na amostra errada.** Mediram atrativos
obscuros do OSM no ES, onde 5% tinham Wikipedia, e a §24 mostrou depois que a carga real são
atrativos do TripAdvisor. Esta seção mede as fontes **nos mesmos 140 atrativos da §26**, cujo
contexto Tavily e texto Gemini já estavam em disco. A comparação é pareada: mesmo atrativo,
mesmo redator (Gemini 2.5 Flash), mesmo prompt, mesmos dois gates.

Medido em 2026-09-14. Sonda: `scripts/poc/fontes_abertas_probe.py`. Custo: **$0,53 no
OpenRouter**. As fontes são gratuitas e sem chave.

### 27.1 O método

`--coletar` busca cada atrativo nas quatro fontes. `--escrever` roda `write_cascade` sem
alteração, com o contexto de cada fonte no lugar da Tavily: cada fonte sozinha e as quatro
juntas. Como cada uma é consultada:

| fonte | consulta | o que entra no contexto |
|---|---|---|
| Wikipedia pt | busca "nome + município + estado" | o artigo do atrativo; sem artigo, os parágrafos do artigo do **município** que citam o atrativo |
| Wikivoyage pt + en | mesma busca | os parágrafos da página do município que citam o atrativo (a Wikivoyage é organizada por destino) |
| Wikidata | o item do artigo da Wikipedia; senão busca por nome com P17 = Brasil e P131 = município | 20 propriedades de atrativo (fundação, tombamento, altitude, arquiteto, parte de…) com rótulos pt |
| OSM (Nominatim) | "nome, município, estado, Brasil", 1 req/s | categoria, endereço e tags; as tags `wikidata`/`wikipedia` servem de ponte |

**Riqueza** = afirmações concretas distintas do texto aprovado que estão no contexto (mesma
extração do gate de groundedness). É um proxy de "fatos", não gabarito manual.

### 27.2 O casamento frouxo escolhe o artigo errado

Na primeira coleta, o artigo era aceito quando o título tinha os termos identificadores do
nome (a regra do gate de menção). **6 de 85 artigos eram de outro objeto:**

| atrativo | artigo escolhido |
|---|---|
| Lagoa do Paraíso | *O Outro Lado do Paraíso* (telenovela) |
| Mirante do Forte São João | *Mata de São João* (município) |
| Cachoeira de Matilde | *Estação Ferroviária de Matilde* |
| Igreja de Santa Isabel (Mucugê) | *Cemitério Santa Isabel de Mucugê* |
| Convento Nossa Senhora da Penha | *Nossa Senhora da Penha de França* (a devoção) |
| Praia de Pajuçara | *Pajuçara (Natal)* |

O gate de menção descarta "lagoa", "mirante" e "igreja" de propósito (§24.1), e é isso que
deixa a novela passar. Nenhum gate a jusante pega o erro: o texto sai fundamentado, só que
sobre outra coisa. **Regra adotada (`confere`):** todas as palavras do nome, inclusive as
genéricas, precisam estar no título ou na abertura do artigo. Ela elimina os 6 e rejeita 7
artigos bons (*Santuário Cristo Redentor → Cristo Redentor*, *Barra da Tijuca*). Os números
abaixo usam a regra estrita.

### 27.3 Resultado por fonte

| contexto | cita o atrativo | **aprovados** | DLQ | riqueza média | contexto p50 | $ modelo |
|---|---|---|---|---|---|---|
| Wikipedia | 86 | **80 (57%)** | 6 | **8,3** | 1.121 car. | $0,13 |
| Wikivoyage pt+en | 72 | 48 (34%) | 24 | 4,0 | 73 car. | $0,08 |
| Wikidata | 80 | 50 (36%) | 30 | 2,5 | 81 car. | $0,07 |
| OSM | 97 | 70 (50%) | 27 | 1,9 | 171 car. | $0,08 |
| **as quatro juntas** | 117 | **99 (71%)** | 18 | 7,3 | 2.078 car. | $0,17 |
| Tavily (§26) | 138 | 131 (94%) | 7 | 7,4 | 11.911 car. | $0,30 |

- **A Wikipedia é a fonte.** Cobre 57% dos atrativos do TA e, onde cobre, dá texto mais rico
  que a Tavily (8,3 contra 7,4). A §15.1 via 5% porque media outra população.
- **Wikivoyage e Wikidata complementam.** Sozinhos, o contexto é curto e o modelo completa de
  memória: 24 e 30 textos na DLQ. Somados à Wikipedia, entram 7 atrativos (Wikivoyage) e 1
  (Wikidata) que a Wikipedia não cobre. Três dos sete são justamente artigos que a regra estrita
  rejeitou.
- **O OSM acha mais objetos (97), mas não sustenta uma descrição.** Os 9 atrativos aprovados
  **só** com OSM saíram com 1 a 3 fatos, contra 3 a 16 da Tavily nos mesmos atrativos, e vários
  sobre o lugar errado:
  - *Lagoa do Paraíso* virou uma lagoa no Cabula, em Salvador;
  - *Projeto Tamar* virou o quiosque do aeroporto de Salvador;
  - *Praia de Juquehy* casou com a "Modesti Imóveis Praia de Juquehy", e o texto aprovado é uma
    recusa ("Desculpe, com as informações fornecidas…").

  O valor do OSM é de **ponte**: a tag `wikidata` trouxe 5 itens (Pão de Açúcar, Praia de
  Ipanema, Gruta do Lago Azul…) e a tag `wikipedia` trouxe 1 artigo (AquaRio) que a busca por
  nome não achou.

### 27.4 A cascata que funciona: abertas primeiro, Tavily no resto

Regra: escreve com as fontes abertas quando o contexto aprovado tem **pelo menos uma fonte
textual** (Wikipedia, Wikivoyage ou Wikidata) citando o atrativo. OSM sozinho não conta. O
resto vai para a Tavily.

| | só Tavily (§26) | **abertas → Tavily** |
|---|---|---|
| resolvidos pelas abertas | — | **90 (64%)** |
| vão para a Tavily | 140 | 50 (36%) |
| **aprovados no total** | 131 | **135** |
| riqueza, pareada nos 86 aprovados pelos dois | 7,9 | **7,9** (mediana 7 contra 8) |
| texto mais rico | Tavily em 45 | abertas em 38 (3 empates) |

**A cascata aprova mais que a Tavily sozinha** e empata na riqueza. Os 4 atrativos a mais são
casos em que a Tavily mandou para a DLQ e a Wikipedia tinha o artigo. A diferença não é
uniforme. A Tavily ganha feio onde a Wikipedia só tem o bairro (*Parque Municipal das
Mangabeiras*: 4 contra 14). As abertas ganham onde o artigo é longo (*AquaRio*: 14 contra 0,
*Inhotim*: 14 contra 2).

### 27.5 A conta dos 10 mil

Mesma forma da §26 (1.000 créditos grátis da Tavily abatidos):

| | Tavily + Gemini (§26) | **abertas → Tavily + Gemini** |
|---|---|---|
| buscas Tavily | 20.000 | 7.143 |
| Tavily | $152 | **$49** |
| Gemini (abertas em todos + Tavily no resto) | $21 | $20 |
| **esperado** | **$173** | **$69** |
| cost guard $10/dia | ~18 dias | **~7 dias** |

**−60%.** Mas a amostra puxa para cima. Os 140 vêm das primeiras páginas do TA (Pão de Açúcar,
Inhotim, Cataratas), e a cauda dos 10 mil tem menos Wikipedia. Sensibilidade:

| abertas resolvem | Tavily | total |
|---|---|---|
| 64% (medido) | $49 | $69 |
| 50% | $72 | ~$93 |
| 30% | $104 | ~$125 |

Mesmo no pior cenário a conta fica 28% abaixo da §26.

### 27.6 OpenTripMap: fora, sem medir

- **Licença.** O único plano publicado é *"Free — $0/mo — Non-commercial use — 5 000 requests /
  day"* (dev.opentripmap.org/price). Não há plano comercial com preço; a cobrança é em rublo.
  A Norteia é uso comercial.
- **Dado.** A própria página diz *"based on cooperative processing of different open data
  sources (OpenStreetMap, Wikidata, Wikipedia…)"*: as três fontes medidas acima, reprocessadas.
  A cobertura no Brasil é no máximo a união delas. A §16.1 já tinha visto isso.

### 27.7 Restrições de uso

| fonte | licença | uso em produção |
|---|---|---|
| Wikipedia, Wikivoyage | **CC BY-SA 4.0** | exige atribuição; **share-alike** pode alcançar o texto derivado — decisão jurídica sobre `descricao_editorial` (a Tavily devolve páginas sem licença nenhuma, o que não é mais limpo) |
| Wikidata | CC0 | livre |
| OSM | ODbL | atribuição "© OpenStreetMap contributors" |
| API da Wikimedia | — | User-Agent com contato, requisições **em série**; 10 mil × ~6 requisições cabem em horas, ou usar os dumps |
| **Nominatim público** | — | *"No heavy uses (an absolute maximum of 1 request per second)"*; geocodificação em massa desencorajada; scripts recorrentes limitados a 4 req/min. **Para 10 mil, não.** A ponte do OSM viria de extrato Geofabrik ou Overpass próprio |

### 27.8 Veredito

- **Wikipedia: adotar como primeira fonte.** Cobre 57% dos atrativos do TA, com texto mais rico
  que a Tavily onde cobre. Exige o casamento estrito (§27.2), senão escreve sobre a novela.
- **Wikivoyage e Wikidata: adotar como complemento, nunca sozinhos.** Somam 8 atrativos e fatos
  estruturados. Sozinhos, o modelo completa de memória.
- **OSM: só como ponte**, por extrato, não pelo Nominatim. Como fonte de texto, reprovado.
- **OpenTripMap: descartado** (licença não comercial, dado redundante).
- **A cascata abertas → Tavily** resolve 64% sem busca paga, aprova 135 contra 131, empata na
  riqueza e leva a conta de **$173 para ~$69** ($93-125 se a cauda tiver menos Wikipedia).
- **Antes de implementar:** (1) decisão jurídica sobre CC BY-SA na descrição; (2) medir a
  cobertura da Wikipedia numa amostra da **cauda** do TA, não das primeiras páginas, porque é
  ela que decide entre $69 e $125.

Ferramenta: `.venv/bin/python scripts/poc/fontes_abertas_probe.py --self-check` (offline) ·
`--coletar` (sem key, ~12 min) · `--escrever` (~$0,53). Coleta em
`scripts/poc/fontes_abertas_probe.fontes.json`, textos e relatório em
`scripts/poc/fontes_abertas_probe.json`.

---

## 28. DeepSeek V4 Flash 0731 como pesquisador e como redador (medido)

Duas perguntas:
1. Com o DeepSeek fazendo **só a busca** e o Gemini escrevendo, quanto custa a busca?
2. Com o DeepSeek fazendo **busca e redação**, quanto custa tudo?

Medido em 2026-09-14 nos mesmos 140 atrativos da §26/§27. Sonda:
`scripts/poc/deepseek_busca_probe.py`. Custo: **$3,34 no OpenRouter**, incluindo a rodada
descartada da §28.3.

### 28.1 A busca nativa da API da DeepSeek não executa

A Responses API da DeepSeek aceita `tools=[{"type": "web_search"}]` (issue
NousResearch/hermes-agent#79820, PR #79103). Testado com chave própria em `api.deepseek.com`:

- **A tool é ecoada e ignorada.** A resposta devolve `tools: [{"type": "web_search"}]`, mas não
  tem item `web_search_call`, não tem anotação, e a entrada fica em 73 tokens. A documentação atual
  da DeepSeek diz exatamente isso: *"web_search / file_search / … other built-in tools —
  **Ignored**"*.
- **Forçada (`tool_choice`), o modelo admite:** *"Não consigo fazer busca na web em tempo real
  neste ambiente"*.
- **Sem forçar, ele finge.** Pedidos 5 fatos com fonte sobre a Praia do Forno, veio uma lista
  confiante: das 4 URLs checáveis, **as 4 dão 404**, inclusive
  `pt.wikipedia.org/wiki/Praia_do_Forno`, que não existe. É a §19 de novo, agora na DeepSeek.
- **Pelo OpenRouter também não há busca nativa.** `engine: native` no DeepSeek cai no Exa sem
  aviso (1 busca = $0,007). O OpenRouter só repassa busca nativa de Anthropic, Google, OpenAI,
  Perplexity e SpaceXAI.

O caminho medido é o do pedido original: **DeepSeek pelo OpenRouter com a server tool
`openrouter:web_search`**. O modelo escolhe as queries e o OpenRouter executa no motor escolhido:

| motor | preço por busca |
|---|---|
| Exa (auto) — padrão para o DeepSeek | $0,007 |
| Parallel fast | $0,001 |
| *(Tavily, para comparar)* | *$0,008* |

### 28.2 O método

1. **Pesquisa.** DeepSeek 0731 + `openrouter:web_search`, 5 resultados por busca e
   `max_tool_calls: 2`, a paridade com as 2 queries da Tavily. O `max_uses: 2` sozinho **não
   trava**: deixou passar 4 buscas no teste.
2. **O contexto do redator é o texto bruto das buscas** (`annotations[].url_citation.content`),
   não o resumo do DeepSeek. O resumo pode trazer memória do modelo, e o gate de groundedness
   contra um resumo inventado lavaria a invenção.
3. **Redação.** `write_cascade` sem alteração, com Gemini 2.5 Flash e com DeepSeek 0731. O
   DeepSeek também escreveu sobre as fontes abertas da §27, para a cascata.

### 28.3 Armadilha: o raciocínio do DeepSeek come a saída

Na primeira rodada o DeepSeek redator falhou em **182 de 420** textos, todos com
`finish_reason: length`. Ele raciocina por padrão, e os 2.048 tokens de `max_tokens` da lane
acabavam antes do texto. **21 textos cortados passaram pelo gate como aprovados.** Refeito com
`reasoning: {"enabled": false}`: zero cortes. A rodada descartada fica em
`deepseek_busca_probe.com-raciocinio.json`. Na lane, o DeepSeek exige o raciocínio desligado
e a rejeição de `finish_reason != stop`, o mesmo defeito do Gemini truncado da §26.4.

### 28.4 Resultado

| busca → redator | cita o atrativo | aprovados | DLQ | riqueza | $/atrativo | **10 mil** |
|---|---|---|---|---|---|---|
| Tavily → Gemini (§26) | 138 | 131 | 7 | 7,4 | $0,0181 | **$173**¹ |
| **DeepSeek+Exa → Gemini** | 137 | **134** | 3 | **10,9** | $0,0157 | **$157** |
| **DeepSeek+Parallel → Gemini** | 132 | 127 | 5 | 8,6 | $0,0047 | **$47** |
| **DeepSeek+Exa → DeepSeek** | 137 | 129 | 8 | 7,6 | $0,0134 | **$134** |
| **DeepSeek+Parallel → DeepSeek** | 132 | 122 | 10 | 6,7 | $0,0030 | **$30** |

¹ Com os 1.000 créditos grátis da Tavily abatidos; sem eles, $181. O OpenRouter não tem cota grátis.

**Riqueza pareada contra a Tavily (Gemini nos dois):** Exa 11,1 contra 7,4 em 127 atrativos;
Parallel 8,7 contra 7,6 em 122. Com as buscas escolhidas pelo DeepSeek, o Gemini escreve **mais
fatos** que com as queries fixas da Tavily. O contexto bruto também é maior: p50 de 18,2 mil
caracteres (Exa) e 12,2 mil (Parallel).

**Objetivo 1, o custo da busca, por atrativo:**

| | taxa de busca | DeepSeek pesquisando | **busca total** | vs Tavily |
|---|---|---|---|---|
| Tavily (2 × $0,008) | $0,0160 | — | $0,0160 | — |
| DeepSeek + Exa | $0,0120 (1,7 busca/atrativo) | $0,0010 | **$0,0129** | −19% |
| DeepSeek + Parallel fast | $0,0018 | $0,0008 | **$0,0026** | **−84%** |

O DeepSeek como pesquisador custa ~$0,001 por atrativo. O que decide é o motor. No Exa, o
modelo fez 1 busca só em 41 dos 140 atrativos, e isso já barateia contra as 2 fixas da Tavily.

**Objetivo 2, o DeepSeek também escrevendo:** a redação cai de $0,0028 (Gemini) para $0,0005
por atrativo, 5,7x menos. Mas escreve com **30% menos fatos** no mesmo contexto (7,6 contra
10,9 no Exa; 6,7 contra 8,6 no Parallel) e manda mais para a DLQ. Os defeitos são raros nos
dois: nenhum recado ao operador, nenhum dado operacional, 1 texto com markdown (DeepSeek+Exa).

### 28.5 Com a cascata da §27 (fontes abertas primeiro)

| redator | busca no resto | aprovados | **10 mil** |
|---|---|---|---|
| Gemini | Tavily (§27) | 135 | $69 |
| Gemini | DeepSeek + Exa | 135 | $68 |
| **Gemini** | **DeepSeek + Parallel** | **130** | **$28** |
| DeepSeek | DeepSeek + Exa | 134 | $50 |
| **DeepSeek** | **DeepSeek + Parallel** | **126** | **$12** |

### 28.6 O que pesa contra

- **Latência: 45 s por atrativo, contra 2 s da Tavily.** Pesquisa p50 49 s e p95 80 s no Exa;
  42 s e 62 s no Parallel. O modelo raciocina entre as buscas. Cabe nos 300 s do `enrich_places`,
  mas os 10 mil levam ~15 h a concorrência 8, contra minutos com a Tavily. O teto de US$ 10/dia
  continua sendo a trava.
- **O resumo do DeepSeek cita fonte que não buscou.** 16 de 774 URLs (Exa) e 21 de 726
  (Parallel) não vieram das buscas; perto de 1% dá 404. Mesmo patamar da auditoria da §21.7, e o
  redator não usa o resumo. Mas confirma que o resumo não pode ser contexto.
- **Termos de uso de Exa e Parallel via OpenRouter não verificados**, principalmente o direito de
  armazenar o derivado (a cláusula que reprovou a Brave na §17.5).
- **Parallel fast é o modo mais raso do motor.** Deu 16 `NAO_ENCONTRADO` no resumo, contra 9 do
  Exa, e 5 atrativos a menos citados. O modo basic custa $0,005 e não foi medido.
- **Pergunta aberta:** o DeepSeek só escolhe queries. Chamar o Parallel direto, com as 2 queries
  fixas da `cascade_queries`, tiraria o modelo e os 45 s. Não medido.

### 28.7 Veredito

- **Objetivo 1:** com o DeepSeek só buscando, a busca custa **$0,0129 por atrativo no Exa
  (−19%)** ou **$0,0026 no Parallel fast (−84%)**. Com o Gemini escrevendo, os 10 mil saem por
  **$157 (Exa) ou $47 (Parallel)**, contra $173, com mais fatos por texto nos dois.
- **Objetivo 2:** com o DeepSeek fazendo tudo, **$134 (Exa) ou $30 (Parallel)**. A economia
  sobre o Gemini é de $17-23 nos 10 mil, e custa ~30% dos fatos. Não compensa.
- **A combinação mais barata com qualidade:** fontes abertas → DeepSeek + Parallel → Gemini, a
  **~$28 os 10 mil**, com 130 aprovados de 140.
- **Antes de adotar:** (1) termos de uso de armazenamento do Parallel/Exa; (2) Parallel direto
  com queries fixas, para tirar os 45 s; (3) na lane, desligar o raciocínio do DeepSeek e rejeitar
  `finish_reason != stop`.

Ferramenta: `.venv/bin/python scripts/poc/deepseek_busca_probe.py --self-check` ·
`--pesquisar --motor exa|parallel` (~$1,81 / ~$0,36) · `--escrever [--so-deepseek]`. Pesquisas
em `scripts/poc/deepseek_busca_probe.pesquisa.{exa,parallel}.json`; textos e relatório em
`scripts/poc/deepseek_busca_probe.json`.

> **Atualizado pela §29.** A Parallel direta substitui esta rota: mesma riqueza, 45 s → 0,6 s,
> e ~$25 nos 10 mil na cascata. Os termos de uso da Parallel e da Exa têm cláusulas que pedem
> confirmação por escrito antes de produção (§29.6).

---

## 29. Parallel direto no lugar do DeepSeek + Parallel (medido)

A §28 deixou uma pergunta aberta: chamar a Search API da Parallel direto, com as 2 queries
fixas da `cascade_queries`, substitui o DeepSeek + Parallel do OpenRouter, sem os 45 s e mais
barato?

Termos lidos em 2026-09-14; medido em 2026-09-15 nos mesmos 140 atrativos da §26–§28. Sonda:
`scripts/poc/parallel_direto_probe.py`. Custo: **$0,81 no OpenRouter + 281 requisições
Parallel**, dentro da cota grátis mensal. A leitura dos termos veio antes da medição e
concluiu que eles barravam o uso. O usuário mandou medir mesmo assim e deixar os termos para
depois. A §29.6 revisa essa conclusão para "ambígua".

### 29.1 O método

1. **Busca.** `POST /v1/search` com `search_queries = cascade_queries(...)`, as 2 da lane
   **numa requisição só**, e o `objective` *"Fatos verificáveis sobre o atrativo turístico X em
   município/UF: história, características, o que ver."*. Dois modos, `fast` e `turbo`, com
   cache em disco. O modo `basic` ficaria só para o caso de o fast perder feio, e não perdeu.
2. **Contexto do redator:** excerpts brutos, com `[título] url` por resultado. Sem resumo de LLM.
3. **Redação:** `write_cascade` sem alteração, Gemini 2.5 Flash sem thinking, `max_tokens`
   2048. **Diferença para a §26 e a §28: `finish_reason != stop` é rejeitado** e vira falha. Não
   houve nenhum caso: 272 de 272 chamadas terminaram em `stop`.
4. **Pareamento** atrativo a atrativo com a Tavily da §26 (riqueza de
   `fontes_abertas_probe.json`) e com o DeepSeek + Parallel da §28.

### 29.2 Resultado

| busca → Gemini | cita o atrativo | **aprovados** | DLQ | sem menção | riqueza | busca p50 / p95 | contexto p50 |
|---|---|---|---|---|---|---|---|
| Tavily (§26) | 138 | 131 | 7 | 2 | 7,4 | ~1,8 s | 11,9 mil car. |
| DeepSeek + Parallel fast (§28) | 132 | 127 | 5 | 8 | 8,6 | 42 / 62 s | 12,2 mil car. |
| **Parallel direto fast** | 135 | 128 | 7 | 5 | 8,5 | **0,93 / 1,31 s** | 18,8 mil car. |
| **Parallel direto turbo** | **137** | **135** | **2** | 3 | 8,3 | **0,56 / 0,80 s** | 22,9 mil car. |

**Riqueza pareada** (só atrativos aprovados nas duas rotas):

| | n | Parallel direto | outro | razão | mais rico / menos rico |
|---|---|---|---|---|---|
| turbo vs Tavily (§26) | 128 | 8,48 | 7,34 | **1,15** | 74 / 40 |
| turbo vs DeepSeek+Parallel (§28) | 124 | 8,46 | 8,63 | **0,98** | 52 / 57 |
| fast vs Tavily (§26) | 122 | 8,58 | 7,58 | 1,13 | 64 / 44 |
| fast vs DeepSeek+Parallel (§28) | 118 | 8,64 | 8,88 | 0,97 | 54 / 55 |

- **Tirar o DeepSeek não custou fatos.** As 2 queries fixas numa requisição dão 98% da riqueza
  das queries que o modelo escolhia, e ficam 15% acima da Tavily com as mesmas queries. O
  ganho sobre a Tavily vem do motor, não da escolha de queries.
- **O turbo foi melhor que o fast**, o contrário do que o nome sugere: 7 atrativos aprovados a
  mais, 5 textos a menos na DLQ e contexto maior. Os dois devolveram 10 resultados em quase todos os
  atrativos (média 10,0 contra 9,5).
- **O turbo funciona em português.** A tabela do OpenRouter diz *"English and Japanese"* para o
  turbo, mas o resultado mostra outra coisa. Vale reconferir se a Parallel mudar o modo.
- **Zero 429 e zero 5xx** em 281 requisições a concorrência 8.
- **A resposta traz `usage`:** `[{"name": "sku_search", "count": 1}]`. É uma requisição cobrada
  por atrativo, mesmo com 2 queries, o que confirma a hipótese de custo principal.

### 29.3 O que passa pelo gate e não devia

Lido texto a texto nos aprovados. O regex de "meta" marcou 3 textos no turbo e 7 no fast, e o de
"operacional" marcou 2 e 5. Quase tudo era falso positivo:

| defeito que chega na coluna | turbo | fast |
|---|---|---|
| recado ao operador | 0 | **1** — *Rua das Pedras*: *"Embora não seja tão amplamente detalhada nas fontes…"* |
| markdown | 0 | 0 |
| texto cortado | 0 | 0 |
| dado operacional proibido | 0¹ | 0² |
| registro com município errado, escrito por cima | 1 — *Cristo Redentor* em Ubá/MG | 1 — o mesmo, descrito como o do Corcovado |
| entidade trocada | 1 — *Capixaba Foto Tour* virou a agência "Capixaba Turismo" | 1 — idem |
| **total** | **2 (1,5%)** | **3 (2,3%)** |

¹ Os 2 alertas são "melhor hora" (*Poço Encantado*, 10h–13h30, permitido pelo prompt) e *"dentro
do horário de funcionamento"* sem o horário. ² Os 5 são "melhor hora" e duração de visita (1h30).

**Nenhum defeito vem da busca nem do prompt.** Os dois que sobram no turbo são registro-lixo da
Nascente (§26.4: *Cristo Redentor* com município Ubá) e um nome de negócio que nenhuma página
cita literalmente ("Foto Tour" não aparece no contexto). O gate de menção deixa passar porque
descarta termos genéricos. Os dois pedem gate na Rio, não troca de busca.

### 29.4 Custo

| | busca / atrativo | Gemini / atrativo | tokens in (Gemini) | **10 mil** | **10 mil com a cota grátis³** |
|---|---|---|---|---|---|
| Tavily → Gemini (§26) | $0,016 | $0,0022 | 3.759 | $173 | — |
| DeepSeek + Parallel → Gemini (§28) | $0,0026 | $0,0021 | ~4.000 | $47 | — |
| **Parallel fast → Gemini** | **$0,001** | $0,0028 | 5.796 | **$38** | $33 |
| **Parallel turbo → Gemini** | **$0,001** | $0,0031 | 6.709 | **$41** | $36 |

**Na cascata da §27** (fontes abertas primeiro, 90 dos 140 resolvidos; Parallel no resto):

| | aprovados | **10 mil** | com a cota grátis³ |
|---|---|---|---|
| abertas → Tavily → Gemini (§27) | 135 | $69 | — |
| abertas → DeepSeek + Parallel → Gemini (§28) | 130 | $28 | — |
| abertas → Parallel fast → Gemini | 132 | $24 | $21 |
| **abertas → Parallel turbo → Gemini** | **135** | **$25** | **$22** |

³ 5 mil requisições por mês grátis (*"Run up to 5,000 requests per month for free"*), supondo os
10 mil num mês só. Na cascata, as ~3.600 buscas cabem inteiras na cota.

- **A busca virou o menor item da conta.** A $0,001 por atrativo, **o Gemini passa a ser ~75% do
  custo**. O contexto do turbo é quase o dobro do da Tavily (6,7 mil tokens contra 3,8 mil), e é
  isso que encarece a redação.
- **Alavanca não medida:** `max_chars_total` limita o total de excerpts. Cortar o contexto pela
  metade tiraria ~$8 do Gemini nos 10 mil sozinho e ~$3 na cascata. Mas pode custar a riqueza,
  que é justamente o ganho medido. Só vale medir se o Gemini virar a restrição.
- **Prazo:** 0,6 s de busca + 4,6 s de Gemini (p50) ≈ 5 s por atrativo. Os 10 mil levam ~1,7 h a
  concorrência 8. O limite da Parallel é 600/min e o teto de US$ 10/dia deixa de ser trava:
  $25 cabem em 3 dias, e na cascata com cota cabem em ~2.

### 29.5 Critério de decisão

| critério | exigido | **turbo** | fast |
|---|---|---|---|
| aprovados | ≥ 125 | **135** ✅ | 128 ✅ |
| riqueza pareada vs §28 | ≥ 90% | **98%** ✅ | 97% ✅ |
| busca p95 | < 5 s | **0,80 s** ✅ | 1,31 s ✅ |
| custo de busca por atrativo | ≤ $0,0026 | **$0,001** ✅ | $0,001 ✅ |
| termos permitem armazenar o derivado | sim | ⚠️ ambíguo, §29.6 | ⚠️ |

### 29.6 Os termos de uso (lidos antes da medição, revistos depois)

Esta subseção foi escrita antes de medir e concluía que os termos **barravam** o uso. A
discussão depois da leitura corrigiu isso para **ambíguo**. O raciocínio segue abaixo.

A cláusula que já tinha aparecido (*"copies or stores any significant portion of the
Content"*) é mesmo do site: está nos **Terms of Service for Parallel Websites**, na lista
"restrictions in how I can use our websites", e "Content" ali é o texto e as imagens do site.
**Quem rege a API são os Customer Terms** (parallel.ai/customer-terms, vigentes desde
11/08/2026), aceitos ao criar a conta na plataforma. "Customer Output" é *"the output generated
and returned by the Services"*, ou seja, os excerpts da busca. Três trechos alcançam a lane.

**1. Armazenar o derivado.** A licença de obra derivada vem com uma condição, §2(b):

> *"Customer may modify, adapt, or create derivative works based on Customer Output and
> incorporate Customer Output (and such derivative works) into materials that Customer provides
> to its End Customers, provided that (i) Customer Output generated from one query shall be
> primarily for the use of one End Customer only, and shall not be copied, cached, stored, or
> made available to other End Customers or other third parties; (ii) Customer shall not copy,
> cache, or store any significant portion of any Customer Output to create the AI and Data
> Selling Services"*

A §4(b) repete: *"each Customer Output shall be primarily for the exclusive use of the
Authorized User who submitted the corresponding query"*. A `descricao_editorial` sai de uma
consulta, fica gravada no Mar, vai para a norteia-api e é mostrada a todos os usuários do app.
Mas a condição recai sobre o **Output**, não sobre a obra derivada. **Gravar só a descrição e
descartar os excerpts cabe nesta cláusula.** A lane já não persiste o contexto: `brave/clients/llm.py`
diz *"NEVER log prompt content"*, e o `descricao_rascunho` também é texto nosso. Guardar os
excerpts, por exemplo numa tabela de fontes para auditoria, é o que ela veda.

**2. Construir base de dados, uso comercial e cache.** §2(c)(vi):

> *"use the Services or any Customer Output to (A) create synthetic training data to develop or
> train a language model or any other machine learning model, or (B) create databases, data
> brokerage, data selling/reselling businesses, or related products or services, whether
> competitive with the Services or not"*

Aqui o verbo é **usar**, não armazenar. Gerar descrições que vão para a base territorial é,
na letra, usar o Output para criar uma base de dados, e a distinção entre dado deles e dado nosso
não resolve esta cláusula. **A favor:** (A) e (B) têm o nome coletivo de *"AI and Data Selling
Services"*, e o contexto é treino de modelo e venda de dados. A Norteia é plataforma de turismo
e não vende a base. **Contra:** o *"whether competitive with the Services or not"* foi escrito para
alargar o alcance, e o contrato não define "databases". A §2(c)(xii) (*"making available Customer
Outputs from previous queries for future uses"*) fala do Output, não do derivado. **Leitura:
ambígua, com defesa razoável; não é proibição clara.**

O FAQ da documentação diz *"you own the output you create with Parallel, including the right
to reprint, sell, and merchandise"*, mas começa com *"Subject to our Terms of Service"* e aponta
para os Customer Terms. Na ordem de precedência da §11(a), o contrato vem primeiro.

**3. Zero Data Retention.** Só no Enterprise. A página de preços lista ZDR, DPA e SSO no bloco
**Enterprise** ("Get a demo"), e o próprio site diz *"ZDR Available for enterprises"*. No plano
por uso vale a §4(b): *"Parallel may use Customer IP to train and improve the machine learning
and other artificial intelligence models used to provide the Services"*. As queries da lane não
têm dado pessoal. Isso pesa pouco aqui, mas pesaria numa lane com PII.

**Duas consequências a mais:**
- **Valem também para a rota da §28.** Os termos do OpenRouter dizem que o uso de serviços de terceiros
  *"is solely between you and the applicable third-party provider, and governed by the terms of
  service […] between you and that third party"*. O Parallel pelo OpenRouter não fica fora dos
  Customer Terms.
- **Benchmark.** A §2(c)(viii) proíbe *"create or provide to any third party the results of any
  benchmark tests or other evaluation of the Services without Parallel's prior written
  consent"*. As §28 e §29 são documento interno. Não publicar os números da Parallel fora do
  time.

### 29.7 Exa e Tavily, pela mesma régua

A §17.5 registrou *"Exa e Tavily não têm cláusula equivalente encontrada"*. Relido hoje:

| provedor | cláusula | leitura |
|---|---|---|
| **Parallel** (direto e via OpenRouter) | §2(b), §2(c)(vi)(B), §2(c)(xii) acima | **ambígua**: o derivado sem os excerpts tem defesa; guardar os excerpts, não |
| **Exa** (direto e via OpenRouter) | Terms §4.2(a): *"download, modify, copy, distribute, transmit, display, perform, reproduce, duplicate, publish, license, **create derivative works from**, or offer for sale any information contained on, or obtained from or through, the Services, except for temporary files that are automatically cached by your web browser for display purposes, or as otherwise expressly permitted in these Terms or by us in writing"* | **barra sem permissão por escrito**. É mais ampla que a da Parallel, porque alcança qualquer derivado. A §17.5 errou |
| **Tavily** | *"For clarity, the term 'Services' does not include Output"*. As restrições (copiar, criar obra derivada, revender) recaem sobre os Services. Sobre o Output só há a regra de uso aceitável, a de decisão automatizada e, na AI Functionality, a proibição de treinar modelo concorrente | **não achei cláusula** de armazenamento nem de base de dados |

Não é parecer jurídico. É a leitura literal que a §17.5 pediu, feita antes de adotar.

### 29.8 Os fatos de preço, confirmados

| fato do handoff | confirmado | fonte |
|---|---|---|
| `POST https://api.parallel.ai/v1/search`, header `x-api-key`, `objective` + `search_queries` + `mode` | sim. `search_queries` é o único campo obrigatório, "provide 2-3 for best results"; `mode` padrão = `advanced` | API reference |
| `/v1beta` é legado | sim: *"Use /v1beta/search … only when maintaining an existing integration"* | docs |
| preço por modo | turbo e fast: **$0,001** por requisição; basic e advanced: **$0,005**; ambos com 10 resultados, e $0,001 por resultado adicional | docs/pricing (fórmula) |
| cota grátis | **recorrente**: *"Run up to 5,000 requests per month for free"* e *"$5 in free credits per month"* (= 5 mil requisições fast), mais *"Earn up to $80 at signup"* | parallel.ai/pricing |
| limite de taxa | **600/min**, *"Each POST to /v1/search"* | docs/rate-limits |
| várias queries por requisição | sim, `search_queries` é lista: as 2 da lane cabem em **1 requisição** | API reference |

### 29.9 Armadilhas

- **Ler o termo do site no lugar do contrato da API.** O trecho *"copies or stores any
  significant portion of the Content"* não se aplica ao resultado da API. O contrato certo são
  os Customer Terms.
- **O FAQ promete o que o contrato condiciona.** "You own the output" vem com "subject to".
- **Termos de provedor acessado pelo OpenRouter continuam valendo.** Passar pelo OpenRouter não
  lava a licença do motor.
- **O cache em disco da sonda** (`parallel_direto_probe.busca.*.json`, excerpts brutos) é o que
  a §2(b)(i) veda guardar. Em produção, não persistir. Os arquivos da POC ficam fora do
  commit até a resposta da Parallel.
- **O nome do modo engana.** O turbo ("latency-sensitive lookups", "English and Japanese" no
  OpenRouter) aprovou mais e trouxe mais contexto em português que o fast.
- **O contexto maior encarece o redator.** Com a busca a $0,001, o Gemini virou ~75% da conta.

### 29.10 Veredito

- **A Parallel direta, modo turbo, substitui o DeepSeek + Parallel.** Nos 140: 135 aprovados
  (contra 127), 98% da riqueza pareada, busca p95 de 0,8 s (contra 62 s) e $0,001 por busca
  (contra $0,0026). Passa nos quatro critérios técnicos; o de termos fica em aberto (§29.6).
- **Também supera a Tavily com as mesmas queries:** 135 contra 131 aprovados, +15% de riqueza
  pareada, 16x mais barata na busca.
- **Combinação para a lane: fontes abertas → Parallel turbo (2 queries, 1 requisição) →
  Gemini 2.5 Flash.** Os 10 mil saem por **~$25**, ou **~$22** com a cota grátis, com 135 de
  140 aprovados e ~1,7 h de computação. Sem as abertas (se o CC BY-SA não passar no jurídico),
  **~$41**, ou $36 com a cota, ainda abaixo dos $69 da rota com Tavily.
- **Os defeitos que sobram (2 de 135) são de registro, não de busca:** município errado na
  Nascente e nome de negócio sem página própria. O gate que falta é na Rio.
- **Antes de produção:**
  1. confirmação por escrito da Parallel sobre a §2(c)(vi)(B), com a pergunta *"we generate
     our own editorial descriptions from Search API excerpts, discard the excerpts, and store
     only our text in our tourism database — is that permitted?"* (hello@parallel.ai);
  2. na lane: um client Parallel (`search()` com as 2 queries numa chamada; hoje `write_cascade`
     chama `search()` uma vez por query), preço na tabela do cost guard, rejeição de
     `finish_reason != stop` e **nenhuma persistência dos excerpts**;
  3. as pendências da §27.8, CC BY-SA e cobertura da Wikipedia na cauda, que decidem entre
     $25 e $41.
- **Exa: não usar** sem permissão por escrito (§4.2(a)), e a Parallel já a supera no custo.

Ferramenta: `.venv/bin/python scripts/poc/parallel_direto_probe.py --self-check` (offline) ·
`--buscar --modo fast|turbo` (140 requisições, cota grátis) · `--escrever --modo fast|turbo`
(~$0,40 cada) · `--relatorio`. Buscas em `parallel_direto_probe.busca.{fast,turbo}.json`,
textos e relatório em `parallel_direto_probe.{fast,turbo}.json`. A chave é lida de
`BRAVE_PARALLEL_API_KEY` ou `PARALLEL_API_KEY`.

---

## Fontes

- [Google AI plans — Gemini API](https://ai.google.dev/gemini-api/docs/google-ai-plans)
- [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Gemini API Additional Terms of Service](https://ai.google.dev/gemini-api/terms)
- [Grounding with Google Search](https://ai.google.dev/gemini-api/docs/grounding)
- [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- [Google Developer Program — Plans & Pricing](https://developers.google.com/program/plans-and-pricing)
- [OmniRoute — repositório](https://github.com/diegosouzapw/OmniRoute) · [`docs/reference/FREE_TIERS.md`](https://github.com/diegosouzapw/OmniRoute/blob/main/docs/reference/FREE_TIERS.md)
- [Brave Search API — planos e preços](https://brave.com/search/api/)
- [Exa — pricing](https://docs.exa.ai/reference/pricing)
- [Tavily — Credits & Pricing](https://docs.tavily.com/documentation/api-credits)
- [Serper](https://serper.dev/) · [API reference](https://serper.dev/playground)
- [Google Programmable Search — Custom Search JSON API](https://developers.google.com/custom-search/v1/overview)
- [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Use Claude Code with your Pro or Max plan](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)
- [How do usage and length limits work?](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)
- [Anthropic Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms)
- [Tavily — API reference (`/search`)](https://docs.tavily.com/documentation/api-reference/endpoint/search)
- [Tavily — Rate Limits](https://docs.tavily.com/documentation/rate-limits)
- [Gemini API — Deprecations](https://ai.google.dev/gemini-api/docs/deprecations)
- [OpenRouter — google/gemini-2.5-flash](https://openrouter.ai/google/gemini-2.5-flash)
- [OpenTripMap API — product](https://dev.opentripmap.org/product) · [price](https://dev.opentripmap.org/price)
- [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/)
- [MediaWiki API:Etiquette](https://www.mediawiki.org/wiki/API:Etiquette)
- [OpenRouter — Web Search server tool](https://openrouter.ai/docs/guides/features/server-tools/web-search)
- [DeepSeek — Responses API](https://api-docs.deepseek.com/guides/responses_api)
- [Parallel — Customer Terms](https://parallel.ai/customer-terms) · [Terms of Service (sites)](https://parallel.ai/terms-of-service) · [FAQs](https://docs.parallel.ai/resources/faqs)
- [Parallel — Pricing](https://parallel.ai/pricing) · [API Pricing](https://docs.parallel.ai/getting-started/pricing) · [Rate limits](https://docs.parallel.ai/resources/rate-limits) · [Search API reference](https://docs.parallel.ai/api-reference/search/search)
- [OpenRouter — Terms of Service](https://openrouter.ai/terms)
- [Exa Labs — Terms of Service (PDF)](https://exa.ai/assets/Exa_Labs_Terms_of_Service.pdf)
- [Tavily — Terms](https://www.tavily.com/terms)
- [NousResearch/hermes-agent#79820](https://github.com/NousResearch/hermes-agent/issues/79820) · [PR #79103](https://github.com/NousResearch/hermes-agent/pull/79103)

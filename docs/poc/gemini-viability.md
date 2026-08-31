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
- **Ganho real: 4,9x a 39x** conforme o provedor, contra os $74,90/mil de hoje.
- O que sobra de risco não é técnico, é de **cobertura**: 1 dos 10 fatos não existia no corpus
  da Tavily. Numa amostra de três atrativos isso é 10% — número pequeno demais para ser taxa.
  Vale medir em escala antes de trocar o provedor em produção.

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
- [Serper](https://serper.dev/)
- [Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Use Claude Code with your Pro or Max plan](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)
- [How do usage and length limits work?](https://support.claude.com/en/articles/11647753-how-do-usage-and-length-limits-work)
- [Anthropic Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms)
- [Tavily — API reference (`/search`)](https://docs.tavily.com/documentation/api-reference/endpoint/search)

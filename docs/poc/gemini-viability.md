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

## Fontes

- [Google AI plans — Gemini API](https://ai.google.dev/gemini-api/docs/google-ai-plans)
- [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Gemini API Additional Terms of Service](https://ai.google.dev/gemini-api/terms)
- [Grounding with Google Search](https://ai.google.dev/gemini-api/docs/grounding)
- [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- [Google Developer Program — Plans & Pricing](https://developers.google.com/program/plans-and-pricing)

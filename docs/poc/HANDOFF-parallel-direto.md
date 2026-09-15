# Handoff — busca direto na API da Parallel (§29)

> Cole o bloco abaixo como primeira mensagem depois do `/clear`.

---

Continuando o trabalho da branch `docs/gemini-viability-poc`. Leia
`docs/poc/gemini-viability.md` **§26, §27 e §28** antes de qualquer coisa: é a medição que
fundamenta tudo abaixo. Não use GSD. É trabalho de POC medido, não fase planejada. Registre o
resultado como **§29** no mesmo arquivo e no mesmo formato.

## Onde paramos (não re-medir)

Tudo medido em 2026-09-14 nos **mesmos 140 atrativos do TripAdvisor**
(`scripts/poc/cascade_timed_probe.sample.json`, filtrando `origem != cadastur`, via
`amostra_ta(140)` em `scripts/poc/cascade_gemini_probe.py`). Pipeline da lane:
`TourismCopywriter.write_cascade` = busca → gate de menção → redator sem ferramenta → gate de
groundedness (≥ 0,75).

| rota (busca → redator) | aprovados /140 | fatos por texto | 10 mil |
|---|---|---|---|
| Tavily → Haiku 4.5 (§25, memorando de 10/09) | 128 | — | $224 hoje |
| Tavily → Gemini 2.5 Flash (§26) | 131 | 7,4 | $173 |
| fontes abertas → Tavily → Gemini (§27) | 135 | 7,9 | $69 |
| DeepSeek 0731 + Exa (OpenRouter) → Gemini (§28) | 134 | 10,9 | $157 |
| DeepSeek 0731 + **Parallel fast** (OpenRouter) → Gemini (§28) | 127 | 8,6 | $47 |
| **abertas → DeepSeek + Parallel fast → Gemini** (§28, a melhor até aqui) | **130** | — | **~$28** |

O que já está decidido:

- **Redator = Gemini 2.5 Flash via OpenRouter**, thinking desligado (§26). A API direta do Google
  devolve 404 para conta nova no 2.5 Flash.
- **DeepSeek não compensa como redator**: economiza $16-23 nos 10 mil e perde ~30% dos fatos (§28).
- **A busca nativa da API da DeepSeek não executa**: ecoa a tool, ignora, e inventa URLs (§28.1).
- **O DeepSeek como pesquisador custa ~$0,001 por atrativo, mas leva ~45 s** (p95 60-80 s), contra
  2 s da Tavily. Ele só escolhe as queries; quem entrega o conteúdo é o motor.
- O contexto do redator é **sempre o texto bruto da busca**, nunca um resumo de LLM (§28.2).

## A pergunta desta sessão

**Chamar a Search API da Parallel direto, com as queries fixas da lane, substitui o DeepSeek +
Parallel do OpenRouter, com o mesmo resultado, sem os 45 s e mais barato?**

Fatos levantados em 2026-09-14 nas páginas da Parallel. **Confirme cada um antes de usar**:

- `POST https://api.parallel.ai/v1/search`, header `x-api-key`; corpo com `objective` (linguagem
  natural), `search_queries` (lista) e `mode` (`turbo` | `fast` | `basic` | `advanced`; o padrão é
  `advanced`, ~3 s). Resposta: `results[] = {url, title, publish_date, excerpts[]}`. **Use
  `/v1/search`, não `/v1beta`** (a doc marca o beta como legado).
- Preço (docs.parallel.ai/getting-started/pricing): "$1–$5 / 1,000 requests, includes 10 results
  per request. Additional results: $1 / 1,000". Turbo/fast no piso, basic/advanced no teto.
  **Confirme o valor exato por modo.**
- parallel.ai/pricing: "Run up to 5,000 requests per month for free" e "$5 in free credits per
  month". Se valer para a Search API, **~5 mil atrativos por mês de graça**. Confirme se é cota
  recorrente ou só de cadastro.
- Limite de taxa da Search: 600 req/min (tabela em parallel.ai/pricing; confirme em
  docs.parallel.ai/resources/rate-limits).
- **Uma requisição aceita várias queries.** Se as 2 queries da `cascade_queries` couberem numa
  requisição, a busca sai por **1 requisição por atrativo**, e não 2 como na Tavily. É a hipótese
  de custo principal.

## Tarefa 1 — termos de uso (antes de gastar um crédito)

A §17.5 reprovou a Brave Search por cláusula de storage rights. Leia os termos da Parallel
(parallel.ai/terms-of-service e os termos específicos da API/plataforma, se houver) e responda
com citação literal:

1. Podemos **armazenar** os excerpts ou um texto derivado deles na nossa base?
   `descricao_editorial` é derivada e redistribuída para a norteia-api.
2. Há restrição de uso comercial, de cache, ou de "construir base de dados"?
3. Há Zero Data Retention no plano por uso, ou só no Enterprise? (parallel.ai/pricing lista ZDR.)

Atenção: o trecho da ToS que já apareceu ("copies or stores any significant portion of the
Content") parece falar do conteúdo do **site** da Parallel, não do resultado da API. Confirme
lendo o contexto inteiro da cláusula; não conclua pelo trecho.

Se a resposta for "não pode armazenar", **pare e reporte**. A medição não serve de nada.

## Tarefa 2 — a medição

A chave: peça ao usuário para criar a conta e colocar a chave no `.env` como
**`BRAVE_PARALLEL_API_KEY`** (é o padrão do projeto: `BRAVE_LLM_DEEPSEEK_API_KEY`,
`BRAVE_LLM_OPENROUTER_API_KEY`). Nunca peça para colar no chat e nunca imprima o valor.

Sonda nova, `scripts/poc/parallel_direto_probe.py`, no padrão de
`scripts/poc/deepseek_busca_probe.py`: reaproveite `amostra_ta`, `GeminiOpenRouter`,
`FonteFixa`, `limpar`, `contexto`, `riqueza` e `obediencia`, e rode `write_cascade` sem alteração.

1. **Busca**, com cache em disco (`parallel_direto_probe.busca.<variante>.json`), nos 140:
   - variante **A**: `search_queries = cascade_queries(nome, municipio, uf)` (as 2 da lane) numa
     requisição, `objective` curto em PT-BR ("fatos verificáveis sobre o atrativo turístico X em
     município/UF: história, características, o que ver"), `mode: "fast"`;
   - variante **B**: igual, `mode: "turbo"`;
   - variante **C**: igual à A, `mode: "basic"`, **só se A perder feio** para o DeepSeek+Parallel da
     §28 (riqueza pareada ou cobertura).

   Registre por atrativo: latência, nº de resultados, chars de excerpt, custo (a resposta traz
   usage? se não, calcule pela tabela confirmada) e status HTTP (429/5xx).
2. **Contexto do redator** = excerpts concatenados com `[título] url` por resultado, como o
   `bruto` da §28. Sem resumo de LLM.
3. **Redação** com Gemini 2.5 Flash (`thinking=False`, `max_tokens` 2048, rejeitar
   `finish_reason != stop`).
4. **Relatório**, pareado atrativo a atrativo contra (a) a Tavily da §26
   (`scripts/poc/cascade_gemini_probe.contexts.json` + `cascade_gemini_probe.gemini-2.5-flash.json`)
   e (b) o DeepSeek+Parallel da §28 (`scripts/poc/deepseek_busca_probe.json`, `fonte=parallel`,
   `redator=google/gemini-2.5-flash`):
   - cobertura (contexto cita o atrativo), aprovados, DLQ, riqueza média e riqueza pareada;
   - defeitos que passam pelo gate: recado ao operador, markdown, dado operacional, texto cortado.
     O regex ingênuo de "meta" dá falso positivo com "não há", confira lendo;
   - latência p50/p95 da busca;
   - **custo por atrativo e para 10 mil**, sozinho e **na cascata da §27** (fontes abertas primeiro:
     regra `subst()` em `deepseek_busca_probe.py::relatorio`; OSM sozinho não conta), com e sem a
     cota grátis mensal, se ela se confirmar.

Orçamento: 140 × 3 variantes ≈ 420 requisições, que devem caber na cota grátis; o Gemini fica em
~$0,30 por variante. Confira o saldo do OpenRouter antes (`/api/v1/credits`); o total da conta é
$15 e já foram ~$7,70.

## Critério de decisão

A Parallel direta **substitui** o DeepSeek+Parallel se, nos 140:
- aprovados ≥ 125 e riqueza pareada ≥ 90% da §28 (Parallel via DeepSeek);
- latência p95 da busca < 5 s;
- custo por atrativo ≤ $0,0026 (o da §28);
- termos de uso permitem armazenar o derivado.

O veredito da §29 tem que dizer qual combinação levar para a lane e a conta dos 10 mil dela.

## Armadilhas que já custaram tempo

- **Env**: sondas precisam de `set -a; . ./.env; set +a` e **`RUN_REAL_EXTERNALS=true`** (o
  `RealTavilyClient` recusa rodar sem ela). Para o pytest, `unset RUN_REAL_EXTERNALS`.
- **zsh não quebra `$args` em palavras**: `for args in "--a b"; do cmd $args` passa um argumento só.
  Escreva os comandos explícitos.
- **O hook `context-mode` bloqueia `curl` e `WebFetch`**: use `.venv/bin/python` + `httpx` para
  buscar páginas e docs.
- **`rtk`**: o hook trunca a saída de `grep`/`ls`; use `rtk proxy <cmd>` quando precisar da saída
  inteira. Nunca leia vazio como "arquivo não existe".
- **Créditos**: a Anthropic está **sem saldo** desde 14/09 (qualquer rota Anthropic falha calada
  como `copywriter_failed_kept_floor`). A Tavily tem ~6 créditos grátis até virar o mês; não
  dependa dela.
- **Casamento de artigo** nas fontes abertas: use o `limpar()`/`confere()` da §27.2, senão
  "Lagoa do Paraíso" vira a novela.
- **O stack Docker pode estar de pé** (`docker compose ps`) com `engine.mode = LIGADO` no overlay;
  a sonda não depende dele.
- **Nada disto está commitado**: as §26-§28, as sondas e os JSONs estão só no working tree da
  branch. Pergunte ao usuário se quer commitar antes de começar, e commite só se ele pedir.

## Arquivos-chave

| arquivo | o que é |
|---|---|
| `docs/poc/gemini-viability.md` §26-§28 | as medições que esta sessão continua |
| `brave/lanes/atrativos/copywriter.py` | `write_cascade`, `cascade_queries`, `COPYWRITER_SYSTEM` |
| `brave/lanes/atrativos/grounding.py` | `menciona`, `groundedness_ratio`, `afirmacoes_concretas` |
| `scripts/poc/cascade_gemini_probe.py` | `amostra_ta`, adaptador `GeminiOpenRouter`, `obediencia` |
| `scripts/poc/fontes_abertas_probe.py` | `FonteFixa`, `limpar`, `contexto`, `riqueza` |
| `scripts/poc/deepseek_busca_probe.py` | o padrão de sonda de busca a seguir, e a regra da cascata |
| `scripts/poc/deepseek_busca_probe.pesquisa.parallel.json` | o que o Parallel devolveu via DeepSeek, para comparar |

## Pronto quando

1. Termos de uso respondidos com citação.
2. §29 escrita com a tabela pareada, as armadilhas e um veredito: qual busca levar para a lane e a
   conta dos 10 mil.
3. Memória do projeto atualizada (`deepseek-busca-medido` e o registro de estado), e o usuário
   avisado do que falta decidir.

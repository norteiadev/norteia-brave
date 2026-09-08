# Handoff — gate de menção na lane + 200 cronometrados

> Cole o bloco abaixo como primeira mensagem depois do `/clear`.

---

Continuando o trabalho da branch `docs/gemini-viability-poc`. Leia
`docs/poc/gemini-viability.md` §23 e §24 antes de qualquer coisa — é a medição que fundamenta
tudo abaixo. Não use GSD para isso: é trabalho de POC/lane medido, não fase planejada.

## O que já está decidido e medido (não re-medir)

A cascata Tavily **serve** para sincronizar os ~10 mil atrativos do TripAdvisor. Medido em
2026-09-08 sobre 50 atrativos reais (§24, sonda `scripts/poc/cascade_scale_probe.py`):

- **98%** de cobertura — o contexto da Tavily menciona o atrativo pelos termos identificadores
- **88%** de groundedness — afirmação concreta do texto gerado existe no contexto que o alimentou
- **1.247 tokens/atrativo**, $0,016 de busca (2 queries × $0,008)
- 10 mil atrativos: **$206 com Haiku 4.5** contra **$749** da rota de produção de hoje
- Haiku e não flash-lite: 3 de 30 chamadas do flash-lite deram HTTP 503 e o free tier tem teto diário

O que a §23 mediu e continua valendo:

- modelo caro **não** escreve melhor com o mesmo contexto — a variável é a recuperação, não o modelo
- a instrução de abstenção do `COPYWRITER_SYSTEM` **não é executável por prompt** (o Sonnet
  escreveu "não achei informação verificável" e produziu 1.898 chars mesmo assim)
- contexto sem menção ao atrativo produziu fabricação confiante em **5 de 5 modelos**

## Tarefa 1 — implementar o gate de menção na lane

**Atenção ao escopo real:** a lane de hoje usa o `web_search` **server-side** dentro de
`TourismCopywriter.write()` (`brave/lanes/atrativos/copywriter.py:144`). Não existe passo de
busca separado onde encaixar um gate. Implementar o gate **é** implementar a cascata:

1. **Cliente Tavily** em `brave/clients/` (não existe ainda — só há a função de sonda em
   `scripts/poc/search_snippets_probe.py::buscar_tavily`). Atrás do boundary de rede, mockável
   com `respx`, key em `TAVILY_API_KEY` via `pydantic-settings`.
2. **Gate determinístico antes de chamar o LLM.** A lógica está pronta e com self-check offline
   em `scripts/poc/cascade_scale_probe.py` — porte `termos_identificadores()`, `menciona()` e
   a lista `GENERICAS` para a lane. Regra: se o contexto da busca não menciona os termos
   identificadores do nome, **não escrever descrição**. Registro segue sem descrição, nunca com
   descrição inventada.
3. **Copywriter em modo cascata**: contexto injetado, `tools=None`, Haiku 4.5
   (`claude-haiku-4-5`, $1/$5 por MTok). Reaproveite `_build_context` removendo a última linha
   (a que manda buscar na web — sem ferramenta ela pede uma ação impossível e alguns modelos
   respondem alucinando "conforme pesquisei"). A montagem correta está em
   `scripts/poc/cascade_probe.py::montar_user`, com self-check.
4. **Groundedness como gate de saída**: medir por texto gerado (`groundedness()` na sonda de
   escala) e mandar para a **DLQ** o que ficar abaixo do limiar, em vez de gravar no Mar.
   Proponha o limiar a partir dos dados de `scripts/poc/cascade_scale_probe.json` (a
   distribuição real das 130 afirmações) e diga por que escolheu esse número.
5. **Atrás de flag**, desligada por padrão, no padrão das que já existem em
   `brave/config/settings.py` (`description_enrichment_enabled`,
   `atrativo_description_batch_enabled`) — com overlay em `brave/config/runtime.py` e
   comentário explicando exatamente o que a flag controla e o que ela NÃO controla.

Testes: 100% offline, `respx` para a Tavily e o LLM, sem key no CI. Cubra pelo menos o caso
do gate barrando (contexto genérico do município) e o do gate deixando passar.

## Tarefa 2 — os 200 cronometrados

É a única coisa que falta para dizer se cabe em semanas. Meça **throughput**, não qualidade:

- 200 atrativos reais pela cascata inteira (Tavily → gate → Haiku), cronometrando ponta a ponta
- reporte: atrativos/hora, p50 e p95 por atrativo, taxa de 429/503 por provedor, quantos o
  gate barrou, custo real total
- extrapole para 10 mil: quantos dias, e qual provedor é o gargalo

Os 100 da amostra existente estão em `docs/poc/pilot-100/atrativos.json`. Para chegar a 200,
puxe do banco (`rio_records` / Nascente) — **o Postgres precisa estar de pé**.

Registre o resultado como **§25** em `docs/poc/gemini-viability.md`, no mesmo formato das
seções anteriores: o que foi medido, a tabela, as armadilhas encontradas, e um veredito que
diga explicitamente se cabe no prazo.

## Arquivos-chave

| arquivo | o que é |
|---|---|
| `brave/lanes/atrativos/copywriter.py` | `TourismCopywriter`, `COPYWRITER_SYSTEM`, `_build_context`, `WEB_SEARCH_TOOL` |
| `brave/lanes/atrativos/places_enrichment.py:234,280` | onde o copywriter é instanciado e chamado na lane |
| `brave/config/settings.py:422-465` | as flags da lane e seus comentários |
| `scripts/poc/cascade_scale_probe.py` | gate + groundedness, com self-check offline |
| `scripts/poc/cascade_probe.py` | montagem do prompt da cascata + controle de produção |
| `scripts/poc/search_snippets_probe.py` | `buscar_tavily` e os outros provedores |
| `scripts/poc/cascade_scale_probe.json` | as 30 medições de groundedness, para calibrar o limiar |

## Armadilhas que já custaram tempo

- **Env**: serviços e sondas precisam de `set -a; . ./.env; set +a`. Não há `env_file` no
  settings — sem isso os secrets saem vazios e toda mutação dá 401.
- **`RUN_REAL_EXTERNALS`**: sourcing do `.env` liga a flag e faz o suite offline bater em API
  real. Rode o pytest com `unset RUN_REAL_EXTERNALS`.
- **Banco**: `docker compose up` é o jeito padrão de subir a stack. Testes de integração
  pulam em silêncio sem `BRAVE_DB_URL` — worktree verde e main vermelho já aconteceu por isso.
  Depois de rodar o suite contra o banco local, resete com a skill `reset-brave-db`.
- **`description_enrichment_enabled` está `false`** no overlay do banco desde 28/08. Sweeps
  rodam sem descrição e **sem erro**. Confirme o estado antes de concluir qualquer coisa sobre
  "a lane não escreveu".
- **Discrepância a resolver**: o comentário em `settings.py:454` diz que o `WEB_SEARCH_TOOL`
  foi de `max_uses` 3 → 2, mas `copywriter.py:52` tem `3`. O comentário está errado ou a
  mudança foi revertida — a conta de custo da rota de produção depende disso.
- **`rtk`**: neste repo o hook faz `ls` devolver vazio de forma intermitente. Use `find` ou
  Read; nunca leia vazio como "arquivo não existe".

## Pronto quando

1. Gate implementado atrás de flag, suite offline verde, ruff limpo.
2. §25 escrita com os 200 cronometrados e um veredito de prazo.
3. Commits separados por achado, no padrão das mensagens de `git log` desta branch.

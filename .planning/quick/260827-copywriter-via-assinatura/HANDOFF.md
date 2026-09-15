# Handoff — piloto do subagente copywriter

Prompt pronto para colar numa sessão nova. Contexto completo, não depende do histórico anterior.

---

Vamos rodar o piloto do subagente `norteia-copywriter` no norteia-brave. Contexto do que já foi
medido (tudo em `docs/poc/gemini-viability.md`, branch `docs/gemini-viability-poc`, já pushada):

**Onde chegamos.** A lane de descrição de atrativos usa `claude-sonnet-4-5` + a tool
`web_search` da Anthropic e custa **$74,90/mil atrativos**. Medimos que 61% disso são os
~11.900 tokens de página que a busca injeta no prompt, não a taxa de busca (§11.1). Medimos
também que uma API de busca contratada entrega **9 dos 10 mesmos fatos em 2.311 tokens** — desde
que sejam **2 queries por atrativo** (uma só recupera 5/10) e **sem ler a página**, que piora as
duas pontas (§18). Isso derruba a lane para ~$17,70/mil.

**A última incerteza.** A §18 mediu que o *fato* chega pelo snippet. Não mediu se a *prosa*
escrita por um modelo barato a partir de snippets se sustenta contra o Sonnet com busca. É a
única coisa que separa a cascata da produção.

**O plano.** Usar a assinatura Claude Max como **oráculo de qualidade**, não como motor (§20 —
rodar os 10 mil da carga inicial pela assinatura foi reprovado: custa $37-177 pela cascata
contra $100/mês de plano, e não cabe na cota por ~10x). O subagente gera um conjunto de
referência em qualidade Sonnet-com-busca; a cascata barata é pontuada contra ele.

**Tarefa desta sessão — começar pelos 3 atrativos obscuros da §15.1**, que dão comparação
cabeça a cabeça com número já medido:

| atrativo | município/UF | Sonnet in-lane gastou |
|---|---|---|
| Mirante da Lagoa | Guarapari/ES | $0,0611 |
| Mirante de Buenos Aires | Guarapari/ES | $0,0532 |
| Vista Linda | Domingos Martins/ES | $0,1131 |

Passos:

1. Rodar o subagente `norteia-copywriter` (já existe em `.claude/agents/norteia-copywriter.md`)
   nos três, salvando o JSON de saída com `descricao_editorial`, `status`, `fontes` e `queries`.
2. Gerar a descrição dos mesmos três pela via barata — snippets da Tavily (2 queries, sem
   `raw_content`) alimentando `gemini-3.5-flash-lite`. A sonda
   `scripts/poc/search_snippets_probe.py` já busca e conta token; falta só plugar a escrita.
3. Comparar as duas saídas: fidelidade factual contra os 10 fatos conhecidos, aderência às
   regras de voz do prompt (PT-BR, sem travessão, sem clichê, sem dado operacional), e custo.
4. Se a prosa barata se sustentar, escrever como §21 e a decisão está tomada para os 10 mil.

**O que já está no repo e não precisa ser refeito:**

- `docs/poc/gemini-viability.md` §1-20 — todo o histórico medido
- `.claude/agents/norteia-copywriter.md` — subagente com o `COPYWRITER_SYSTEM` de produção
  verbatim, regra de 2 queries, regra anti-fabricação e contrato de saída JSON
- `scripts/poc/search_snippets_probe.py` — busca + contagem de fato/token (`--self-check` offline)
- `scripts/poc/parametric_memory_probe.py` — prova que o modelo inventa sem busca
- `brave/lanes/atrativos/copywriter.py` — `COPYWRITER_SYSTEM` e `_build_context` de produção,
  importáveis

**Armadilhas conhecidas:**

- O stack local está **desligado**. `docker compose up` antes de qualquer coisa que toque o DB.
- Carregar env com `set -a; . ./.env; set +a`, e **`unset RUN_REAL_EXTERNALS`** antes de rodar
  pytest, senão a suíte offline bate em API real.
- Só existe `TAVILY_API_KEY` no `.env` (1.000 créditos/mês, ~970 restantes). Não há key de Exa
  nem de Brave Search.
- Se o piloto for gravar descrição na base: **nunca `UPDATE` direto em `rio_records.canonical`**
  — pula o `record_event` de auditoria e não recomputa o score. Escrever arquivo e ingerir por
  endpoint que gere o evento.
- Depois de rodar a suíte contra o DB local, resetar com a skill `reset-brave-db`.
- Medimos (§19) que o modelo **fabrica fontes** quando não consegue buscar — o Sonnet inventou
  4 URLs, uma delas um `es.gov.br` que dá 404. Qualquer descrição sem `fontes` preenchidas é
  suspeita por construção.

Trabalhe na branch `docs/gemini-viability-poc`. Commits doc-only seguem o padrão das §17-§20.

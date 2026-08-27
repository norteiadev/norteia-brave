---
quick_id: 260827-rp1
slug: copywriter-via-assinatura
date: 2026-08-27
type: poc
status: complete
---

# Quick: rodar o copywriter pela assinatura Claude Code Max?

Proposta do usuário: subagente do Claude Code com o prompt do copywriter, desligar o
enriquecimento de descrição no Brave e gerar a prosa externamente, absorvendo LLM + busca na
assinatura Max 5x já paga. Alvo: a carga inicial de ~10 mil atrativos.

## Entregas

1. `.claude/agents/norteia-copywriter.md` — subagente com o `COPYWRITER_SYSTEM` de produção
   verbatim, mais a regra de 2 queries (§18), a regra de fabricação (§19) e contrato de saída
   JSON com `fontes`/`queries` para auditoria.
2. §20 de `docs/poc/gemini-viability.md` — a avaliação.

## Veredito

Não como motor, nem para a carga inicial. Não por ToS (`claude -p` é uso sancionado), mas por
três motivos medidos: a carga inicial inteira custa $37–177 pela cascata contra $100/mês de
assinatura; não cabe na cota por ~10x contra a régua publicada pela Anthropic ($100/mês para
Max 5x); e não dispensa construir a cascata, porque a lane continua rodando depois.

Sobrevive como **oráculo de qualidade**: ~100 atrativos viram conjunto de referência para medir
se a prosa da cascata barata se sustenta contra o Sonnet com busca — a última incerteza aberta.

## Próximo passo

Rodar o piloto. Começar pelos 3 obscuros da §15.1, que dão comparação cabeça a cabeça com
número já medido.

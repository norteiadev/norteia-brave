---
quick_id: 260820-iig
slug: omniroute-secao-17
date: 2026-08-20
status: complete
---

# Resumo

Seção 17 acrescentada a `docs/poc/gemini-viability.md` (linhas 759-875, antes de `## Fontes`),
mais 5 URLs na lista de Fontes. Doc-only.

## Conteúdo entregue

- **17.1** — o que é o OmniRoute, com métricas do repo (51.760 stars, 14.605 arquivos, MIT,
  ~1,53B tokens grátis/mês em 43 pools).
- **17.2** — reprovação como provider, quatro motivos: ToS calibrado para proxy pessoal
  single-user; não ataca o custo real (61% são tokens de busca, §11.1); runtime Node no hot
  path; roteamento `auto` produz voz não-determinística num campo que vai ao Mar canônico.
- **17.3** — tabela de preços de busca (Exa/Tavily/Brave/Serper) verificada nas páginas
  oficiais em 2026-08-20.
- **17.4** — duas correções ao próprio relatório: (a) a projeção de ~$0,95/mil da §15.2
  dependia do Serper, que não tem free tier recorrente — faixa real é $0,95 a $7,60/mil contra
  $74,90 hoje; (b) o catálogo do OmniRoute erra ao dizer que o tier grátis da Brave acabou.
- **17.5** — item aberto de compliance: cláusula de storage rights da Brave contra a persistência
  da descrição derivada em Mar → norteia-api. Não medido, não consultado juridicamente.
- **17.6** — veredito e o desdobramento da pendência da §15.3 em três provedores, somando
  ~4.000 buscas/mês grátis.

## Decisão de execução

O texto foi escrito diretamente em vez de delegado a um executor: todos os números vieram de
verificação ao vivo nas páginas oficiais nesta sessão, e a seção existe justamente porque um
catálogo de terceiro publicou um preço errado. Delegar a redação reintroduziria o risco que a
seção documenta.

## Pendências que a seção deixa

- Rodar o teste da §15.3 (snippet basta?) nos free tiers de Exa + Tavily + Brave.
- Ler a cláusula de storage rights da Brave antes de adotá-la.

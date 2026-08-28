# Piloto do subagente — artefatos da §21

Medição de 2026-08-28: 30 atrativos do Espírito Santo passados pela lane TripAdvisor completa
**menos** o copywriter, exportados, descritos pelo subagente `norteia-copywriter` rodando sobre
a assinatura Claude Max, e devolvidos ao JSON de trabalho.

```
atrativos.json     os 30 registros, já com descricao_editorial/status/fontes/queries preenchidos
entradas/          o que cada invocação do subagente recebeu (10 singles + 2 lotes de 10)
saidas/            o que cada invocação escreveu, no contrato do subagente + rio_id
```

O split de entrada é a medição: `single-01..10` custeiam um atrativo isolado, `lote-1` e `lote-2`
custeiam dez de uma vez. A diferença entre os dois é o fator de amortização do system prompt e das
definições de tool, que são pagos uma vez por invocação e não por atrativo.

Reproduzir:

```bash
set -a; . ./.env; set +a
.venv/bin/python -m scripts.poc.pilot_descricoes export --limit 30
#  ... rodar o subagente sobre entradas/, que escreve em saidas/ ...
.venv/bin/python -m scripts.poc.pilot_descricoes merge
.venv/bin/python -m scripts.poc.pilot_descricoes import            # dry-run
.venv/bin/python -m scripts.poc.pilot_descricoes import --commit   # grava, auditado
```

O `import` passa por `PATCH /api/v1/atrativos/{rio_id}/edit` (que emite `cms_edited` no audit)
seguido de `PATCH /api/v1/dlq/{rio_id}/reprocess` (que recomputa o score), nunca por `UPDATE`
direto em `rio_records.normalized` — a armadilha da §20.7. O motor precisa estar PAUSADO ou
DESLIGADO, senão o edit devolve 423 Locked.

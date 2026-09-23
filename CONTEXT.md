# Glossário do domínio — Pipeline Brave

**Nascente** — o dado bruto como chegou da fonte (payload JSONB em `nascente_records`), sem limpeza nem score. Nunca é editado; é a prova do que a fonte disse.

**Rio** — o registro limpo, deduplicado, normalizado e pontuado (`rio_records`). O score de confiabilidade decide o roteamento: segue para o Mar, cai na DLQ ou é descartado.

**Mar** — o registro canônico validado (`mar_records`), o único que a norteia-api recebe. Toda entrada no Mar passa por validação humana (ENG-05); o sweep nunca promove sozinho.

**DLQ** — a fila de revisão humana: registros do Rio que não cruzaram o gate de confiabilidade (score baixo, sem review recente, etc.) e esperam decisão do steward ou do dono.

**Promoção** — a entrada síncrona, validada por humano, de um registro do Rio no Mar: o steward (Painel/DLQ) ou o dono via WhatsApp confirma, o registro é re-pontuado com `validacao_humana=100`, promovido (ou segurado pelo backstop de 90 dias), auditado e commitado — e só então a Publicação é enfileirada. Verbo: `promote` em `brave/core/mar/publication.py`.

**Publicação** — o envio assíncrono da linha ativa do Mar para a norteia-api pela task `brave.publish_mar` (verbo `publish`). Nunca promove; pula o POST quando o payload não mudou (`push_hash`) e só carimba `pushed_at` quando a norteia-api aceitou.

**Pendente** — linha ativa do Mar com `pushed_at` NULL: o outbox. Fica pendente quando o broker ou a norteia-api estão fora; o beat de 15 min e o "Reenviar" do Painel chamam `republish_pending` para reenfileirar a Publicação.

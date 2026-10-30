# Glossário do domínio — Pipeline Brave

**Nascente** — o dado bruto como chegou da fonte (payload JSONB em `nascente_records`), sem limpeza nem score. Nunca é editado; é a prova do que a fonte disse.

**Rio** — o registro limpo, deduplicado, normalizado e pontuado (`rio_records`). O score de confiabilidade decide o roteamento: segue para o Mar, cai na DLQ ou é descartado.

**Mar** — o registro canônico validado (`mar_records`), o único que a norteia-api recebe. Toda entrada no Mar passa por validação humana (ENG-05); o sweep nunca promove sozinho.

**DLQ** — a fila de revisão humana: registros do Rio que não cruzaram o gate de confiabilidade (score baixo, sem review recente, etc.) e esperam decisão do steward ou do dono.

**Promoção** — a entrada síncrona, validada por humano, de um registro do Rio no Mar: o steward (Painel/DLQ) ou o dono via WhatsApp confirma, o registro é re-pontuado com `validacao_humana=100`, promovido (ou segurado pelo backstop de 90 dias), auditado e commitado — e só então a Publicação é enfileirada. Verbo: `promote` em `brave/core/mar/publication.py`.

**Publicação** — o envio assíncrono da linha ativa do Mar para a norteia-api pela task `brave.publish_mar` (verbo `publish`). Nunca promove; pula o POST quando o payload não mudou (`push_hash`) e só carimba `pushed_at` quando a norteia-api aceitou.

**Pendente** — linha ativa do Mar com `pushed_at` NULL: o outbox. Fica pendente quando o broker ou a norteia-api estão fora; o beat de 15 min e o "Reenviar" do Painel chamam `republish_pending` para reenfileirar a Publicação.

**Lane de imagens** — a coleta de fotos (capa e galeria) no Wikidata e no Wikimedia Commons para atrativos e destinos que já estão no Rio (engine action `images`, task `brave.images_uf`). Não é uma fonte de coleta e nunca escreve no `normalized`: grava só `image_lookups`, `images` e `entity_images`. A origem de cada foto (`p18`, `p180`, `p373`, `geo500`, `muni_top`, `atrativo`) fica em `entity_images.lane`, que não tem relação com as lanes Destinos/Atrativos do sweep.

**Acervo** — os arquivos de imagem do Brave, endereçados pelo sha1 dos bytes (`images/{sha1[:2]}/{sha1}.{ext}`), num diretório local ou no bucket R2 privado. É uma cópia reconstruível do Commons.

**Foto pendente** — linha de `entity_images` com `pushed_at` NULL, sem `push_error` e não removida: o outbox das fotos, separado do outbox do Mar. O beat de 15 min (`brave.push_pending_media`) envia só fotos de entidades que já estão na norteia-api, e só com `images_push_enabled` ligado (desligado por padrão: as fotos esperam a revisão humana); `brave.cli images-repush` reenfileira todas.

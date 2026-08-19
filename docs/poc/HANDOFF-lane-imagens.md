# Handoff — implementar a lane de completude de imagens

Prompt de entrada para a sessão de implementação. Carrega os achados da POC (PR #13),
as decisões já tomadas e as armadilhas conhecidas, para não redescobrir nada.

---

Implemente a lane de completude de imagens para atrativos no norteia-brave.

## Contexto — POC já feita, não refazer

PR #13 (branch `poc/image-enrichment`) contém a POC de viabilidade, read-only.
Leia antes de começar:
- `docs/poc/atrativo-imagens.md` — resultado e veredito
- `docs/poc/embratur-licenciamento-imagens.md` — por que Embratur está bloqueada
- `scripts/poc_images/` — código da POC (commons.py, mtur.py, harvest_mtur.py, pixabay.py)

Resultado medido, cobertura ancorada no lugar (match MTur ou geosearch Commons):
db (15 reais, ES) 14/15 · famoso 4/4 · médio 4/4 · **obscuro 2/4**.
Verificado por humano: matches de MTur e Commons-geo estão semanticamente corretos.

## Decisões já tomadas — não reabrir

1. **Fontes: MTur Destinos + Wikimedia Commons geosearch. Nessa ordem.**
2. **Pixabay NÃO entra em produção.** A POC provou que ele casa tokens do nome contra
   tags de stock, sem noção de lugar: "Praia da Costa" (Vila Velha/ES) retornou pôr-do-sol
   no Mar do Norte, e uma imagem foi servida para 17 atrativos diferentes. Legendar isso
   como foto do atrativo cai em "misleading or deceptive" no ToS deles.
3. **Google Places Photos está fora.** ToS do Maps Platform proíbe `store`/`rehost` de
   Google Maps Content e proíbe cachear até o `photo name`. Manter o field mask de
   `brave/clients/places.py:45-66` sem `photos`.
4. **Flickr direto está fora** — criação de chave desabilitada para conta gratuita, e
   chave comercial passa por análise de equipe. O acervo MTur vem via Commons, keyless.
5. **Política de licença: permissivas + share-alike.** Aceitar `cc0`, `pd`, `cc-by-*`,
   `cc-by-sa-*`. Rejeitar `-nd-` (redimensionamos) e `-nc-`. Rejeitar `Restrictions`
   não-vazio. Licença desconhecida vai para balde contado, nunca aceita em silêncio.
   Reusar `license_verdict()` de `scripts/poc_images/commons.py`.

## As 7 peças, com o padrão a copiar

| Peça | Copiar de |
|---|---|
| Protocolo do client | `MelhoresDestinosClientProtocol` — `brave/clients/base.py:347` |
| Real + Null client | `brave/clients/melhores_destinos.py` / `null_melhores_destinos.py` |
| Settings | `MelhoresDestinosConfig` (`settings.py:378-446`), `env_prefix="BRAVE_IMAGES_"` |
| Toggle | `images_enrichment_enabled` + `_IMAGES_ENRICH_KEY` (`runtime.py:70,132,277`) |
| Agent | `PlacesEnrichmentAgent` (`brave/lanes/atrativos/places_enrichment.py`) |
| Celery task | `brave.enrich_images`, despachada junto de `enrich_places_task` (`pipeline.py:1426-1430`) |
| Degrau completude | bump in-place `90.0 → 100.0`, precedente em `description.py` |

## Três armadilhas — já custaram bug em produção

1. **Denylist do Mar.** `mar/service.py:102-108` monta `canonical` = `normalized` menos
   uma lista. `images` deve fluir para a norteia-api (desejado), mas o marcador
   `images_enriched` **precisa** entrar na exclusão, senão vaza no payload publicado.
   É exatamente por isso que `google_enriched` está lá.

2. **NÃO gatear por `sub_state`.** Atrativo TA pontua ~55 < 80 e o dlq-bounce zera o
   `sub_state` no passo de descrição — qualquer step gateado por `sub_state` depois disso
   nunca dispara. Foi o bug do commit `733134f`. Usar idempotência por marcador em
   `normalized`, como o `PlacesEnrichmentAgent`.

3. **Trava de UF no match fuzzy é obrigatória.** Sem ela, 1 de 11 matches do MTur era
   falso positivo (convento de Itanhaém/**SP** atribuído a Vila Velha/**ES**). O código
   de UF pode estar em qualquer posição do título, colado ao fotógrafo. Ver `uf_hint()`
   e o teste de regressão em `scripts/poc_images/mtur.py`.

## O que a POC descobriu sobre os dados (não presumir o contrário)

- **O Commons renomeia arquivos na importação.** A convenção
  `Fotografo_Atrativo_Municipio_UF` do Flickr NÃO sobrevive. O sinal confiável é a
  **categoria de lugar** do Commons — 98,7% de presença, curada por humano na importação.
  O match roda contra `place_categories + object_name`, não contra o nome do arquivo.
- **Nada do MTur é geotagueado** (0/100 na amostra). Não existe fallback geo nessa lane.
- **842 fotos com tag `fotoshumanizadas2018`** têm janela de uso expirada em 03/04/2023
  (declaração do próprio MTur no perfil). O Commons **não tem categoria** para essa tag —
  o filtro é heurístico do nosso lado. Manter `is_restricted()` e **contar/logar o que
  não conseguiu classificar**.
- **User-Agent descritivo é obrigatório** no Commons: sem UA → 403, `python-requests` →
  403. O default do httpx provavelmente é bloqueado. Requisições em série, nunca paralelo.

## Três decisões de desenho que você precisa tomar — recomendação junto

1. **Onde vive o índice do MTur (4.886 fotos usáveis).**
   O acervo é congelado (subiu entre mar–jun/2018, nada novo em 8 anos), então não precisa
   de refresh periódico. Opções: arquivo em `data/`, tabela no Postgres, ou Redis.
   **Recomendo tabela** (`mtur_images`) populada por uma task de ops rodada sob demanda —
   evita 7,2 MB no git e deixa o índice consultável. Só os campos usados no match e no
   payload; o JSON cru da POC tem muito lixo.

2. **Download para S3 entra nesta lane ou é etapa separada?**
   **Recomendo separar.** Esta lane resolve *descoberta e match* e grava `images[]` com
   URL de origem + licença + atribuição. Um passo posterior baixa e reescreve a URL para o
   bucket. Motivo: descoberta e transferência de bytes têm perfis de falha e de retry
   completamente diferentes, e misturar torna o retry caro.
   Se decidir juntar, note que **Commons pede para não hotlinkar em escala** — a URL de
   origem não deve ir para produção como está.

3. **Revalidação — só onde faz sentido.**
   - **MTur: não precisa.** Domínio público não é revogável.
   - **Commons: precisa.** Licença é auto-declarada e arquivos são deletados post-hoc por
     violação de copyright — nossa cópia continuaria servindo conteúdo infrator.
     Desenhar TTL por imagem + revalidação em lote + fallback promovendo a próxima candidata.

## Escopo

Fazer: client, settings, toggle, agent, task, degrau de completude, testes.
Não fazer: Pixabay, Places photos, Embratur (bloqueada — ver doc), download S3 (se seguir
a recomendação 2), dedup de imagem entre atrativos.

## Testes

Suite offline por padrão, nada bate em rede real sem flag (constraint do repo).
Mockar Commons com `respx`, seguindo `tests/unit/lanes/test_places_enrichment.py`.
Cobrir obrigatoriamente: a trava de UF (caso Itanhaém/SP × Vila Velha/ES), o filtro de
licença incluindo o balde desconhecido, o filtro `fotoshumanizadas2018`, e a idempotência
por marcador.

Rodar: `.venv/bin/python -m pytest`. Antes, `unset RUN_REAL_EXTERNALS` — se você deu
source no `.env`, os testes de integração vão bater em API real.
Integração precisa de `BRAVE_DB_URL`, senão pula em silêncio e mascara regressão.
Depois de rodar contra o banco local, `python scripts/reset_db.py --yes`.

## Limitação honesta a carregar

O long tail continua sem solução: **2/4 dos atrativos obscuros** têm imagem ancorada.
`Vale do Pati` e `Poço Encantado` não têm nada em nenhuma fonte gratuita. A lane melhora
a cobertura, não a resolve. Se aparecer pressão para fechar essa lacuna com stock
genérico, a resposta é não — a POC mediu exatamente por que isso engana.

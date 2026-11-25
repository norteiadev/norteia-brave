# Fonte editorial "web" — campos de atrativo a partir de fontes públicas

Status: desenho (24/11/2026). Piloto no ES feito; implementação pendente de aprovação.

## Por quê

O blog Viagens e Caminhos bloqueia o crawler (403 do servidor de origem em páginas, feed, API e
sitemap; navegador passa). Não contornamos bloqueio (regra em `CLAUDE.md`). A alternativa testada:
uma busca no Parallel por atrativo e o Gemini redigindo os campos editoriais só com os trechos
encontrados, citando as fontes.

## O que o piloto mostrou (ES, 87 atrativos que o blog cobria)

Prompt v2 (o deste documento), Gemini 2.5 Flash, mesma busca do Parallel:

| Campo | Blog | Web v1 | Web v2 | Descartes v2 |
|---|---|---|---|---|
| how_to_get_there | 35 | 87 | 84 | 1 enchimento |
| tips | 43 | 87 | 71 | 9 enchimento |
| local_infrastructure | 47 | 87 | 71 | 4 enchimento |
| curiosities | 56 | 87 | 84 | — |
| accessibility | 0 | 54 | 20 | 6 sem fato, 1 enchimento, 1 curto |

- Em nenhum atrativo o blog tinha um campo que a web não preencheu.
- Blog × web nos mesmos campos (v1, 181 pares): 86% complementares, 6% equivalentes, 8%
  conflitantes. Em vários conflitos a web está mais atual: agendamento obrigatório em Pedra Azul,
  endereço do Museu Solar Monjardim, infraestrutura limitada na Bacutia.
- Custo: ≈ US$ 0,003 por atrativo (US$ 0,001 do Parallel + ~US$ 0,002 do Gemini). Brasil, ~9.200
  atrativos na API: ≈ US$ 28.
- O que se perde: a experiência de quem visitou ("chegue cedo", "trecho de terra"). O blog
  continua valendo onde já tem fato.

## Desenho

### Coleta (por atrativo do Mar)

1. **Busca**: uma `/v1/search` do Parallel (`turbo`), com duas queries: `"<nome> <município> <UF>"`
   e a mesma com os temas dos campos. Resultados de `viagensecaminhos.com` e de hosts de baixo
   sinal (Google Maps, TripAdvisor, Booking, redes sociais) são descartados antes do prompt.
2. **Redação**: um `generate()` do Gemini com `response_schema` (um objeto `{value, sources}` por
   campo), com o prompt v2: só fatos concretos dos trechos; campo sem fato fica vazio; proibido
   escrever que falta informação, recomendar confirmar ou preencher com descrição genérica; até
   500 caracteres; definição de cada campo com o que **não** vale.
3. **Filtros determinísticos** (um campo que cai não derruba os outros):
   - toda URL de `sources` tem que ter vindo da busca; sem fonte, o campo cai;
   - regex de enchimento (`não há informação…`, `recomenda-se confirmar`, `consulte`…);
   - `accessibility` só com termo concreto (rampa, piso tátil, cadeira, adaptado, acessível…);
   - mínimo de 40 caracteres;
   - `tips` passa por `schema.strip_volatile_tips` (preço e horário saem, como no blog, D3);
   - a guarda de cópia literal que já existe (≥10 palavras seguidas de um trecho → retido).

### Armazenamento (reusa a lane editorial; nada novo no Mar)

- **Documento**: uma linha em `editorial_documents` por atrativo, `source = "web_parallel"`,
  `url = "web://parallel/<canonical_key>"`, `uf`, `published_time = data da busca`. O `body_text`
  guarda os trechos renderizados e a versão (`editorial_document_versions`) guarda a resposta
  inteira do Parallel em JSON, para poder regenerar com outro modelo sem pagar a busca de novo.
- **Claims**: uma por campo preenchido, já com `canonical_key` (o atrativo é conhecido, não há
  casamento por coordenada), `match_method = "direct"`, `evidence` = as URLs citadas, status
  `matched`. Um campo descartado vira claim `held_web` com o motivo, para auditoria — nada é
  apagado.
- **Refresh**: rodar de novo um atrativo cria uma versão nova do documento; as claims antigas viram
  `superseded` pelo mecanismo que já existe.

### Resolução e envio

- O resolvedor **pula o casamento** das claims `match_method = "direct"`; o resto do fluxo é o
  mesmo (`normalized["editorial"]` → `processed_at` → outbox do Mar → norteia-api).
- Validação: a claim web nasce com a data da busca, então não é "post velho" e não vai para a
  validação. Depois de 1 ano passa a valer a regra de perecível; o refresh resolve antes disso.
- **Precedência blog × web** (decisão em aberto, abaixo). Hoje o resolvedor escolhe o
  `published_time` mais recente, o que faria a web sempre ganhar.

### Operação

- Task `brave.editorial_web_fill(uf, limit)` atrás da flag `editorial_web_enabled` e de
  `run_real_externals`; lote de N atrativos do Mar sem documento web (ou com documento mais velho
  que `editorial_web_refresh_days`), mais antigos primeiro; commit por atrativo; cost guard antes
  de cada chamada.
- CLI: `editorial-web <UF>|--all [--limit N]` e uma linha nova em `editorial-status` com o custo
  esperado (`editorial_web_est_usd_per_attraction = 0.005`, arredondado para cima).
- Tempo: ~4 s por atrativo, 4 em paralelo → Brasil inteiro em ~2,5 h de relógio.
- Identidade: o Brave só chama a API do Parallel e a do Gemini; quem busca nos sites é o Parallel.
  Nenhum User-Agent nosso chega aos sites (regra de identidade sem nome da empresa atendida).

### Testes (offline)

- Filtros: enchimento, fonte fora da busca, acessibilidade sem termo, `tips` com horário, cópia
  literal — cada um derruba só o campo.
- Armazenamento: documento + versão + claims `direct`; refresh supersede as antigas sem apagar.
- Resolvedor: claims `direct` não são re-casadas; precedência conforme a decisão.
- Task: flag desligada não chama nada; cost guard para o lote; atrativo que falha não para os outros.

## Decisões em aberto

1. **Precedência blog × web por campo.** Recomendação: perecível (`how_to_get_there`, `tips`,
   `local_infrastructure`, `accessibility`) → o mais recente (na prática, a web); perene
   (`curiosities`) → o blog quando existir, senão a web.
2. **Modelo de redação**: o piloto usou 2.5 Flash. Com o PR #160 (thinkingLevel), testar o
   3.5 Flash-Lite neste prompt antes de rodar o Brasil (o 2.5 Flash é redirecionado em 01/12).
3. **Escopo da 1ª rodada**: só atrativos que estão na API (~9.200) ou todo o Mar.
4. **Destinos**: mesma fonte com os campos de `DestinoFields` numa 2ª fase.

## Arquivos do piloto (scratchpad da sessão, não versionados)

`webfields.py` (v1), `webfields_v2.py` (v2), `webfields_result.json`, `webfields_v2_result.json`.

# poc/maps_scraper

Esta POC testa se o scraper do Google Maps, alimentado pelo sweep do TripAdvisor, pode substituir a Places API. Ela roda fora do Brave: são scripts standalone (stdlib + httpx) que não importam `brave.config`, não abrem conexão com DB, Redis ou Celery e não chamam a norteia-api. Os resultados estão em [RELATORIO.md](RELATORIO.md).

Rode sempre a partir da raiz do repo com `--frozen`. Sem ele, o uv regrava o `uv.lock`.

```bash
cd /Users/leandro/orca/workspaces/norteia-brave/POC-Places-alternative
```

## 1. Sweep do TripAdvisor (`ta_sweep.py`)

```bash
uv run --frozen python poc/maps_scraper/ta_sweep.py --selftest     # offline, parseia a fixture
uv run --frozen python poc/maps_scraper/ta_sweep.py --from-pilot   # piloto de 100, sem rede
```

Sem sessão, o TA responde 403 DataDome. Para fazer o sweep real com a sua sessão:

1. Abra o tripadvisor.com.br logado no navegador e vá a uma página de atrativos.
2. No DevTools, aba Network, clique com o botão direito numa requisição a `/data/graphql/ids` e escolha **Copy as cURL**.
3. Salve o conteúdo num arquivo fora do repo, por exemplo `~/ta.curl`.
4. Rode:

```bash
uv run --frozen python poc/maps_scraper/ta_sweep.py --uf ES --max-pages 2 --curl-file ~/ta.curl
# opções: --geo-id N | --transport gql|html | --sleep S (mínimo 3 s) | --qid ID | --out PATH
```

**Os cookies e o User-Agent do TA são lidos só em memória e nunca são impressos nem logados.** O stderr mostra apenas a página, o offset, os cards e o tempo. O sweep para no primeiro 403, 429 ou captcha. Apague o `~/ta.curl` quando terminar.

Saída: `out/ta_atrativos.jsonl`.

## 2. Scraper do Google Maps (`maps_scraper.py`)

Faz requisições anônimas, com UA fixo, sem cookies, proxy ou captcha, e para no primeiro 429, `/sorry/` ou captcha.

```bash
uv run --frozen python poc/maps_scraper/maps_scraper.py --selftest
uv run --frozen python poc/maps_scraper/maps_scraper.py --in poc/maps_scraper/out/ta_atrativos.jsonl \
  --out poc/maps_scraper/out/maps.jsonl --delay 1.5 --limit 10
```

## 3. Avaliação contra o Brave e a Places (`avaliar.py`)

```bash
uv run --frozen python poc/maps_scraper/avaliar.py --selftest
uv run --frozen python poc/maps_scraper/avaliar.py                       # cobertura e gate, sem rede
BRAVE_PLACES_API_KEY=... uv run --frozen python poc/maps_scraper/avaliar.py --ground-truth --n 20 \
  --md poc/maps_scraper/out/avaliacao.md                                 # pago, ~US$0,09/atrativo
```

A chave da Places API é lida só da variável de ambiente e nunca é impressa.

## 4. Rampa de rate limit (`rate_probe.py`)

```bash
uv run --frozen python poc/maps_scraper/rate_probe.py --selftest
uv run --frozen python poc/maps_scraper/rate_probe.py   # faz rede: rampa em degraus e para no 1º sinal
```

## Dados

`out/` está no `.gitignore` porque contém nomes de reviewers. Não versione e apague quando a POC acabar.

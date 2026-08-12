# Cadastur → `local_businesses` (lado Brave)

Runbook do `scripts/cadastur_import.py`. Traz o registro federal de prestadores de
serviços turísticos do MTur para a tabela de referência `local_businesses` do Brave.

**Escopo desta entrega: só o lado Brave.** Não existe endpoint
`POST /api/internal/territorial/local-businesses` na norteia-api, não existe
`push_local_business` no cliente, e nada é enviado para lá. A tabela fica pronta e
populada; a rota até a plataforma é uma segunda rodada.

---

## Por que script, e não uma lane

Cadastur é registro estático trimestral, com emissor oficial e chave natural (o
número do certificado). Não precisa de score, dedup vetorial nem DLQ. Rodar isso pelo
Nascente → Rio → Mar é exatamente o que a lane de destino-seed do Mtur fazia antes de
ser aposentada — ver o docstring de `scripts/seed_reference_data.py`.

---

## Pré-requisitos

```bash
set -a; source .env; set +a
```

Duas variáveis:

| var | para quê |
|---|---|
| `BRAVE_DB_URL` | destino do upsert |
| `BRAVE_DADOS_GOV_API_KEY` | **só para descobrir a URL do arquivo** |

A chave do dados.gov.br sai em `https://dados.gov.br` → login pelo **gov.br**
(conta nível Prata ou Ouro) → **"Minha Conta"**, no lado direito da página.

O download em si **não** usa a chave: o `link` de cada recurso aponta para
`dados.turismo.gov.br` e baixa sem autenticação. A chave é o custo de descobrir a
URL, não de baixar os dados.

E rode a migração antes:

```bash
.venv/bin/alembic upgrade head   # 0013_local_businesses
```

---

## Uso

```bash
# ver os 12 conjuntos e o business_type de cada um
.venv/bin/python -m scripts.cadastur_import --list

# um conjunto
.venv/bin/python -m scripts.cadastur_import --dataset cadastur-04

# vários
.venv/bin/python -m scripts.cadastur_import --dataset cadastur-04 --dataset cadastur-03

# tudo
.venv/bin/python -m scripts.cadastur_import --all
```

### Resultado real da carga (12/08/2026, trimestre 2ºTri/2026)

```
cadastur-01: sheets=2 read=43335 kept=43332 ibge=43106 written=43332
cadastur-02: sheets=1 read=638   kept=608   ibge=602   written=608
cadastur-03: sheets=1 read=57723 kept=55726 ibge=55607 written=55726
cadastur-04: sheets=1 read=21109 kept=20626 ibge=20420 written=20626
cadastur-05: sheets=1 read=293   kept=293   ibge=290   written=293
cadastur-06: sheets=1 read=15894 kept=15506 ibge=15464 written=15506
cadastur-07: sheets=1 read=745   kept=726   ibge=719   written=726
cadastur-08: sheets=1 read=581   kept=564   ibge=560   written=564
cadastur-09: sheets=1 read=510   kept=491   ibge=488   written=491
cadastur-10: sheets=1 read=473   kept=473   ibge=466   written=473
cadastur-11: sheets=1 read=2363  kept=2317  ibge=2307  written=2317
cadastur-12: sheets=1 read=12536 kept=12293 ibge=12225 written=12293
```

**152.955 linhas. Cobertura de IBGE: 99,5%.**

- `sheets` — planilhas de dados lidas (ver a armadilha do multi-sheet abaixo)
- `read` — linhas nas planilhas
- `kept` — sobreviveram aos filtros (Situação Cadastral = Regular **e** Situação da
  Atividade = Operação, com certificado e nome)
- `ibge` — quantas casaram com um município da tabela `municipios`
- `written` — linhas no upsert

Se aparecer uma linha `⚠ <slug>: N row(s) still match the CPF pattern`, o MTur mandou
uma grafia de CPF que o scrub não conhece. **Investigue antes de usar a carga** — ver
a seção de LGPD.

**Idempotente.** A chave é **composta**: `(cadastur_dataset, cadastur)`. Não é o
certificado sozinho — **6.808 entidades estão registradas em mais de uma categoria**
(um CNPJ aparece em 9 dos 12 conjuntos), então a chave simples fazia uma categoria
sobrescrever a outra em silêncio.

O upsert é `ON CONFLICT DO UPDATE`, não `DO NOTHING`: rodar de novo com um trimestre
mais novo tem que atualizar telefone/endereço, não pular a linha.

---

## Os 12 conjuntos

| slug | conjunto | `business_type` |
|---|---|---|
| `cadastur-01` | Guias de Turismo | `tour_guide` |
| `cadastur-02` | Acampamentos Turísticos | `accommodation` |
| `cadastur-03` | Agências de Turismo | `agency` |
| `cadastur-04` | Meios de Hospedagem | `accommodation` |
| `cadastur-05` | Parques Temáticos | `experience_operator` |
| `cadastur-06` | Transportadoras Turísticas | `transportation` |
| `cadastur-07` | Casas de Espetáculos / Animação | `cultural_space` |
| `cadastur-08` | Centros de Convenções | `cultural_space` |
| `cadastur-09` | Apoio ao turismo náutico / pesca | `experience_operator` |
| `cadastur-10` | Entretenimento e lazer | `experience_operator` |
| `cadastur-11` | Locadoras de Veículos | `transportation` |
| `cadastur-12` | Organizadoras de Eventos | `experience_operator` |

Os valores da direita são exatamente o enum `business_type` de `local_businesses` na
norteia-api. Dois dos oito — `restaurant` e `local_producer` — não têm equivalente:
o Cadastur não registra esses.

---

## LGPD — leia antes de mexer no parser

**"Guias de Turismo" (`cadastur-01`) traz CPF, data de nascimento, tipo sanguíneo e
número do documento de identificação na mesma planilha das colunas de negócio.**

O importador lê uma **allow-list** explícita de colunas (`_COMMON_FIELDS` e
`_EXTRA_FIELDS` em `scripts/cadastur_import.py`). Nenhuma coluna fora dessas listas
chega a virar linha. Verificado na planilha real: em `cadastur-01` saem fora `CPF`,
`Sexo`, `Data de Nascimento`, `Nacionalidade`, `Nome Social/Tratamento`,
`Documento de Identificação`, `Carteira de Estrangeiro`, `Órgão Expedidor` e
`Tipo Sanguíneo`.

Isso **não** é preciosismo: uma deny-list vaza no dia em que o MTur adicionar uma
coluna nova. Uma allow-list não vaza. Se alguém trocar por "lê tudo e derruba
algumas", o teste `test_a_new_pii_column_added_by_mtur_cannot_leak` quebra — é para
isso que ele existe.

### A allow-list sozinha não basta: CPF vem dentro do texto livre

Derrubar a **coluna** CPF não resolve. O nome de empresa da Receita Federal para
empresário individual carrega o número **dentro da string**:

```
THIAGO DINIZ FREIRE CPF 919.267.006-78
ADRIANA DOS REIS GONCALVES CPF 030647716-55
CLARICE MOREIRA DE QUEIROZ CPF.:127986146-00
JOAO PEREIRA COSTA-CPF-407.421.806.20
NELCI JULIETA PORTO CPF 365 401 026 15
```

Todos reais, todos de trimestres correntes. Repare que **cada um usa uma pontuação
diferente** — a primeira versão do regex só pegava a forma canônica e deixou passar as
outras quatro; só apareceram numa varredura das 152.955 linhas carregadas.

`_strip_cpf` limpa `trade_name`, `company_name` e `address` em duas formas:

1. **rotulada** — a palavra `CPF` seguida de 11 dígitos em **qualquer** pontuação;
2. **canônica sem rótulo** — `999.999.999-99` (não ambígua: CNPJ formatado é
   `99.999.999/9999-99`).

**Não** limpa um bloco de 11 dígitos sem rótulo: celular com DDD também tem 11 dígitos,
e limpar comeria telefone de dentro do nome do negócio. E `\bCPF\b` impede disparo em
palavras que só começam com essas letras (`CPFISCAL@…`, `icpf_cabofrio@…`, ambos reais
e ambos **não** são CPF).

Além dos testes, o importador **se audita**: antes de gravar, conta quantas linhas ainda
casam com o padrão de CPF e imprime `⚠` se houver. A suíte offline só conhece as
grafias que já vimos; esse contador é o que faz a próxima aparecer como número na saída
em vez de ficar guardada sem ninguém ver.

O **nome** do guia é importado. É pessoa física, mas o nome profissional é justamente
o ponto de um registro público (o turista confere o guia por ele). CPF, nascimento,
tipo sanguíneo e documento, não.

O `Número do Certificado` **não é o CPF** — verificado nas duas planilhas de
`cadastur-01`. Para PJ é o CNPJ; para PF é um número de registro próprio do Cadastur,
diferente do CPF da linha.

---

## Armadilhas do arquivo (todas tratadas, todas testadas)

| armadilha | tratamento |
|---|---|
| **Várias planilhas por arquivo.** `cadastur-01` tem `Guia PJ` (2.332 linhas) e `Guia PF` (**41.005**), com cabeçalhos diferentes. Ler só a `sheet1` importava **5%** do conjunto imprimindo linha de sucesso | `worksheet_paths()` percorre todas, mapeando cabeçalho por planilha. Planilha sem coluna de certificado é pulada **com aviso**, nunca em silêncio |
| **Células vazias somem** do XML. Contar `<c>` desloca toda coluna depois do primeiro branco — e numa planilha de 40 colunas isso é garantido | posição vem da referência da célula (`_col_index("BC12") → 54`) |
| **Shared strings**: o texto real fica em `xl/sharedStrings.xml`, e um `<si>` pode ter vários `<r><t>` | resolvidos e concatenados |
| **Datas são serial do Excel** (`34485`, `46798.80956`), nunca string. Época é **1899-12-30**, não 1900-01-01 — o bug do ano bissexto de 1900 está embutido no formato | `_excel_serial_to_iso` existe e está testado, mas **hoje não tem chamador**: nenhuma coluna de data está na allow-list. Está lá para quem for adicionar a primeira — sem ele a coluna guarda `"34485"` e ninguém percebe por meses |
| **`-` é o null** do registro (e `--`, e "Não informado") | `_clean` |
| **Multivalor por pipe**, geralmente com pipe inicial: `"\| Português \| Inglês"` | `_split_pipe` |
| **Cabeçalho muda de trimestre para trimestre** (com/sem acento, caixa, espaço duplo) | casamento por forma acent-folded (`_fold`) |
| **Prestador morto**: Situação Cadastral cancelada / Atividade baixada | descartado — importar publicaria um negócio que legalmente não existe |
| **Recursos anteriores a ~2023 são CSV latin-1**, não XLSX | erro claro (`not an XLSX`), nunca meio-parseado. Só queremos o mais recente |

---

## Consultando

`languages` e `extra` são **JSONB** (não `json` como as tabelas de pipeline — essas são
lidas inteiras pela aplicação, esta é feita para ser consultada). Então dá para filtrar:

```sql
-- hospedagens com unidade habitacional acessível declarada  → 13.256 no Brasil
select trade_name, uf, municipio,
       extra->>'uhs_acessiveis' as uhs, extra->>'leitos_acessiveis' as leitos
from local_businesses
where cadastur_dataset = 'cadastur-04'
  and (extra->>'uhs_acessiveis')::int > 0;

-- guias que falam inglês num município
select trade_name, extra->'categorias'
from local_businesses
where cadastur_dataset = 'cadastur-01'
  and municipio_ibge = '2927408'
  and languages ? 'Inglês';
```

> **13.256 meios de hospedagem declaram UH acessível.** É acessibilidade estruturada,
> primeira-parte e corrente — justamente o eixo onde a POC das colunas editoriais
> (PR #24) não achou fonte boa. Não serve para `attractions.accessibility` (é outra
> entidade), mas serve para `local_businesses.accessibility`.

---

## O que a tabela **não** tem

- **Coordenada.** O Cadastur só traz endereço textual. `latitude`/`longitude` e
  `destination_id` (que existem na tabela da norteia-api) ficaram de fora de
  propósito — precisam de um passe de geocodificação que não está no escopo.
- **`municipio_ibge` para todo mundo.** Resolvido por nome exato acent-folded contra
  a tabela `municipios`, sem fuzzy. Um código IBGE errado liga o negócio ao
  território errado, o que é pior que `NULL`.

---

## Interação com `reset-brave-db`

`local_businesses` está em `REFERENCE_TABLES`, então **o reset preserva a tabela**.
Rebaixar ~500 mil linhas depois de cada wipe seria absurdo.

O `reset_db.py` só tem `--keep` (adicionar à lista), não o inverso — então, para
zerar de fato, vá direto no banco. Nada faz FK para ela:

```sql
TRUNCATE local_businesses;
```

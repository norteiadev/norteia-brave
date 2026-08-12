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

Saída por conjunto:

```
cadastur-04: read=41203 kept=38771 ibge=37944 written=38771
```

- `read` — linhas na planilha
- `kept` — sobreviveram aos filtros (Situação Cadastral = Regular **e** Situação da
  Atividade = Operação, com certificado e nome)
- `ibge` — quantas casaram com um município da tabela `municipios`
- `written` — linhas no upsert

**Idempotente.** A chave é `cadastur` (Número do Certificado) e o upsert é
`ON CONFLICT DO UPDATE` — não `DO NOTHING`, porque rodar de novo com um trimestre mais
novo tem que atualizar telefone/endereço, não pular a linha.

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
chega a virar linha.

Isso **não** é preciosismo: uma deny-list vaza no dia em que o MTur adicionar uma
coluna nova. Uma allow-list não vaza. Se alguém trocar por "lê tudo e derruba
algumas", o teste `test_a_new_pii_column_added_by_mtur_cannot_leak` quebra — é para
isso que ele existe.

O **nome** do guia é importado. É pessoa física, mas o nome profissional é justamente
o ponto de um registro público (o turista confere o guia por ele). CPF, nascimento,
tipo sanguíneo e documento, não.

---

## Armadilhas do arquivo (todas tratadas, todas testadas)

| armadilha | tratamento |
|---|---|
| **Células vazias somem** do XML. Contar `<c>` desloca toda coluna depois do primeiro branco — e numa planilha de 40 colunas isso é garantido | posição vem da referência da célula (`_col_index("BC12") → 54`) |
| **Shared strings**: o texto real fica em `xl/sharedStrings.xml`, e um `<si>` pode ter vários `<r><t>` | resolvidos e concatenados |
| **Datas são serial do Excel** (`34485`, `46798.80956`), nunca string. Época é **1899-12-30**, não 1900-01-01 — o bug do ano bissexto de 1900 está embutido no formato | `_excel_serial_to_iso` existe e está testado, mas **hoje não tem chamador**: nenhuma coluna de data está na allow-list. Está lá para quem for adicionar a primeira — sem ele a coluna guarda `"34485"` e ninguém percebe por meses |
| **`-` é o null** do registro (e `--`, e "Não informado") | `_clean` |
| **Multivalor por pipe**, geralmente com pipe inicial: `"\| Português \| Inglês"` | `_split_pipe` |
| **Cabeçalho muda de trimestre para trimestre** (com/sem acento, caixa, espaço duplo) | casamento por forma acent-folded (`_fold`) |
| **Prestador morto**: Situação Cadastral cancelada / Atividade baixada | descartado — importar publicaria um negócio que legalmente não existe |
| **Recursos anteriores a ~2023 são CSV latin-1**, não XLSX | erro claro (`not an XLSX`), nunca meio-parseado. Só queremos o mais recente |

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

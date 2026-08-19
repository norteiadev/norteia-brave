"""Import the 12 Cadastur (MTur) provider registers into `local_businesses`.

Cadastur is the federal register of tourism-service providers — 12 datasets on
dados.gov.br (slugs ``cadastur-01`` … ``cadastur-12``), one resource per quarter since
~2006, and the freshest data in the whole federal catalogue. It maps almost 1:1 onto
norteia-api's `local_businesses` table, which the Brave pipeline does not feed today.

WHY a script and not a lane: this is a static quarterly register with an official
issuer and a natural key ((dataset, certificate number)). Nothing about it needs
scoring, dedup or a DLQ. Running it through Nascente → Rio → Mar is exactly what the
retired Mtur destino-seed lane used to do — see scripts/seed_reference_data.py.

LGPD — the single most important thing in this file
---------------------------------------------------
"Guias de Turismo" (cadastur-01) ships CPF, date of birth, blood type and
ID-document number in the same sheet as the business columns. Every column read
here goes through an explicit ALLOW-LIST (``_COMMON_FIELDS`` / ``_EXTRA_FIELDS``).
A deny-list would leak the day MTur adds a column; an allow-list cannot. Do not
replace it with "read everything and drop a few".

Format
------
Recent resources are XLSX, parsed with stdlib ``zipfile`` + ``xml.etree`` — no
openpyxl, no new runtime dependency. Resources older than ~2023 are latin-1 CSV and
are rejected with a clear error rather than half-parsed; we only ever want the latest.

Usage
-----
    set -a; source .env; set +a          # BRAVE_DB_URL + BRAVE_DADOS_GOV_API_KEY
    .venv/bin/python -m scripts.cadastur_import --list
    .venv/bin/python -m scripts.cadastur_import --dataset cadastur-04
    .venv/bin/python -m scripts.cadastur_import --all

The dados.gov.br key is only needed to DISCOVER the file URL — the resource links
point at dados.turismo.gov.br and download without authentication. Get one at
https://dados.gov.br → login gov.br (Prata/Ouro) → "Minha Conta".
"""

from __future__ import annotations

import argparse
import os
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from datetime import date, timedelta
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from brave.core.models import LocalBusiness, Municipio

DADOS_GOV_BASE = "https://dados.gov.br/dados/api/publico"

# The 12 datasets → norteia-api's local_businesses.business_type enum. The enum has
# 8 values and covers all 12; `restaurant` and `local_producer` have no Cadastur
# counterpart (Cadastur does not register them).
DATASETS: dict[str, tuple[str, str]] = {
    "cadastur-01": ("Guias de Turismo", "tour_guide"),
    "cadastur-02": ("Acampamentos Turísticos", "accommodation"),
    "cadastur-03": ("Agências de Turismo", "agency"),
    "cadastur-04": ("Meios de Hospedagem", "accommodation"),
    "cadastur-05": ("Parques Temáticos", "experience_operator"),
    "cadastur-06": ("Transportadoras Turísticas", "transportation"),
    "cadastur-07": ("Casas de Espetáculos e Equipamentos de Animação", "cultural_space"),
    "cadastur-08": ("Centros de Convenções", "cultural_space"),
    "cadastur-09": ("Apoio ao Turismo Náutico e de Pesca", "experience_operator"),
    "cadastur-10": ("Entretenimento e Lazer", "experience_operator"),
    "cadastur-11": ("Locadoras de Veículos", "transportation"),
    "cadastur-12": ("Organizadoras de Eventos", "experience_operator"),
}

# ---------------------------------------------------------------------------
# LGPD allow-list. Keys are ACCENT-FOLDED, lower-cased header names (see _fold).
# Header wording drifts between quarters ("Prestadores Serviços Turisticos" vs
# "Prestadores de Serviços Turísticos"), so matching is on the folded form and
# several spellings may map to the same field.
# ---------------------------------------------------------------------------
_COMMON_FIELDS: dict[str, str] = {
    "numero do certificado": "cadastur",
    "certificado": "cadastur",
    "numero de inscricao do cnpj": "cnpj",
    "cnpj": "cnpj",
    "nome da pessoa juridica": "company_name",
    "razao social": "company_name",
    "nome fantasia": "trade_name",
    # Guias de Turismo are natural persons and have no trade name. The professional
    # name is the point of a public register (a tourist verifies a guide by it), so
    # it IS imported — CPF, birth date, blood type and ID document are NOT, and are
    # absent from this list by design.
    "nome completo": "trade_name",
    "nome": "trade_name",
    "natureza juridica": "legal_type",
    "uf": "uf",
    "municipio": "municipio",
    "endereco completo comercial": "address",
    "endereco comercial": "address",
    "telefone comercial": "phone",
    "e-mail comercial": "email",
    "email comercial": "email",
    "website": "website",
    "idiomas": "languages",
    # Excel serial, converted by _excel_serial_to_iso. Present in all 12 datasets and
    # the ONLY per-row freshness signal the register has — stored, never used as a
    # filter, because MTur leaves stale validity dates on otherwise Regular rows and
    # dropping on it would silently delete live providers.
    "validade do certificado": "certificate_valid_until",
    "situacao cadastral": "_situacao_cadastral",
    "situacao da atividade": "_situacao_atividade",
}

# Type-specific columns worth keeping, landed in the `extra` JSON blob. Header names
# verified against the live 2ºTri/2026 sheets, not guessed — several early guesses
# ("Área Total do Empreendimento", "Categoria(s)") did not exist.
_EXTRA_FIELDS: dict[str, str] = {
    # cadastur-04 Meios de Hospedagem
    "tipo de hospedagem": "tipo_hospedagem",
    "unidade habitacionais": "unidades_habitacionais",
    "unidades habitacionais": "unidades_habitacionais",
    "leitos": "leitos",
    "uhs acessiveis": "uhs_acessiveis",
    "leitos acessiveis": "leitos_acessiveis",
    # cadastur-01 Guias de Turismo
    "municipio de atuacao": "municipios_atuacao",
    "categoria(s)": "categorias",
    "segmento(s)": "segmentos",
    "guia motorista": "guia_motorista",
    # cadastur-03 Agências de Turismo
    "categoria de atuacao": "categorias",
    "segmentos turisticos": "segmentos",
    "quantidade de veiculos": "quantidade_veiculos",
    "quantidade de embarcacoes": "quantidade_embarcacoes",
    # cadastur-05 Parques Temáticos · cadastur-08 Centros de Convenções
    "area total do empreendimento": "area_total",
    "area total construida(m2)": "area_total",
    "area locavel(m2)": "area_locavel",
    "ambientacao tematica principal": "ambientacao_tematica",
}

# `-` is the register's null. Empty and whitespace-only count too.
_NULLS = {"", "-", "--", "n/a", "não informado", "nao informado"}

# Two shapes, both measured against the live 43 332-row cadastur-01 sheet:
#
#   1. LABELLED — the word CPF followed by 11 digits in ANY punctuation. Operators
#      type it every way imaginable, and a regex pinned to the canonical form misses
#      almost all of them. Real values that leaked past the first version:
#        "CPF 030647716-55"      9 digits + dash
#        "CPF.:127986146-00"     '.:' separator
#        "-CPF-407.421.806.20"   dots where the dash belongs
#        "CPF 365 401 026 15"    spaces
#      `\bCPF\b` keeps it from firing on words that merely start with those letters
#      ("CPFISCAL@…", "icpf_cabofrio@…" — both real, both NOT CPFs).
#
#   2. UNLABELLED but canonical — 999.999.999-99. Unambiguous: a formatted CNPJ is
#      99.999.999/9999-99, so there is no overlap.
#
# Deliberately NOT matched: a bare unlabelled 11-digit run. A BR mobile with DDD is
# also 11 digits, so scrubbing it would eat phone numbers out of business names.
_CPF_RE = re.compile(
    r"\bCPF\b[\s.:/-]*(?:\d[\s.\-/]*){11}"
    r"|\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
    re.IGNORECASE,
)

_XL_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
# Excel's day 0 is 1899-12-30 (the 1900 leap-year bug is baked into the format).
_EXCEL_EPOCH = date(1899, 12, 30)


# --------------------------------------------------------------------------- text


def _fold(raw: str) -> str:
    """Accent-fold + lower-case + collapse whitespace — the header matching key."""
    stripped = unicodedata.normalize("NFKD", raw or "")
    ascii_only = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_only).strip().lower()


def _strip_cpf(text: str | None) -> str | None:
    """Remove a CPF embedded in a free-text field.

    The allow-list keeps the `CPF` COLUMN out, but Receita Federal company names for
    individual entrepreneurs carry the number inside the name string itself — real row
    from cadastur-01: "THIAGO DINIZ FREIRE CPF 919.267.006-78". Dropping the column
    and then storing the same number in `company_name` would defeat the entire design,
    in precisely the dataset where it matters most.

    Only the UNAMBIGUOUS forms are scrubbed: the punctuated 999.999.999-99 (which no
    phone or CNPJ can look like — a formatted CNPJ is 99.999.999/9999-99), and a bare
    11-digit run when it is explicitly labelled "CPF". A bare unlabelled 11-digit run
    is left alone on purpose: a BR mobile with DDD is also 11 digits, so scrubbing it
    would eat phone numbers out of business names.
    """
    if text is None:
        return None
    scrubbed = _CPF_RE.sub(" ", text)
    return re.sub(r"\s+", " ", scrubbed).strip(" -–,") or None


def _clean(raw: Any) -> str | None:
    """A cell's usable text, or None for the register's several spellings of null."""
    if raw is None:
        return None
    text = re.sub(r"\s+", " ", str(raw)).strip()
    return None if text.lower() in _NULLS else text


def _split_pipe(raw: Any) -> list[str] | None:
    """Multivalue cells are pipe-separated, often with a leading pipe.

    "| Português | Inglês" → ["Português", "Inglês"]
    """
    text = _clean(raw)
    if text is None:
        return None
    parts = [p.strip() for p in text.split("|")]
    kept = [p for p in parts if p and p.lower() not in _NULLS]
    return kept or None


def _excel_serial_to_iso(raw: Any) -> str | None:
    """Excel serial ("34485", "46798.809560613423") → ISO date. Never a string in the sheet.

    Used for `Validade do Certificado`, which is a raw serial in the real files — the
    second value above is a verbatim cell from cadastur-04 2ºTri/2026 (→ 2028-02-15).
    The epoch is the trap: Excel's day 0 is 1899-12-30, not 1900-01-01, because the
    format bakes in the 1900 leap-year bug; get it wrong and every date is two days out.
    """
    text = _clean(raw)
    if text is None:
        return None
    try:
        serial = float(text)
    except ValueError:
        return text  # already a date string in some older sheets — pass it through
    if not 1 <= serial < 100_000:
        return None
    return (_EXCEL_EPOCH + timedelta(days=int(serial))).isoformat()


# --------------------------------------------------------------------------- xlsx


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    """xl/sharedStrings.xml → the string table cells with t="s" index into."""
    try:
        blob = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(blob)
    # A <si> may hold one <t> or several <r><t> runs (mixed formatting) — join them.
    return ["".join(t.text or "" for t in si.iter(f"{_XL_NS}t")) for si in root]


def _col_index(ref: str) -> int:
    """"BC12" → 54. Cell refs are the ONLY reliable column position.

    Excel omits empty cells entirely, so counting <c> elements shifts every column
    after the first blank — which in a 40-column MTur sheet is guaranteed.
    """
    letters = "".join(c for c in ref if c.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def worksheet_paths(data: bytes) -> list[str]:
    """Every worksheet in the workbook, in natural sheet order.

    NOT just sheet1: cadastur-01 ships TWO sheets — "Guia PJ" (2 332 rows) and
    "Guia PF" (41 005 rows), with DIFFERENT headers. Reading only the first silently
    imported 5% of the dataset while reporting success, which is the worst possible
    failure mode for an importer.

    Natural sort so sheet10 lands after sheet9, not after sheet1.
    """
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.startswith("xl/worksheets/") and n.endswith(".xml")
        ]
    return sorted(names, key=lambda n: int(re.sub(r"\D", "", n) or 0))


def parse_xlsx(data: bytes, sheet: str = "xl/worksheets/sheet1.xml") -> Iterator[list[str]]:
    """Yield the sheet's rows as lists of strings, first row = header.

    Streaming (iterparse + clear) because a Cadastur quarter can be tens of MB of XML.
    """
    if not data.startswith(b"PK"):
        raise ValueError(
            "not an XLSX (no PK magic). Resources older than ~2023 are latin-1 CSV; "
            "this importer only reads the current XLSX resources."
        )
    with zipfile.ZipFile(BytesIO(data)) as zf:
        strings = _shared_strings(zf)
        with zf.open(sheet) as fh:
            for _event, row in ET.iterparse(fh, events=("end",)):
                if row.tag != f"{_XL_NS}row":
                    continue
                cells: dict[int, str] = {}
                for c in row.findall(f"{_XL_NS}c"):
                    ref = c.get("r") or ""
                    kind = c.get("t")
                    if kind == "inlineStr":
                        node = c.find(f"{_XL_NS}is")
                        value = (
                            "".join(t.text or "" for t in node.iter(f"{_XL_NS}t"))
                            if node is not None
                            else ""
                        )
                    else:
                        v = c.find(f"{_XL_NS}v")
                        value = v.text or "" if v is not None else ""
                        if kind == "s" and value.isdigit():
                            idx = int(value)
                            value = strings[idx] if idx < len(strings) else ""
                    if value and ref:
                        cells[_col_index(ref)] = value
                row.clear()
                yield [cells.get(i, "") for i in range(max(cells) + 1)] if cells else []


# --------------------------------------------------------------------------- rows


def map_headers(header: list[str]) -> tuple[dict[int, str], dict[int, str]]:
    """(column index → common field, column index → extra field).

    Every column NOT in either allow-list is dropped here and never reaches a row —
    this is the LGPD boundary.
    """
    common: dict[int, str] = {}
    extra: dict[int, str] = {}
    for i, raw in enumerate(header):
        key = _fold(raw)
        if key in _COMMON_FIELDS:
            # First spelling wins: a sheet carrying both "Nome Fantasia" and "Nome"
            # must keep the fantasia, not overwrite it with the person's name.
            common.setdefault(i, _COMMON_FIELDS[key])
        elif key in _EXTRA_FIELDS:
            extra[i] = _EXTRA_FIELDS[key]
    # Invert-safe: two headers can map to the same field (cnpj / numero de inscricao).
    return common, extra


def normalize_row(
    row: list[str],
    common: dict[int, str],
    extra: dict[int, str],
    *,
    dataset: str,
    business_type: str,
    quarter: str | None = None,
) -> dict[str, Any] | None:
    """One sheet row → one local_businesses row, or None when it must be dropped.

    Dropped: no certificate number, no usable name, or a provider that is no longer
    active (`Situação Cadastral` != Regular / `Situação da Atividade` != Operação).
    Importing a cancelled provider would publish a business that legally is not one.
    """
    values: dict[str, Any] = {}
    for i, field in common.items():
        if i < len(row):
            values.setdefault(field, None)
            if values.get(field) is None:
                values[field] = _clean(row[i])

    situacao = _fold(values.pop("_situacao_cadastral", None) or "")
    atividade = _fold(values.pop("_situacao_atividade", None) or "")
    if situacao and "regular" not in situacao:
        return None
    if atividade and "operacao" not in atividade:
        return None

    # Scrub CPFs out of the free-text name/address fields BEFORE anything reads them —
    # the allow-list keeps the CPF column out, but Receita Federal company names carry
    # the number inside the string ("THIAGO DINIZ FREIRE CPF 919.267.006-78").
    for field in ("company_name", "trade_name", "address"):
        if values.get(field):
            values[field] = _strip_cpf(values[field])

    cadastur = values.get("cadastur")
    trade_name = values.get("trade_name") or values.get("company_name")
    if not cadastur or not trade_name:
        return None

    extras: dict[str, Any] = {}
    for i, field in extra.items():
        if i >= len(row):
            continue
        value = (
            _split_pipe(row[i])
            if field in ("municipios_atuacao", "categorias", "segmentos")
            else _clean(row[i])
        )
        if value is not None:
            extras[field] = value

    uf = (values.get("uf") or "").upper()[:2] or None
    return {
        "cadastur": cadastur[:64],
        "cadastur_dataset": dataset,
        "business_type": business_type,
        "trade_name": trade_name[:300],
        "company_name": (values.get("company_name") or None) and values["company_name"][:300],
        "cnpj": (values.get("cnpj") or None) and values["cnpj"][:18],
        "legal_type": (values.get("legal_type") or None) and values["legal_type"][:120],
        "uf": uf,
        "municipio": (values.get("municipio") or None) and values["municipio"][:128],
        "municipio_ibge": None,  # filled by resolve_municipios()
        "address": values.get("address"),
        "phone": (values.get("phone") or None) and values["phone"][:64],
        "email": (values.get("email") or None) and values["email"][:256],
        "website": (values.get("website") or None) and values["website"][:512],
        "languages": _split_pipe(values.get("languages")),
        "certificate_valid_until": _excel_serial_to_iso(values.get("certificate_valid_until")),
        "extra": extras or None,
        # Free-text resource title straight from the catalogue — truncated at the
        # column width so a future MTur title cannot fail the whole import.
        "source_quarter": quarter[:128] if quarter else None,
    }


def resolve_municipios(session: Session, rows: list[dict[str, Any]]) -> int:
    """Fill `municipio_ibge` from the `municipios` reference table by (nome, uf).

    Exact accent-folded name match only — no fuzzy matching. A wrong IBGE code links
    a business to the wrong território, which is worse than leaving it NULL for a
    later geocoding pass. Returns how many rows were resolved.
    """
    lookup: dict[tuple[str, str], str] = {
        (_fold(nome), uf): code
        for code, nome, uf in session.execute(
            select(Municipio.ibge_code, Municipio.nome, Municipio.uf)
        )
    }
    hits = 0
    for row in rows:
        if row["municipio"] and row["uf"]:
            code = lookup.get((_fold(row["municipio"]), row["uf"]))
            if code:
                row["municipio_ibge"] = code
                hits += 1
    return hits


def upsert(session: Session, rows: list[dict[str, Any]], batch: int = 1000) -> int:
    """Idempotent bulk upsert keyed on (cadastur_dataset, cadastur). Returns rows written.

    The key is COMPOSITE because the same CNPJ can hold certificates in more than one
    Cadastur category — 3 of them sit in both cadastur-05 and cadastur-08, and the
    overlap is routine across the 12. On `cadastur` alone, importing the second dataset
    silently overwrote the first category.

    ON CONFLICT DO UPDATE, not DO NOTHING: re-running with a newer quarter must refresh
    a provider's phone/address, not skip it. Caller commits.
    """
    written = 0
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        stmt = pg_insert(LocalBusiness).values(chunk)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["cadastur_dataset", "cadastur"],
                set_={
                    c: stmt.excluded[c]
                    for c in chunk[0]
                    if c not in ("cadastur", "cadastur_dataset", "imported_at")
                },
            )
        )
        written += len(chunk)
    return written


# --------------------------------------------------------------------------- i/o


def latest_resource(slug: str, api_key: str) -> tuple[str, str | None]:
    """(download url, quarter label) for the most recent resource of a dataset.

    The endpoint takes the slug directly, so the paginated search — whose `pagina`
    parameter is required and whose omission returns a misleading
    {"Erro na API": "Erro ao executar a consulta"} — is skipped entirely.
    """
    resp = httpx.get(
        f"{DADOS_GOV_BASE}/conjuntos-dados/{slug}",
        headers={"chave-api-dados-abertos": api_key},
        timeout=60.0,
    )
    if resp.status_code == 401:
        # A 401 here comes back with an EMPTY body, so `curl -s | jq` shows nothing.
        raise RuntimeError("dados.gov.br rejected the key (401). Check BRAVE_DADOS_GOV_API_KEY.")
    resp.raise_for_status()
    data = resp.json()
    resources = data.get("recursos") or data.get("resources") or []
    if not resources:
        raise RuntimeError(f"{slug}: no resources in the catalogue response")

    def _quarter_key(r: dict[str, Any]) -> tuple[int, int]:
        """Sort by "NºTri/AAAA" in the title — the API's own ordering is not reliable."""
        m = re.search(r"(\d)\s*º?\s*Tri[^0-9]*(\d{4})", r.get("titulo") or r.get("title") or "")
        return (int(m.group(2)), int(m.group(1))) if m else (0, 0)

    newest = max(resources, key=_quarter_key)
    url = newest.get("link") or newest.get("url")
    if not url:
        raise RuntimeError(f"{slug}: newest resource has no link")
    return url, newest.get("titulo") or newest.get("title")


def import_dataset(session: Session, slug: str, api_key: str) -> dict[str, int]:
    """Download → parse → normalize → resolve → upsert one Cadastur dataset."""
    _label, business_type = DATASETS[slug]
    url, quarter = latest_resource(slug, api_key)
    # The resource link is public — no auth header here on purpose.
    blob = httpx.get(url, timeout=600.0, follow_redirects=True).content

    rows: list[dict[str, Any]] = []
    read = 0
    sheets_used = 0
    # EVERY worksheet, each with its OWN header: cadastur-01 splits legal entities
    # ("Guia PJ") from individuals ("Guia PF") across two sheets with different columns,
    # and the second holds 95% of the rows.
    for path in worksheet_paths(blob):
        common: dict[int, str] = {}
        extra: dict[int, str] = {}
        seen_header = False
        for raw in parse_xlsx(blob, sheet=path):
            if not raw:
                continue
            if not seen_header:
                common, extra = map_headers(raw)
                seen_header = True
                if "cadastur" not in common.values():
                    # Not a data sheet (a legend or a pivot tab) — skip it rather than
                    # failing the import, but never skip in silence.
                    print(f"  {slug}: skipping {path} — no certificate column")
                    break
                sheets_used += 1
                continue
            read += 1
            row = normalize_row(
                raw, common, extra, dataset=slug, business_type=business_type, quarter=quarter
            )
            if row is not None:
                rows.append(row)

    if not sheets_used:
        raise RuntimeError(f"{slug}: no worksheet carried a certificate column")

    # Last row wins on a duplicate key — the bulk upsert cannot carry the same conflict
    # target twice in one statement ("ON CONFLICT DO UPDATE command cannot affect row a
    # second time"). Keyed on the full composite key, matching the upsert.
    deduped = list({(r["cadastur_dataset"], r["cadastur"]): r for r in rows}.values())
    # Self-audit BEFORE writing. The offline suite can only test the CPF shapes we have
    # already seen; MTur ships new ones every quarter (the first version of the regex
    # missed four distinct spellings that only a full-table scan surfaced). Counting
    # survivors here means the next new shape shows up as a number on the import line
    # instead of sitting in the DB unnoticed.
    leaks = sum(
        1
        for r in deduped
        for f in ("trade_name", "company_name", "address")
        if r.get(f) and _CPF_RE.search(r[f])
    )
    if leaks:
        print(f"  ⚠ {slug}: {leaks} row(s) still match the CPF pattern after scrubbing")

    resolved = resolve_municipios(session, deduped)
    written = upsert(session, deduped)
    return {
        "read": read,
        "kept": len(deduped),
        "ibge_resolved": resolved,
        "written": written,
        "sheets": sheets_used,
        "cpf_leaks": leaks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", action="append", choices=sorted(DATASETS))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list:
        for slug, (label, kind) in sorted(DATASETS.items()):
            print(f"{slug}  {kind:20s}  {label}")
        return 0

    slugs = sorted(DATASETS) if args.all else (args.dataset or [])
    if not slugs:
        parser.error("pass --dataset <slug> (repeatable), --all, or --list")

    api_key = os.environ.get("BRAVE_DADOS_GOV_API_KEY")
    if not api_key:
        print("ERROR: BRAVE_DADOS_GOV_API_KEY not set (get one at dados.gov.br → Minha Conta)")
        return 1
    db_url = os.environ.get("BRAVE_DB_URL")
    if not db_url:
        print("ERROR: BRAVE_DB_URL not set. Run: set -a; source .env; set +a")
        return 1

    session_factory = sessionmaker(bind=create_engine(db_url))
    with session_factory() as session:
        for slug in slugs:
            stats = import_dataset(session, slug, api_key)
            session.commit()
            print(
                f"{slug}: sheets={stats['sheets']} read={stats['read']} "
                f"kept={stats['kept']} ibge={stats['ibge_resolved']} "
                f"written={stats['written']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

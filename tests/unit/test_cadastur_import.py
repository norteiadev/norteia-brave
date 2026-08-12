"""scripts/cadastur_import.py — parser, LGPD allow-list and row normalization.

Offline: the XLSX fixture is synthesized in-test with stdlib zipfile, so no binary
lands in the repo and no network or DB is touched. Only the pure functions are
covered here; `latest_resource`/`import_dataset` are thin I/O over them.

The sheet shape mirrors the real MTur export, including the two traps that make a
naive parser silently wrong: cell references with gaps (Excel omits empty cells) and
shared strings.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from scripts.cadastur_import import (
    DATASETS,
    _excel_serial_to_iso,
    _fold,
    _split_pipe,
    _strip_cpf,
    map_headers,
    normalize_row,
    parse_xlsx,
    worksheet_paths,
)

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def _xlsx(rows: list[list[str]]) -> bytes:
    """Build a minimal single-sheet XLSX with every cell as an inline string.

    Inline strings (not the shared table) for the rows keep the fixture readable;
    _shared_strings is exercised separately by test_shared_strings_are_resolved.
    """
    sheet = [f"<worksheet {_NS}><sheetData>"]
    for r, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{v}</t></is></c>'
            for c, v in enumerate(row)
            if v != ""  # Excel omits empty cells — the gap is the point
        )
        sheet.append(f'<row r="{r}">{cells}</row>')
    sheet.append("</sheetData></worksheet>")

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/worksheets/sheet1.xml", "".join(sheet))
    return buf.getvalue()


_HEADER = [
    "Número do Certificado",
    "Nome Fantasia",
    "Nome da Pessoa Jurídica",
    "Número de Inscrição do CNPJ",
    "UF",
    "Município",
    "Idiomas",
    "Situação Cadastral",
    "Situação da Atividade",
    "CPF",
    "Data de Nascimento",
    "Tipo Sanguíneo",
    "UHs Acessíveis",
]


def _rows(*data: list[str]) -> tuple[dict[int, str], dict[int, str], list[list[str]]]:
    parsed = list(parse_xlsx(_xlsx([_HEADER, *data])))
    common, extra = map_headers(parsed[0])
    return common, extra, parsed[1:]


def _one(row: list[str], dataset: str = "cadastur-04"):
    common, extra, parsed = _rows(row)
    return normalize_row(
        parsed[0],
        common,
        extra,
        dataset=dataset,
        business_type=DATASETS[dataset][1],
        quarter="2ºTri/2026",
    )


# --------------------------------------------------------------------------- LGPD


def test_pii_columns_never_reach_a_row():
    """CPF / birth date / blood type are present in the sheet and must be unreachable.

    This is the whole reason the importer uses an allow-list. If someone swaps it for
    a deny-list, this test is what should stop the PR.
    """
    common, extra, _ = _rows()
    mapped = set(common.values()) | set(extra.values())

    assert "cpf" not in mapped
    assert not any("nascimento" in f or "sanguineo" in f for f in mapped)
    # And the row itself carries no trace of them.
    row = _one(["C1", "Pousada Sol", "Sol LTDA", "12.345.678/0001-90", "BA", "Ilhéus",
                "| Português | Inglês", "Regular", "Operação",
                "111.222.333-44", "34485", "O+", "3"])
    assert "111.222.333-44" not in str(row)
    assert "O+" not in str(row)


def test_a_cpf_embedded_in_the_company_name_is_scrubbed():
    """Dropping the CPF column is not enough — Receita Federal names carry it inline.

    Verbatim row from the live cadastur-01 sheet (1ºTri/2026): the `CPF` column is
    dropped by the allow-list and the SAME number rides in `Nome da Pessoa Jurídica`.
    Without the scrub, the whole design fails in exactly the dataset where it matters.
    """
    header = ["Número do Certificado", "Nome da Pessoa Jurídica", "CPF"]
    parsed = list(parse_xlsx(_xlsx([header, ["05199620000115",
                                             "THIAGO DINIZ FREIRE CPF 919.267.006-78",
                                             "91926700678"]])))
    common, extra = map_headers(parsed[0])
    row = normalize_row(parsed[1], common, extra, dataset="cadastur-01",
                        business_type="tour_guide")

    assert row["trade_name"] == "THIAGO DINIZ FREIRE"
    assert "919" not in str(row["trade_name"])
    assert "91926700678" not in str(row)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # --- verbatim values that LEAKED past the first version of the regex, found by
        # scanning all 152 955 imported rows. Operators type a CPF every way there is,
        # so the labelled form must accept any punctuation between the digits.
        ("ADRIANA DOS REIS GONCALVES CPF 030647716-55", "ADRIANA DOS REIS GONCALVES"),
        ("CLARICE MOREIRA DE QUEIROZ CPF.:127986146-00", "CLARICE MOREIRA DE QUEIROZ"),
        ("JOAO PEREIRA COSTA-CPF-407.421.806.20", "JOAO PEREIRA COSTA"),
        ("NELCI JULIETA PORTO CPF 365 401 026 15", "NELCI JULIETA PORTO"),
        ("THIAGO DINIZ FREIRE CPF 919.267.006-78", "THIAGO DINIZ FREIRE"),
        # --- canonical form, unlabelled
        ("MARIA 123.456.789-00 TURISMO", "MARIA TURISMO"),
        ("MARIA CPF: 12345678900", "MARIA"),
        ("MARIA cpf 12345678900", "MARIA"),
        # --- must SURVIVE: a formatted CNPJ is not a CPF (99.999.999/9999-99).
        ("07.987.881/0001-25 ROBERTO", "07.987.881/0001-25 ROBERTO"),
        # A bare 11-digit run is left alone: a BR mobile with DDD is also 11 digits,
        # so scrubbing it unlabelled would eat phone numbers out of business names.
        ("POUSADA 11987654321", "POUSADA 11987654321"),
        # `\bCPF\b` must not fire on words that merely begin with those letters —
        # both of these are real values from the register.
        ("CPFISCAL SERVICOS", "CPFISCAL SERVICOS"),
        ("ICPF CABO FRIO", "ICPF CABO FRIO"),
    ],
)
def test_cpf_scrub_is_precise_about_what_it_removes(raw, expected):
    assert _strip_cpf(raw) == expected


def test_a_new_pii_column_added_by_mtur_cannot_leak():
    """The allow-list is what makes this true — an unknown header is simply dropped."""
    header = [*_HEADER, "Número do Documento de Identificação"]
    parsed = list(parse_xlsx(_xlsx([header])))
    common, extra = map_headers(parsed[0])

    assert len(header) - 1 not in common
    assert len(header) - 1 not in extra


# --------------------------------------------------------------------------- parse


def test_gaps_in_the_row_do_not_shift_columns():
    """Excel omits empty cells; positions must come from the cell ref, not the order.

    Counting <c> elements would slide `UF` into the CNPJ slot here.
    """
    row = _one(["C2", "Hotel Vazio", "", "", "PE", "Recife", "", "Regular", "Operação"])

    assert row["uf"] == "PE"
    assert row["municipio"] == "Recife"
    assert row["cnpj"] is None


def test_shared_strings_are_resolved():
    """Real MTur sheets use the shared-string table (t="s"), not inline strings."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "xl/sharedStrings.xml",
            f'<sst {_NS}><si><t>Número do Certificado</t></si>'
            f"<si><r><t>Nome </t></r><r><t>Fantasia</t></r></si></sst>",
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet {_NS}><sheetData><row r="1">'
            f'<c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
            f"</row></sheetData></worksheet>",
        )
    header = next(iter(parse_xlsx(buf.getvalue())))

    # The second cell is split across two runs — they must be joined, not truncated.
    assert header == ["Número do Certificado", "Nome Fantasia"]


def test_every_worksheet_is_found_not_only_the_first():
    """cadastur-01 splits "Guia PJ" (2 332 rows) from "Guia PF" (41 005) across two sheets.

    Reading only sheet1 imported 5% of the dataset while printing a success line — the
    worst failure mode an importer has. Natural sort so sheet10 follows sheet9.
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in (1, 2, 10):
            zf.writestr(f"xl/worksheets/sheet{n}.xml", f"<worksheet {_NS}/>")
        zf.writestr("xl/sharedStrings.xml", f"<sst {_NS}/>")  # must not be mistaken for one

    assert worksheet_paths(buf.getvalue()) == [
        "xl/worksheets/sheet1.xml",
        "xl/worksheets/sheet2.xml",
        "xl/worksheets/sheet10.xml",
    ]


def test_a_csv_resource_is_rejected_with_a_clear_error():
    """Pre-2023 resources are latin-1 CSV. Half-parsing one would be worse than failing."""
    with pytest.raises(ValueError, match="not an XLSX"):
        list(parse_xlsx(b"Certificado;Nome\n1;Pousada\n"))


# ----------------------------------------------------------------------- normalize


def test_a_regular_operating_provider_is_kept_and_mapped():
    row = _one(["C3", "Pousada Sol", "Sol Hotelaria LTDA", "12.345.678/0001-90",
                "ba", "Ilhéus", "| Português | Inglês", "Regular", "Operação",
                "", "", "", "4"])

    assert row["cadastur"] == "C3"
    assert row["trade_name"] == "Pousada Sol"
    assert row["company_name"] == "Sol Hotelaria LTDA"
    assert row["cnpj"] == "12.345.678/0001-90"
    assert row["uf"] == "BA"  # upper-cased
    assert row["languages"] == ["Português", "Inglês"]  # leading pipe dropped
    assert row["extra"] == {"uhs_acessiveis": "4"}
    assert row["business_type"] == "accommodation"
    assert row["source_quarter"] == "2ºTri/2026"
    assert row["municipio_ibge"] is None  # resolve_municipios fills it, needs a session


@pytest.mark.parametrize(
    "situacao,atividade",
    [("Cancelada", "Operação"), ("Regular", "Baixada"), ("Inapta", "Paralisada")],
)
def test_a_provider_that_is_no_longer_active_is_dropped(situacao, atividade):
    """Importing a cancelled provider would publish a business that legally is not one."""
    assert _one(["C4", "Fantasma", "", "", "BA", "Ilhéus", "", situacao, atividade]) is None


def test_a_row_without_a_certificate_is_dropped():
    """`cadastur` is the primary key — a row without it has nowhere to go."""
    assert _one(["", "Sem Certificado", "", "", "BA", "Ilhéus", "", "Regular", "Operação"]) is None


def test_a_guia_falls_back_to_the_person_name_as_trade_name():
    """Guias de Turismo are natural persons: no Nome Fantasia, only Nome Completo.

    The professional name is the point of a public register, so it IS imported —
    unlike the CPF sitting in the next column.
    """
    header = ["Número do Certificado", "Nome Completo", "UF", "Município",
              "Situação Cadastral", "Situação da Atividade", "CPF"]
    parsed = list(parse_xlsx(_xlsx([header, ["G1", "Maria Silva", "AM", "Manaus",
                                             "Regular", "Operação", "111.222.333-44"]])))
    common, extra = map_headers(parsed[0])
    row = normalize_row(parsed[1], common, extra, dataset="cadastur-01",
                        business_type=DATASETS["cadastur-01"][1])

    assert row["trade_name"] == "Maria Silva"
    assert row["business_type"] == "tour_guide"
    assert "111.222.333-44" not in str(row)


def test_nome_fantasia_wins_over_a_bare_nome_column():
    """A sheet carrying both must not overwrite the fantasia with the person's name."""
    header = ["Número do Certificado", "Nome Fantasia", "Nome"]
    parsed = list(parse_xlsx(_xlsx([header, ["C5", "Pousada Sol", "Maria Silva"]])))
    common, extra = map_headers(parsed[0])
    row = normalize_row(parsed[1], common, extra, dataset="cadastur-04",
                        business_type="accommodation")

    assert row["trade_name"] == "Pousada Sol"


# ------------------------------------------------------------------------- helpers


@pytest.mark.parametrize("raw", ["-", "--", "", "   ", "Não informado"])
def test_the_registers_several_spellings_of_null(raw):
    assert _split_pipe(raw) is None


def test_pipe_multivalue_keeps_order_and_drops_the_leading_empty():
    assert _split_pipe("|Aiuruoca|Alagoa|Baependi") == ["Aiuruoca", "Alagoa", "Baependi"]


def test_excel_serial_dates_are_real_dates_not_the_number():
    # Excel's day 0 is 1899-12-30 (the 1900 leap-year bug is baked into the format);
    # using 1900-01-01 would put every date two days out.
    assert _excel_serial_to_iso("34485") == "1994-05-31"
    # Fractional part is the time of day — truncated, not rounded up to the next day.
    assert _excel_serial_to_iso("46798.809560613423") == "2028-02-15"
    assert _excel_serial_to_iso("-") is None


def test_header_matching_survives_accent_and_spacing_drift():
    """Header wording changes between quarters — matching is on the folded form."""
    assert _fold("Prestadores de Serviços  Turísticos") == "prestadores de servicos turisticos"
    assert _fold("SITUAÇÃO CADASTRAL") == "situacao cadastral"


def test_the_same_cnpj_in_two_datasets_is_two_distinct_rows():
    """The key is (cadastur_dataset, cadastur), not the certificate alone.

    Measured on the live 2ºTri/2026 files: 3 CNPJs sit in BOTH cadastur-05 and
    cadastur-08 (a parque temático that is also a centro de convenções). On the
    certificate alone, importing the second dataset silently overwrote the first
    category and the company lost it.
    """
    rows = [
        _one(["02422440000162", "Acqua Park", "", "", "GO", "Caldas Novas", "",
              "Regular", "Operação"], dataset="cadastur-05"),
        _one(["02422440000162", "Acqua Park", "", "", "GO", "Caldas Novas", "",
              "Regular", "Operação"], dataset="cadastur-08"),
    ]
    keys = {(r["cadastur_dataset"], r["cadastur"]) for r in rows}

    assert len({r["cadastur"] for r in rows}) == 1
    assert len(keys) == 2


def test_every_dataset_maps_to_a_valid_norteia_api_business_type():
    """The 12 slugs must land inside the API's 8-value enum, or the future push 422s."""
    allowed = {
        "accommodation", "restaurant", "tour_guide", "agency",
        "transportation", "local_producer", "cultural_space", "experience_operator",
    }
    assert len(DATASETS) == 12
    assert {kind for _label, kind in DATASETS.values()} <= allowed

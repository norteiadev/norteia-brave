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
    map_headers,
    normalize_row,
    parse_xlsx,
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


def test_every_dataset_maps_to_a_valid_norteia_api_business_type():
    """The 12 slugs must land inside the API's 8-value enum, or the future push 422s."""
    allowed = {
        "accommodation", "restaurant", "tour_guide", "agency",
        "transportation", "local_producer", "cultural_space", "experience_operator",
    }
    assert len(DATASETS) == 12
    assert {kind for _label, kind in DATASETS.values()} <= allowed

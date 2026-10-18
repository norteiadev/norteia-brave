"""normalize_name / normalize_address — PT-BR-aware casing, idempotent (F19/F52)."""

import pytest

from brave.core.rio.normalize import normalize_address, normalize_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MASP - Museu de Arte de São Paulo", "MASP - Museu de Arte de São Paulo"),
        ("Palácio Dom Pedro II", "Palácio Dom Pedro II"),
        ("McDonald's", "McDonald's"),
        ("SESC  Pompeia ", "SESC Pompeia"),
        ("Embu Das Artes", "Embu das Artes"),
        ("praia do forte", "Praia do Forte"),
        ("Dom Pedro Ii", "Dom Pedro II"),  # repairs the old str.title() damage
        ("Rua Xv De Novembro", "Rua XV de Novembro"),
        ("pedra d'água", "Pedra d'Água"),
        ("Pedra D'Água", "Pedra D'Água"),  # deliberate source casing is kept
        ("Machadinho D'Oeste", "Machadinho D'Oeste"),  # IBGE's official spelling
        ("IGREJA MATRIZ DE SANTANA", "Igreja Matriz de Santana"),
        ("PEDRA D'ÁGUA", "Pedra d'Água"),
        ("DOM PEDRO II", "Dom Pedro II"),
        ("MASP", "MASP"),  # short all-caps name: an acronym
        ("São João del-Rei", "São João del-Rei"),
        ("Igreja e Convento", "Igreja e Convento"),
        ("Do Ó", "Do Ó"),  # a leading connector keeps its capital
        ("Shopping Mix", "Shopping Mix"),  # not a numeral
        ("2º batalhão (centro)", "2º Batalhão (Centro)"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected
    assert normalize_name(expected) == expected  # idempotent (consensus cache key)


def test_normalize_address_keeps_source_casing():
    assert normalize_address("  R. da Praia,   s/n ") == "R. da Praia, s/n"
    assert normalize_address("   ") is None
    assert normalize_address(None) is None


def test_backfill_rewrites_only_untouched_title_cased_names():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "backfill_rio_names.py"
    spec = importlib.util.spec_from_file_location("backfill_rio_names", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    raw = "MASP - Museu de Arte de São Paulo"
    assert mod.renamed("Masp - Museu De Arte De São Paulo", raw) == raw
    assert mod.renamed("MASP (editado)", raw) is None  # a steward edit is kept
    assert mod.renamed("Praia", "Praia") is None  # nothing to fix
    # An all-caps source never turns a readable title-cased name back into shouting.
    assert mod.renamed("Igreja Matriz De Santana", "IGREJA MATRIZ DE SANTANA") == (
        "Igreja Matriz de Santana"
    )

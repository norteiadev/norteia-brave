"""Literal-copy guard: a ``value`` must be a rewrite. Pilot ES: 9 claims repeated >= 12
consecutive words of the post (a Pancas tip was 80% verbatim). Pure functions, no I/O."""

from __future__ import annotations

import pytest

from brave.domains.editorial.schema import (
    COPY_RUN_LIMIT,
    is_address,
    is_copied,
    longest_shared_run,
)

BODY = (
    "A subida da Pedra do Frade leva cerca de duas horas e exige calçado fechado. "
    "Quem vai de manhã cedo encontra a trilha vazia, com sombra até a metade do caminho."
)


def test_longest_shared_run_counts_consecutive_words_ignoring_case_accents_and_punctuation() -> (
    None
):
    value = "Dica: quem vai de MANHA cedo, encontra a trilha vazia e tranquila"
    # "quem vai de manha cedo encontra a trilha vazia" = 9 words in a row in the body
    assert longest_shared_run(value, BODY) == 9
    assert longest_shared_run("Leve água e protetor solar", BODY) == 1  # only "e"


def test_a_run_is_consecutive_not_a_bag_of_words() -> None:
    value = "trilha vazia quem vai sombra de manhã cedo"
    assert longest_shared_run(value, BODY) <= 3


def test_the_limit_is_ten_words() -> None:
    assert COPY_RUN_LIMIT == 10
    ten = "leva cerca de duas horas e exige calçado fechado quem vai"  # 11 in a row
    nine = "leva cerca de duas horas e exige calçado fechado"  # 9 in a row
    assert is_copied(ten, BODY)
    assert not is_copied(nine, BODY)


def test_a_rewrite_is_not_a_copy() -> None:
    assert not is_copied("A trilha demora uns 120 minutos; use tênis ou bota.", BODY)


def test_a_list_value_is_checked_too() -> None:
    body = "Entre dezembro e março chove muito na região serrana e as trilhas ficam escorregadias"
    assert is_copied(["Entre dezembro e março chove muito na região serrana e as trilhas"], body)
    assert not is_copied(["Dezembro a março: chuva forte", "Julho: seco"], body)


def test_a_boolean_is_never_a_copy() -> None:
    assert not is_copied(True, BODY)


@pytest.mark.parametrize(
    "value",
    [
        "Rua Principal, 120, Centro",
        "Av. Beira-Mar, s/n, Praia do Morro",
        "Praça Dom Pedro II, 45",
        "Rodovia ES-060, km 12, s/n",
    ],
)
def test_an_address_is_exempt_even_when_it_is_verbatim(value: str) -> None:
    body = f"Fica na {value} e abre cedo."
    assert is_address(value)
    assert longest_shared_run(value, body) >= 3
    assert not is_copied(value, body, limit=3)


def test_a_street_in_a_long_direction_text_is_not_an_address() -> None:
    value = (
        "Siga pela Rua Principal por dois quilômetros, vire à direita na terceira rua, "
        "passe a igreja e continue até o estacionamento no fim da estrada de terra, número 4"
    )
    assert not is_address(value)
    assert is_copied(value, value)

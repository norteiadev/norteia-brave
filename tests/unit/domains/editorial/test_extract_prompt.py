"""The extraction prompt keeps the author's voice out of the facts.

Pilot ES: ``recommended_duration`` came back as "Os autores ficaram 6 dias na região da Grande
Vitória." — the trip report of the post's author, not advice to a visitor. The model cannot be
called offline, so the rules are pinned where the model reads them (system prompt)."""

from __future__ import annotations

import pytest

from brave.domains.editorial.extract_batch import EXTRACT_SYSTEM, build_request


def test_the_prompt_forbids_the_author_voice_in_every_value() -> None:
    low = EXTRACT_SYSTEM.lower()
    assert "primeira pessoa" in low
    for marker in ("os autores", "ficamos", "fomos"):
        assert marker in low
    assert "recomendação ao visitante" in low or "recomendação ao leitor" in low


def test_a_trip_report_duration_is_not_emitted() -> None:
    low = " ".join(EXTRACT_SYSTEM.lower().split())
    assert "recommended_duration" in low
    assert "só relata" in low and "omita" in low


@pytest.mark.parametrize(
    "column",
    [
        "estimated_spending_range",
        "pace_profile",
        "predominant_audience_profile",
        "how_to_get_there",
    ],
)
def test_the_columns_that_read_like_a_diary_are_called_out(column: str) -> None:
    section = EXTRACT_SYSTEM.split("## Voz do autor", 1)[1].split("\n## ", 1)[0]
    assert column in section


def test_the_rules_reach_the_request() -> None:
    from types import SimpleNamespace

    doc = SimpleNamespace(url="https://x/a/", published_time=None, body_text="texto")
    assert build_request(doc, "claude-sonnet-5-5")["params"]["system"] == EXTRACT_SYSTEM  # type: ignore[arg-type]

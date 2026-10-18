"""Post-write verifier (rubric CF v1.2) — scoring, deterministic correction, failure posture,
and where its verdict lands (write_cascade → PlacesEnrichmentAgent → Mar exclusion).

100% offline: a scripted fake LLM returns the writer's prose and then the verifier's JSON.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from brave.config.settings import ScoreConfig
from brave.core.mar.service import _NON_CANONICAL_KEYS
from brave.domains.places.copywriter import TourismCopywriter
from brave.domains.places.verifier import (
    APROVA,
    CORRIGE,
    REPROVA,
    VERIFIER_SYSTEM,
    AtrativoVerifier,
    VerifierError,
    _frases,
    cf,
    decidir,
    remover_frases,
)
from brave.shared.exceptions import CostGuardError, ProviderBalanceError
from tests.unit.domains.places.test_copywriter_cascade import (
    _COM_MENCAO,
    _FakeSearch,
    _rio,
)


class _ScriptedLLM:
    """generate() pops the next scripted reply; an Exception item is raised instead."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def generate(self, messages, model="x", *, system=None, tools=None) -> str:
        self.calls.append({"messages": messages, "model": model, "system": system})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    async def extract(self, prompt, schema, mode="tools"):  # pragma: no cover — unused
        raise AssertionError("the verifier must not use extract()")


def _j(*claims: tuple[str, str], estrutural: bool = False) -> str:
    return json.dumps(
        {
            "claims": [{"trecho": t, "rotulo": r, "evidencia": "—"} for t, r in claims],
            "estrutural": estrutural,
        },
        ensure_ascii=False,
    )


def _n(S=0, L=0, U=0, C=0, OP=0) -> dict[str, int]:  # noqa: N803 — the rubric's own labels
    return {"S": S, "L": L, "U": U, "C": C, "OP": OP}


# ---------------------------------------------------------------------------
# Score + decision (in code, deterministic)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "estrutural", "esperado"),
    [
        (_n(S=3, L=2), False, APROVA),  # CF = 4.5/5 = 0.90 exactly → APROVA
        (_n(S=1, L=1), False, CORRIGE),  # 0.875 < 0.90, nothing flagged
        (_n(S=13, U=7), False, CORRIGE),  # CF = 0.65 exactly is not "< 0.65"
        (_n(S=12, U=8), False, REPROVA),  # 0.60
        (_n(S=20, C=1), False, CORRIGE),
        (_n(S=20, C=2), False, REPROVA),  # C ≥ 2 whatever the CF (0.86 here)
        (_n(S=9, U=1), False, CORRIGE),  # CF 0.90 but U is never tolerated on approval
        (_n(), False, APROVA),  # N = 0 → CF 1 (sensory-only prose)
        (_n(OP=1), False, CORRIGE),  # OP alone: CF 1, still removed
        (_n(S=10), True, REPROVA),  # describes another place
    ],
)
def test_decision_boundaries(n: dict[str, int], estrutural: bool, esperado: str) -> None:
    assert decidir(n, cf(n), estrutural) == esperado


def test_cf_formula_and_clamp() -> None:
    assert cf(_n(S=6, L=2, U=1, C=1)) == pytest.approx((6 + 1.5 - 0.5) / 10)
    assert cf(_n(OP=5)) == 1.0, "OP is outside N"
    assert cf(_n(C=1)) == 0.0, "clamped at 0"


# ---------------------------------------------------------------------------
# Deterministic correction
# ---------------------------------------------------------------------------


def test_sentence_split_survives_abbreviations_and_decimals() -> None:
    t = "Fundada pelo Sr. Silva, fica a 2,5 km. do centro. O salto tem 1.200 m! E a Dra. Ana? Sim."
    assert [t[a:b] for _, a, b in _frases(t)] == [
        "Fundada pelo Sr. Silva, fica a 2,5 km. do centro.",
        "O salto tem 1.200 m!",
        "E a Dra. Ana?",
        "Sim.",
    ]


def test_removal_keeps_paragraphs_and_matches_folded() -> None:
    t = "Primeira frase longa sobre o lugar. Segunda frase “com aspas”.\n\nTerceira frase aqui."
    # The model re-typed the accents/quotes and a trailing period: still found.
    assert remover_frases(t, ['Segunda frase "com aspas"']) == (
        "Primeira frase longa sobre o lugar.\n\nTerceira frase aqui."
    )
    assert remover_frases(t, ["Terceira frase aqui."]) == (
        "Primeira frase longa sobre o lugar. Segunda frase “com aspas”."
    )


def test_trecho_across_two_sentences_removes_both() -> None:
    t = "Um. Dois tres. Quatro cinco. Seis."
    assert remover_frases(t, ["tres. Quatro"]) == "Um. Seis."


def test_unit_before_a_capital_ends_the_sentence() -> None:
    t = "A trilha tem 2 km. O local é lindo. A queda tem 80 m. Há um poço. Fica a 3 km. do centro."
    assert [t[a:b] for _, a, b in _frases(t)] == [
        "A trilha tem 2 km.",
        "O local é lindo.",
        "A queda tem 80 m.",
        "Há um poço.",
        "Fica a 3 km. do centro.",
    ]
    assert remover_frases(t, ["O local é lindo"]) == (
        "A trilha tem 2 km. A queda tem 80 m. Há um poço. Fica a 3 km. do centro."
    )


def test_lowercase_after_a_period_never_splits_and_hours_unit_does() -> None:
    t = "Abre cedo, a trilha leva 8 h. A vista vale. Fica perto da pça. central."
    assert [t[a:b] for _, a, b in _frases(t)] == [
        "Abre cedo, a trilha leva 8 h.",
        "A vista vale.",
        "Fica perto da pça. central.",
    ]


def test_decomposed_accents_still_match() -> None:
    import unicodedata

    t = "Primeira frase do texto. A cachoeira é famosa. Última frase do texto."
    nfd = unicodedata.normalize("NFD", t)
    assert remover_frases(nfd, ["A cachoeira é famosa"]) == unicodedata.normalize(
        "NFD", "Primeira frase do texto. Última frase do texto."
    )


def test_ambiguous_trecho_removes_every_sentence_it_appears_in() -> None:
    t = "O parque tem trilhas sinalizadas. Há um lago. Há trilhas secretas até a gruta."
    assert remover_frases(t, ["trilhas"]) == "Há um lago."


def test_trecho_not_in_text_is_none() -> None:
    assert remover_frases("Um texto qualquer.", ["frase que o modelo inventou"]) is None


# ---------------------------------------------------------------------------
# AtrativoVerifier — the loop around the model
# ---------------------------------------------------------------------------

_TEXTO = (
    "A Praia da Costa tem calçadão de 3 km em Vila Velha. O Morro do Moreno fica ao fundo. "
    "Para muitos, é a praia mais bonita do estado. Vá cedo, quando a luz é suave."
)
_FRASE_U = "Para muitos, é a praia mais bonita do estado."


async def test_aprova_returns_the_original() -> None:
    llm = _ScriptedLLM(_j(("calçadão de 3 km", "S"), ("Morro do Moreno", "S")))
    v = await AtrativoVerifier(llm, "m").verificar(_TEXTO, "ctx")
    assert v.texto == _TEXTO and v.resumo["decisao"] == APROVA
    assert llm.calls[0]["system"] == VERIFIER_SYSTEM and llm.calls[0]["model"] == "m"
    assert "ctx" in llm.calls[0]["messages"][0]["content"]


async def test_corrige_removes_and_reverifies_once() -> None:
    llm = _ScriptedLLM(
        _j(("calçadão de 3 km", "S"), ("Morro do Moreno", "S"), ("Para muitos", "U")),
        _j(("calçadão de 3 km", "S"), ("Morro do Moreno", "S")),
    )
    v = await AtrativoVerifier(llm, "m").verificar(_TEXTO, "ctx")
    assert v.texto == _TEXTO.replace(_FRASE_U + " ", "")
    assert v.resumo["decisao"] == CORRIGE and v.resumo["cf_corrigido"] == 1.0
    assert v.resumo["trechos"] == [{"rotulo": "U", "trecho": "Para muitos"}]
    assert "evidencia" not in json.dumps(v.resumo), "evidencia may quote a review (LGPD)"
    assert len(llm.calls) == 2
    assert _FRASE_U not in llm.calls[1]["messages"][0]["content"]


async def test_corrige_that_does_not_reverify_to_aprova_is_reprova() -> None:
    llm = _ScriptedLLM(
        _j(("calçadão de 3 km", "S"), ("Morro do Moreno", "S"), ("Para muitos", "U")),
        _j(("calçadão de 3 km", "S"), ("Morro do Moreno", "U")),
    )
    v = await AtrativoVerifier(llm, "m").verificar(_TEXTO, "ctx")
    assert v.texto is None and v.resumo["decisao"] == REPROVA
    assert len(llm.calls) == 2, "one re-verification, never a third call"


async def test_corrige_left_with_no_concrete_claim_is_reprova() -> None:
    """Puffery is outside N: a correction that strips every S/L would re-verify to APROVA on
    CF 1 and publish only generic poetry."""
    texto = (
        "Um lugar que convida a desacelerar e transmite paz a quem chega com calma. "
        "O museu tem acervo de 300 peças e é considerado por muitos o melhor do estado. "
        "Cada canto parece feito para ser contemplado sem pressa alguma, em silêncio."
    )
    llm = _ScriptedLLM(
        _j(("acervo de 300 peças", "S"), ("O museu tem", "S"), ("considerado por muitos", "U")),
        _j(),
    )
    v = await AtrativoVerifier(llm, "m").verificar(texto, "ctx")
    assert v.texto is None and v.resumo["decisao"] == REPROVA and len(llm.calls) == 2


async def test_corrige_with_trecho_not_found_is_reprova_without_reverifying() -> None:
    llm = _ScriptedLLM(_j(("calçadão de 3 km", "S"), ("um trecho que não existe", "U")))
    v = await AtrativoVerifier(llm, "m").verificar(_TEXTO, "ctx")
    assert v.texto is None and v.resumo["decisao"] == REPROVA and len(llm.calls) == 1


async def test_corrige_that_guts_the_text_is_reprova() -> None:
    # Removing three of the four sentences leaves < 60% of the original.
    llm = _ScriptedLLM(
        _j(
            ("calçadão de 3 km", "S"),
            ("calçadão de 3 km", "OP"),
            ("Morro do Moreno", "OP"),
            ("Para muitos", "U"),
            ("Vá cedo", "S"),
            ("Vila Velha", "S"),
            ("praia", "S"),
        )
    )
    v = await AtrativoVerifier(llm, "m").verificar(_TEXTO, "ctx")
    assert v.texto is None and v.resumo["decisao"] == REPROVA and len(llm.calls) == 1


async def test_corrige_with_nothing_removable_is_reprova() -> None:
    llm = _ScriptedLLM(_j(("calçadão de 3 km", "S"), ("Morro do Moreno", "L")))  # CF 0.875
    v = await AtrativoVerifier(llm, "m").verificar(_TEXTO, "ctx")
    assert v.texto is None and v.resumo["decisao"] == REPROVA and len(llm.calls) == 1


async def test_unparseable_reply_is_retried_once_then_fails() -> None:
    fenced = "```json\n" + _j(("calçadão de 3 km", "S")) + "\n```"
    llm = _ScriptedLLM("não é json", fenced)
    v = await AtrativoVerifier(llm, "m").verificar(_TEXTO, "ctx")
    assert v.texto == _TEXTO and len(llm.calls) == 2

    with pytest.raises(VerifierError):
        await AtrativoVerifier(_ScriptedLLM("lixo", '{"claims": [{"rotulo": "X"}]}'), "m").verificar(
            _TEXTO, "ctx"
        )


async def test_real_case_multiple_contradictions_is_reprova() -> None:
    """Cânion do Rio Preto (result2.json #16, A/B round 5): the writer attached the facts of
    neighbouring points of the trail (Salto 120 m, Garimpão 80 m, setas laranjas) to the
    canyon. Both judges: 5-6 C → REPROVA."""
    texto = (
        "Aventure-se pelos Cânions I e II do Rio Preto, um dos trechos mais fotografados do "
        "Parque Nacional da Chapada dos Veadeiros. A trilha conduz você por campos sujos e "
        "limpos, adentrando o cerrado rupestre. Um dos pontos altos é o Mirante do Salto do Rio "
        "Preto, onde se revela uma queda d’água de 120 metros. A 800 metros dali, a Cachoeira "
        "do Garimpão, com seus 80 metros, oferece um poço para banho. Fique atento às setas "
        "laranjas que indicam o caminho."
    )
    llm = _ScriptedLLM(
        _j(
            ("Cânions I e II do Rio Preto", "S"),
            ("um dos trechos mais fotografados", "S"),
            ("Parque Nacional da Chapada dos Veadeiros", "S"),
            ("campos sujos e limpos, adentrando o cerrado rupestre", "C"),
            ("Mirante do Salto do Rio Preto, onde se revela uma queda d’água de 120 metros", "C"),
            ("a Cachoeira do Garimpão, com seus 80 metros", "C"),
            ("setas laranjas que indicam o caminho", "C"),
        )
    )
    v = await AtrativoVerifier(llm, "m").verificar(texto, "ctx")
    assert v.texto is None
    assert v.resumo["decisao"] == REPROVA and v.resumo["C"] == 4
    assert v.resumo["cf"] == pytest.approx((3 - 2) / 7, abs=1e-3)
    assert len(llm.calls) == 1, "REPROVA never attempts a correction"


# ---------------------------------------------------------------------------
# write_cascade: the verifier runs AFTER the groundedness gate
# ---------------------------------------------------------------------------

_PROSA = (
    "O calçadão da Praia da Costa acompanha a areia por 3 km em Vila Velha, com o Morro do "
    "Moreno ao fundo. Para muitos, é a praia mais bonita do estado. Vá cedo, quando a luz "
    "ainda é suave."
)
_SEM_U = _PROSA.replace(" Para muitos, é a praia mais bonita do estado.", "")
_J_U = _j(("acompanha a areia por 3 km", "S"), ("Morro do Moreno", "S"), ("Para muitos", "U"))
_J_OK = _j(("acompanha a areia por 3 km", "S"), ("Morro do Moreno", "S"))
_J_REPROVA = _j(("acompanha a areia por 3 km", "C"), ("Morro do Moreno", "C"))


def _writer(*replies: Any, verifier: bool = True) -> tuple[TourismCopywriter, _ScriptedLLM]:
    llm = _ScriptedLLM(*replies)
    return (
        TourismCopywriter(
            llm,
            model="w",
            search_client=_FakeSearch(_COM_MENCAO),
            verifier=AtrativoVerifier(llm, "v") if verifier else None,
        ),
        llm,
    )


async def _cascade(cw: TourismCopywriter):
    return await cw.write_cascade("Praia Da Costa", "Vila Velha", "ES")


async def test_cascade_aprova_publishes() -> None:
    cw, llm = _writer(_PROSA, _J_OK)
    r = await _cascade(cw)
    assert r.prose == _PROSA and r.motivo is None and r.verificacao["decisao"] == APROVA
    assert [c["model"] for c in llm.calls] == ["w", "v"]


async def test_cascade_corrige_publishes_the_corrected_text() -> None:
    cw, _ = _writer(_PROSA, _J_U, _J_OK)
    r = await _cascade(cw)
    assert r.prose == _SEM_U and r.verificacao["decisao"] == CORRIGE


async def test_cascade_reprova_keeps_the_draft_for_the_steward() -> None:
    cw, _ = _writer(_PROSA, _J_REPROVA)
    r = await _cascade(cw)
    assert r.prose is None and r.motivo == "nao_verificada" and r.rascunho == _PROSA
    assert r.verificacao["decisao"] == REPROVA and r.busca is not None


async def test_cascade_verifier_failure_never_publishes_and_is_not_a_steward_case() -> None:
    cw, _ = _writer(_PROSA, RuntimeError("boom"))
    r = await _cascade(cw)
    assert (r.prose, r.motivo, r.rascunho) == (None, "verificacao_falhou", None)
    assert r.busca is not None


@pytest.mark.parametrize("exc", [CostGuardError("budget"), ProviderBalanceError("gemini")])
async def test_cascade_verifier_budget_errors_propagate(exc: Exception) -> None:
    cw, _ = _writer(_PROSA, exc)
    with pytest.raises(type(exc)):
        await _cascade(cw)


async def test_ungrounded_text_never_reaches_the_verifier() -> None:
    memoria = "Inaugurado em 1952, o Forte de Copacabana fica a 300 metros da Praia da Costa."
    cw, llm = _writer(memoria)
    r = await _cascade(cw)
    assert r.motivo == "nao_fundamentada" and len(llm.calls) == 1


async def test_no_verifier_is_the_old_behavior() -> None:
    cw, llm = _writer(_PROSA, verifier=False)
    r = await _cascade(cw)
    assert r.prose == _PROSA and r.verificacao is None and len(llm.calls) == 1


# ---------------------------------------------------------------------------
# PlacesEnrichmentAgent: persistence, DLQ, Mar exclusion, wiring
# ---------------------------------------------------------------------------


async def _run_agent(rio: MagicMock, *replies: Any, search: bool = True) -> _ScriptedLLM:
    from brave.domains.places.places_enrichment import PlacesEnrichmentAgent
    from tests.fakes.fake_places import FakePlacesClient

    llm = _ScriptedLLM(*replies)
    agent = PlacesEnrichmentAgent(
        places_client=FakePlacesClient(),
        session=MagicMock(),
        llm_client=llm,
        search_client=_FakeSearch(_COM_MENCAO) if search else None,
        config=ScoreConfig(),
        verifier_llm_client=llm,
        verifier_model="v",
    )
    with patch("brave.domains.places.places_enrichment.write_audit"), \
         patch("brave.domains.places.places_enrichment.record_event"), \
         patch("brave.domains.places.places_enrichment.route_by_score"):
        await agent.run(rio)
    return llm


async def test_agent_reprova_goes_to_dlq_with_the_draft_and_the_summary() -> None:
    rio = _rio()
    await _run_agent(rio, _PROSA, _J_REPROVA)
    assert "descricao_editorial" not in rio.normalized
    assert rio.normalized["descricao_gate"] == "nao_verificada"
    assert rio.normalized["descricao_rascunho"] == _PROSA
    assert rio.normalized["descricao_verificacao"]["decisao"] == REPROVA
    assert rio.normalized["descricao_attempts"] == 1
    assert (rio.routing, rio.dlq_reason) == ("dlq", "descricao_nao_fundamentada")


async def test_agent_corrige_writes_the_corrected_text() -> None:
    rio = _rio()
    await _run_agent(rio, _PROSA, _J_U, _J_OK)
    assert rio.normalized["descricao_editorial"] == _SEM_U
    assert rio.normalized["descricao_gate"] is None
    assert rio.normalized["descricao_verificacao"]["decisao"] == CORRIGE
    assert rio.routing == "mar"


async def test_agent_verifier_failure_retries_later_outside_the_dlq() -> None:
    rio = _rio()
    await _run_agent(rio, _PROSA, RuntimeError("boom"))
    assert "descricao_editorial" not in rio.normalized
    assert "descricao_gate" not in rio.normalized  # a failure is not a verdict
    assert rio.normalized["descricao_attempts"] == 1  # bounded retry, like any unwritten pass
    assert rio.routing == "mar" and rio.dlq_reason is None


async def test_agent_transient_failure_on_retry_keeps_the_earlier_verdict() -> None:
    # Pass 1 REPROVA → DLQ with the draft. Pass 2 hits a verifier failure: it judged nothing,
    # so it must neither release the sticky description verdict nor wipe the steward's draft.
    rio = _rio()
    await _run_agent(rio, _PROSA, _J_REPROVA)
    with patch("brave.domains.places.places_enrichment.route_by_score") as route:
        from brave.domains.places.places_enrichment import PlacesEnrichmentAgent
        from tests.fakes.fake_places import FakePlacesClient

        llm = _ScriptedLLM(_PROSA, RuntimeError("boom"))
        agent = PlacesEnrichmentAgent(
            places_client=FakePlacesClient(), session=MagicMock(), llm_client=llm,
            search_client=_FakeSearch(_COM_MENCAO), config=ScoreConfig(),
            verifier_llm_client=llm, verifier_model="v",
        )
        with patch("brave.domains.places.places_enrichment.write_audit"), \
             patch("brave.domains.places.places_enrichment.record_event"):
            await agent.run(rio)
    assert "descricao_nao_fundamentada" not in route.call_args.kwargs["redecided"]
    assert rio.normalized["descricao_gate"] == "nao_verificada"
    assert rio.normalized["descricao_rascunho"] == _PROSA
    assert rio.normalized["descricao_verificacao"]["decisao"] == REPROVA
    assert rio.normalized["descricao_attempts"] == 2


async def test_agent_web_search_mode_never_builds_the_verifier() -> None:
    rio = _rio()
    llm = await _run_agent(rio, _PROSA, search=False)
    assert len(llm.calls) == 1 and rio.normalized["descricao_editorial"] == _PROSA


def test_verification_summary_is_not_canonical() -> None:
    assert "descricao_verificacao" in _NON_CANONICAL_KEYS


@pytest.mark.parametrize(("flag", "cascade", "built"), [(True, True, True), (False, True, False), (True, False, False)])
def test_enrich_agent_builds_the_verifier_only_with_flag_and_cascade(
    monkeypatch: pytest.MonkeyPatch, flag: bool, cascade: bool, built: bool
) -> None:
    from brave.clients.factory import Clients
    from brave.tasks import pipeline
    from tests.fakes.fake_llm import FakeLLMClient
    from tests.fakes.fake_places import FakePlacesClient

    cfg = MagicMock(
        run_real_externals=True,
        places_enrichment_enabled=True,
        description_enrichment_enabled=True,
        atrativo_description_batch_enabled=False,
        atrativo_description_cascade_enabled=cascade,
        atrativo_description_verifier_enabled=flag,
        atrativo_verifier_model="gemini-x",
    )
    agent_cls = MagicMock()
    monkeypatch.setattr("brave.domains.places.places_enrichment.PlacesEnrichmentAgent", agent_cls)
    given: dict[str, Any] = {"places": FakePlacesClient(), "llm": FakeLLMClient()}
    if cascade:
        given["search"] = _FakeSearch([])
    ctx = pipeline._EnrichCtx(cfg, [], None)
    pipeline._enrich_agent(MagicMock(), ctx, Clients(cfg, **given), describe=True)

    kwargs = agent_cls.call_args.kwargs
    assert (kwargs["verifier_llm_client"] is not None) is built
    assert kwargs["verifier_model"] == "gemini-x"

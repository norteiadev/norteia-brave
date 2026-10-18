"""AtrativoVerifier — post-write fact check of a cascade description (rubric CF v1.2).

The groundedness gate (grounding.py) only sees concrete tokens (years, measures, compound
proper names). What it cannot see is the class the A/B found past it: a reputation made up
from one review ("considerado por muitos"), a fabricated plural ("artistas locais"), a fact
borrowed from the neighbouring attraction, an operational datum the prose must not carry.
This module asks a model to LABEL each claim against the same context the writer got, and
then scores and decides IN CODE — the model never decides, so the threshold is auditable.

Rubric: docs/copywriter-verificacao.md (CF v1.2, agreed Claude ↔ Antigravity; 13/20
independent agreement, 20/20 after conciliation, mean |ΔCF| 0.035).

  CF = clamp((S + 0.75·L − 0.5·C) / N, 0, 1), N = S+L+U+C, N = 0 → 1.
  APROVA  : U = C = OP = 0 and CF ≥ 0.90.
  REPROVA : CF < 0.65, or C ≥ 2, or estrutural (describes another place / a homonym).
  CORRIGE : otherwise — remove every sentence carrying a U/C/OP claim (deterministic, no LLM
            rewrite), and re-verify ONCE; only APROVA publishes the corrected text.

LGPD: the model's ``evidencia`` may quote a review — it is never logged nor returned in the
summary. The flagged ``trecho`` is the writer's own prose, kept for the steward.

D-18 boundary: no imports from other domains or brave.tasks.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import structlog
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from brave.clients.base import LLMClientProtocol

logger = structlog.get_logger(__name__)

# Rubric CF v1.2 ("Decisão") — docs/copywriter-verificacao.md.
APROVA_MIN_CF: float = 0.90
REPROVA_MAX_CF: float = 0.65  # CF strictly below this → REPROVA
REPROVA_MIN_C: int = 2  # two contradictions → the writer read the wrong place
PESO_L: float = 0.75  # loose (divergent sources, old announcement) is worth less than S
PESO_C: float = 0.5  # a contradiction also costs, beyond not counting
# CORRIGE may not gut the text: less than this fraction of the original chars → REPROVA.
MIN_RESTANTE: float = 0.60

APROVA, CORRIGE, REPROVA = "APROVA", "CORRIGE", "REPROVA"
_REMOVIVEIS = ("U", "C", "OP")

VERIFIER_SYSTEM = """Você é um verificador de fatos de descrições turísticas da Norteia. Recebe o CONTEXTO (as únicas fontes que o redator tinha) e o TEXTO escrito a partir dele. Sua tarefa é listar as afirmações do TEXTO e rotular cada uma contra o CONTEXTO. Você NÃO decide aprovação, só rotula.

EXTRAÇÃO DE AFIRMAÇÕES:
- Uma afirmação é um fato verificável sobre o atrativo: característica física, história, data, número, nome, comodidade, atividade, localização, opinião de visitante, reputação, recorde.
- Enumerações da mesma categoria formam UMA afirmação ("trilhas, cachoeiras e mirantes"); se um único item foi inventado, a afirmação inteira é U.
- Fatos embutidos em convites e dicas contam: "observe a fachada barroca azul" afirma barroco e azul.
- Dica que embute suporte físico ("à beira do lago", "detalhes da arquitetura", "vistas da cachoeira X"): o suporte conta, S se está no contexto, U se não.
- FORA da lista (não rotule): puffery interpretativo abstrato ("convida a desacelerar", "transmite paz", "redefiniu a experiência"), poesia de bioma, superlativo claramente retórico, e fenômeno temporal/climático de dica (pôr do sol, luz da manhã, levar água), salvo se contradiz o funcionamento do contexto (sugerir pôr do sol num local que fecha antes → U).

RÓTULOS:
- S (sustentada): está no contexto. Adjetivo descritivo presente na fonte = S. Recorde/título com lastro = S. Fontes divergentes com valor consensual/aproximado = S. Opinião atribuída/modalizada sem plural ("há quem destaque X"; "visitantes costumam notar X" só com 2 ou mais avaliações) = S. Paráfrase de citação da fonte = S. "Coração da cidade" = S se o endereço é central. Comodidade gratuita do local com fonte ("Wi-Fi gratuito") = S. Rua, bairro, praça ou "estrada que liga A a B" = localização = S.
- L (frouxa): fontes divergem e o texto escolheu um lado num fato duro; anúncio antigo dito no presente como anúncio. Se o lado escolhido é obsoleto ou contradito por outra fonte = U.
- U (sem lastro): não está no contexto, ou amplia o que a fonte diz. Sempre U quando o lastro é 1 única avaliação/fonte: "para muitos", "muitos visitantes", "a maioria", "considerado por muitos", "é reconhecido por", "é famoso por", "é preparado para PCD", "visitantes apreciam/elogiam" (exceção: se uma fonte publicada da web traz a frase de consenso literalmente, é S). Plural fabricado a partir de 1 agente (1 artista → "artistas locais"; 1 avaliação → "visitantes") = U. Recorde sem lastro = U. Alegação histórica/causal sem fonte ("foi pioneiro", "inspirou outros parques") = U. Anúncio afirmado como já existente = U. "Coração da cidade" com endereço não central = U.
- C (contradita): o contexto diz outra coisa, ou o dado é de entidade vizinha/homônima (atribuir ao atrativo um fato de outro lugar citado nas fontes).
- OP (operacional, proibido na prosa): distância de acesso em km, tempo de deslocamento, código de rodovia (BR/GO-xxx), instruções de como chegar, dias/horários de funcionamento, preço, entrada/ingresso gratuito, contato. NÃO são OP: características físicas (altura de queda, extensão de trilha, altitude, área), período do dia ou estação.

ESTRUTURAL: "estrutural" é true se o texto descreve outro lugar ou um homônimo (o atrativo errado), não só um detalhe trocado.

SAÍDA: responda APENAS com JSON válido, sem markdown, sem comentários, neste formato:
{"claims":[{"trecho":"<substring EXATA do TEXTO, copiada caractere por caractere>","rotulo":"S|L|U|C|OP","evidencia":"<até 10 palavras do CONTEXTO, ou —>"}],"estrutural":false}
O "trecho" deve ser curto (a oração que carrega a afirmação) e aparecer literalmente no TEXTO."""


class _Claim(BaseModel):
    trecho: str
    rotulo: Literal["S", "L", "U", "C", "OP"]
    evidencia: str = "—"


class _Julgamento(BaseModel):
    claims: list[_Claim]
    estrutural: bool = False


class VerifierError(ValueError):
    """The verifier's reply is not the JSON the rubric asks for (after one retry)."""


@dataclass(frozen=True)
class Verificacao:
    """Final verdict of one text.

    texto:  what may be published — the original (APROVA), the corrected one (CORRIGE
            re-verified to APROVA), or None (REPROVA).
    resumo: board-only summary for ``descricao_verificacao`` — CF, counts, decision and the
            flagged trechos (writer's prose). Never ``evidencia`` (may quote a review).
    """

    texto: str | None
    resumo: dict[str, Any]


def contagens(claims: list[_Claim]) -> dict[str, int]:
    out = dict.fromkeys(("S", "L", "U", "C", "OP"), 0)
    for c in claims:
        out[c.rotulo] += 1
    return out


def cf(n: dict[str, int]) -> float:
    """CF = clamp((S + 0.75·L − 0.5·C) / N, 0, 1); N = S+L+U+C (OP is out); N = 0 → 1."""
    total = n["S"] + n["L"] + n["U"] + n["C"]
    if not total:
        return 1.0
    return max(0.0, min(1.0, (n["S"] + PESO_L * n["L"] - PESO_C * n["C"]) / total))


def decidir(n: dict[str, int], valor_cf: float, estrutural: bool) -> str:
    """REPROVA wins over everything; APROVA only with nothing to remove."""
    if estrutural or valor_cf < REPROVA_MAX_CF or n["C"] >= REPROVA_MIN_C:
        return REPROVA
    if n["U"] == n["C"] == n["OP"] == 0 and valor_cf >= APROVA_MIN_CF:
        return APROVA
    return CORRIGE


# A "." after one of these does not end a sentence ("Sr. João"). Single letters (initials)
# are handled in _frases. Decimals ("3.5", "1.200") never split: the boundary requires
# whitespace after the punctuation.
_ABREVIACOES = frozenset(
    {
        "sr", "sra", "srta", "dr", "dra", "prof", "profa", "sto", "sta", "av", "pça",
        "aprox", "etc", "nº", "n", "séc", "sec",
        "pe", "fr", "gen", "cel", "cap", "ten", "eng", "arq", "jr", "vol", "tel",
    }
)
# A "." followed by a lowercase letter never ends a sentence (PT-BR sentences open in
# uppercase: "a 3 km. do centro", "Sr. joão" typos). Units, unlike abbreviations and
# initials, do end a sentence before an uppercase word ("tem 2 km. A trilha", "8 h. O").
_UNIDADES = frozenset({"km", "m", "kg", "cm", "mm", "ha", "min", "h"})
_FIM_FRASE = re.compile(r"[.!?…]+[\"”»')]*\s+")
_PARAGRAFO = re.compile(r"(?:[^\n]|\n(?![ \t]*\n))+")
_ASPAS = str.maketrans({"“": '"', "”": '"', "«": '"', "»": '"', "‘": "'", "’": "'"})


def _frases(texto: str) -> list[tuple[int, int, int]]:
    """Sentences as (paragraph, start, end) spans over ``texto``, whitespace trimmed."""
    out: list[tuple[int, int, int]] = []
    for p, par in enumerate(_PARAGRAFO.finditer(texto)):
        inicio = par.start()
        for m in _FIM_FRASE.finditer(texto, par.start(), par.end()):
            palavra = re.search(r"(\w+)\W*$", texto[inicio : m.start() + 1])
            w = palavra.group(1).casefold() if palavra else ""
            if texto[m.start()] == "." and (
                texto[m.end() : m.end() + 1].islower()
                or (
                    w not in _UNIDADES
                    and (w in _ABREVIACOES or (len(w) == 1 and w.isalpha()))
                )
            ):
                continue
            out.append((p, inicio, m.end()))
            inicio = m.end()
        out.append((p, inicio, par.end()))
    return [
        (p, a + (len(texto[a:b]) - len(texto[a:b].lstrip())), a + len(texto[a:b].rstrip()))
        for p, a, b in out
        if texto[a:b].strip()
    ]


def _dobra(s: str) -> tuple[str, list[int]]:
    """Accent/case/quote-folded ``s`` with collapsed whitespace + the index map back to ``s``."""
    out: list[str] = []
    idx: list[int] = []
    for i, c in enumerate(s.translate(_ASPAS)):
        if c.isspace():
            if out and out[-1] == " ":
                continue
            c = " "
        elif unicodedata.combining(c):
            continue  # NFD input: the accent is its own code point — drop it like NFC's
        else:
            c = unicodedata.normalize("NFKD", c)[0].lower()
        out.append(c)
        idx.append(i)
    return "".join(out), idx


def _localizar(texto: str, trecho: str) -> list[tuple[int, int]]:
    """Every span of ``trecho`` in ``texto`` (a short trecho may repeat: we cannot tell which
    one the model meant, so all go). Folded search: an exact match is a folded match too,
    and the fold also absorbs the model re-typing accents, quotes or line breaks."""
    alvo, idx = _dobra(texto)
    agulha = _dobra(trecho.strip().strip(".,;:!?…\"'”“ "))[0]
    spans: list[tuple[int, int]] = []
    pos = alvo.find(agulha) if agulha else -1
    while pos >= 0:
        spans.append((idx[pos], idx[pos + len(agulha) - 1] + 1))
        pos = alvo.find(agulha, pos + 1)
    return spans


def remover_frases(texto: str, trechos: list[str]) -> str | None:
    """Drop every sentence a flagged trecho touches. None when a trecho is not in the text —
    then we cannot prove what we removed, and unverified text is never kept."""
    frases = _frases(texto)
    fora: set[int] = set()
    for trecho in trechos:
        spans = _localizar(texto, trecho)
        if not spans:
            return None
        fora |= {i for i, (_, a, b) in enumerate(frases) for x, y in spans if a < y and x < b}
    paragrafos: dict[int, list[str]] = {}
    for i, (p, a, b) in enumerate(frases):
        if i not in fora:
            paragrafos.setdefault(p, []).append(texto[a:b])
    return "\n\n".join(" ".join(fs) for fs in paragrafos.values())


def _parse(raw: str) -> _Julgamento:
    """Strict: the outermost {...} must validate (tolerates a markdown fence around it)."""
    inicio, fim = raw.find("{"), raw.rfind("}")
    if inicio < 0 or fim < inicio:
        raise VerifierError("no JSON object in the verifier reply")
    try:
        return _Julgamento.model_validate_json(raw[inicio : fim + 1])
    except ValidationError as exc:
        raise VerifierError("verifier reply does not match the schema") from exc


class AtrativoVerifier:
    """Labels the claims of a description with an LLM, then scores and decides in code.

    Uses ``generate()`` + strict JSON parsing: the client's structured ``extract()`` runs on
    the OpenRouter/DeepSeek route only, not on the Gemini route the cascade writes with.
    Spend is recorded by the client under its own lane (pipeline builds it as
    "atrativo_verifier"). Errors propagate — the caller owns the failure posture.
    """

    def __init__(self, llm_client: LLMClientProtocol, model: str) -> None:
        self._llm_client = llm_client
        self._model = model

    async def _julgar(self, texto: str, contexto: str) -> _Julgamento:
        user = f"CONTEXTO:\n{contexto}\n\nTEXTO:\n{texto}"
        for tentativa in range(2):  # one retry on an unparseable reply
            raw = await self._llm_client.generate(
                [{"role": "user", "content": user}],
                model=self._model,
                system=VERIFIER_SYSTEM,
                tools=None,
            )
            try:
                return _parse(raw or "")
            except VerifierError:
                if tentativa:
                    raise
                logger.warning("verifier_unparseable_retry", model=self._model)
        raise AssertionError("unreachable")  # pragma: no cover

    async def verificar(self, texto: str, contexto: str) -> Verificacao:
        julg = await self._julgar(texto, contexto)
        n = contagens(julg.claims)
        valor = cf(n)
        decisao = decidir(n, valor, julg.estrutural)
        sinalizados = [c for c in julg.claims if c.rotulo in _REMOVIVEIS]
        resumo: dict[str, Any] = {
            "decisao": decisao,
            "cf": round(valor, 3),
            **n,
            "estrutural": julg.estrutural,
            "trechos": [{"rotulo": c.rotulo, "trecho": c.trecho} for c in sinalizados],
        }
        logger.info("verifier_verdict", decisao=decisao, cf=resumo["cf"], **n)
        if decisao == APROVA:
            return Verificacao(texto, resumo)
        if decisao == REPROVA:
            return Verificacao(None, resumo)

        # CORRIGE. Nothing removable (CF dragged down by L only): removal cannot help.
        corrigido = remover_frases(texto, [c.trecho for c in sinalizados]) if sinalizados else None
        if not corrigido or len(corrigido) < MIN_RESTANTE * len(texto):
            logger.info("verifier_correction_rejected", restante=len(corrigido or ""))
            return Verificacao(None, {**resumo, "decisao": REPROVA})
        julg2 = await self._julgar(corrigido, contexto)
        n2 = contagens(julg2.claims)
        decisao2 = decidir(n2, cf(n2), julg2.estrutural)
        resumo["cf_corrigido"] = round(cf(n2), 3)
        logger.info("verifier_reverified", decisao=decisao2, cf=resumo["cf_corrigido"])
        # The rubric's CORRIGE needs the rest to still say something concrete: puffery is
        # outside N, so a text left with no S/L claim would re-verify to APROVA on CF 1.
        if decisao2 == APROVA and (n2["S"] or n2["L"]):
            return Verificacao(corrigido, resumo)  # decisao stays CORRIGE: published corrected
        return Verificacao(None, {**resumo, "decisao": REPROVA})


if __name__ == "__main__":  # pragma: no cover — ponytail runnable check
    assert cf(dict(S=0, L=0, U=0, C=0, OP=1)) == 1.0
    assert decidir(dict(S=9, L=0, U=0, C=0, OP=1), 1.0, False) == CORRIGE
    t = "O Sr. Silva fundou a casa a 3 km. do centro. Tem 1.200 m de altitude! Fim."
    assert [t[a:b] for _, a, b in _frases(t)] == [
        "O Sr. Silva fundou a casa a 3 km. do centro.",
        "Tem 1.200 m de altitude!",
        "Fim.",
    ]
    assert remover_frases(t, ["1.200 m de altitude"]) == "O Sr. Silva fundou a casa a 3 km. do centro. Fim."
    print("verifier self-check ok")

"""Name matching between a Brave entity and an outside label (images lane, editorial lane).

Moved out of ``brave.domains.images.matching`` so two lanes can share ONE rule without a
domain importing its sibling (D-18). The images lane re-exports these names.
"""

from __future__ import annotations

import re
import unicodedata

STOP = {"de", "da", "do", "das", "dos", "e", "a", "o", "as", "os", "em", "na", "no"}
# A generic word alone never decides a match ("praia", "igreja"...).
GENERIC = {
    "praia",
    "igreja",
    "museu",
    "parque",
    "cachoeira",
    "mirante",
    "trilha",
    "centro",
    "historico",
    "teatro",
    "praca",
    "mercado",
    "catedral",
    "capela",
    "lagoa",
    "rio",
    "serra",
    "morro",
    "ilha",
    "municipal",
    "nacional",
    "estadual",
    "casa",
    "memorial",
    # "Recife Antigo" is not "Antigo Hotel Central de Recife"; "Nossa Senhora do Rosário"
    # is not "Nossa Senhora do Pilar".
    "antigo",
    "antiga",
    "velho",
    "velha",
    "nossa",
    "senhora",
}
# Different words, same place: "Basílica do Pilar" is Wikidata's "Igreja Matriz do Pilar",
# "Centro Histórico de Ouro Preto" its "Cidade Histórica de Ouro Preto".
SAME = {"basilica": "igreja", "matriz": "igreja", "cidade": "centro", "historica": "historico"}


def norm(s: str) -> list[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return [SAME.get(t, t) for t in re.findall(r"[a-z0-9]+", s) if t not in STOP]


def name_score(a: str, b: str) -> float:
    """0 = no match. Containment counts only with 2+ specific tokens on the smaller side,
    otherwise "Mercado Municipal de Goiânia" matches the city item "Goiânia"."""
    ta, tb = set(norm(a)), set(norm(b))
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    jac = len(ta & tb) / len(ta | tb)
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if small <= big and len(small - GENERIC) >= 2:
        return max(jac, 0.6)
    return jac if jac >= 0.6 and len((ta & tb) - GENERIC) >= 1 else 0.0

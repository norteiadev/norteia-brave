"""A market, fair or craft-center name — the one sign a TA "Lojas" card is a place to visit.

TA files the Mercado de São Brás, the Feira Central and the Paneleiras de Goiabeiras under
"Lojas" next to malls and chocolate stores. Reviews do not tell them apart (Shopping Mueller
1134, Nugali 259), the name does: every "Lojas"-only card that reached Mar in the 21 UFs synced
by 10/2026 but two. Shared by the TA card filter and the Places gate (sibling domains).
"""

import re

_MARKET_NAME = re.compile(
    r"\b(mercados?|feiras?|feirinhas?|artesanato|artes[aã]os?|artes|paneleiras|engenhos?"
    r"|cer[aâ]mica)\b",
    re.IGNORECASE,
)


def is_market_name(name: object) -> bool:
    return bool(_MARKET_NAME.search(str(name or "")))

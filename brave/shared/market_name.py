"""A market, fair or craft-center name — the one sign a TA "Lojas" card is a place to visit.

TA files the Mercado de São Brás, the Feira Central and the Paneleiras de Goiabeiras under
"Lojas" next to malls and chocolate stores. Reviews do not tell them apart (Shopping Mueller
1134, Nugali 259), the name does: every "Lojas"-only card that reached Mar in the 21 UFs synced
by 10/2026 but two. Shared by the TA card filter and the Places gate (sibling domains).
"""

import re
import unicodedata

# Matched on the accent-folded name: "Mercadão", "Casa das Artesãs", "Cerâmicas" and an
# unaccented "Artesao" all count. Not "Mercadinho" (a grocery chain name in the Nordeste).
_MARKET_NAME = re.compile(
    r"\b(mercad(?:os?|ao|oes)|feiras?|feirinhas?|artesanatos?|artes(?:aos?|oes|as?)?"
    r"|paneleiras?|engenhos?|ceramicas?)\b",
    re.IGNORECASE,
)


def is_market_name(name: object) -> bool:
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", str(name or "")) if not unicodedata.combining(c)
    )
    return bool(_MARKET_NAME.search(folded))

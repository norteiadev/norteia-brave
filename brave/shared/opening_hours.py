"""Google weekdayDescriptions → o mapa de horários que a norteia-api guarda.

``attractions.opening_hours`` é um mapa dia→horário neutro de idioma::

    {"mon_fri": "09:00-17:00", "sat": "09:00-13:00", "sun": "closed"}

Chave, valor e token de status são inglês/ASCII; a tradução é do i18n do front.
Google entrega 7 linhas humanas em ``regularOpeningHours.weekdayDescriptions``,
guardadas no Rio/Mar como ``weekday_text``. Este módulo é o único conversor.

Locale de ENTRADA: ``GetPlaceRequest`` (clients/places.py:369) não manda
``language_code``, então em produção as strings vêm em inglês. Fixtures e um futuro
pin de locale dão PT-BR. Os dois são aceitos — pinar o locale depois vira no-op aqui.
"""

from __future__ import annotations

import re
import unicodedata

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ALL_DAYS_KEY = "daily"
CLOSED = "closed"
OPEN_24H = "open_24h"

_DAY_INDEX = {
    "monday": 0, "segunda": 0,
    "tuesday": 1, "terca": 1,
    "wednesday": 2, "quarta": 2,
    "thursday": 3, "quinta": 3,
    "friday": 4, "sexta": 4,
    "saturday": 5, "sabado": 5,
    "sunday": 6, "domingo": 6,
}

_CLOSED_IN = {"closed", "fechado", "fechada"}
_ALL_DAY_IN = {"open 24 hours", "24 hours", "aberto 24 horas", "24 horas"}

# Aceita en dash / em dash / hífen na ENTRADA (Google usa U+2013); emite sempre hífen ASCII.
_RANGE_SPLIT = re.compile(r"\s*[–—-]\s*")
_MERIDIEM = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?$", re.IGNORECASE)
_24H = re.compile(r"^(\d{1,2}):(\d{2})$")


def _fold(text: str) -> str:
    """lowercase + NFD accent strip: 'Terça-feira' → 'terca-feira'."""
    nfd = unicodedata.normalize("NFD", text.lower().strip())
    return nfd.encode("ascii", "ignore").decode()


def _day_index(label: str) -> int | None:
    folded = _fold(label).replace("-feira", "").replace(" feira", "").strip()
    return _DAY_INDEX.get(folded)


def _to_24h(token: str) -> str | None:
    """'5:00 AM' → '05:00'; '12:00 AM' → '00:00'; '12:00 PM' → '12:00'."""
    # Google às vezes emite NARROW NO-BREAK SPACE (U+202F) antes de AM/PM.
    token = token.strip().replace(" ", " ").replace(" ", " ")
    m = _MERIDIEM.match(token)
    if m:
        hour, minute, mer = int(m.group(1)), m.group(2) or "00", m.group(3).lower()
        if not 1 <= hour <= 12 or int(minute) > 59:
            return None
        return f"{hour % 12 + (12 if mer == 'p' else 0):02d}:{minute}"
    m = _24H.match(token)
    if m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def _parse_hours(value: str) -> str | None:
    """'9:00 AM – 12:00 PM, 2:00 PM – 6:00 PM' → '09:00-12:00, 14:00-18:00'."""
    folded = _fold(value)
    if folded in _CLOSED_IN:
        return CLOSED
    if folded in _ALL_DAY_IN:
        return OPEN_24H
    shifts = []
    for chunk in value.split(","):          # turnos partidos vêm na mesma linha
        parts = _RANGE_SPLIT.split(chunk.strip())
        if len(parts) != 2:
            return None
        start, end = _to_24h(parts[0]), _to_24h(parts[1])
        if start is None or end is None:
            return None
        shifts.append(f"{start}-{end}")
    return ", ".join(shifts) if shifts else None


def to_hours_map(weekday_text: list[str] | None) -> dict[str, str] | None:
    """Converte weekdayDescriptions no mapa dia→horário.

    Tudo-ou-nada: dia desconhecido, hora impossível de parsear, ou ≠7 dias distintos
    devolve ``None`` para o caller omitir o campo. Mapa parcial é pior que mapa
    nenhum — faltar quarta-feira o turista lê como "fecha quarta", uma mentira
    entregue com confiança. Nada se perde: as linhas cruas continuam viajando em
    ``place.opening_hours``.
    """
    if not weekday_text:
        return None

    by_index: dict[int, str] = {}
    for line in weekday_text:
        label, sep, value = line.partition(":")   # nome de dia nunca tem ':'
        if not sep:
            return None
        idx = _day_index(label)
        hours = _parse_hours(value)
        if idx is None or hours is None:
            return None
        by_index[idx] = hours

    if len(by_index) != 7:
        return None

    week = [by_index[i] for i in range(7)]
    if len(set(week)) == 1:
        return {ALL_DAYS_KEY: week[0]}

    # Corridas CONTÍGUAS de horário igual viram {primeiro}_{último}; corrida de 1
    # vira chave simples. Corridas iguais não-adjacentes NÃO são fundidas — não há
    # como expressar conjunto descontínuo, e inventar (tue_thu_sun) é ilegível.
    result: dict[str, str] = {}
    start = 0
    for i in range(1, 8):
        if i == 7 or week[i] != week[start]:
            key = DAY_KEYS[start] if i - 1 == start else f"{DAY_KEYS[start]}_{DAY_KEYS[i - 1]}"
            result[key] = week[start]
            start = i
    return result


if __name__ == "__main__":  # pragma: no cover — self-check
    _week = [f"{d}: 9:00 AM – 5:00 PM" for d in
             ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")]
    _week += ["Saturday: 9:00 AM – 1:00 PM", "Sunday: Closed"]
    assert to_hours_map(_week) == {
        "mon_fri": "09:00-17:00", "sat": "09:00-13:00", "sun": "closed"
    }, to_hours_map(_week)
    assert to_hours_map(["Monday: Closed"]) is None
    print("ok")

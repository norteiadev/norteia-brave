"""Pre-flight check of the two TA cURLs a UF sync needs. One OK/WARN/FAIL line each.

- Paginação (ta_paginacao.curl): the listing POST whose batch carries
  request.routeParameters.pagee — the qid the sweep's pages ride on.
- Página Brazil (ta_brazil.curl): a load of the Brazil attractions page
  (g294280) with a fresh datadome cookie — DataDome 403s the attraction
  pages on a stale one while the listing still answers (24/10, 28/10).
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DIR = ROOT / "scripts" / "single_attraction"
MAX_AGE_H = 6


def split(curl: str) -> list[str]:
    """shlex has no ANSI-C quoting: DevTools emits $'...' whenever a value has a quote or
    non-ASCII ("São", "d'Ajuda") — decode it into a plain '...' token (as scripts/ta_bootstrap.py)."""
    curl = re.sub(
        r"\$'((?:[^'\\]|\\.)*)'",
        lambda m: shlex.quote(
            m.group(1).encode("latin-1", "backslashreplace").decode("unicode_escape")
        ),
        curl,
        flags=re.DOTALL,
    )
    return shlex.split(curl.replace("\\\n", " "))


def batch(curl: str) -> list:
    tokens = split(curl)
    for flag in ("--data-raw", "--data", "--data-binary", "-d"):
        if flag in tokens and tokens.index(flag) + 1 < len(tokens):
            body = json.loads(tokens[tokens.index(flag) + 1])
            return body if isinstance(body, list) else [body]
    return []


def check(name: str, label: str, test) -> None:
    path = DIR / name
    if not path.is_file():
        print(f"FAIL  cURL {label} ausente: salve em scripts/single_attraction/{name}")
        return
    age_h = (time.time() - path.stat().st_mtime) / 3600
    try:
        problem, note = test(path.read_text(encoding="utf-8"))
    except Exception as exc:  # a crash would print no FAIL line and preflight.sh would count 0
        problem, note = f"ilegível ({type(exc).__name__})", ""
    if problem:
        print(f"FAIL  cURL {label}: {problem} (references/events.md › TA session)")
        return
    if note:
        print(f"WARN  cURL {label}: {note}")
    if age_h > MAX_AGE_H:
        print(f"WARN  cURL {label} tem {age_h:.0f} h: capture de novo antes de uma UF grande")
    elif not note:
        print(f"OK    cURL {label} ({age_h:.1f} h)")


def injectable(curl: str) -> str:
    """What the inject would refuse, judged by the parser that builds its payload
    (scripts/ta_bootstrap.py): no datadome cookie, or a batch of telemetry/ad qids only."""
    spec = importlib.util.spec_from_file_location(
        "ta_bootstrap", ROOT / "scripts" / "ta_bootstrap.py"
    )
    ta_bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ta_bootstrap)
    with contextlib.redirect_stderr(io.StringIO()):  # its per-qid warnings are not ours
        parsed = ta_bootstrap.parse_curl(curl)
    if "datadome" not in parsed["cookies"]:
        return "sem cookie datadome"
    if not parsed["query_ids"]:
        return "lote só de telemetria/anúncio — o inject o recusaria (422): copie outro POST"
    return ""


def paginacao(curl: str) -> tuple[str, str]:
    if problem := injectable(curl):
        return problem, ""
    items = [
        i
        for i in batch(curl)
        if "pagee"
        in json.dumps(i.get("variables", {}).get("request", {}).get("routeParameters", {}))
    ]
    if not items:
        return "sem item com routeParameters.pagee — não é o POST da paginação", ""
    qid = items[0].get("extensions", {}).get("preRegisteredQueryId", "")
    pinned = listing_qid()
    if pinned and qid != pinned:
        return "", (
            f"qid do navegador {qid} ≠ do Brave {pinned} — só age se o sweep der "
            f'PersistedQueryNotFound: BRAVE_TA_QUERY_ID_OVERRIDE={{"listing":"{qid}"}}'
        )
    return "", ""


def listing_qid() -> str:
    """The qid the sweep sends: the .env override, else _LISTING_QID_GQL in the client."""
    override = json.loads(os.environ.get("BRAVE_TA_QUERY_ID_OVERRIDE") or "{}").get("listing")
    if override:
        return override
    src = (ROOT / "brave" / "domains" / "tripadvisor" / "client.py").read_text(encoding="utf-8")
    m = re.search(r'^_LISTING_QID_GQL: str = "(\w+)"', src, re.MULTILINE)
    return m.group(1) if m else ""


def brazil(curl: str) -> tuple[str, str]:
    if problem := injectable(curl):
        return problem, ""
    if "g294280" not in curl:
        return "não veio da página Brazil (g294280)", ""
    return "", ""


if __name__ == "__main__":
    check("ta_paginacao.curl", "paginação", paginacao)
    check("ta_brazil.curl", "página Brazil", brazil)
    sys.exit(0)

"""Pre-flight's TA cURL checker (.claude/skills/run-uf-sync/scripts/check_curls.py)."""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / ".claude/skills/run-uf-sync/scripts/check_curls.py"
_spec = importlib.util.spec_from_file_location("check_curls", _SCRIPT)
check_curls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_curls)

_LISTING = {
    "variables": {"request": {"routeParameters": {"geoId": 303370, "pagee": "30"}}},
    "extensions": {"preRegisteredQueryId": "0e26761de5110a17"},
}
_TELEMETRY = {
    "variables": {"events": []},
    "extensions": {"preRegisteredQueryId": "636d0b9184b2fc29"},
}


def _curl(batch: list, cookie: str = "datadome=abc; TASID=X", referer: str = "g303370") -> str:
    return (
        "curl --url 'https://www.tripadvisor.com.br/data/graphql/ids' \\\n"
        f"  -b '{cookie}' \\\n"
        f"  -H 'referer: https://www.tripadvisor.com.br/Attractions-{referer}.html' \\\n"
        f"  --data-raw '{json.dumps(batch)}'"
    )


@pytest.fixture(autouse=True)
def _pinned_qid(monkeypatch):
    monkeypatch.setenv("BRAVE_TA_QUERY_ID_OVERRIDE", '{"listing": "0e26761de5110a17"}')


def test_paginacao_passes_the_listing_pagee_post():
    assert check_curls.paginacao(_curl([_TELEMETRY, _LISTING])) == ("", "")


def test_paginacao_needs_datadome():
    problem, _ = check_curls.paginacao(_curl([_LISTING], cookie="TASID=X"))
    assert "datadome" in problem


def test_brazil_refuses_a_telemetry_only_batch():
    """The inject would 422 on empty query_ids: pre-flight must fail it, not OK it."""
    problem, _ = check_curls.brazil(_curl([_TELEMETRY], referer="g294280"))
    assert "telemetria" in problem


def test_brazil_needs_the_brazil_page():
    other = {"variables": {}, "extensions": {"preRegisteredQueryId": "7b2d1f78e3dc76a4"}}
    problem, _ = check_curls.brazil(_curl([other], referer="g303370"))
    assert "g294280" in problem


@pytest.mark.parametrize(
    ("note", "age_h", "expected"),
    [
        ("", 1, ["OK"]),
        ("qid", 1, ["WARN"]),
        ("", 10, ["WARN"]),
        ("qid", 10, ["WARN", "WARN"]),
    ],
)
def test_check_prints_one_line_per_signal(tmp_path, monkeypatch, capsys, note, age_h, expected):
    monkeypatch.setattr(check_curls, "DIR", tmp_path)
    f = tmp_path / "x.curl"
    f.write_text("curl")
    old = time.time() - age_h * 3600
    os.utime(f, (old, old))
    check_curls.check("x.curl", "x", lambda _curl: ("", note))
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == expected

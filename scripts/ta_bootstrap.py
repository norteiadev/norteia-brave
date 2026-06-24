#!/usr/bin/env python3
"""TripAdvisor session-injection bootstrap helper (TA-09).

Parses a DevTools "Copy as cURL (bash)" string and POSTs the extracted
cookies + query_ids to POST /api/v1/tripadvisor/session.

stdlib-only — runs before the venv is activated.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone


def parse_curl(curl_str: str) -> dict:
    """Parse a DevTools Copy-as-cURL string into a session payload dict.

    The cURL string has the shape:
        curl 'https://www.tripadvisor.com/data/graphql/ids' \
          -H 'Cookie: datadome=...; TASession=...' \
          -H 'User-Agent: ...' \
          --data-raw '[{"variables":{...},"extensions":{"preRegisteredQueryId":"<hex>"}}]'

    Returns a dict matching the POST /api/v1/tripadvisor/session body:
        {
            "cookies": {name: value, ...},
            "query_ids": {"destinations": "...", "attractions": "..."},
            "user_agent": "...",
            "acquired_at": "...",
        }
    """
    # --- Extract cookies ---
    cookies: dict[str, str] = {}

    # Look for -H 'Cookie: ...' (double or single quotes)
    cookie_header_match = re.search(
        r"""-H\s+['"]Cookie:\s*([^'"]+)['"]""",
        curl_str,
        re.IGNORECASE,
    )
    # Also look for -b / --cookie flag
    cookie_flag_match = re.search(
        r"""(?:-b|--cookie)\s+['"]([^'"]+)['"]""",
        curl_str,
    )

    raw_cookie_str = ""
    if cookie_header_match:
        raw_cookie_str = cookie_header_match.group(1)
    elif cookie_flag_match:
        raw_cookie_str = cookie_flag_match.group(1)

    if raw_cookie_str:
        for pair in raw_cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                name, _, value = pair.partition("=")
                cookies[name.strip()] = value.strip()

    # --- Extract User-Agent ---
    user_agent = ""
    ua_header_match = re.search(
        r"""-H\s+['"]User-Agent:\s*([^'"]+)['"]""",
        curl_str,
        re.IGNORECASE,
    )
    ua_flag_match = re.search(
        r"""(?:-A|--user-agent)\s+['"]([^'"]+)['"]""",
        curl_str,
    )
    if ua_header_match:
        user_agent = ua_header_match.group(1).strip()
    elif ua_flag_match:
        user_agent = ua_flag_match.group(1).strip()

    # --- Extract query_ids from --data-raw (batch-array JSON) ---
    query_ids: dict[str, str] = {}

    data_raw_match = re.search(
        r"""--data-raw\s+['"](\[.*?\])['"]""",
        curl_str,
        re.DOTALL,
    )
    # Also handle $'...' quoting style (curl sometimes escapes single quotes)
    if not data_raw_match:
        data_raw_match = re.search(
            r"""--data-raw\s+\$'(\[.*?\])'""",
            curl_str,
            re.DOTALL,
        )
    # And plain unquoted JSON array
    if not data_raw_match:
        data_raw_match = re.search(
            r"""--data(?:-raw)?\s+(\[.*?\])""",
            curl_str,
            re.DOTALL,
        )

    if data_raw_match:
        raw_json = data_raw_match.group(1)
        # Unescape single-quoted shell strings: \' -> '
        raw_json = raw_json.replace("\\'", "'")
        try:
            batch = json.loads(raw_json)
            if not isinstance(batch, list):
                batch = [batch]

            destinations_qid = None
            attractions_qid = None

            for item in batch:
                qid = None
                if isinstance(item, dict):
                    extensions = item.get("extensions", {})
                    if isinstance(extensions, dict):
                        qid = extensions.get("preRegisteredQueryId")

                if qid:
                    # Heuristic: check variables for entity type hint
                    variables_str = json.dumps(item.get("variables", {})).upper()
                    if "ATTRACTION" in variables_str and attractions_qid is None:
                        attractions_qid = qid
                    elif destinations_qid is None:
                        destinations_qid = qid
                    elif attractions_qid is None:
                        attractions_qid = qid

            # If only one query ID found, use it as safe fallback for both keys
            if destinations_qid and not attractions_qid:
                attractions_qid = destinations_qid
            if attractions_qid and not destinations_qid:
                destinations_qid = attractions_qid

            if destinations_qid:
                query_ids["destinations"] = destinations_qid
            if attractions_qid:
                query_ids["attractions"] = attractions_qid

        except json.JSONDecodeError as exc:
            print(
                f"Warning: could not parse --data-raw JSON: {exc}",
                file=sys.stderr,
            )

    acquired_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "cookies": cookies,
        "query_ids": query_ids,
        "user_agent": user_agent,
        "acquired_at": acquired_at,
    }


def inject_session(payload: dict, endpoint: str, bearer: str) -> None:
    """POST the session payload to {endpoint}/api/v1/tripadvisor/session.

    Prints canary result on success; raises SystemExit on 4xx/5xx.
    stdlib-only (urllib.request).
    """
    url = endpoint.rstrip("/") + "/api/v1/tripadvisor/session"
    body = json.dumps(payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {bearer}",
    }

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            resp_body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        resp_body = exc.read().decode("utf-8", errors="replace")

    if status in (200, 201, 202):
        try:
            resp_data = json.loads(resp_body)
            canary = resp_data.get("canary", "(no canary field)")
        except json.JSONDecodeError:
            canary = resp_body
        print(f"Session injected — canary result: {canary}")
    elif status == 422:
        print(f"Validation error — check cookies/query_ids")
        print(f"Response: {resp_body}", file=sys.stderr)
    else:
        raise SystemExit(f"HTTP {status}: {resp_body}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "TripAdvisor session-injection bootstrap helper (TA-09). "
            "Parses a DevTools 'Copy as cURL (bash)' string and POSTs "
            "extracted cookies + query_ids to POST /api/v1/tripadvisor/session."
        )
    )
    parser.add_argument(
        "--curl",
        metavar="FILE",
        help=(
            "Path to a file containing a pasted cURL string. "
            "If omitted, reads from stdin (paste the cURL interactively, "
            "then press Ctrl-D / Ctrl-Z to finish)."
        ),
    )
    parser.add_argument(
        "--endpoint",
        metavar="URL",
        default="http://localhost:8000",
        help="Base URL of the FastAPI service (default: http://localhost:8000).",
    )
    parser.add_argument(
        "--bearer",
        metavar="TOKEN",
        default=None,
        help=(
            "Bearer token for auth. "
            "Prefer setting BRAVE_DASHBOARD_BEARER_TOKEN env var to keep "
            "the token off shell history."
        ),
    )
    args = parser.parse_args()

    # Read cURL string
    if args.curl:
        with open(args.curl, "r", encoding="utf-8") as fh:
            curl_str = fh.read()
    else:
        print(
            "Paste the cURL string below (Ctrl-D / Ctrl-Z on a new line to finish):",
            file=sys.stderr,
        )
        curl_str = sys.stdin.read()

    # Parse
    payload = parse_curl(curl_str)

    cookie_count = len(payload.get("cookies", {}))
    query_ids = payload.get("query_ids", {})
    print(f"Parsed: {cookie_count} cookies, query_ids={query_ids}")

    if not payload["cookies"]:
        print(
            "Warning: no cookies found in cURL string. "
            "Ensure the cURL includes a Cookie: header.",
            file=sys.stderr,
        )

    # Bearer token: arg → env → empty string (server will reject with 401/403)
    bearer = args.bearer or os.environ.get("BRAVE_DASHBOARD_BEARER_TOKEN", "")

    inject_session(payload, args.endpoint, bearer)


if __name__ == "__main__":
    main()

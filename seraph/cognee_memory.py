"""Cognee Memory adapter — login + store/search against the platform API.

Credentials (email/password) live in the 0600 env file; values never leave
this process. Auth flow: POST {base}/auth/login → bearer token → /search.
Marked experimental while the endpoint map is confirmed live.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config


class CogneeUnavailable(RuntimeError):
    pass


def _token() -> str:
    creds = config.cognee_credentials()
    email = creds.get("COGNEE_EMAIL")
    password = creds.get("COGNEE_PASSWORD")
    if not email or not password:
        raise CogneeUnavailable("no cognee credentials in env file")
    body = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(
        config.COGNEE_BASE.rstrip("/") + "/auth/login", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        raise CogneeUnavailable(f"login failed: {e}") from e
    tok = data.get("access_token") or data.get("token") or (data.get("data") or {}).get("token")
    if not tok:
        raise CogneeUnavailable("login ok but no token in response")
    return tok


def _call(path: str, token: str | None = None, *, method: str = "GET",
          body: dict | None = None) -> dict:
    req = urllib.request.Request(
        config.COGNEE_BASE.rstrip("/") + path,
        data=json.dumps(body).encode() if body else None, method=method,
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise CogneeUnavailable(f"{path}: HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise CogneeUnavailable(f"{path}: {e}") from e


def store(text: str) -> dict:
    """Persist a fact/snippet; cognify runs server-side. Experimental."""
    return _call("/add", None, method="POST", body={"data": [{"text": text}], "dataset": "seraph"})


def search(query: str, token: str | None = None) -> list:
    tok = token or _token()
    try:
        out = _call("/search", tok, method="POST",
                    body={"searchType": "INSIGHTS", "searchQuery": query})
    except CogneeUnavailable:
        out = _call("/search", token, method="GET", body=None)
    items = out if isinstance(out, list) else out.get("data") or out.get("results") or []
    return items[:5]


def available() -> bool:
    """True if creds exist; network errors are deferred to search/store."""
    creds = config.cognee_credentials()
    return bool(creds.get("COGNEE_EMAIL") and creds.get("COGNEE_PASSWORD"))

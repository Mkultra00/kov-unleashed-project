"""Cognee Memory adapter — Cognee Cloud knowledge-graph lanes.

Auth (docs.cognee.ai): tenant base https://{tenant}.aws.cognee.ai + header
X-Api-Key. Endpoints used: /api/v1/add (ingest), /api/v1/search (searchType
INSIGHTS). Credentials live in the 0600 env file (COGNEE_TENANT_BASE,
COGNEE_API_KEY); values never leave this process. Email/password bearer
login stays as a legacy fallback path.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config

_BLOCKED_UNTIL = 0.0  # negative cache so a dead lane never stalls a turn


class CogneeUnavailable(RuntimeError):
    pass


def _base() -> str:
    creds = config.cognee_credentials()
    base = (creds.get("COGNEE_TENANT_BASE") or config.COGNEE_BASE or "").rstrip("/")
    if not base:
        raise CogneeUnavailable("no cognee tenant base configured")
    if "/api/v1" not in base:
        base += "/api/v1"
    return base


def _headers() -> dict:
    creds = config.cognee_credentials()
    key = creds.get("COGNEE_API_KEY")
    if key:
        return {"X-Api-Key": key}
    tok = _token()
    return {"Authorization": f"Bearer {tok}"}


def _token() -> str:
    """Legacy: email/password bearer login (kept for key-less testing)."""
    creds = config.cognee_credentials()
    email, password = creds.get("COGNEE_EMAIL"), creds.get("COGNEE_PASSWORD")
    if not email or not password:
        raise CogneeUnavailable("no cognee credentials in env file")
    body = json.dumps({"username": email, "password": password}).encode()
    req = urllib.request.Request(
        _base() + "/auth/login", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        raise CogneeUnavailable(f"login failed: {e}") from e
    tok = data.get("access_token") or data.get("token") or (data.get("data") or {}).get("token")
    if not tok:
        raise CogneeUnavailable("login ok but no token in response")
    return tok


def _call(path: str, *, method: str = "GET", body: dict | None = None,
          timeout: float = 8.0) -> dict:
    req = urllib.request.Request(
        _base() + path,
        data=json.dumps(body).encode() if body else None, method=method,
        headers={"Content-Type": "application/json", **_headers()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise CogneeUnavailable(f"{path}: HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise CogneeUnavailable(f"{path}: {e}") from e


def store(text: str) -> dict:
    """Ingest a fact/snippet; cognify runs server-side. Best-effort by callers."""
    try:
        return _call("/add", method="POST",
                     body={"data": [{"text": text}], "dataset": "seraph"})
    except CogneeUnavailable:
        # tolerate body-shape drift
        return _call("/add", method="POST", body={"data": text, "dataset": "seraph"})


def search(query: str) -> list:
    try:
        out = _call("/search", method="POST",
                    body={"query": query, "searchType": "INSIGHTS"})
    except CogneeUnavailable:
        out = _call("/search", method="POST",
                    body={"searchQuery": query})
    items = out if isinstance(out, list) else out.get("data") or out.get("results") or []
    return items[:5]


def recall_block(query: str) -> str:
    """Turn-safe recall: '' on any failure (negative cache 30 s)."""
    global _BLOCKED_UNTIL
    if time.time() < _BLOCKED_UNTIL:
        return ""
    try:
        items = search(query)
    except Exception:
        _BLOCKED_UNTIL = time.time() + 30
        return ""
    out = []
    for it in items:
        t = it.get("text") if isinstance(it, dict) else str(it)
        if t:
            out.append(str(t)[:200])
    return "\n".join(f"- {t}" for t in out[:3])


def available() -> bool:
    """True when any usable credential set is configured (primary: API key)."""
    try:
        creds = config.cognee_credentials()
    except Exception:
        return False
    if creds.get("COGNEE_API_KEY"):
        return True
    return bool(creds.get("COGNEE_EMAIL") and creds.get("COGNEE_PASSWORD"))

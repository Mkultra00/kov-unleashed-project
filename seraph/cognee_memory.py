"""Cognee Memory adapter — Cognee Cloud knowledge-graph lanes.

Auth (dashboard-verified live): tenant base https://tenant-<id>.aws.cognee.ai +
header X-Api-Key. Endpoints: /api/v1/add is MULTIPART (data file + datasetName
field), /api/v1/cognify JSON {"datasets":[name]} builds the graph async,
/api/v1/search JSON {"query", "search_type": "GRAPH_COMPLETION"} returns
list of {dataset_name, search_result:[strings]}. Credentials live in the
0600 env file (COGNEE_TENANT_BASE, COGNEE_API_KEY); values never leave this
process. Email/password bearer login stays as a legacy fallback path.
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


def _multipart(fields: dict[str, str], files: dict[str, tuple[str, str]]) -> tuple[bytes, str]:
    """Encode a multipart/form-data body (add endpoint is form, not JSON)."""
    boundary = "----seraphcognee7d3a"
    out = []
    for name, value in fields.items():
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                   f"name=\"{name}\"\r\n\r\n{value}\r\n")
    for name, (filename, content) in files.items():
        out.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                   f"name=\"{name}\"; filename=\"{filename}\"\r\n"
                   f"Content-Type: text/plain\r\n\r\n{content}\r\n")
    out.append(f"--{boundary}--\r\n")
    return "".join(out).encode(), boundary


def store(text: str) -> dict:
    """Ingest a fact into the seraph dataset, then trigger cognify (async
    graph build). Multipart add verified live; cognify is best-effort."""
    body, boundary = _multipart(
        {"datasetName": "seraph"},
        {"data": (f"turn-{int(time.time() * 1000)}.txt", text[:4000])})
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
               **_headers()}
    req = urllib.request.Request(_base() + "/add", data=body, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            resp = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        # tolerate body-shape drift; never stall a turn on this lane
        raise CogneeUnavailable(f"add: {e}") from e
    try:  # graph build trigger (verified shape); failure here is tolerable
        tok_headers = _headers()
        cj = urllib.request.Request(
            _base() + "/cognify", method="POST",
            data=json.dumps({"datasets": ["seraph"]}).encode(),
            headers={"Content-Type": "application/json", **tok_headers})
        urllib.request.urlopen(cj, timeout=10).read()
    except Exception:  # noqa: BLE001, S110
        pass
    return resp if isinstance(resp, dict) else {}


def search(query: str) -> list:
    out = _call("/search", method="POST",
                body={"query": query, "search_type": "GRAPH_COMPLETION"},
                timeout=12.0)
    items = out if isinstance(out, list) else (out.get("results") or [])
    texts: list = []
    for item in items:
        if isinstance(item, dict) and item.get("search_result"):
            for s in item["search_result"]:
                if isinstance(s, str) and s.strip():
                    texts.append(s)
        elif isinstance(item, dict) and item.get("text"):
            texts.append(str(item["text"]))
        elif isinstance(item, str):
            texts.append(item)
    return texts[:5]


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
    return "\n".join(f"- {str(t)[:200]}" for t in items[:3])


def available() -> bool:
    """True when any usable credential set is configured (primary: API key)."""
    try:
        creds = config.cognee_credentials()
    except Exception:
        return False
    if creds.get("COGNEE_API_KEY"):
        return True
    return bool(creds.get("COGNEE_EMAIL") and creds.get("COGNEE_PASSWORD"))

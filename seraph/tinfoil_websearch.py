"""Tinfoil confidential websearch — MCP over streamable HTTP (ZDR enclave).

Server: POST https://websearch.tinfoil.sh/mcp — Bearer auth, tools
'search' (Exa) + 'fetch'. Zero data retention: queries are processed inside
Tinfoil\'s enclave with no logs. Key lives in the 0600 env file
(TINFOIL_API_KEY). Lane joins emergency web context once the key lands.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from . import config

MCP_URL = config.TINFOIL_MCP_URL
_TIMEOUT = 8.0
_BLOCKED_UNTIL = 0.0


class McpUnavailable(RuntimeError):
    pass


def credentials() -> dict:
    """Read Tinfoil keys from the 0600 env file. Values stay in-process."""
    import os
    from pathlib import Path

    vals: dict[str, str] = {}
    env_path = config.TINFOIL_ENV
    if os.getenv("TINFOIL_API_KEY"):
        vals["TINFOIL_API_KEY"] = os.environ["TINFOIL_API_KEY"]
    p = Path(env_path)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def available() -> bool:
    try:
        return bool(credentials().get("TINFOIL_API_KEY"))
    except Exception:
        return False


def _parse_sse(raw: bytes) -> dict:
    """Streamable-HTTP MCP may answer with JSON or SSE ('event:… data: {…}').

    Prefer the last data line that parses (matches stream-order results)."""
    body = raw.decode("utf-8", "replace")
    if body.lstrip().startswith("{"):
        try:
            return json.loads(body)
        except ValueError:
            pass
    out = {}
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:") and len(line) > 5:
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                try:
                    out = json.loads(payload)
                except ValueError:
                    continue
    return out


def _rpc(payload: dict, key: str, session: str | None) -> tuple[dict, str | None]:
    req = urllib.request.Request(
        MCP_URL, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "Authorization": f"Bearer {key}",
                 **({"mcp-session-id": session} if session else {})})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            sid = r.headers.get("mcp-session-id")
            raw = r.read()
            return _parse_sse(raw), sid
    except urllib.error.HTTPError as e:
        try:
            detail = e.read()[:120]
        except Exception:
            detail = b""
        raise McpUnavailable(f"HTTP {e.code} {detail!r}") from e
    except Exception as e:  # noqa: BLE001
        raise McpUnavailable(str(e)) from e


def search(query: str) -> str:
    """Confidential web search; '' on any failure (negative cache 30 s)."""
    global _BLOCKED_UNTIL
    if time.time() < _BLOCKED_UNTIL or not available():
        return ""
    key = credentials()["TINFOIL_API_KEY"]
    session: str | None = None
    try:
        # streamable HTTP: initialize; server returns session id in a header;
        # the spec requires an 'initialized' notification before calls.
        try:
            _, session = _rpc({
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "seraph", "version": "1.0"}}},
                key, session)
            _rpc({"jsonrpc": "2.0", "method": "notifications/initialized"},
                 key, session)
        except Exception:
            session = None
        out, session = _rpc({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": query}}},
            key, session)
        content = ((out.get("result") or {}).get("content")) or []
        text = "\n".join(
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("text")).strip()
        return text[:1400]
    except Exception:
        _BLOCKED_UNTIL = time.time() + 30
        return ""

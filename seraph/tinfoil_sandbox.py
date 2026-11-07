"""Tinfoil Sandbox adapter — confidential-compute chat lane.

Two bases:
- config.TINFOIL_BASE → dedicated bunker (provisioned via grant; default chat
  lane for data that must not leave Tinfoil\'s enclave)
- config.TINFOIL_API_BASE (https://api.tinfoil.sh/v1) → direct inference
  (llama-3.3-70b), used when no bunker is set.

Bearer auth via TINFOIL_API_KEY (0600 env file). Lane stays manual
(SERAPH_SANDBOX=auto) — inference is billable.
"""
from __future__ import annotations

import json
import urllib.request

from . import config
from . import tinfoil_websearch as _tws


class SandboxUnavailable(RuntimeError):
    pass


def available() -> bool:
    """Bunker base set OR key on box — gate stays manual (SERAPH_SANDBOX)."""
    if config.SERAPH_SANDBOX == "off":
        return False
    return bool(config.TINFOIL_BASE or _tws.credentials().get("TINFOIL_API_KEY"))


def chat(messages: list, model: str | None = None, *, timeout_s: float = 45.0) -> str:
    if not available():
        raise SandboxUnavailable(
            "sandbox not provisioned — set TINFOIL_BASE or TINFOIL_API_KEY")
    base = config.TINFOIL_BASE or config.TINFOIL_API_BASE
    headers = {"Content-Type": "application/json"}
    key = _tws.credentials().get("TINFOIL_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"model": model or "llama-3.3-70b", "messages": messages}
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(payload).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            out = json.loads(r.read())
        return (out["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:  # noqa: BLE001
        raise SandboxUnavailable(f"tinfoil: {e}") from e

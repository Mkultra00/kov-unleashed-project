"""Tinfoil Sandbox adapter — confidential-compute chat lane.

Off by default (no bunker provisioned yet — $200 credit, awaiting grant row).
When TINFOIL_BASE is set, this lane sends openai-compatible chat calls.
"""
from __future__ import annotations

import json
import urllib.request

from . import config


class SandboxUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(config.SERAPH_SANDBOX != "off" and config.TINFOIL_BASE)


def chat(messages: list, model: str | None = None, *, timeout_s: float = 45.0) -> str:
    if not available():
        raise SandboxUnavailable(
            "sandbox not provisioned — set SERAPH_TINFOIL_BASE after bunker grant")
    payload = {"model": model or "llama-3.3-70b", "messages": messages}
    req = urllib.request.Request(
        config.TINFOIL_BASE + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            out = json.loads(r.read())
        return (out["choices"][0]["message"].get("content") or "").strip()
    except Exception as e:  # noqa: BLE001
        raise SandboxUnavailable(f"tinfoil: {e}") from e

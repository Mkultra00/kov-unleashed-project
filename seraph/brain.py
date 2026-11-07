"""Seraph brain — routes a user turn to the best available LLM lane.

Lanes (in auto order):
  gw      — M4 gateway (openai-compatible chat w/ tools); Kov's own inference lane
  hermes  — `hermes chat -q` CLI one-shot (full toolset + skills + terminal/CUA);
            opt-in via SERAPH_BRAIN=hermes (slow one-shot, not for the fast
            voice loop). `--source tool` is mandatory: gateway policy only
            accepts SURFACES = {cli,tui,gui,acp,tool,oneshot} — any other
            tag → `invalid_params` (gateway/session_policy.py:78)
  ollama  — local Ollama chat completions, minimal tool loop

`reply()` always returns the FIRST lane that answers; each is timeout-guarded.
On total failure it raises LaneUnavailable so the server can fall back to the
battle-tested sovereign lane.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable

from . import config


class LaneUnavailable(RuntimeError):
    pass


@dataclass
class BrAnswer:
    text: str
    lane: str
    latency_ms: int
    tool_runs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"lane": self.lane, "latency_ms": self.latency_ms, "tools": self.tool_runs}


def _chat(base_url: str, model: str, messages: list, *, tools: list | None = None,
          timeout_s: float = 45.0, headers: dict | None = None) -> dict:
    """POST /chat/completions to any openai-compatible endpoint."""
    import urllib.request

    payload: dict = {"model": model, "messages": messages, "max_tokens": 700}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read())


def _reply_from(resp: dict) -> str | None:
    try:
        msg = resp["choices"][0]["message"]
        return (msg.get("content") or "").strip() or None
    except (KeyError, IndexError, TypeError):
        return None


def _lane_gw(user_text: str, history: list, runner: Callable | None,
             timeout_s: float, context: str = "") -> BrAnswer:
    t0 = time.time()
    system = "Answer in at most three tight spoken sentences, then stop."
    if context:
        system += "\n\n[RECALL — grounded facts, prefer these when relevant]\n" + context
    messages = ([{"role": "system", "content": system}]
                + [{"role": m["role"], "content": m["content"]} for m in history[-8:]]
                + [{"role": "user", "content": user_text}])
    try:
        out = _chat(config.GW_BASE, config.GW_MODEL, messages, timeout_s=timeout_s)
    except Exception as e:  # noqa: BLE001
        raise LaneUnavailable(f"gw: {e}") from e
    text = _reply_from(out)
    if not text:
        raise LaneUnavailable("gw: empty reply")
    return BrAnswer(text, "gw", int((time.time() - t0) * 1000))


def _lane_ollama(user_text: str, history: list, runner: Callable | None,
                 timeout_s: float, context: str = "") -> BrAnswer:
    t0 = time.time()
    tool_runs: list[str] = []
    system = (
        "You are Seraph — the voice presence of the Kov Unleashed assistant. "
        "Answer in at most three tight spoken sentences, then stop."
    )
    if context:
        system += "\n\n[RECALL — grounded facts, prefer these when relevant]\n" + context
    messages = ([{"role": "system", "content": system}]
                + [{"role": m["role"], "content": m["content"]} for m in history[-8:]]
                + [{"role": "user", "content": user_text}])
    try:
        out = _chat(config.OLLAMA_BASE, config.OLLAMA_MODEL, messages, timeout_s=timeout_s)
    except Exception as e:  # noqa: BLE001
        raise LaneUnavailable(f"ollama: {e}") from e
    text = _reply_from(out)
    if not text:
        raise LaneUnavailable("ollama: empty reply")
    # Minimal tool pass — one round only; keep the voice loop fast.
    if runner is not None:
        try:
            calls = out["choices"][0]["message"].get("tool_calls") or []
        except (KeyError, IndexError, TypeError):
            calls = []
        if calls:
            call = calls[0]
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = str(runner(name, args))[:800]
            tool_runs.append(name)
            messages += [
                out["choices"][0]["message"],
                {"role": "tool", "name": name,
                 "content": f"{name} -> {result}"},
                {"role": "user", "content": "Reply now as Seraph."},
            ]
            out = _chat(config.OLLAMA_BASE, config.OLLAMA_MODEL, messages, timeout_s=timeout_s)
            text = _reply_from(out) or text
    return BrAnswer(text, "ollama", int((time.time() - t0) * 1000), tool_runs)


def _lane_hermes(user_text: str, history: list, runner: Callable | None,
                 timeout_s: float) -> BrAnswer:
    """Full Kov-grade lane: hermes one-shot with toolsets + skills.

    `--source` must be a whitelisted SURFACES tag ('tool' = third-party
    integrations); arbitrary tags fail `invalid_params` at session create.
    """
    t0 = time.time()
    cmd = [config.HERMES_BIN, "chat", "-q", user_text, "-Q", "--source", "tool", "--yolo"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise LaneUnavailable(f"hermes: {e}") from e
    if p.returncode != 0 or "couldn't start the model connection" in p.stdout:
        raise LaneUnavailable(f"hermes rc={p.returncode}: {p.stdout.strip()[:120]}")
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()
             and not ln.startswith("session_id:")
             and "__HERMES_CWD_" not in ln]
    text = "\n".join(lines) if lines else ""
    if not text:
        raise LaneUnavailable("hermes: empty")
    return BrAnswer(text, "hermes", int((time.time() - t0) * 1000))


def reply(user_text: str, history: list | None = None, *, tool_runner: Callable | None = None,
          timeout_s: float = 40.0, context: str = "") -> BrAnswer:
    """Run user_text through the best lane; raise LaneUnavailable if all fail.
    `context` = recall block (e.g. Cognee graph hits) injected as system grounding."""
    want = config.SERAPH_BRAIN
    order = ("gw", "ollama") if want == "auto" else (want,)
    tried: list[str] = []
    for lane in order:
        try:
            if lane == "gw":
                return _lane_gw(user_text, history or [], tool_runner, timeout_s, context)
            if lane == "ollama":
                return _lane_ollama(user_text, history or [], tool_runner, timeout_s, context)
            if lane == "hermes":
                injected = f"{user_text}\n\n[RECALL]\n{context}" if context else user_text
                return _lane_hermes(injected, history or [], tool_runner, timeout_s)
        except LaneUnavailable as e:
            tried.append(f"{lane}: {e}")
            if want != "auto":
                break
    raise LaneUnavailable("all lanes failed — " + " | ".join(tried)[:400])

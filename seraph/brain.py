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
import re
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


def _try_json(s: str):
    """Best-effort decode of a stringified JSON literal ('[\\"uptime\\"]' -> list)."""
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return s


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
    if runner is not None:
        # Agent-grade kit (terminal/browser/screen/type/websearch) rides along:
        # native tool-calls when the model offers them, else a TOOL {} line.
        system += (
            "\n\nYou have real tools on the host Mac (terminal, browser, screen, "
            "type, websearch). Use one ONLY when the question truly needs live "
            "data; if the model lane supports tool-calls emit one normally. "
            "Otherwise, when you need a tool, make the FIRST line exactly: "
            'TOOL {"name": "<tool>", "args": {…}} and nothing else before it.'
        )
    if context:
        system += "\n\n[RECALL — grounded facts, prefer these when relevant]\n" + context
    messages = ([{"role": "system", "content": system}]
                + [{"role": m["role"], "content": m["content"]} for m in history[-8:]]
                + [{"role": "user", "content": user_text}])
    tools = None
    if runner is not None:
        try:
            from .tools import TOOLS as _TOOLS
            tools = _TOOLS
        except Exception:  # noqa: BLE001 — kit missing → lane still works
            tools = None
    try:
        out = _chat(config.OLLAMA_BASE, config.OLLAMA_MODEL, messages,
                    tools=tools, timeout_s=timeout_s)
    except Exception as e:  # noqa: BLE001
        raise LaneUnavailable(f"ollama: {e}") from e
    text = _reply_from(out)
    try:
        has_native = bool(out["choices"][0]["message"].get("tool_calls"))
    except (KeyError, IndexError, TypeError):
        has_native = False
    if not text and not has_native:
        # glm emits empty content when it answers with tool_calls only —
        # that's not an empty reply; only raise when there is truly nothing.
        raise LaneUnavailable("ollama: empty reply")
    # Minimal tool pass — one round only; keep the voice loop fast.
    if runner is not None:
        try:
            calls = out["choices"][0]["message"].get("tool_calls") or []
        except (KeyError, IndexError, TypeError):
            calls = []
        if not calls and text:  # text-protocol fallback: TOOL {…} first line
            m = re.match(r"\s*TOOL\s+(\{.*?\})\s*(?:\n|$)", text, re.S)
            if m:
                try:
                    parsed = json.loads(m.group(1))
                except json.JSONDecodeError:
                    parsed = {}
                name = str(parsed.get("name", "")).strip()
                if name:
                    calls = [{"function": {
                        "name": name,
                        "arguments": json.dumps(parsed.get("args") or {})}}]
                    text = None  # TOOL line is not prose; fresh reply post-call
        if calls:
            call = calls[0]
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            # glm quirk: list/dict literals arrive as STRINGS inside the key
            # (e.g. {"argv": "[\"uptime\"]"}) — decode one level deeper.
            if isinstance(args, dict):
                args = {k: (_try_json(v) if isinstance(v, str) and v[:1] in "[{" else v)
                        for k, v in args.items()}
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
    text = (text or "").strip() or "Tool ran, but the reply came back empty."
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

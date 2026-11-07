"""Seraph tool lane — the M5 agent kit for the Kov inside Seraph.

The directive: Seraph gets the same access as the agent running on this Mac —
terminals, browser reads, the screen (CUA), and confidential websearch.

Actions (all synchronous, tight timeouts, voice-loop-safe):
  terminal  — run a command on the M5 (list-of-args argv, 20 s, output capped)
  browser   — fetch a URL and return readable text
  screen    — capture the screen + name the frontmost app/window (CUA eyes)
  type      — type literal text into whatever field has focus on the Mac
  websearch — Tinfoil confidential MCP search (ZDR enclave)

Every action returns a plain string; failures come back as short error text so
the brain can speak about them instead of crashing the turn.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# --- terminal ----------------------------------------------------------------
_DENY = re.compile(r"\brm\s+-rf\s+[/~]|mkfs|dd\s+if=|shutdown|reboot|killall\s+-9|pkill\b|\bsudo\b",
                   re.I)
_MAX_OUT = 3000


def terminal(argv: list[str] | str, timeout_s: float = 20.0) -> str:
    if isinstance(argv, str):
        argv = argv.split()
    if not argv:
        return "terminal: empty command"
    joined = " ".join(map(str, argv))
    if _DENY.search(joined):
        return "terminal: refused — destructive/system-control command"
    try:
        p = subprocess.run(
            [str(a) for a in argv], capture_output=True, text=True,
            timeout=timeout_s, cwd=str(Path.home()))
        out = ((p.stdout or "") + ((" STDERR: " + p.stderr) if p.stderr.strip() else "")).strip()
        return (out or f"(exit {p.returncode}, no output)")[:_MAX_OUT]
    except subprocess.TimeoutExpired:
        return f"terminal: timeout after {timeout_s:.0f}s"
    except Exception as e:  # noqa: BLE001
        return f"terminal: {type(e).__name__}: {e}"


# --- browser -----------------------------------------------------------------
def browser(url: str) -> str:
    try:
        import urllib.request

        req = urllib.request.Request(
            url if url.startswith("http") else "https://" + url,
            headers={"User-Agent": "Seraph/1.0 (Kov voice assistant)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read(200_000).decode("utf-8", "replace")
        raw = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
        text = re.sub(r"(?s)<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:1600] or "(empty page)"
    except Exception as e:  # noqa: BLE001
        return f"browser: {type(e).__name__}: {e}"


# --- screen (CUA eyes) -------------------------------------------------------
_SCREENSHOT = Path("/tmp/seraph-screen.png")


def screen(*_args) -> str:
    try:
        subprocess.run(["screencapture", "-x", str(_SCREENSHOT)],
                       capture_output=True, timeout=6)
        shot = "captured" if _SCREENSHOT.exists() else "FAILED"
    except Exception as e:  # noqa: BLE001
        return f"screen: screenshot error {e}"
    try:
        p = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first '
             'application process whose frontmost is true'],
            capture_output=True, text=True, timeout=4)
        app = p.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        app = "unknown"
    size = _SCREENSHOT.stat().st_size if _SCREENSHOT.exists() else 0
    return f"frontmost app: {app}; screen captured to {_SCREENSHOT} ({size} bytes, {shot})"


# --- typing (CUA hands) ------------------------------------------------------
def type_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "type: nothing to type"
    safe = text.replace("\\", "\\\\").replace('"', '\\"')
    try:
        p = subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to keystroke "{safe}"'],
            capture_output=True, text=True, timeout=6)
        return "typed" if p.returncode == 0 else f"type: rc={p.returncode} {p.stderr[:120]}"
    except Exception as e:  # noqa: BLE001
        return f"type: {type(e).__name__}: {e}"


# --- confidential websearch (Tinfoil MCP, ZDR) --------------------------------
def websearch(query: str) -> str:
    if not (query or "").strip():
        return "websearch: empty query"
    try:
        from . import tinfoil_websearch as tws

        return tws.search(query) or "websearch: no results (lane cool-down or empty)"
    except Exception as e:  # noqa: BLE001
        return f"websearch: {type(e).__name__}: {e}"


_ACTIONS = {
    "terminal": lambda a: terminal(a.get("argv") if a.get("argv") else a.get("command", "")),
    "browser": lambda a: browser(str(a.get("url", ""))),
    "screen": lambda a: screen(),
    "type": lambda a: type_text(str(a.get("text", ""))),
    "websearch": lambda a: websearch(str(a.get("query", ""))),
}

TOOLS = [
    {"type": "function", "function": {
        "name": "terminal", "description": (
            "Run a command on the host Mac (this machine). 'argv' is a list of "
            "strings, e.g. ['uptime'] or ['ls', '-la']. No sudo/reboot/rm-rf.")}},
    {"type": "function", "function": {
        "name": "browser", "description": (
            "Fetch a URL and return readable page text (first ~1600 chars).")}},
    {"type": "function", "function": {
        "name": "screen", "description": (
            "Capture the Mac's screen to /tmp/seraph-screen.png and report the "
            "frontmost app. Use before typing so the right field has focus.")}},
    {"type": "function", "function": {
        "name": "type", "description": (
            "Type literal text into the currently focused field of the Mac UI.")}},
    {"type": "function", "function": {
        "name": "websearch", "description": (
            "Confidential web search inside Tinfoil's zero-data-retention "
            "enclave. Pass the user's question as 'query'.")}},
]

TOOL_NAMES = tuple(_ACTIONS)


def run_tool(name: str, args: dict | str | None) -> str:
    """Entry point handed to the brain: execute one tool call, return its text."""
    action = _ACTIONS.get(str(name).strip())
    if action is None:
        return f"unknown tool '{name}' — have: {', '.join(_ACTIONS)}"
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip().startswith(("{", "[")) else {"argv": args.split()}
        except json.JSONDecodeError:
            args = {"text": args}
    args = args or {}
    try:
        return str(action(args))[:_MAX_OUT]
    except Exception as e:  # noqa: BLE001 — tools never crash a turn
        return f"{name}: {type(e).__name__}: {e}"

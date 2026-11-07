"""Seraph package tests — offline only (no network, no secrets printed)."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from seraph import brain, cognee_memory, tinfoil_sandbox, config


# --- lane ordering / auto-fallback ---

def test_reply_falls_back(monkeypatch):
    monkeypatch.setattr(brain.config, "SERAPH_BRAIN", "auto")
    monkeypatch.setattr(brain, "_lane_gw",
                        lambda *a, **k: (_ for _ in ()).throw(brain.LaneUnavailable("down")))
    monkeypatch.setattr(brain, "_lane_ollama",
                        lambda *a, **k: brain.BrAnswer("fallback says hi", "ollama", 5, []))
    a = brain.reply("hello")
    assert a.lane == "ollama" and "hi" in a.text


def test_reply_all_fail(monkeypatch):
    monkeypatch.setattr(brain.config, "SERAPH_BRAIN", "auto")
    monkeypatch.setattr(brain, "_lane_gw",
                        lambda *a, **k: (_ for _ in ()).throw(brain.LaneUnavailable("down")))
    monkeypatch.setattr(brain, "_lane_ollama",
                        lambda *a, **k: (_ for _ in ()).throw(brain.LaneUnavailable("down")))
    with pytest.raises(brain.LaneUnavailable):
        brain.reply("hello")


def test_single_lane_stops(monkeypatch):
    monkeypatch.setattr(brain.config, "SERAPH_BRAIN", "ollama")
    called = []
    monkeypatch.setattr(brain, "_lane_gw",
                        lambda *a, **k: called.append("gw") or brain.BrAnswer("x", "gw", 1, []))
    monkeypatch.setattr(brain, "_lane_ollama",
                        lambda *a, **k: brain.BrAnswer("ok", "ollama", 1, []))
    a = brain.reply("hello")
    assert a.lane == "ollama" and called == []


# --- hermes lane contract ---

def test_hermes_lane_unavailable_on_bad_rc(monkeypatch):
    import subprocess  # noqa: PLC0415
    monkeypatch.setattr(brain.config, "HERMES_BIN", "false")
    with pytest.raises(brain.LaneUnavailable):
        brain._lane_hermes("hi", [], None, 5.0)


def test_hermes_lane_uses_tool_source_and_strips_trailers(monkeypatch):
    import subprocess  # noqa: PLC0415
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = ("I am running locally.\n"
                  "session_id: local-abc\n"
                  "__HERMES_CWD_ffa__/Users/franky__HERMES_CWD_ffa__\n")

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    a = brain._lane_hermes("hi", [], None, 5.0)
    cmd = captured["cmd"]
    assert "--source" in cmd and cmd[cmd.index("--source") + 1] == "tool"
    assert "session" not in a.text and "HERMES_CWD" not in a.text and a.lane == "hermes"


# --- reply extraction ---

def test_reply_from_shape():
    assert brain._reply_from({"choices": [{"message": {"content": " hi "}}]}) == "hi"
    assert brain._reply_from({"choices": []}) is None
    assert brain._reply_from({}) is None


# --- cognee adapter (offline) ---

def test_cognee_available_true_with_creds(monkeypatch, tmp_path):
    env = tmp_path / "cognee.env"
    env.write_text("COGNEE_EMAIL=x@example.com\nCOGNEE_PASSWORD=y\n")
    monkeypatch.setattr(config, "COGNEE_ENV", str(env))
    assert cognee_memory.available() is True


def test_cognee_token_missing_creds(monkeypatch, tmp_path):
    env = tmp_path / "cognee.env"
    env.write_text("# empty\n")
    monkeypatch.setattr(config, "COGNEE_ENV", str(env))
    with pytest.raises(cognee_memory.CogneeUnavailable):
        cognee_memory._token()


def test_cognee_credentials_env_read(monkeypatch, tmp_path):
    env = tmp_path / "cognee.env"
    env.write_text("COGNEE_EMAIL=\"quoted@x\"\nOTHER=1\n")
    monkeypatch.setattr(config, "COGNEE_ENV", str(env))
    vals = config.cognee_credentials()
    assert vals["COGNEE_EMAIL"] == "quoted@x" and "OTHER" in vals


# --- tinfoil sandbox ---

def test_sandbox_off_by_default(monkeypatch):
    monkeypatch.setattr(config, "SERAPH_SANDBOX", "off")
    monkeypatch.setattr(config, "TINFOIL_BASE", "")
    assert tinfoil_sandbox.available() is False
    with pytest.raises(tinfoil_sandbox.SandboxUnavailable):
        tinfoil_sandbox.chat([{"role": "user", "content": "hi"}])


def test_sandbox_on_when_base_set(monkeypatch):
    monkeypatch.setattr(config, "SERAPH_SANDBOX", "auto")
    monkeypatch.setattr(config, "TINFOIL_BASE", "https://sandbox.example/v1")
    assert tinfoil_sandbox.available() is True


# --- config defaults ---

def test_defaults():
    assert config.SERAPH_ENABLED is False  # atlas-tested flow untouched by default
    assert config.SERAPH_BRAIN == "auto"

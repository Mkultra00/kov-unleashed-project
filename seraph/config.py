"""Seraph App configuration — env-driven, no secrets in repo."""
from __future__ import annotations

import os
from pathlib import Path

# --- Brain lanes (openai-compatible chat endpoints) ---
SERAPH_BRAIN = os.getenv("SERAPH_BRAIN", "auto").lower()  # auto | gw | hermes | ollama
GW_BASE = os.getenv("SERAPH_GW_URL", "http://192.168.12.131:8000/v1")
GW_MODEL = os.getenv("SERAPH_GW_MODEL", "glm-5.3-flash:cloud")
OLLAMA_BASE = os.getenv("SERAPH_OLLAMA_URL", "http://127.0.0.1:11434/v1")
OLLAMA_MODEL = os.getenv("SERAPH_OLLAMA_MODEL", "glm-5.3-flash:cloud")
HERMES_BIN = os.getenv("SERAPH_HERMES_BIN", "hermes")
HERMES_TIMEOUT = int(os.getenv("SERAPH_HERMES_TIMEOUT", "45"))

# --- Memory (Cognee platform) ---
COGNEE_ENV = os.getenv("SERAPH_COGNEE_ENV", str(Path.home() / "scripts/kov-accounts/cognee.env"))
SERAPH_MEMORY = os.getenv("SERAPH_MEMORY", "auto").lower()  # auto | cognee | off
COGNEE_BASE = os.getenv("SERAPH_COGNEE_BASE", "https://platform.cognee.ai/api/v1")

# --- Tinfoil Sandbox (compute lane; off until a bunker is provisioned) ---
SERAPH_SANDBOX = os.getenv("SERAPH_SANDBOX", "off").lower()  # off | auto
TINFOIL_BASE = os.getenv("SERAPH_TINFOIL_BASE", "").rstrip("/")

# --- Master switch (the Seraph app runs the server with SERAPH=1) ---
SERAPH_ENABLED = os.getenv("SERAPH", "0") == "1"


def cognee_credentials() -> dict:
    """Read Cognee creds from the 0600 env file. Values stay in-process."""
    vals: dict[str, str] = {}
    p = Path(COGNEE_ENV)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals

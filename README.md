# Kov Unleashed Project — **Seraph App**

Kov, exposed as a user-facing voice brain: you talk, Seraph answers through the
best available LLM lane, with the Kov agent orchestrator (tools, skills,
terminal/CUA) one lane away.

## Seraph brain lanes (`seraph/`)
- `hermes` — local Hermes agent one-shot (`hermes chat -q --source tool`): full
  toolsets + skills + terminal/CUA. *`--source` must be a whitelisted gateway
  SURFACES tag; arbitrary tags fail `invalid_params`.*
- `gw` — M4 gateway (OpenAI-compatible, tools).
- `ollama` — local Ollama (`glm-5.3-flash:cloud`), fast default voice lane
  (~0.7–1.1 s), minimal tool loop.

## Sponsor stack
- **Tinfoil Sandbox** — confidential-compute lane (`seraph/tinfoil_sandbox.py`),
  off by default until a bunker is provisioned (`SERAPH_TINFOIL_BASE`).
- **Cognee Memory** — long-term memory via `seraph/cognee_memory.py`
  (email+password → bearer `/search`; creds in `SERAPH_COGNEE_ENV`).

## Run
```
SERAPH=1 .venv/bin/python -m sovereign.server          # default :8790
# or standalone Seraph port:
SERAPH=1 /path/to/py3.12-venv/bin/python -m uvicorn sovereign.server:app --port 8791
```
- `GET /health` → includes a `seraph` block (enabled / lanes / memory / sandbox).
- `POST /api/turn` (multipart `text=`) → JSON with `reply` + `seraph` block
  (`lane`, `latency_ms`, `tools`). Emergency turns still hit the sovereign
web-data pipeline; any Seraph exception falls through to the battle-tested
Atlas flow.

## Status
- 12/12 seraph tests green (`pytest tests/test_seraph.py`); full flow 38/38 on
  the sovereign lane.
- Atlas heritage: emergency web-data lane (OSM / Wikipedia / USGS + ~60-country
  emergency numbers), operator hinting, e2e text+voice paths.
- Base: cloned from [atlas-voice-agent](https://github.com/Mkultra00/atlas-voice-agent)
  (built on LiveKit's agent-starter-python for Localhost NYC '26).

First turn opens with: *"Hi, I'm Atlas — your travel-safety guide."*
(persona copy comes from the sovereign greeting; Seraph answers when wired).

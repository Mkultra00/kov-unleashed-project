# Kov Unleashed Project

Sovereign voice-agent base: FastAPI server (:8790) with an Ollama-side LLM lane,
an emergency web-data lane (OSM / Wikipedia / USGS + a ~60-country emergency
numbers table), operator hinting, and e2e text+voice paths. **174 tests green.**

- Base: cloned from [atlas-voice-agent](https://github.com/Mkultra00/atlas-voice-agent)
  (itself built on LiveKit's agent-starter-python for Localhost NYC '26).
- Working branch: `pivot/sovereign-stack`. `main` still holds the original demo base.
- Run: `.venv/bin/python -m sovereign.server` → http://127.0.0.1:8790

First turn opens with: *"Hi, I'm Atlas — your travel-safety guide."*
(brand/persona rename comes with the new project's work).

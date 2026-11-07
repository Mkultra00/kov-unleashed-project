"""ElevenLabs Scribe STT — reuses the existing ELEVEN_API_KEY, no new account."""
from __future__ import annotations

import httpx

from .config import SovereignConfig

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


_STT_MIME = {".webm": "audio/webm", ".mp3": "audio/mpeg", ".mp4": "audio/mp4",
             ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg",
             ".opus": "audio/opus", ".aac": "audio/aac", ".flac": "audio/flac",
             ".aiff": "audio/aiff"}


async def transcribe(audio: bytes, filename: str = "utterance.webm", cfg: SovereignConfig | None = None) -> str:
    cfg = cfg or SovereignConfig.resolve()
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = _STT_MIME.get(ext, "application/octet-stream")
    files = {"file": (filename, audio, mime)}
    data = {"model_id": "scribe_v1"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(STT_URL, files=files, data=data, headers={"xi-api-key": cfg.eleven_api_key})
        r.raise_for_status()
        body = r.json()
    return (body.get("text") or "").strip()

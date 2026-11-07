"""Agent memory, sovereign edition. Slot: Cognee Cloud knowledge graph.

Cognee recall is primary when configured (tenant base + API key); the JSONL
incident store always stays as the audit trail and keyword recall fallback.
Cognee calls are best-effort — they never stall or fail a turn.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

STORE = Path(__file__).resolve().parent / "memory" / "incidents.jsonl"


def _cognee(name: str):
    """Lazy seraph.cognee_memory; None when the seraph pkg isn't loadable."""
    try:
        from seraph import cognee_memory
        return getattr(cognee_memory, name)
    except Exception:
        return None


def remember(text: str, path: Path = STORE) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.time(), "text": text}
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    store = _cognee("store")
    if store:
        try:
            avail = _cognee("available")
            if avail and avail():
                store(text)  # best-effort cognify; audit trail already written
        except Exception:
            pass
    return entry


def recall(query: str, k: int = 3, path: Path = STORE) -> list[dict]:
    if not path.exists():
        return []
    entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not entries:
        return []
    from .retrieval import keyword_score

    scored = sorted(((keyword_score(query, e["text"]), e) for e in entries), key=lambda p: -p[0])
    top = [e for score, e in scored[:k] if score > 0.02]
    if not top:  # nothing relevant — return the freshest entries so context stays real
        top = entries[-k:]
    return top


def memory_block(query: str) -> str:
    cog_recall = _cognee("recall_block")
    cog_available = _cognee("available")
    lines: list[str] = []
    if cog_recall and cog_available and cog_available():
        try:
            cog = cog_recall(query)
            if cog:
                lines.append("KNOWLEDGE GRAPH (Cognee):\n" + cog)
        except Exception:
            pass
    hits = recall(query)
    if hits:
        lines.append("JSONL AUDIT TRAIL:\n" + "\n".join(
            f"- [{time.strftime('%m-%d %H:%M', time.localtime(e['ts']))}] {e['text']}"
            for e in hits))
    return "\n".join(lines)

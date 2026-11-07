"""Triage-time retrieval. Sponsor slot: Superlinked (SIE embeddings).

SIE (Metal, on-device, :8090) embeds KB bodies once and ranks by cosine
against the transcript — better grounding than tokens, zero API calls.
Fallback chain: SIE vector rank → TF-overlap keyword scorer (offline).
SIE is optional at runtime: every failure degrades to keyword, never fails.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

SIE_URL = os.getenv("SERAPH_SIE_URL", "http://127.0.0.1:8090")
SIE_MODEL = os.getenv("SERAPH_SIE_MODEL", "")

from .kb import KB  # noqa: E402

_EMB_CACHE: dict[str, list[float]] = {}
_SIE_DOWN_UNTIL = 0.0


def _tokens(text: str) -> set[str]:
    stop = {"the", "a", "an", "is", "are", "and", "or", "to", "of", "in", "on", "for", "my", "i", "me", "it", "at", "was", "be"}
    words = re.findall(r"[a-z]{3,}", text.lower())
    return {w for w in words if w not in stop}


def keyword_score(query: str, doc: str) -> float:
    qt, dt = _tokens(query), _tokens(doc)
    if not qt:
        return 0.0
    return len(qt & dt) / len(qt)


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5 or 1.0
    db = sum(y * y for y in b) ** 0.5 or 1.0
    return num / (da * db)


def _embed(texts: list[str], timeout: float = 6.0) -> list[list[float]] | None:
    """SIE OpenAI-style embeddings; None when SIE is down (30 s negative cache)."""
    global _SIE_DOWN_UNTIL
    if time.time() < _SIE_DOWN_UNTIL:
        return None
    req = urllib.request.Request(
        SIE_URL.rstrip("/") + "/v1/embeddings",
        data=json.dumps({"input": texts, "model": SIE_MODEL}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
        data = out.get("data") or []
        vecs = [d.get("embedding") for d in data]
        if not vecs or any(not v for v in vecs):
            _SIE_DOWN_UNTIL = time.time() + 30
            return None
        return vecs
    except Exception:
        _SIE_DOWN_UNTIL = time.time() + 30
        return None


def _vector_rank(query: str, docs: list[tuple[str, str]], k: int) -> list[dict[str, str]] | None:
    texts = [query] + [body for title, body in docs]
    missing = [t for t in texts if t not in _EMB_CACHE]
    if missing:
        vecs = _embed(missing)
        if vecs is None:
            return None
        for t, v in zip(missing, vecs):
            if len(_EMB_CACHE) < 512:  # bounded cache
                _EMB_CACHE[t] = v
    qv = _EMB_CACHE.get(query)
    if not qv:
        return None
    scored = sorted(((_cos(qv, _EMB_CACHE.get(b, [])), i) for i, (_, b) in enumerate(docs)),
                    key=lambda p: -p[0])
    return [{"title": docs[i][0], "body": docs[i][1], "via": "sie"}
            for _, i in scored[:k] if _EMB_CACHE.get(docs[i][1])]


def retrieve(query: str, k: int = 3) -> list[dict[str, str]]:
    docs = [(e["title"], e["body"]) for e in KB]
    try:
        vec_hits = _vector_rank(query, docs, k)
    except Exception:
        vec_hits = None
    if vec_hits:
        return vec_hits
    scored = sorted(
        ((keyword_score(query, e["body"] + " " + e["title"]), e) for e in KB),
        key=lambda pair: -pair[0],
    )
    return [e for score, e in scored[:k] if score > 0]


def context_block(query: str, k: int = 3) -> str:
    hits = retrieve(query, k)
    if not hits:
        return ""
    lines = [f"- {e['title']}: {e['body']}" for e in hits]
    return "Relevant procedures:\n" + "\n".join(lines)

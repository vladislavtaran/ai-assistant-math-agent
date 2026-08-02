#!/usr/bin/env python3
"""Load the embedded portfolio index and return the most relevant chunks.

Pure stdlib: the index is a small JSON file of {source, title, text, embedding}
records; similarity is plain cosine over Python lists. Fast enough for a few
hundred chunks and keeps the backend dependency-free.
"""
import os
import json
import math

INDEX_PATH = os.environ.get(
    "PORTFOLIO_INDEX",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portfolio_index.json"),
)


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sn = sm = 0.0
    for x, y in zip(a, b):
        dot += x * y
        sn += x * x
        sm += y * y
    if sn == 0 or sm == 0:
        return 0.0
    return dot / (math.sqrt(sn) * math.sqrt(sm))


class Retriever:
    def __init__(self, index_path=INDEX_PATH):
        self.index_path = index_path
        self._records = None

    def available(self):
        try:
            return bool(self._load())
        except Exception:
            return False

    def _load(self):
        if self._records is None:
            if not os.path.exists(self.index_path):
                self._records = []
            else:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._records = data.get("chunks", []) if isinstance(data, dict) else data
        return self._records

    def search(self, query_embedding, k=4, min_score=0.35):
        records = self._load()
        scored = []
        for r in records:
            s = _cosine(query_embedding, r.get("embedding", []))
            if s >= min_score:
                scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, r in scored[:k]:
            out.append({
                "source": r.get("source", ""),
                "title": r.get("title", ""),
                "text": r.get("text", ""),
                "score": round(s, 3),
            })
        return out

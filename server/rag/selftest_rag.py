#!/usr/bin/env python3
"""Offline RAG plumbing test — no API key, no network.

Builds a temporary index from the real knowledge/*.md files using a deterministic
LOCAL hashing embedding (lexical, not semantic), then checks that keyword queries
surface the right chunks. This validates chunking + cosine ranking + the
retriever end to end. Semantic quality is validated on the server with the real
Google embeddings.

Run:  python3 rag/selftest_rag.py
"""
import os
import re
import sys
import json
import math
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.build_index import gather_chunks  # noqa: E402
from rag.retriever import Retriever  # noqa: E402

DIM = 512


def hash_embed(text):
    """Deterministic bag-of-words vector via the hashing trick (lexical only)."""
    vec = [0.0] * DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        vec[hash(tok) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec] if n else vec


# hash() is salted per-process; fix it so a query embeds the same way as the index
os.environ.setdefault("PYTHONHASHSEED", "0")

_fail = 0


def check(name, cond, detail=""):
    global _fail
    if not cond:
        _fail += 1
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, ("- " + detail) if detail else ""))


chunks = gather_chunks()
for c in chunks:
    c["embedding"] = hash_embed("%s\n%s" % (c["title"], c["text"]))

tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
json.dump({"chunks": chunks}, tmp)
tmp.close()

r = Retriever(index_path=tmp.name)
check("index available", r.available(), "%d chunks" % len(chunks))

cases = [
    # NOTE: lexical (hashing) embedding — queries share actual words with the
    # target chunk. Real Google embeddings match on meaning, not just wording.
    ("ChromeOS enterprise MDM escalations", "Google"),
    ("how does the OSI Explorer dissect protocols", "OSI Explorer"),
    ("Anthropic Google Cloud certification records", "Credentials"),
    ("how does this chatbot AI assistant work", "AI Assistant"),
    ("contact email and phone", "Contact"),
]
for query, expect in cases:
    hits = r.search(hash_embed(query), k=3, min_score=0.0)
    titles = [h["title"] for h in hits]
    top = titles[0] if titles else "(none)"
    check("retrieve: %s" % query, any(expect in t for t in titles),
          "top=%r expected substring %r" % (top, expect))

os.unlink(tmp.name)
print("\n%s" % ("RAG PLUMBING OK" if _fail == 0 else "%d CHECK(S) FAILED" % _fail))
sys.exit(1 if _fail else 0)

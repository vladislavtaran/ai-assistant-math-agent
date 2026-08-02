#!/usr/bin/env python3
"""Build the portfolio embedding index.

Reads knowledge/*.md, splits each file into sections by Markdown headings,
embeds every section with the Google embeddings API, and writes
portfolio_index.json (a list of {source, title, text, embedding}).

Usage:
    GEMINI_API_KEY=... python3 rag/build_index.py
    GEMINI_API_KEY=... python3 rag/build_index.py --dry-run   # chunks only, no API

The index is committed to the repo so deployment is a plain file copy — rebuild
only when the knowledge base changes.
"""
import os
import re
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import llm  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KNOWLEDGE_DIR = os.path.join(HERE, "knowledge")
OUT_PATH = os.path.join(HERE, "portfolio_index.json")

MAX_CHARS = 1400  # split long sections so no chunk is too big to embed well


def split_sections(text):
    """Split on Markdown headings; further split overly long sections by blank lines."""
    sections = []
    cur_title, cur_lines = None, []

    def flush():
        body = "\n".join(cur_lines).strip()
        if body:
            sections.append((cur_title or "", body))

    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            flush()
            cur_title = line.lstrip("#").strip()
            cur_lines = []
        else:
            cur_lines.append(line)
    flush()

    out = []
    for title, body in sections:
        if len(body) <= MAX_CHARS:
            out.append((title, body))
            continue
        buf = ""
        for para in re.split(r"\n\s*\n", body):
            if buf and len(buf) + len(para) > MAX_CHARS:
                out.append((title, buf.strip()))
                buf = para
            else:
                buf = (buf + "\n\n" + para) if buf else para
        if buf.strip():
            out.append((title, buf.strip()))
    return out


def gather_chunks():
    chunks = []
    for fname in sorted(os.listdir(KNOWLEDGE_DIR)):
        if not fname.endswith(".md"):
            continue
        with open(os.path.join(KNOWLEDGE_DIR, fname), "r", encoding="utf-8") as f:
            text = f.read()
        for title, body in split_sections(text):
            chunks.append({"source": fname, "title": title, "text": body})
    return chunks


def main():
    dry = "--dry-run" in sys.argv
    chunks = gather_chunks()
    print("Found %d chunks across %d files" % (
        len(chunks), len({c["source"] for c in chunks})))
    if dry:
        for c in chunks:
            print("  - [%s] %s (%d chars)" % (c["source"], c["title"], len(c["text"])))
        return

    if not llm.API_KEY:
        print("ERROR: GEMINI_API_KEY not set — needed to embed. Use --dry-run to preview.")
        sys.exit(2)

    for i, c in enumerate(chunks, 1):
        # embed "title: body" so the heading adds context to the vector
        payload = ("%s\n%s" % (c["title"], c["text"])).strip()
        c["embedding"] = llm.embed(payload)
        print("  embedded %d/%d  [%s] %s" % (i, len(chunks), c["source"], c["title"]))
        time.sleep(0.15)  # be gentle with the free tier

    out = {
        "model": llm.EMBED_MODEL,
        "dim": len(chunks[0]["embedding"]) if chunks else 0,
        "count": len(chunks),
        "chunks": chunks,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("Wrote %s (%d chunks, dim=%d)" % (OUT_PATH, out["count"], out["dim"]))


if __name__ == "__main__":
    main()

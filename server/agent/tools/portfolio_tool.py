#!/usr/bin/env python3
"""portfolio_search tool — RAG grounding over Vladyslav Taran's work.

Embeds the query, retrieves the most relevant knowledge-base chunks, and returns
them with source labels so the agent can answer questions about Vladyslav's
experience and projects *with citations* — and say "not in my knowledge base"
when nothing matches.
"""
from ..registry import Tool
from ..llm import embed

_retriever = None


def _get_retriever():
    # imported lazily: `rag` is a sibling top-level package (server/ on sys.path),
    # and this keeps the import off the hot path for non-portfolio queries.
    global _retriever
    if _retriever is None:
        from rag.retriever import Retriever
        _retriever = Retriever()
    return _retriever


def _run(args):
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "summary": "empty query"}
    _retriever = _get_retriever()
    if not _retriever.available():
        return {"ok": False, "summary": "portfolio knowledge base is not built yet"}
    vec = embed(query)
    hits = _retriever.search(vec, k=int(args.get("k", 4)))
    if not hits:
        return {"ok": True, "summary": "No matching information in the portfolio knowledge base.",
                "citations": []}
    blocks = []
    citations = []
    for h in hits:
        label = h["title"] or h["source"]
        blocks.append("[%s]\n%s" % (label, h["text"]))
        citations.append({"source": h["source"], "title": h["title"], "score": h["score"]})
    return {
        "ok": True,
        "summary": "\n\n".join(blocks),
        "citations": citations,
    }


TOOL = Tool(
    name="portfolio_search",
    description=(
        "Search Vladyslav Taran's portfolio knowledge base (his experience, "
        "skills, and projects like the OSI Explorer, API Sandbox, QR tool, "
        "Divine Comedy game, CV generator). Use for ANY question about "
        "Vladyslav, his background, or what he has built. Answer only from the "
        "returned text and cite the sources; if nothing matches, say so."
    ),
    args={"query": "what to look up about Vladyslav or his projects",
          "k": "optional number of passages to retrieve (default 4)"},
    examples=[
        '{"tool":"portfolio_search","args":{"query":"experience with ChromeOS and MDM"}}',
        '{"tool":"portfolio_search","args":{"query":"how does the OSI Explorer work"}}',
    ],
    run=_run,
)

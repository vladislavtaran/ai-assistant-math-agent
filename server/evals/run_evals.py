#!/usr/bin/env python3
"""Eval harness for the agent loop.

Replays recorded model responses through run_agent and scores each case on
tool selection, model-call budget, answer content and citation grounding.
No network, no API key, no quota consumed - so it can gate every push.

Run from the server/ directory:
  MATH_PY=../.venv-test/bin/python MATH_AGENT=./mathagent.py python3 evals/run_evals.py

Exit code 0 if every case passes, 1 otherwise.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.dirname(HERE)
sys.path.insert(0, SERVER)

from agent.loop import run_agent, build_registry  # noqa: E402


# --- offline RAG -----------------------------------------------------------
# portfolio_search needs an embedded index, and building the real one costs an
# API key and quota. The project already ships a deterministic LOCAL hashing
# embedding (rag/selftest_rag.py) for exactly this. We build a throwaway index
# from the real knowledge/*.md in-process and stub agent.llm.embed to match it,
# so citation grounding is testable with no network and no key.
DIM = 256


def hash_embed(text):
    """Deterministic bag-of-words vector via the hashing trick (lexical only)."""
    vec = [0.0] * DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        vec[hash(tok) % DIM] += 1.0
    return vec


def setup_offline_rag():
    """Returns True if the offline index was built and wired in."""
    try:
        import tempfile
        from rag.build_index import gather_chunks
        from agent import llm
        from agent.tools import portfolio_tool

        chunks = gather_chunks()
        if not chunks:
            return False
        for c in chunks:
            c["embedding"] = hash_embed("%s\n%s" % (c.get("title", ""), c["text"]))

        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(chunks, tmp)
        tmp.close()

        from rag.retriever import Retriever

        class _OfflineRetriever:
            """Thin shim over Retriever.

            The real min_score (0.35) is tuned for SEMANTIC embeddings, where
            related text scores high. The offline lexical stand-in scores much
            lower, so the default floor filters everything out. We drop the floor
            here because what these evals assert is tool selection and citation
            plumbing - retrieval quality is a separate concern, measured live.
            """

            def __init__(self, path):
                self._r = Retriever(index_path=path)

            def available(self):
                return self._r.available()

            def search(self, vec, k=4, min_score=0.0):
                return self._r.search(vec, k=k, min_score=min_score)

        portfolio_tool._retriever = _OfflineRetriever(tmp.name)
        llm.embed = hash_embed            # queries embed the same way the index did
        portfolio_tool.embed = hash_embed  # it imported the symbol directly
        return True
    except Exception as exc:
        print("note: offline RAG unavailable (%s: %s)" % (type(exc).__name__, exc))
        return False


class ReplayLLM:
    """Returns the recorded responses in order. Signature matches the real
    gen_text the loop expects: (system_text, contents) -> str."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, system, contents):
        self.calls += 1
        if not self.script:
            # Ran past the script: the loop asked for more steps than we recorded.
            return '{"answer":"(script exhausted)"}'
        return self.script.pop(0)


def tools_from(out):
    """The loop records each tool invocation in the trace."""
    return [t.get("tool") for t in out.get("trace", []) if t.get("type") == "tool"]


def score(case, out):
    """Return a list of failure strings. Empty list means the case passed."""
    fails = []

    expected = case.get("expect_tools")
    if expected is not None:
        actual = tools_from(out)
        if actual != expected:
            fails.append("tools %s != expected %s" % (actual, expected))

    budget = case.get("expect_max_calls")
    if budget is not None and out.get("calls", 0) > budget:
        fails.append("calls %d > budget %d" % (out["calls"], budget))

    for needle in case.get("answer_contains", []):
        if needle.lower() not in (out.get("reply") or "").lower():
            fails.append("reply missing %r" % needle)

    for needle in case.get("answer_not_contains", []):
        if needle.lower() in (out.get("reply") or "").lower():
            fails.append("reply should not contain %r" % needle)

    if case.get("require_citations") and not out.get("citations"):
        fails.append("no citations returned")

    return fails


def main():
    path = os.path.join(HERE, "cases.json")
    cases = json.load(open(path, encoding="utf-8"))["cases"]
    setup_offline_rag()
    registry = build_registry()

    failed = 0
    for case in cases:
        contents = [{"role": "user", "parts": [{"text": case["prompt"]}]}]
        try:
            out = run_agent(contents, ReplayLLM(case["model_responses"]), registry=registry)
            fails = score(case, out)
        except Exception as exc:                      # a crash is a failure, not a stack trace
            out, fails = {}, ["raised %s: %s" % (type(exc).__name__, exc)]

        status = "PASS" if not fails else "FAIL"
        if fails:
            failed += 1
        print("[%s] %-10s tools=%s calls=%s citations=%d"
              % (status, case["id"], tools_from(out), out.get("calls", "-"),
                 len(out.get("citations") or [])))
        for f in fails:
            print("         -> %s" % f)

    total = len(cases)
    print("\n%d/%d passed" % (total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

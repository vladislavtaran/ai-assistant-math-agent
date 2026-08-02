#!/usr/bin/env python3
"""The agent loop: plan -> act -> observe, one LLM decision at a time.

Each iteration the LLM returns ONE JSON object — either a tool call or a final
answer. Tool results are fed back as observations and the loop continues, up to
a small step cap that also bounds LLM calls (important on a free-tier quota):

    general/conversational : 1 call  (answers directly, no tool)
    single-tool question   : 2 calls (call tool -> narrate result)
    composite question     : up to MAX_STEPS+1 calls

Everything is model-agnostic (Gemini *and* Gemma) because decisions are plain
JSON in the text response — no provider function-calling required.
"""
import re
import json

from .registry import Registry
from .tools import math_tool, datetime_tool, network_tool, portfolio_tool

MAX_STEPS = 4          # planning iterations (incl. empty-response retries) before we stop
MAX_ARGS_CHARS = 2000

SYSTEM = """You are the reasoning core of the assistant on Vladyslav Taran's \
personal website (he is a Lead Systems Engineer specializing in ChromeOS, \
enterprise MDM, and applied AI). You help visitors by either calling a TOOL or \
giving a FINAL answer.

Available tools:
%s

Respond with exactly ONE JSON object and nothing else — no code fences, no prose.
Keep "thought" to a short phrase (a dozen words at most):
  to use a tool:  {"thought": "<short>", "tool": "<tool name>", "args": { ... }}
  to answer:      {"thought": "<short>", "answer": "<final answer, Markdown ok>"}

For a direct math / date-time / network question where the tool's own output IS
the complete answer, add "final": true to the tool call — the exact result is
then shown to the user as-is (do NOT set "final" for portfolio_search, and do not
set it when you still need to combine several tools).

Rules:
- For ANY arithmetic or mathematics, you MUST call math_solver — never compute it yourself.
- For anything about Vladyslav, his background, skills, or his projects, call \
portfolio_search and answer ONLY from what it returns, citing the source names in \
your answer. If it returns nothing relevant, say you don't have that information.
- After a tool runs you receive an "Observation". Quote its numbers, dates, and \
results VERBATIM — do not alter them.
- If a plain conversational or general-knowledge question needs no tool, answer \
it directly and immediately.
- Be concise, friendly, and professional. Prefer one short answer over many tool calls."""


COMPOSE_SYS = """You are the assistant on Vladyslav Taran's personal website. \
Using ONLY the notes below, answer the user's question in a friendly, concise \
way, and mention the relevant source name(s) in brackets, e.g. [Experience]. If \
the notes don't contain the answer, say you don't have that information. Write \
the answer directly — do not call any tools."""


def _last_user(contents):
    for t in reversed(contents):
        if t.get("role") == "user" and t.get("parts"):
            return t["parts"][0].get("text", "")
    return ""


def _compose(question, notes, gen_text):
    """One clean generation that turns retrieved notes into a grounded answer.

    Deliberately a plain Q&A prompt (no tool framing) so it is reliable across
    models — gemini-3.x otherwise tends to emit a native functionCall or empty
    text when it sees the tool-decision protocol on a follow-up turn.
    """
    contents = [{"role": "user", "parts": [{"text":
        "Question: %s\n\nNotes:\n%s" % (question, notes)}]}]
    txt = (gen_text(COMPOSE_SYS, contents) or "").strip()
    if txt.startswith("{"):  # a stray decision slipped through -> salvage its answer
        d = _parse_decision(txt)
        if isinstance(d, dict) and d.get("answer"):
            txt = str(d["answer"]).strip()
        elif isinstance(d, dict) and "tool" in d:
            txt = ""
    return txt


def build_registry():
    reg = Registry()
    reg.register(math_tool.TOOL)
    reg.register(portfolio_tool.TOOL)
    reg.register(datetime_tool.TOOL)
    reg.register(network_tool.TOOL)
    return reg


def _first_json(text):
    """Return the first *balanced* {...} object as a string, ignoring code
    fences and any prose before/after it. None if there is no complete object."""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None  # never closed -> truncated


def _parse_decision(text):
    if not text:
        return None
    blob = _first_json(text)
    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        return None


def _agent_label(tools_used):
    if "math_solver" in tools_used:
        return "math"
    if "portfolio_search" in tools_used:
        return "portfolio"
    if tools_used:
        return "tools"
    return "general"


def run_agent(contents, gen_text, registry=None, max_steps=MAX_STEPS):
    """Run the loop.

    contents  : Gemini-style chat turns [{role, parts:[{text}]}], ending with the user.
    gen_text  : callable(system_text, contents) -> str  (the LLM text response).
                Injected so the loop is testable without the network.
    Returns   : {reply, agent, trace, citations, calls}
    """
    registry = registry or build_registry()
    system = SYSTEM % registry.spec()
    convo = list(contents)
    question = _last_user(contents)
    trace = []
    citations = []
    tools_used = []
    calls = 0

    def _finish(reply):
        return {"reply": str(reply).strip(), "agent": _agent_label(tools_used),
                "trace": trace, "citations": citations, "calls": calls}

    for step in range(max_steps):
        raw = gen_text(system, convo)
        calls += 1
        decision = _parse_decision(raw)

        # unparseable, or a plain-text reply -> treat as the final answer.
        if not decision or ("tool" not in decision and "answer" not in decision):
            reply = (raw or "").strip()
            if reply:
                return _finish(reply)  # a plain-text answer
            # empty response (some thinking models emit only reasoning on a turn):
            if tools_used:
                notes = "\n\n".join(t["observation"] for t in trace if t.get("type") == "tool")
                reply = _compose(question, notes, gen_text)
                calls += 1
                return _finish(reply or "Sorry, I couldn't complete that — please try rephrasing.")
            if step < max_steps - 1:
                continue  # nothing decided yet -> retry the planning step
            return _finish("Sorry, I didn't catch that — could you rephrase?")

        if "answer" in decision and "tool" not in decision:
            if decision.get("thought"):
                trace.append({"type": "thought", "text": str(decision["thought"])[:400]})
            return _finish(decision.get("answer", ""))

        # --- a tool call ---
        name = decision.get("tool")
        args = decision.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        tool = registry.get(name)
        result = registry.run(name, args)
        tools_used.append(name)
        for c in result.get("citations", []) or []:
            if c not in citations:
                citations.append(c)

        trace.append({
            "type": "tool",
            "tool": name,
            "args": args,
            "thought": str(decision.get("thought", ""))[:400],
            "ok": bool(result.get("ok", True)),
            "observation": str(result.get("summary", ""))[:1200],
        })

        # call-saver: if the model marked a terminal tool "final" and it
        # succeeded, its deterministic summary IS the answer — return it now and
        # skip the extra narration call (halves quota cost for math/time/network).
        if (decision.get("final") and tool is not None and tool.terminal
                and result.get("ok", True)):
            return _finish(result.get("summary", ""))

        # a non-terminal tool (portfolio RAG) returns notes, not a finished
        # answer — synthesize a grounded reply now with a clean compose prompt and
        # return. This is reliable across models and keeps RAG at exactly 2 calls.
        if tool is not None and not tool.terminal:
            reply = _compose(question, result.get("summary", ""), gen_text)
            calls += 1
            return _finish(reply or result.get("summary", ""))

        # otherwise (a terminal tool without "final") feed the observation back and
        # let the model continue — it may chain another tool or answer directly.
        convo.append({"role": "model", "parts": [{"text": json.dumps(
            {"tool": name, "args": args})[:MAX_ARGS_CHARS]}]})
        convo.append({"role": "user", "parts": [{"text":
            "Observation from %s:\n%s\n\nUsing this, either call another tool or "
            "give the final answer now." % (name, result.get("summary", ""))}]})

    # step budget spent -> synthesize a final answer from the observations gathered
    notes = "\n\n".join(t["observation"] for t in trace if t.get("type") == "tool")
    reply = _compose(question, notes, gen_text) if notes else ""
    calls += 1
    return _finish(reply or "Sorry, I couldn't complete that — please try rephrasing.")

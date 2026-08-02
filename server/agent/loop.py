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

MAX_STEPS = 3          # tool-taking iterations before we force a final answer
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
    trace = []
    citations = []
    tools_used = []
    calls = 0

    for step in range(max_steps):
        raw = gen_text(system, convo)
        calls += 1
        decision = _parse_decision(raw)

        # unparseable, or a plain-text reply -> treat as the final answer
        if not decision or ("tool" not in decision and "answer" not in decision):
            reply = (raw or "").strip() or "Sorry, I didn't catch that — could you rephrase?"
            return {"reply": reply, "agent": _agent_label(tools_used),
                    "trace": trace, "citations": citations, "calls": calls}

        if "answer" in decision and "tool" not in decision:
            if decision.get("thought"):
                trace.append({"type": "thought", "text": str(decision["thought"])[:400]})
            return {"reply": str(decision.get("answer", "")).strip(),
                    "agent": _agent_label(tools_used), "trace": trace,
                    "citations": citations, "calls": calls}

        # --- a tool call ---
        name = decision.get("tool")
        args = decision.get("args") or {}
        if not isinstance(args, dict):
            args = {}
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

        # feed the decision + observation back for the next turn
        convo.append({"role": "model", "parts": [{"text": json.dumps(
            {"tool": name, "args": args})[:MAX_ARGS_CHARS]}]})
        convo.append({"role": "user", "parts": [{"text":
            "Observation from %s:\n%s\n\nUsing this, either call another tool or "
            "give the final answer now." % (name, result.get("summary", ""))}]})

    # step budget spent -> force a final answer from the observations gathered
    convo.append({"role": "user", "parts": [{"text":
        "Provide the final answer to the user now, based on the observations above."}]})
    reply = (gen_text(system, convo) or "").strip()
    calls += 1
    reply = _parse_decision(reply)
    reply = reply.get("answer") if isinstance(reply, dict) and reply.get("answer") else None
    if not reply:
        # last resort: hand back the most recent tool observation
        reply = next((t["observation"] for t in reversed(trace) if t.get("type") == "tool"),
                     "Sorry, I couldn't complete that — please try rephrasing.")
    return {"reply": str(reply).strip(), "agent": _agent_label(tools_used),
            "trace": trace, "citations": citations, "calls": calls}

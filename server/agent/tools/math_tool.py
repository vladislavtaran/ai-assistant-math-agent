#!/usr/bin/env python3
"""math_solver tool — routes to the standalone SymPy math agent (own process).

The numbers come from SymPy, not the LLM. The tool returns both the structured
result and a deterministic `summary` string that the LLM is told to quote
verbatim, so `48273 * 19847` is guaranteed exact.
"""
import os
import json
import subprocess

from ..registry import Tool

MATH_PY = os.environ.get("MATH_PY", "/opt/chatbot/venv/bin/python3")
MATH_AGENT = os.environ.get("MATH_AGENT", "/opt/chatbot/mathagent.py")


def _call_agent(job):
    try:
        proc = subprocess.run(
            [MATH_PY, MATH_AGENT],
            input=json.dumps(job).encode("utf-8"),
            capture_output=True, timeout=8,
        )
        out = proc.stdout.decode("utf-8").strip()
        return json.loads(out) if out else {"ok": False, "error": "no output from math agent"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "computation timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _nice(s):
    return str(s).replace("**", "^")


def _format(t):
    if not t.get("ok"):
        return "could not compute (%s)" % t.get("error", "error")
    op = t.get("operation")
    if op == "solve":
        var, sols = t.get("variable", "x"), t.get("solutions", [])
        body = "no solution" if not sols else "; ".join(
            "%s = %s" % (var, _nice(s)) for s in sols)
        ap = t.get("approx", [])
        extra = "  (≈ %s)" % ", ".join(ap) if ap and ap != t.get("solutions") else ""
        return "%s  →  %s%s" % (_nice(t.get("equation", "")), body, extra)
    if op == "evaluate":
        exact, num = _nice(t.get("exact", "")), t.get("numeric")
        extra = "  ≈ %s" % num if num and num != t.get("exact") else ""
        return "%s = %s%s" % (_nice(t.get("input", "")), exact, extra)
    if op == "differentiate":
        return "d/d%s ( %s ) = %s" % (
            t.get("variable", "x"), _nice(t.get("input", "")), _nice(t.get("result", "")))
    if op == "integrate":
        return "∫ %s d%s = %s" % (
            _nice(t.get("input", "")), t.get("variable", "x"), _nice(t.get("result", "")))
    return "%s → %s" % (_nice(t.get("input", "")), _nice(t.get("result", "")))


def _run(args):
    job = {
        "operation": args.get("operation", "evaluate"),
        "expression": args.get("expression", ""),
        "variable": args.get("variable"),
    }
    result = _call_agent(job)
    return {
        "ok": bool(result.get("ok")),
        "summary": _format(result),
        "raw": result,
    }


TOOL = Tool(
    name="math_solver",
    description=(
        "Do exact mathematics: arithmetic, solve equations, differentiate, "
        "integrate, simplify, factor, expand. Use for ANY calculation — the "
        "result is computed by SymPy, not guessed."
    ),
    args={
        "operation": "one of solve|evaluate|differentiate|integrate|simplify|factor|expand",
        "expression": "the expression, using * for multiply and ^ for power; keep = for equations",
        "variable": "optional variable name, e.g. x",
    },
    examples=[
        '{"tool":"math_solver","args":{"operation":"evaluate","expression":"48273*19847"}}',
        '{"tool":"math_solver","args":{"operation":"solve","expression":"2*x+5=17","variable":"x"}}',
        '{"tool":"math_solver","args":{"operation":"integrate","expression":"x*sin(x)","variable":"x"}}',
    ],
    run=_run,
    terminal=True,
)

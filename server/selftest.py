#!/usr/bin/env python3
"""Offline self-test: exercises the tools directly and the agent loop with a
scripted fake LLM — no network, no API key, no quota used.

Run:  python3 selftest.py
Point the math agent at a local sympy env first, e.g.:
  MATH_PY=../.venv-test/bin/python MATH_AGENT=./mathagent.py python3 selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.tools import math_tool, datetime_tool, network_tool  # noqa: E402
from agent.loop import run_agent, build_registry  # noqa: E402
from agent import llm  # noqa: E402

_fail = 0


def check(name, cond, detail=""):
    global _fail
    status = "PASS" if cond else "FAIL"
    if not cond:
        _fail += 1
    print("[%s] %s %s" % (status, name, ("- " + detail) if detail else ""))


# --- tools (deterministic, no LLM) ---------------------------------------
r = math_tool._run({"operation": "evaluate", "expression": "48273*19847"})
check("math evaluate", r["ok"] and "958074231" in r["summary"], r["summary"])

r = math_tool._run({"operation": "solve", "expression": "2*x+5=17", "variable": "x"})
check("math solve", r["ok"] and "6" in r["summary"], r["summary"])

r = math_tool._run({"operation": "integrate", "expression": "x*sin(x)", "variable": "x"})
check("math integrate", r["ok"] and "sin" in r["summary"], r["summary"])

r = datetime_tool._run({"timezone": "Asia/Tokyo"})
check("datetime tokyo", "Asia/Tokyo" in r["summary"], r["summary"])

r = datetime_tool._run({"timezone": "kyiv"})
check("datetime alias", "Europe/Kyiv" in r["summary"], r["summary"])

r = network_tool._run({"operation": "cidr", "value": "192.168.1.0/26"})
check("network cidr", "192.168.1.63" in r["summary"] and "62 usable" in r["summary"], r["summary"])

r = network_tool._run({"operation": "dns", "value": "localhost"})
check("network dns", r.get("ok", True), r["summary"])

# --- llm.parts_decision: native functionCall (gemini-3.x) -> decision JSON ---
import json as _json  # noqa: E402
dec = llm.parts_decision([{"functionCall": {"name": "portfolio_search", "args": {"query": "chromeos"}}}])
dec = _json.loads(dec)
check("parts_decision functionCall", dec.get("tool") == "portfolio_search" and dec["args"]["query"] == "chromeos", str(dec))
# plain text still passes through
check("parts_decision text", llm.parts_decision([{"text": '{"answer":"hi"}'}]) == '{"answer":"hi"}')
# thought-only parts -> empty (so the loop can fall back)
check("parts_decision thought-only", llm.parts_decision([{"text": "reasoning", "thought": True}]) == "")


# --- agent loop with a scripted fake LLM ---------------------------------
class FakeLLM:
    """Returns queued responses in order, ignoring inputs."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, system, contents):
        self.calls += 1
        return self.script.pop(0) if self.script else '{"answer":"(no more script)"}'


reg = build_registry()

# 1) general question -> answers directly in 1 call
fake = FakeLLM(['{"thought":"greeting","answer":"Hello! How can I help?"}'])
out = run_agent([{"role": "user", "parts": [{"text": "hi"}]}], fake, registry=reg)
check("loop general 1-call", out["reply"].startswith("Hello") and out["calls"] == 1 and out["agent"] == "general")

# 2) math question -> calls math_solver, then narrates (2 calls)
fake = FakeLLM([
    '{"thought":"needs math","tool":"math_solver","args":{"operation":"evaluate","expression":"12*34+5"}}',
    '{"thought":"quote result","answer":"12*34+5 = 413."}',
])
out = run_agent([{"role": "user", "parts": [{"text": "what is 12*34+5"}]}], fake, registry=reg)
check("loop math 2-call", "413" in out["reply"] and out["calls"] == 2 and out["agent"] == "math",
      "reply=%r calls=%d" % (out["reply"], out["calls"]))
check("loop math trace", any(t.get("tool") == "math_solver" for t in out["trace"]))

# 2b) math with final:true -> short-circuits to 1 call (quota saver)
fake = FakeLLM(['{"tool":"math_solver","args":{"operation":"evaluate","expression":"12*34+5"},"final":true}'])
out = run_agent([{"role": "user", "parts": [{"text": "what is 12*34+5"}]}], fake, registry=reg)
check("loop math final 1-call", "413" in out["reply"] and out["calls"] == 1 and out["agent"] == "math",
      "reply=%r calls=%d" % (out["reply"], out["calls"]))

# 2c) portfolio must NOT short-circuit even if final is (wrongly) set: not terminal
fake = FakeLLM([
    '{"tool":"portfolio_search","args":{"query":"chromeos"},"final":true}',
    '{"answer":"Vladyslav works on ChromeOS."}',
])
out = run_agent([{"role": "user", "parts": [{"text": "chromeos?"}]}], fake, registry=reg)
check("loop portfolio no-shortcircuit", out["calls"] == 2 and "ChromeOS" in out["reply"],
      "calls=%d" % out["calls"])

# 3) composite: math then datetime then answer
fake = FakeLLM([
    '{"tool":"math_solver","args":{"operation":"evaluate","expression":"2^10"}}',
    '{"tool":"datetime","args":{"timezone":"UTC"}}',
    '{"answer":"2^10 = 1024, and the UTC time is included above."}',
])
out = run_agent([{"role": "user", "parts": [{"text": "2^10 and the time in UTC?"}]}], fake, registry=reg)
tool_steps = [t for t in out["trace"] if t.get("type") == "tool"]
check("loop composite", "1024" in out["reply"] and len(tool_steps) == 2 and out["calls"] == 3,
      "tool_steps=%d calls=%d" % (len(tool_steps), out["calls"]))

# 4) unparseable model output -> treated as final answer, never crashes
fake = FakeLLM(['just some plain text, no json here'])
out = run_agent([{"role": "user", "parts": [{"text": "hey"}]}], fake, registry=reg)
check("loop plain-text fallback", "plain text" in out["reply"])

# 5) unknown tool -> error observation, loop still returns
fake = FakeLLM([
    '{"tool":"nonexistent","args":{}}',
    '{"answer":"Sorry, I could not do that."}',
])
out = run_agent([{"role": "user", "parts": [{"text": "x"}]}], fake, registry=reg)
check("loop unknown-tool", "Sorry" in out["reply"])

print("\n%s" % ("ALL PASS" if _fail == 0 else "%d CHECK(S) FAILED" % _fail))
sys.exit(1 if _fail else 0)

#!/usr/bin/env python3
"""
Chatbot proxy for chrome.net.ua  (model-agnostic).

- General questions  -> the LLM answers directly.
- Math questions     -> the LLM *prepares* a structured job (JSON) and we *call*
                        a separate Python math agent (mathagent.py, SymPy) that
                        does the real, deterministic computation. The numbers
                        never come from the LLM.
- Works with Gemini *and* Gemma models (no function-calling required), so we can
  use whichever free model has the highest quota.
- On quota exhaustion (HTTP 429) returns a clear "free limit reached" message.
"""
import os
import re
import json
import time
import threading
import subprocess
import urllib.request
import urllib.error
from collections import deque, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemma-4-26b-a4b-it").strip()
HOST = os.environ.get("CHATBOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("CHATBOT_PORT", "8090"))
MATH_PY = os.environ.get("MATH_PY", "/opt/chatbot/venv/bin/python3")
MATH_AGENT = os.environ.get("MATH_AGENT", "/opt/chatbot/mathagent.py")
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"

MAX_MSG_CHARS = 4000
MAX_HISTORY = 12

GENERAL_SYS = (
    "You are the assistant on Vladyslav Taran's personal website (a Lead Systems Engineer "
    "specializing in ChromeOS, enterprise MDM and applied AI). Be helpful, accurate and concise. "
    "Friendly, professional tone. You may use simple Markdown."
)
# the LLM only PREPARES a job for the Python math agent
EXTRACT_SYS = (
    "You translate a mathematics question into ONE compact JSON job and output ONLY that JSON "
    "(no prose, no code fences). Schema: "
    '{"operation": one of ["solve","evaluate","differentiate","integrate","simplify","factor","expand"], '
    '"expression": string using * for multiply and ^ for power, keep "=" for equations, '
    '"variable": optional string}. '
    'Examples: "what is 12*34+5" -> {"operation":"evaluate","expression":"12*34+5"} ; '
    '"solve 2x+5=17 for x" -> {"operation":"solve","expression":"2*x+5=17","variable":"x"} ; '
    '"derivative of x^2+3x" -> {"operation":"differentiate","expression":"x^2+3*x","variable":"x"} ; '
    '"integral of x sin x" -> {"operation":"integrate","expression":"x*sin(x)","variable":"x"}.'
)
MATH_FALLBACK_SYS = (
    "You are a careful mathematics assistant. Solve the problem step by step and finish with the final "
    "result on its own line prefixed with 'Answer: '."
)

# ---- math routing (no API call) ------------------------------------------
MATH_KW = re.compile(
    r"\b(solve|calculate|compute|equation|integral|integrate|derivative|differentiate|factor|"
    r"simplify|expand|probability|matrix|matrices|determinant|polynomial|quadratic|sqrt|square root|"
    r"logarithm|\blog\b|\bln\b|sine|cosine|tangent|\bsin\b|\bcos\b|\btan\b|percentage|percent|evaluate|"
    r"prove|theorem|sum of|product of|average|\bmean\b|median|variance|standard deviation|factorial|"
    r"permutation|combination|modulo|\bgcd\b|\blcm\b|fraction|geometry|area of|volume of|perimeter|"
    r"circumference|how much is|what is .* (plus|minus|times|divided)|roots? of|series|limit of)\b",
    re.I,
)
MATH_EXPR = re.compile(r"\d\s*[\+\-\*/×÷=^]\s*\d|[∫∑√π≤≥≠±×÷∞]|\d+\s*%|\bx\s*\^|\d+!\B")


def is_math(text):
    return bool(MATH_KW.search(text) or MATH_EXPR.search(text))


# ---- per-IP rate limit ----------------------------------------------------
_WINDOW = 300
_MAX_PER_WINDOW = 25
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def too_many(ip):
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > _WINDOW:
            q.popleft()
        if len(q) >= _MAX_PER_WINDOW:
            return True
        q.append(now)
        return False


# ---- separate math agent (own process) ------------------------------------
def call_math_agent(args):
    try:
        proc = subprocess.run(
            [MATH_PY, MATH_AGENT],
            input=json.dumps(args).encode("utf-8"),
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


def format_answer(t):
    if not t.get("ok"):
        return "I couldn't compute that — please rephrase. (%s)" % t.get("error", "error")
    op = t.get("operation")
    if op == "solve":
        var, sols = t.get("variable", "x"), t.get("solutions", [])
        if not sols:
            body = "no solution"
        else:
            body = "; ".join("%s = %s" % (var, _nice(s)) for s in sols)
        ap = t.get("approx", [])
        extra = "  (≈ %s)" % ", ".join(ap) if ap and ap != t.get("solutions") else ""
        return "Equation: %s\n\n**Answer:** %s%s" % (_nice(t.get("equation", "")), body, extra)
    if op == "evaluate":
        exact, num = _nice(t.get("exact", "")), t.get("numeric")
        extra = "  ≈ %s" % num if num and num != t.get("exact") else ""
        return "%s = %s%s\n\n**Answer:** %s%s" % (_nice(t.get("input", "")), exact, extra, exact, extra)
    if op == "differentiate":
        return "d/d%s ( %s ) = %s\n\n**Answer:** %s" % (
            t.get("variable", "x"), _nice(t.get("input", "")), _nice(t.get("result", "")), _nice(t.get("result", "")))
    if op == "integrate":
        return "∫ %s d%s = %s\n\n**Answer:** %s" % (
            _nice(t.get("input", "")), t.get("variable", "x"), _nice(t.get("result", "")), _nice(t.get("result", "")))
    return "%s → %s\n\n**Answer:** %s" % (_nice(t.get("input", "")), _nice(t.get("result", "")), _nice(t.get("result", "")))


# ---- LLM ------------------------------------------------------------------
def generate(system_text, contents, temperature=0.7, max_tokens=2048):
    payload = {
        "system_instruction": {"parts": [{"text": system_text}]},
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens, "topP": 0.95},
    }
    req = urllib.request.Request(
        ENDPOINT % MODEL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))


def first_parts(data):
    cands = data.get("candidates", [])
    return cands[0].get("content", {}).get("parts", []) if cands else []


def parts_text(parts):
    # skip "thought" parts (Gemma/2.5 thinking) — only show the real answer
    return "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought")).strip()


def answer_general(contents):
    data = generate(GENERAL_SYS, contents, 0.7)
    return parts_text(first_parts(data)) or "Sorry, I didn't catch that — could you rephrase?"


def extract_job(question):
    data = generate(EXTRACT_SYS, [{"role": "user", "parts": [{"text": question}]}], 0.0, max_tokens=1024)
    txt = parts_text(first_parts(data))
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("no json job")
    return json.loads(m.group(0))


def answer_math(contents, last):
    """LLM prepares a job -> separate Python agent computes it deterministically."""
    job = extract_job(last)
    result = call_math_agent(job)
    return format_answer(result), True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, obj):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass

    def client_ip(self):
        xff = self.headers.get("X-Forwarded-For", "")
        return xff.split(",")[0].strip() if xff else self.client_address[0]

    def do_GET(self):
        if self.path.rstrip("/") == "/api/health":
            return self._send(200, {"ok": True, "configured": bool(API_KEY), "model": MODEL})
        self._send(404, {"error": "not_found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/api/chat":
            return self._send(404, {"error": "not_found"})
        if not API_KEY:
            return self._send(503, {"error": "not_configured",
                "message": "The assistant isn't activated yet — the site owner needs to add an API key."})
        if too_many(self.client_ip()):
            return self._send(429, {"error": "rate_limit",
                "message": "You're sending messages too quickly. Please wait a minute and try again."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(min(length, 200_000)).decode("utf-8"))
            messages = payload.get("messages", [])
            if not isinstance(messages, list) or not messages:
                return self._send(400, {"error": "bad_request", "message": "No message provided."})
        except Exception:
            return self._send(400, {"error": "bad_request", "message": "Invalid request."})

        last = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last = (m.get("content") or "")[:MAX_MSG_CHARS]
                break
        if not last.strip():
            return self._send(400, {"error": "bad_request", "message": "Empty message."})

        contents = []
        for m in messages[-MAX_HISTORY:]:
            role = "model" if m.get("role") == "assistant" else "user"
            text = (m.get("content") or "")[:MAX_MSG_CHARS]
            if text.strip():
                contents.append({"role": role, "parts": [{"text": text}]})

        agent = "math" if is_math(last) else "general"
        try:
            if agent == "math":
                try:
                    reply, used_tool = answer_math(contents, last)
                except urllib.error.HTTPError:
                    raise
                except Exception:
                    # extraction/parse fell over -> let the model answer directly
                    reply = parts_text(first_parts(generate(MATH_FALLBACK_SYS, contents, 0.2))) or \
                        "Sorry, I couldn't work that out — could you rephrase?"
                    used_tool = False
                return self._send(200, {"reply": reply, "agent": "math", "tool": used_tool})
            return self._send(200, {"reply": answer_general(contents), "agent": "general"})
        except urllib.error.HTTPError as e:
            if e.code == 429:
                return self._send(429, {"error": "rate_limit",
                    "message": "The free usage limit for the AI service has been reached. "
                               "Please try again later."})
            if e.code in (400, 401, 403):
                return self._send(502, {"error": "config",
                    "message": "The assistant is temporarily unavailable (configuration issue)."})
            return self._send(502, {"error": "upstream",
                "message": "The AI service returned an error. Please try again."})
        except (urllib.error.URLError, TimeoutError):
            return self._send(504, {"error": "timeout", "message": "The assistant timed out. Please try again."})
        except Exception:
            return self._send(500, {"error": "server", "message": "Something went wrong. Please try again."})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("chatbot on %s:%d  model=%s  configured=%s" % (HOST, PORT, MODEL, bool(API_KEY)))
    srv.serve_forever()


if __name__ == "__main__":
    main()

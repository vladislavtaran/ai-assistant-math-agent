#!/usr/bin/env python3
"""Agent backend for chrome.net.ua/chat/  (model-agnostic, stdlib only).

A visitor message is handled by a small plan -> act -> observe agent loop
(see agent/loop.py). The LLM decides, one JSON step at a time, whether to call a
deterministic TOOL or answer directly:

  - math_solver     -> separate SymPy process (exact arithmetic/calculus)
  - portfolio_search -> RAG over Vladyslav's knowledge base (answers with citations)
  - datetime        -> real current time in any timezone
  - network         -> subnet (CIDR) math and DNS lookups

Only the routing/JSON logic uses the model; the numbers, dates, and retrieved
facts come from the tools. Works with Gemini *and* Gemma (no function-calling).
The response includes a `trace` so the UI can show the agent's reasoning.
"""
import os
import sys
import json
import time
import threading
from collections import deque, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# make the sibling packages (agent/, rag/) importable no matter the CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import urllib.error  # noqa: E402
from agent import llm  # noqa: E402
from agent.loop import run_agent, build_registry  # noqa: E402

HOST = os.environ.get("CHATBOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("CHATBOT_PORT", "8090"))

MAX_MSG_CHARS = 4000
MAX_HISTORY = 12

_REGISTRY = build_registry()


def _gen_text(system_text, contents):
    # low temperature -> reliable JSON decisions and concise final answers
    return llm.gen_text(system_text, contents, temperature=0.2, max_tokens=1536)


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
            return self._send(200, {
                "ok": True,
                "configured": bool(llm.API_KEY),
                "model": llm.MODEL,
                "tools": _REGISTRY.names(),
            })
        self._send(404, {"error": "not_found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/api/chat":
            return self._send(404, {"error": "not_found"})
        if not llm.API_KEY:
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

        try:
            result = run_agent(contents, _gen_text, registry=_REGISTRY)
            return self._send(200, {
                "reply": result["reply"],
                "agent": result["agent"],
                "trace": result["trace"],
                "citations": result["citations"],
            })
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
    print("chatbot on %s:%d  model=%s  tools=%s  configured=%s" % (
        HOST, PORT, llm.MODEL, ",".join(_REGISTRY.names()), bool(llm.API_KEY)))
    srv.serve_forever()


if __name__ == "__main__":
    main()

# AI Assistant — a grounded, tool-using agent

A small web chatbot that is **not** just an LLM wrapper. A large language model
sits at the center of a **plan → act → observe loop** and decides, one step at a
time, whether to call a deterministic **tool** or answer directly. The numbers,
dates, and facts come from the tools — not from the model — and every answer
ships with a **visible reasoning trace**.

**Live demo:** https://chrome.net.ua/chat/

## What it can do

| Tool | What it does | Why a tool (not the LLM) |
|------|--------------|--------------------------|
| `math_solver` | arithmetic, solve, differentiate, integrate, simplify, factor, expand | exact results from **SymPy** in a separate process — `48273 × 19847` is computed, not guessed |
| `portfolio_search` | answers about Vladyslav's background & projects | **RAG**: retrieves from a knowledge base and answers *with citations*, or admits it doesn't know |
| `datetime` | current time in any timezone | the model has no real clock; this reads the server's |
| `network` | subnet/CIDR math and DNS lookups | deterministic, read-only networking helpers |

## How it works

```
user message
   │
   ▼   ┌───────────────────────────────────────────────┐
       │  agent loop  (agent/loop.py)                  │
       │  the LLM returns ONE JSON decision:           │
       │    {"thought": …, "tool": …, "args": …}       │  ← call a tool
       │    {"thought": …, "answer": …}                │  ← or answer
       └───────────────┬───────────────────────────────┘
        dispatch to the TOOL REGISTRY (agent/registry.py)
        ├─ math_solver      → mathagent.py   (SymPy, own process)
        ├─ portfolio_search → rag/retriever  (cosine top-k + citations)
        ├─ datetime         → zoneinfo
        └─ network          → ipaddress / socket
        │
        ▼   observation is fed back → loop continues → final answer + trace
```

Decisions are **plain JSON in the text response**, so the whole thing is
**model-agnostic** — it works with Google **Gemini** *and* **Gemma** with no
provider-specific function-calling. Answers are grounded: for anything about
Vladyslav the agent must use `portfolio_search` and cite sources; for any
calculation it must use `math_solver`.

### Quota-aware by design
The loop bounds how many model calls a message can cost (important on a free
tier):

- general / conversational → **1 call** (answers directly, no tool)
- single-tool question → **2 calls** (call the tool, then narrate its result)
- composite question → up to `MAX_STEPS + 1` calls

On HTTP 429 (free limit reached) it returns a friendly message and never a
charge; a per-IP soft rate limit protects the quota.

## Features

- **Agentic** plan → act → observe loop with a pluggable **tool registry**
- **Deterministic tools**: SymPy math (own process), portfolio RAG, timezone
  clock, CIDR/DNS
- **RAG with citations** over a small Markdown knowledge base (pure-Python
  cosine — no vector DB)
- **Visible reasoning trace** in the UI: every tool call, its args, the
  observation, and the sources
- **Model-agnostic** (Gemini *and* Gemma; no function-calling required)
- Graceful free-tier limit handling; per-IP rate limit
- Light / dark theme, mobile-friendly, Markdown rendering
- **Backend is Python standard-library only** — the sole third-party dependency
  (SymPy) lives in the isolated math agent's venv

## Repository layout

```
web/                 static frontend (index.html, chat.css, chat.js)
server/
  chatbot.py         HTTP server + wiring (stdlib)
  agent/
    loop.py          the plan → act → observe orchestrator
    registry.py      tool registry / contract
    llm.py           Gemini generate() + embed() (stdlib urllib)
    tools/           math_tool, portfolio_tool, datetime_tool, network_tool
  rag/
    build_index.py   knowledge/*.md → embeddings → portfolio_index.json
    retriever.py     load index + cosine top-k
  knowledge/         the portfolio knowledge base (Markdown)
  mathagent.py       standalone SymPy solver (own process/venv)
  selftest.py        offline tests (tools + loop with a fake LLM)
  chatbot.service    systemd unit
  requirements.txt   math agent deps (sympy)
nginx.conf.example   reverse-proxy snippet
```

## Setup

### 1. Get a model API key (free)
Create a Google AI Studio key: https://aistudio.google.com/apikey

### 2. Backend
```bash
sudo mkdir -p /opt/chatbot
sudo cp -r server/chatbot.py server/mathagent.py server/agent server/rag \
           server/knowledge /opt/chatbot/

# the math agent's own environment (SymPy)
sudo python3 -m venv /opt/chatbot/venv
sudo /opt/chatbot/venv/bin/pip install -r server/requirements.txt

# config (keep it private — chmod 600)
sudo cp server/.env.example /etc/chatbot.env
sudo nano /etc/chatbot.env          # set GEMINI_API_KEY=...
sudo chmod 600 /etc/chatbot.env

# build the portfolio RAG index (needs the key; embeds knowledge/*.md)
sudo GEMINI_API_KEY=$(grep -oP '(?<=GEMINI_API_KEY=).*' /etc/chatbot.env) \
     python3 /opt/chatbot/rag/build_index.py

# run as a service
sudo cp server/chatbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chatbot
curl -s http://127.0.0.1:8090/api/health
# {"ok":true,"configured":true,"model":"...","tools":["math_solver","portfolio_search","datetime","network"]}
```

`GEMINI_MODEL` and `EMBED_MODEL` are configurable in the env file.

### 3. Frontend + reverse proxy
Serve `web/` as static files and proxy `/chat/api/` to the backend — see
`nginx.conf.example`. The frontend posts to `/chat/api/chat` (same origin), so
the API key never reaches the browser.

## Testing (offline, no key, no quota)

```bash
cd server
# tools + agent loop, using a local sympy env for the math agent:
MATH_PY=../.venv-test/bin/python MATH_AGENT=./mathagent.py python3 selftest.py
# RAG plumbing (chunking + cosine ranking) with a local hashing embedding:
PYTHONHASHSEED=0 python3 rag/selftest_rag.py
```

## API

`POST /chat/api/chat`

```json
{ "messages": [ { "role": "user", "content": "solve 2x + 5 = 17" } ] }
```

→
```json
{
  "reply": "…",
  "agent": "math",
  "trace": [ { "type": "tool", "tool": "math_solver", "args": {…},
              "observation": "2*x + 5 = 17  →  x = 6" } ],
  "citations": []
}
```

(or HTTP 429 `{ "error": "rate_limit", "message": "…" }` when the free limit is hit).

## License

[MIT](LICENSE) © 2026 Vladyslav Taran

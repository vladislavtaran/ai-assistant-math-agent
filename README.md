# AI Assistant — a grounded, tool-using agent

A small web chatbot that is **not** just an LLM wrapper. A large language model
sits at the center of a **plan → act → observe loop** and decides, one step at a
time, whether to call a deterministic **tool** or answer directly. The numbers,
dates, and facts come from the tools — not from the model — and every answer
ships with a **visible reasoning trace**.

**Live demo:** https://chrome.net.ua/chat/

**Runs in two places:** a self-hosted Linux VPS (nginx, TLS, hardened systemd units) and
Google **Cloud Run**, deployed by GitHub Actions with **no service-account key anywhere**.
See [Deployment](#deployment).

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
- single deterministic tool (math / time / network) → **1 call** (the model marks
  the call `final` and the tool's exact output is returned as-is)
- portfolio / grounded answer → **2 calls** (retrieve, then narrate with citations)
- composite question → up to `MAX_STEPS + 1` calls

**Model fallback chain** further stretches the free tier: set `GEMINI_MODELS` to a
comma-separated list and each model's *separate* daily bucket is tried in turn, so
one model's 429 transparently falls through to the next (keep any model another
app uses off the list so the chat never spends its quota).

On HTTP 429 (free limit reached) it returns a friendly message and never a
charge; a per-IP soft rate limit protects the quota.

> **Heads-up on the free tier.** Google's free tier for `gemini-2.5-flash-lite`
> currently allows only **~20 model requests per day** per project
> (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`). Because each user
> message costs 1–4 of those, the public demo is good for roughly a handful of
> questions per day before it shows the "limit reached" notice (resets at
> midnight US Pacific). For heavier use, raise `GEMINI_MODEL` to a
> higher-quota/paid model or enable billing.

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
mcp-server/          MCP server exposing the deterministic tools (see its README)
nginx.conf.example   reverse-proxy snippet
```

## Deployment

The agent runs in two environments from the same source tree.

**1. Self-hosted VPS** — nginx terminating TLS and reverse-proxying `/chat/api/`, the backend
under systemd as a dedicated service account with `NoNewPrivileges`, `ProtectSystem=strict`,
`ProtectHome` and `PrivateTmp`. Secrets live outside the repo. Deployed over SSH by GitHub
Actions with a concurrency guard, a post-deploy health check, and a commit-hash verification
step that fails the build if the live version does not match. This is what serves
[chrome.net.ua/chat/](https://chrome.net.ua/chat/).

**2. Google Cloud Run** — containerised, `europe-central2`, deployed by
[`.github/workflows/deploy-cloudrun.yml`](.github/workflows/deploy-cloudrun.yml) on every push
to `main`:

- **Keyless CI** via Workload Identity Federation. GitHub presents a short-lived OIDC token,
  Google exchanges it for short-lived credentials. No service-account JSON key is created,
  stored or rotated, because none exists. The provider carries an attribute condition
  restricting it to this repository owner, and a second binding narrows it to this repo alone.
- **Two least-privilege service accounts.** The runtime identity holds `secretAccessor` on a
  single secret and nothing else — it cannot deploy. The CI identity can push images and deploy
  but cannot read that secret.
- **Secret Manager** for the model API key, mounted at runtime rather than baked into the image.
- **Artifact Registry**, scale-to-zero (`min-instances 0`, `max-instances 3`), and a smoke test
  that curls `/api/health` after every deploy.
- **Not publicly invocable.** `allUsers` was removed after verification: an unauthenticated LLM
  endpoint is an unbounded spend vector, and `--max-instances` caps compute but not token usage.

[`CLOUDRUN-RUNBOOK.md`](CLOUDRUN-RUNBOOK.md) is the reproducible procedure.
[`DEPLOYMENT-LOG.md`](DEPLOYMENT-LOG.md) is the record of the real run, including the three
failures that only surfaced on execution — a `--set-env-vars` delimiter bug latent in the
workflow for a month, hardcoded VPS paths that do not exist in a container, and a reserved
`gh` CLI environment variable collision.

## Evals

The agent is gated by an eval suite, not just unit tests. `server/evals/cases.json` holds cases
of the shape *prompt → recorded model responses → expectations*; `server/evals/run_evals.py`
replays each one through the real agent loop and scores it on four things:

- **tool selection** — did a maths question reach `math_solver`, did a portfolio question reach
  `portfolio_search`, and did a conversational one use no tool at all
- **model-call budget** — `run_agent` already returns `calls`; a case fails if it exceeds its
  budget. On a free tier that caps daily requests, cost per answer *is* a correctness property
- **answer content** — assertions on the final reply
- **citation grounding** — portfolio answers must come back with sources attached

**It runs on every push and costs nothing.** The loop takes its text-generation function as a
parameter, so the suite injects a replayer over recorded responses: no API key, no network, no
quota. Retrieval is exercised offline too — the project's deterministic lexical hashing
embedding builds a throwaway index from `knowledge/*.md` in-process, so grounding is testable
without embedding credits.

The first run paid for itself twice. It caught that `portfolio_search` **silently** drops to an
ungrounded answer when the index is missing — no error, no failure, just a reply with no
citations — and that the `min_score` floor is tuned to semantic embeddings, so swapping the
embedder empties the result set without raising anything.

```bash
cd server
MATH_PY=../.venv-test/bin/python MATH_AGENT=./mathagent.py PYTHONHASHSEED=0 \
  python3 evals/run_evals.py
```

See [`.github/workflows/evals.yml`](.github/workflows/evals.yml).

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

## MCP server

The project's deterministic tools (math, date/time, subnet/DNS) are also exposed
as a **Model Context Protocol** server, so any MCP client — Claude Desktop,
Claude Code, Cursor — can call them. It needs no LLM, API key, or quota, so it's
free to run locally. See [`mcp-server/`](mcp-server/) for setup and client config.

## License

[MIT](LICENSE) © 2026 Vladyslav Taran

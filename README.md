# AI Assistant with a dedicated Math Agent

A small web chatbot that answers general questions with an LLM **and routes
mathematical questions to a separate, deterministic Python math agent** — so
calculations are exact, not "guessed" by the model.

**Live demo:** https://chrome.net.ua/chat/

## How it works

```
user message
   │
   ▼  router (keyword / expression classifier — no API call)
general ─────────────────────────► LLM answers directly
math ──► LLM PREPARES a job  ──┐    (structured JSON, e.g.
                               ▼     {"operation":"solve","expression":"2*x+5=17"})
                    ⚙ mathagent.py  — separate Python process (SymPy)
                               ▼     exact result
                    deterministic, formatted answer
```

The language model only **prepares** a job and **calls** the math agent; the
numbers come from **SymPy**, not the LLM. That's why things like
`48273 × 19847 = 958074231` are guaranteed correct.

## Features

- **General chat** + automatic **math routing** with a step‑by‑step solver
- Deterministic math: `solve`, `evaluate`, `differentiate`, `integrate`,
  `simplify`, `factor`, `expand`
- **Model‑agnostic** — works with Google **Gemini** *and* **Gemma** models
  (no function‑calling required; uses structured‑output extraction)
- Graceful **free‑tier limit** handling (HTTP 429 → friendly message)
- Per‑IP soft rate limit to protect your quota
- Light / dark theme, mobile‑friendly, Markdown rendering
- Backend is **stdlib‑only** Python; the math agent runs in its own venv

## Repository layout

```
web/      static frontend (index.html, chat.css, chat.js)
server/   chatbot.py        — HTTP proxy + router (Python stdlib)
          mathagent.py      — standalone SymPy solver (own process)
          chatbot.service   — systemd unit
          requirements.txt  — math agent deps (sympy)
          .env.example      — copy to /etc/chatbot.env and add your key
nginx.conf.example          — reverse‑proxy snippet
```

## Setup

### 1. Get a model API key (free)
Create a Google AI Studio key: https://aistudio.google.com/apikey

### 2. Backend
```bash
sudo mkdir -p /opt/chatbot
sudo cp server/chatbot.py server/mathagent.py /opt/chatbot/

# the math agent's own environment (SymPy)
sudo python3 -m venv /opt/chatbot/venv
sudo /opt/chatbot/venv/bin/pip install -r server/requirements.txt

# config (keep it private — chmod 600)
sudo cp server/.env.example /etc/chatbot.env
sudo nano /etc/chatbot.env        # set GEMINI_API_KEY=...
sudo chmod 600 /etc/chatbot.env

# run as a service
sudo cp server/chatbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chatbot
curl -s http://127.0.0.1:8090/api/health   # {"ok":true,"configured":true,...}
```

`GEMINI_MODEL` is configurable in the env file. `gemma-4-26b-a4b-it` has a very
generous free daily quota; `gemini-2.5-flash-lite` / `gemini-2.5-flash` also work.

### 3. Frontend + reverse proxy
Serve `web/` as static files and proxy `/chat/api/` to the backend. See
`nginx.conf.example`. The frontend posts to `/chat/api/chat` (same origin), so
the API key never reaches the browser.

## API

`POST /chat/api/chat`

```json
{ "messages": [ { "role": "user", "content": "solve 2x + 5 = 17" } ] }
```

→ `{ "reply": "...", "agent": "math", "tool": true }`
(or HTTP 429 `{ "error": "rate_limit", "message": "..." }` when the free limit is hit).

## License

[MIT](LICENSE) © 2026 Vladyslav Taran

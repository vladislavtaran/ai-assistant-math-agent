# Project — OSI Explorer

OSI Explorer (chrome.net.ua/osi/) dissects a real network request across the 7
OSI layers. Enter a URL and it performs a real UDP DNS query, TCP connect, TLS
handshake (reading cipher and certificate), and HTTP exchange, then honestly
labels what is real (layers 3–7), reconstructed (layer 4 bytes), or illustrated
(layers 1–2). It now dissects eight protocol families — HTTP(S), FTP(S), SMTP(S),
IMAP(S), POP3(S), SSH, and WebSocket — via a curated scheme→port allow-list with
read-only banners. The backend is Python standard-library only and is
SSRF-hardened (http/https on ports 80/443, public IPs only, connecting to a
validated IP). It showcases protocol forensics. Source is public on GitHub
(github.com/vladislavtaran/osi-explorer).

# Project — AI Assistant (this chatbot)

The AI Assistant (chrome.net.ua/chat/) is this chatbot. It is a small,
model-agnostic agent: a plan → act → observe loop where a large language model
decides, one JSON step at a time, whether to call a deterministic tool or answer
directly. Its tools are: math_solver (a separate SymPy process for exact
arithmetic and calculus), portfolio_search (retrieval-augmented grounding over
Vladyslav's background and projects, with citations), datetime (real current time
in any timezone), and network (subnet/CIDR math and DNS lookups). The design
principle is that numbers, dates, and facts come from deterministic tools, not
from the model — so a calculation like 48273 × 19847 is guaranteed exact. It works
with both Google Gemini and Gemma models without provider function-calling
because decisions are plain JSON. The backend is Python standard-library only;
each answer includes a visible reasoning trace. It demonstrates LLM API
integration, tool/function-calling, and RAG. Source is public on GitHub
(github.com/vladislavtaran/ai-assistant-math-agent).

# Project — QR Generator

The QR Generator (chrome.net.ua/qr/) is a client-side text→QR code generator with
custom module and corner shapes, colors, a center logo, and frames, composited to
PNG on a canvas (SVG export is also available). It has a full CI/CD pipeline:
GitHub Actions runs syntax checks, writes commit provenance, and auto-deploys to
the server as a locked-down deploy user on every push to main. Repo:
github.com/vladislavtaran/qr-code-generator.

# Project — API Sandbox

The API Sandbox (chrome.net.ua/api/, docs at /api/docs) is a self-hosted testing
REST API built with FastAPI/Uvicorn, documented with OpenAPI/Swagger. It offers
SQLite-backed CRUD plus health/version endpoints and demonstrates production API
conventions: X-API-Key auth on writes, per-IP rate limiting with X-RateLimit
headers, RFC 9457 problem+json errors, ETag/If-None-Match 304s, idempotency keys,
RFC 8288 Link pagination, an audit log, and storage/row guards. Repo:
github.com/vladislavtaran/api-sandbox.

# Projects — overview

Vladyslav's portfolio site chrome.net.ua is itself built and deployed by him.
The publicly listed projects are the OSI Explorer (protocol forensics), the AI
Assistant (LLM API integration with function-calling and a math agent), the QR
Generator (CI/CD-deployed QR tool), and the API Sandbox (REST API with
OpenAPI/Swagger, key-auth, rate-limiting, and audit logging). Together they
showcase his focus on Networks, Cloud, APIs, and AI.

#!/usr/bin/env python3
"""Google Generative Language API helpers (stdlib only).

Two calls are used by the agent:
  - generate()  -> text generation (model-agnostic: Gemini *and* Gemma)
  - embed()     -> a single embedding vector (for portfolio RAG)

Kept dependency-free (urllib) so the whole backend runs on the system Python
with no venv — the only third-party dependency lives in the separate math agent.
"""
import os
import json
import time
import urllib.request
import urllib.error

API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite").strip()
EMBED_MODEL = os.environ.get("EMBED_MODEL", "gemini-embedding-001").strip()

_BASE = "https://generativelanguage.googleapis.com/v1beta/models/%s:%s"
_GEN_TIMEOUT = int(os.environ.get("GEN_TIMEOUT", "45"))
_EMBED_TIMEOUT = int(os.environ.get("EMBED_TIMEOUT", "20"))
_RETRIES = 3
_BACKOFF = 0.4


def _post(url, payload, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def generate(system_text, contents, temperature=0.7, max_tokens=2048):
    """One text-generation call. Retries transient upstream errors; re-raises 429
    immediately (quota is exhausted — retrying only makes it worse)."""
    gen_cfg = {"temperature": temperature, "maxOutputTokens": max_tokens, "topP": 0.95}
    # gemini-2.5 models "think" by default; those reasoning tokens can eat the
    # output budget and truncate our JSON decision. Disable it (ignored elsewhere).
    if MODEL.startswith("gemini-2.5"):
        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
    payload = {
        "system_instruction": {"parts": [{"text": system_text}]},
        "contents": contents,
        "generationConfig": gen_cfg,
    }
    url = _BASE % (MODEL, "generateContent")
    last = None
    for attempt in range(_RETRIES):
        try:
            return _post(url, payload, _GEN_TIMEOUT)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise
            last = e
            if e.code in (400, 401, 403):  # config errors won't fix themselves
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(_BACKOFF * (attempt + 1))
    if last:
        raise last
    raise RuntimeError("generate: no response")


def embed(text):
    """Return the embedding vector (list[float]) for one piece of text."""
    payload = {"model": "models/%s" % EMBED_MODEL,
               "content": {"parts": [{"text": text}]}}
    url = _BASE % (EMBED_MODEL, "embedContent")
    last = None
    for attempt in range(_RETRIES):
        try:
            data = _post(url, payload, _EMBED_TIMEOUT)
            return data.get("embedding", {}).get("values", [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise
            last = e
            if e.code in (400, 401, 403):
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(_BACKOFF * (attempt + 1))
    if last:
        raise last
    raise RuntimeError("embed: no response")


def first_parts(data):
    cands = data.get("candidates", [])
    return cands[0].get("content", {}).get("parts", []) if cands else []


def parts_text(parts):
    """Join text parts, skipping 'thought' parts (Gemma / 2.5 thinking output)."""
    return "".join(
        p.get("text", "") for p in parts if "text" in p and not p.get("thought")
    ).strip()


def gen_text(system_text, contents, temperature=0.7, max_tokens=2048):
    """Convenience: generate() + extract the plain answer text."""
    return parts_text(first_parts(generate(system_text, contents, temperature, max_tokens)))

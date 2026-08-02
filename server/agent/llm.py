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

# Fallback chain: each model has its OWN free-tier daily bucket, so when the
# first one is exhausted (429) we transparently try the next. GEMINI_MODELS is a
# comma-separated list; it falls back to the single GEMINI_MODEL for compat.
# NOTE: keep the game's model out of this list so the chat never spends the
# game's quota (the game is the priority workload).
MODELS = [m.strip() for m in os.environ.get("GEMINI_MODELS", MODEL).split(",") if m.strip()]

_BASE = "https://generativelanguage.googleapis.com/v1beta/models/%s:%s"
_GEN_TIMEOUT = int(os.environ.get("GEN_TIMEOUT", "45"))
_EMBED_TIMEOUT = int(os.environ.get("EMBED_TIMEOUT", "20"))
_RETRIES = 2          # transient retries per model before moving to the next
_BACKOFF = 0.4


def _thinks(model):
    # gemini-2.5 "thinks" by default and *supports* disabling it (thinkingBudget:0),
    # which we want so reasoning tokens don't truncate our JSON decision. The
    # gemini-3.x models reject that arg (400), so we don't send it to them — the
    # per-model variant retry in generate() is the safety net if any model differs.
    return model.startswith("gemini-2.5")


def _post(url, payload, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": API_KEY},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def generate(system_text, contents, temperature=0.7, max_tokens=2048):
    """Generate text, walking the MODELS fallback chain.

    Each model is tried in order. A 429 (that model's daily/minute quota) or a
    model-specific config error (400/403 — e.g. an unsupported param) moves on to
    the next model; transient errors (timeout / 5xx) retry a couple of times
    first. Only if EVERY model is exhausted do we raise — a 429 if any model was
    rate-limited, so the UI still shows the friendly "limit reached" message.
    The winning response is tagged with `_model`.
    """
    errors = []
    saw_429 = False
    base_cfg = {"temperature": temperature, "maxOutputTokens": max_tokens, "topP": 0.95}
    for model in MODELS:
        url = _BASE % (model, "generateContent")
        # config variants to try for this model: with thinking disabled (where it
        # helps), then a plain variant — some models (e.g. gemini-3.5-flash-lite)
        # reject thinkingConfig with a 400, so we retry without it rather than skip.
        cfgs = []
        if _thinks(model):
            cfgs.append(dict(base_cfg, thinkingConfig={"thinkingBudget": 0}))
        cfgs.append(dict(base_cfg))

        skip_model = False
        for cfg in cfgs:
            if skip_model:
                break
            payload = {
                "system_instruction": {"parts": [{"text": system_text}]},
                "contents": contents,
                "generationConfig": cfg,
            }
            for attempt in range(_RETRIES):
                try:
                    data = _post(url, payload, _GEN_TIMEOUT)
                    data["_model"] = model
                    return data
                except urllib.error.HTTPError as e:
                    errors.append(e)
                    if e.code == 429:
                        saw_429 = True
                        skip_model = True
                        break  # quota gone for this model -> next model
                    if e.code == 400:
                        break  # bad arg (maybe thinkingConfig) -> try next cfg variant
                    if e.code in (401, 403):
                        skip_model = True
                        break  # auth/config -> next model
                    # 5xx -> transient, retry this variant
                except (urllib.error.URLError, TimeoutError) as e:
                    errors.append(e)
                time.sleep(_BACKOFF * (attempt + 1))
    if saw_429:
        # surface a 429 so callers show the free-limit message
        for e in errors:
            if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                raise e
    if errors:
        raise errors[-1]
    raise RuntimeError("generate: no model responded")


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


def parts_decision(parts):
    """Return the model's decision as a JSON string the loop can parse.

    gemini-3.x models often answer a "which tool?" prompt with a NATIVE
    functionCall part instead of our text JSON — e.g.
      {"functionCall": {"name": "portfolio_search", "args": {"query": "..."}}}
    That is exactly our decision in a different envelope, so we render it back to
    {"tool": name, "args": args}. Otherwise we fall back to the joined text.
    """
    for p in parts:
        fc = p.get("functionCall")
        if fc and fc.get("name"):
            return json.dumps({"tool": fc["name"], "args": fc.get("args", {}) or {}})
    return parts_text(parts)


def gen_text(system_text, contents, temperature=0.7, max_tokens=2048):
    """Convenience: generate() + extract the decision/answer (text or functionCall)."""
    return parts_decision(first_parts(generate(system_text, contents, temperature, max_tokens)))

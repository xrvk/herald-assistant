import time
import requests
from collections import deque

from scout_report.config import (
    TZ, OLLAMA_URL, OLLAMA_MODEL, GEMINI_MODEL,
    _get_gemini_client, get_backend,
    SYSTEM_PROMPT, HISTORY_DAYS,
    CONV_HISTORY_TURNS, CONV_HISTORY_TTL, CONV_HISTORY_CTX_BUMP, _CONV_ANSWER_TRUNC,
    _OLLAMA_CTX_BASE, _OLLAMA_CTX_WITH_PAST, _MAX_OUTPUT_TOKENS,
    _GEMINI_MAX_RETRIES, _GEMINI_RETRY_WAIT_BASE,
    PAST_PATTERNS, FUTURE_PATTERNS, CLASSIFY_PROMPT,
)

# ── Question classification ──

def classify_question(question):
    """Classify whether a question needs past events, future events, or both.
    
    Returns 'past' or 'future'.
    Tier 1: fast keyword regex. Tier 2: LLM fallback for ambiguous.
    """
    has_past = bool(PAST_PATTERNS.search(question))
    has_future = bool(FUTURE_PATTERNS.search(question))

    if has_past and not has_future:
        return "past"
    if has_future and not has_past:
        return "future"
    if has_past and has_future:
        return "past"  # mixed signals → include history to be safe

    # Tier 2: LLM fallback for ambiguous questions
    try:
        if get_backend() == "gemini":
            resp = _get_gemini_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=question,
                config={
                    "system_instruction": CLASSIFY_PROMPT,
                    "temperature": 0,
                    "max_output_tokens": 16,
                    "thinking_config": {"thinking_budget": 0},
                },
            )
            answer = (resp.text or "").strip().lower()
        else:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": CLASSIFY_PROMPT},
                        {"role": "user", "content": question},
                    ],
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0, "num_predict": 16, "num_ctx": 512},
                },
                timeout=15,
            )
            resp.raise_for_status()
            answer = resp.json()["message"]["content"].strip().lower()
        if "past" in answer or "both" in answer:
            return "past"
        return "future"
    except Exception as e:
        error_msg = str(e).lower()
        if "429" in error_msg or "resource_exhausted" in error_msg:
            print(f"Classification skipped (rate limited) — defaulting to future context")
        else:
            print(f"Classification fallback failed: {e}")
        return "future"  # safe default

# ── Conversation history helpers ──

# Keyed by (channel_id, user_id) → deque of (question, answer, timestamp)
_conv_history: dict[tuple, deque] = {}

def _get_history(channel_id, user_id):
    """Return non-stale conversation history as [(question, answer), ...]."""
    key = (channel_id, user_id)
    hist = _conv_history.get(key)
    if not hist:
        return []
    now = time.time()
    # Filter to non-stale entries
    valid = [(q, a) for q, a, ts in hist if (now - ts) < CONV_HISTORY_TTL]
    if not valid:
        del _conv_history[key]
    return valid

def _store_exchange(channel_id, user_id, question, answer):
    """Store a Q&A exchange, truncating the answer for token budget."""
    key = (channel_id, user_id)
    if key not in _conv_history:
        _conv_history[key] = deque(maxlen=CONV_HISTORY_TURNS)
    truncated = answer[:_CONV_ANSWER_TRUNC] + "…" if len(answer) > _CONV_ANSWER_TRUNC else answer
    _conv_history[key].append((question, truncated, time.time()))

def _estimate_tokens(text):
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4

# ── LLM ──

def ask_llm(question, calendar_context, include_past=False, history=None):
    """Send a question + calendar context to Ollama and return the response."""
    num_ctx = _OLLAMA_CTX_WITH_PAST if include_past else _OLLAMA_CTX_BASE
    system_prompt = f"{SYSTEM_PROMPT}\n\nCALENDAR DATA:\n{calendar_context}"

    # Build multi-turn messages: [system, ...history pairs..., current user]
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        # Token budget check — only bump num_ctx if history doesn't fit in base.
        # Avoiding unnecessary bumps keeps Ollama's KV cache hot (~5x faster prompt eval).
        num_predict = _MAX_OUTPUT_TOKENS
        base_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(question)
        remaining = num_ctx - num_predict - base_tokens
        trimmed = []
        for q, a in history:
            cost = _estimate_tokens(q) + _estimate_tokens(a)
            if remaining - cost < 0:
                break
            remaining -= cost
            trimmed.append((q, a))
        # If history was trimmed AND a ctx bump is configured, retry with more room
        if len(trimmed) < len(history) and CONV_HISTORY_CTX_BUMP > 0:
            num_ctx += CONV_HISTORY_CTX_BUMP
            remaining = num_ctx - num_predict - base_tokens
            trimmed = []
            for q, a in history:
                cost = _estimate_tokens(q) + _estimate_tokens(a)
                if remaining - cost < 0:
                    break
                remaining -= cost
                trimmed.append((q, a))
            print(f"[Conv] Bumped num_ctx to {num_ctx} to fit history")
        if len(trimmed) < len(history):
            print(f"[Conv] Trimmed history from {len(history)} to {len(trimmed)} exchanges (token budget)")
        for q, a in trimmed:
            messages.append({"role": "user", "content": q})
            messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": question})

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": _MAX_OUTPUT_TOKENS,
                    "num_ctx": num_ctx,
                },
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except requests.exceptions.ConnectionError:
        return "🔌 LLM is offline — Ollama may not be running or is unreachable."
    except requests.exceptions.Timeout:
        return "⏳ LLM timed out — the model may still be loading. Try again in a minute."
    except Exception as e:
        print(f"LLM error: {e}")
        return "❌ Something went wrong with the LLM. Check server logs."

def ask_gemini(question, calendar_context, history=None):
    """Send a question + calendar context to Gemini and return the response."""
    from google.genai import types
    system_prompt = f"{SYSTEM_PROMPT}\n\nCALENDAR DATA:\n{calendar_context}"
    # Build multi-turn contents list
    contents = []
    if history:
        for q, a in history:
            contents.append(types.Content(role="user", parts=[types.Part(text=q)]))
            contents.append(types.Content(role="model", parts=[types.Part(text=a)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))
    last_err = None
    for attempt in range(_GEMINI_MAX_RETRIES):
        try:
            resp = _get_gemini_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0.3,
                    "max_output_tokens": _MAX_OUTPUT_TOKENS,
                },
            )
            text = resp.text
            if text:
                return text
            return "I couldn't generate a response. Try rephrasing your question."
        except Exception as e:
            last_err = e
            error_msg = str(e).lower()
            if "429" in error_msg or "resource_exhausted" in error_msg:
                if attempt < _GEMINI_MAX_RETRIES - 1:
                    wait = (attempt + 1) * _GEMINI_RETRY_WAIT_BASE
                    print(f"Gemini rate limited, retrying in {wait}s (attempt {attempt + 1}/3)")
                    time.sleep(wait)
                    continue
                return "⏳ Gemini rate limit reached — try again in a minute."
            if "api key" in error_msg or "401" in error_msg or "403" in error_msg:
                return "🔑 Gemini API key is invalid or expired. Check your GEMINI_API_KEY."
            print(f"Gemini error: {e}")
            return "❌ Something went wrong with Gemini. Check server logs."
    print(f"Gemini error after retries: {last_err}")
    return "❌ Something went wrong with Gemini. Check server logs."

def ask_backend(question, calendar_context, include_past=False, history=None):
    """Route LLM calls to the configured backend."""
    if get_backend() == "gemini":
        return ask_gemini(question, calendar_context, history=history)
    return ask_llm(question, calendar_context, include_past, history=history)

import asyncio

from scout_report.config import (
    OLLAMA_MODEL, GEMINI_MODEL, HISTORY_DAYS,
    get_backend, set_backend,
)
from scout_report.llm import (
    classify_question, _get_history, _store_exchange, _conv_history,
    ask_backend,
)
from scout_report.calendar import build_context


def normalize_text(text):
    """Normalize smart quotes/punctuation that mobile keyboards may inject."""
    return (text
            .replace("\u201c", '"').replace("\u201d", '"')
            .replace("\u2018", "'").replace("\u2019", "'"))


_BACKEND_MAP = {"1": "ollama", "2": "gemini"}


async def handle_backend_command(parts, hist_key, reply_fn, *, markdown=True):
    """Handle !backend show/switch. Returns True if handled.

    Args:
        parts: Split command parts (e.g. ["!backend"] or ["!backend", "2"])
        hist_key: Conversation history key tuple
        reply_fn: async callable(str) to send the response
        markdown: If True, use Discord markdown formatting; else plain text
    """
    if len(parts) == 1:
        current = get_backend()
        if markdown:
            text = (
                f"1. **Ollama** - model: `{OLLAMA_MODEL}`{' <-- Current' if current == 'ollama' else ''}\n"
                f"2. **Gemini** - model: `{GEMINI_MODEL}`{' <-- Current' if current == 'gemini' else ''}\n\n"
                "Switch with: `!backend 1` or `!backend 2`"
            )
        else:
            text = (
                f"1. Ollama - model: {OLLAMA_MODEL}{' <-- Current' if current == 'ollama' else ''}\n"
                f"2. Gemini - model: {GEMINI_MODEL}{' <-- Current' if current == 'gemini' else ''}\n\n"
                "Switch with: !backend 1 or !backend 2"
            )
        await reply_fn(text)
    else:
        target = _BACKEND_MAP.get(parts[1].strip(), parts[1].strip().lower())
        try:
            set_backend(target)
            # Clear conversation history on backend switch
            _conv_history.pop(hist_key, None)
            if markdown:
                await reply_fn(f"Switched to **{get_backend()}**")
            else:
                await reply_fn(f"Switched to {get_backend()}")
            print(f"[Backend] Switched to {get_backend()}")
        except (ValueError, RuntimeError) as e:
            await reply_fn(f"Failed: {e}")
    return True


async def process_question(question, hist_key, reply_fn, *, typing_ctx=None):
    """Shared chat pipeline: classify → history → context → LLM → store → reply.

    Args:
        question: The user's question text (already normalized)
        hist_key: Tuple for conversation history keying (e.g. (channel_id, user_id) or ("signal", phone))
        reply_fn: async callable(str) to send the response
        typing_ctx: Optional async context manager for typing indicators
    """
    print(f"[Chat] {hist_key}: {question}")
    try:
        async def _do_work():
            # Classify whether the question needs past events (skip if history disabled)
            include_past = False
            if HISTORY_DAYS > 0:
                classification = await asyncio.to_thread(classify_question, question)
                include_past = classification == "past"
                if include_past:
                    print(f"[Chat] Including past events (classification: {classification})")
            # Retrieve conversation history for follow-ups
            history = _get_history(hist_key[0], hist_key[1])
            if history:
                print(f"[Conv] Including {len(history)} previous exchange(s)")
            # Run blocking calendar fetch + LLM call in a thread
            calendar_context = await asyncio.to_thread(build_context, include_past)
            return await asyncio.to_thread(ask_backend, question, calendar_context, include_past, history)

        if typing_ctx:
            async with typing_ctx:
                answer = await _do_work()
        else:
            answer = await _do_work()

        # Store exchange for future follow-ups
        _store_exchange(hist_key[0], hist_key[1], question, answer)

        print(f"[Chat] Reply ({len(answer)} chars): {answer[:100]}...")
        await reply_fn(answer)
    except Exception as e:
        print(f"[Chat] Error handling message {hist_key}: {e}")
        await reply_fn("❌ Something went wrong processing your question. Please try again.")

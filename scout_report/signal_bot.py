import asyncio
import json
import requests
from urllib.parse import quote

import websockets

from scout_report.config import (
    _signal_api_url, _signal_from, _signal_to,
    _signal_chat_enabled, _SIGNAL_MSG_LIMIT,
    _mask_number,
)
from scout_report.chat import normalize_text, handle_backend_command, process_question


async def _send_reply(recipient, text):
    """Send a Signal message via signal-cli REST API."""
    if len(text) > _SIGNAL_MSG_LIMIT:
        text = text[:_SIGNAL_MSG_LIMIT] + "\n…(truncated)"

    def _post():
        requests.post(
            f"{_signal_api_url}/v2/send",
            json={
                "message": text,
                "number": _signal_from,
                "recipients": [recipient],
            },
            timeout=30,
        )

    try:
        await asyncio.to_thread(_post)
    except Exception as e:
        print(f"[Signal] Failed to send reply to {_mask_number(recipient)}: {e}")


async def _handle_envelope(envelope):
    """Process an incoming Signal envelope."""
    # Extract sender and message text from the envelope
    source = envelope.get("source") or envelope.get("sourceNumber")
    data_message = envelope.get("dataMessage")
    if not source or not data_message:
        return
    text = data_message.get("message")
    if not text:
        return

    # Only allow the configured recipient to chat back
    if source != _signal_to:
        return

    # Ignore group messages — only DMs
    if data_message.get("groupInfo"):
        return

    question = normalize_text(text.strip())
    if not question:
        return

    hist_key = ("signal", source)

    # !backend command
    if question.lower().startswith("!backend"):
        parts = question.split(maxsplit=1)
        async def reply_fn(msg):
            await _send_reply(source, msg)
        await handle_backend_command(parts, hist_key, reply_fn, markdown=False)
        return

    print(f"[Signal] {_mask_number(source)}: {question}")

    async def reply_fn(msg):
        await _send_reply(source, msg)

    await process_question(question, hist_key, reply_fn)


async def ws_loop():
    """Persistent WebSocket connection to signal-cli REST API for receiving messages."""
    if not _signal_chat_enabled:
        return

    from_encoded = quote(_signal_from, safe="")
    ws_url = _signal_api_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    ws_url = f"{ws_url}/v1/receive/{from_encoded}"

    print(f"Signal interactive chat enabled: {_mask_number(_signal_from)} (receives from {_mask_number(_signal_to)})")

    backoff = 1
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=30, ping_timeout=10) as ws:
                backoff = 1  # reset on successful connection
                print(f"[Signal] WebSocket connected")
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                        envelope = data.get("envelope")
                        if envelope:
                            await _handle_envelope(envelope)
                    except json.JSONDecodeError:
                        pass
                    except Exception as e:
                        print(f"[Signal] Error handling message: {e}")
        except asyncio.CancelledError:
            print("[Signal] WebSocket task cancelled")
            return
        except Exception as e:
            print(f"[Signal] WebSocket disconnected ({e}), reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

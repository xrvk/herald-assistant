"""
Live integration tests for Scout Report Discord bot.

This script connects to Discord as a TEST client, sends commands to the bot,
and validates responses. Requires a SECOND bot/user token (TEST_BOT_TOKEN).

Setup:
  1. Create a second Discord bot (or use a test account).
  2. Invite it to the same server/channel as Scout Report.
  3. Set env vars:
       TEST_BOT_TOKEN    — token for the test client
       TEST_CHANNEL_ID   — channel where Scout Report listens
       TEST_TIMEOUT      — seconds to wait for bot reply (default: 30)

Run:
  # From project root:
  source .env && python tests/test_integration.py

  # Or with explicit vars:
  TEST_BOT_TOKEN=xxx TEST_CHANNEL_ID=123 python tests/test_integration.py
"""

import os
import sys
import asyncio
import random
import time
from datetime import datetime

import discord

# ── Configuration ──
TEST_BOT_TOKEN = os.getenv("TEST_BOT_TOKEN")
TEST_CHANNEL_ID = os.getenv("TEST_CHANNEL_ID") or os.getenv("DISCORD_CHANNEL_ID")
TEST_TIMEOUT = int(os.getenv("TEST_TIMEOUT", "30"))

if not TEST_BOT_TOKEN:
    print("ERROR: TEST_BOT_TOKEN is required.")
    print("Create a second Discord bot and set TEST_BOT_TOKEN to its token.")
    print("This test client sends commands and validates the bot's responses.")
    sys.exit(1)

if not TEST_CHANNEL_ID:
    print("ERROR: TEST_CHANNEL_ID (or DISCORD_CHANNEL_ID) is required.")
    sys.exit(1)

TEST_CHANNEL_ID = int(TEST_CHANNEL_ID)

# ── Test framework ──

class TestResult:
    def __init__(self, name, passed, detail=""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        detail = f" — {self.detail}" if self.detail else ""
        return f"  [{status}] {self.name}{detail}"


class BotTester:
    """Integration test runner for the Scout Report bot."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)
        self.results: list[TestResult] = []
        self.channel = None
        self._response_event = asyncio.Event()
        self._last_response = None
        self._waiting_for_reply_to = None
        # Track the bot user (the bot we're testing, not ourselves)
        self._bot_user_id = None

    async def send_and_wait(self, command: str, timeout: int = TEST_TIMEOUT) -> str | None:
        """Send a command and wait for the bot's reply."""
        self._response_event.clear()
        self._last_response = None

        msg = await self.channel.send(command)
        self._waiting_for_reply_to = msg.id

        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
            return self._last_response
        except asyncio.TimeoutError:
            return None

    async def run_tests(self):
        """Run all integration tests sequentially."""
        print(f"\n{'═' * 60}")
        print(f"  Scout Report — Live Integration Tests")
        print(f"  Channel: {TEST_CHANNEL_ID}")
        print(f"  Timeout: {TEST_TIMEOUT}s per command")
        print(f"{'═' * 60}\n")

        await self.test_cal_command()
        await self.test_llm_command()
        await self.test_switch_to_gemini()
        await self.test_ask_gemini_weekday()
        await self.test_switch_to_ollama()
        await self.test_ask_ollama_weekday()
        await self.test_switch_back_original()
        await self.test_ignored_events_audit()

        # Print summary
        print(f"\n{'═' * 60}")
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        print(f"  Results: {passed}/{total} passed")
        print(f"{'═' * 60}")
        for r in self.results:
            print(r)
        print()

        if passed < total:
            print(f"  {total - passed} test(s) FAILED")
        else:
            print("  All tests passed!")

    # ── Individual tests ──

    async def test_cal_command(self):
        """!cal — list connected calendars."""
        print("  Testing !cal ...")
        resp = await self.send_and_wait("!cal")
        if resp is None:
            self.results.append(TestResult("!cal", False, "No response (timeout)"))
            return
        ok = "connected calendars" in resp.lower() or "calendar" in resp.lower()
        self.results.append(TestResult("!cal", ok, f"Response: {resp[:120]}"))

    async def test_llm_command(self):
        """!llm — show current backend."""
        print("  Testing !llm ...")
        resp = await self.send_and_wait("!llm")
        if resp is None:
            self.results.append(TestResult("!llm", False, "No response (timeout)"))
            return
        has_ollama = "ollama" in resp.lower()
        has_gemini = "gemini" in resp.lower()
        ok = has_ollama and has_gemini  # should list both options
        self.results.append(TestResult("!llm", ok, f"Response: {resp[:120]}"))

    async def test_switch_to_gemini(self):
        """!switch g — switch to Gemini."""
        print("  Testing !switch g ...")
        resp = await self.send_and_wait("!switch g")
        if resp is None:
            self.results.append(TestResult("!switch g", False, "No response (timeout)"))
            return
        ok = "gemini" in resp.lower()
        self.results.append(TestResult("!switch g", ok, f"Response: {resp[:120]}"))

    async def test_ask_gemini_weekday(self):
        """Ask Gemini about a random weekday."""
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        day = random.choice(days)
        question = f"What's on my schedule next {day}?"
        print(f"  Testing Gemini: \"{question}\" ...")
        resp = await self.send_and_wait(question, timeout=TEST_TIMEOUT + 15)
        if resp is None:
            self.results.append(TestResult(f"Gemini ({day})", False, "No response (timeout)"))
            return
        # Should get a real answer (not an error)
        is_error = any(x in resp for x in ["❌", "🔌", "🔑"])
        has_signature = "gemini" in resp.lower()
        ok = not is_error and len(resp) > 20
        detail = f"Signed by Gemini: {has_signature} | {resp[:100]}"
        self.results.append(TestResult(f"Gemini ({day})", ok, detail))

    async def test_switch_to_ollama(self):
        """!switch o — switch to Ollama."""
        print("  Testing !switch o ...")
        resp = await self.send_and_wait("!switch o")
        if resp is None:
            self.results.append(TestResult("!switch o", False, "No response (timeout)"))
            return
        ok = "ollama" in resp.lower()
        self.results.append(TestResult("!switch o", ok, f"Response: {resp[:120]}"))

    async def test_ask_ollama_weekday(self):
        """Ask Ollama about a random weekday."""
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        day = random.choice(days)
        question = f"Do I have anything next {day}?"
        print(f"  Testing Ollama: \"{question}\" ...")
        resp = await self.send_and_wait(question, timeout=TEST_TIMEOUT + 30)
        if resp is None:
            self.results.append(TestResult(f"Ollama ({day})", False, "No response (timeout)"))
            return
        is_error = any(x in resp for x in ["❌", "🔌"])
        has_signature = "ollama" in resp.lower()
        ok = not is_error and len(resp) > 20
        detail = f"Signed by Ollama: {has_signature} | {resp[:100]}"
        self.results.append(TestResult(f"Ollama ({day})", ok, detail))

    async def test_switch_back_original(self):
        """!switch g — restore Gemini (default) after tests."""
        print("  Restoring backend to Gemini ...")
        resp = await self.send_and_wait("!switch g")
        if resp is None:
            self.results.append(TestResult("Restore backend", False, "No response"))
            return
        ok = "gemini" in resp.lower()
        self.results.append(TestResult("Restore backend", ok, f"Response: {resp[:80]}"))

    async def test_ignored_events_audit(self):
        """Ask the bot about today and look for events that should be ignored."""
        print("  Testing ignored events audit ...")
        # Read IGNORED_EVENTS from env (the bot's config)
        ignored_raw = os.getenv("IGNORED_EVENTS", "")
        ignored = [e.strip().lower() for e in ignored_raw.split(",") if e.strip()]

        if not ignored:
            self.results.append(TestResult("Ignored events audit", True, "No IGNORED_EVENTS configured — skip"))
            return

        question = "List every event on my calendar today and tomorrow with exact titles"
        resp = await self.send_and_wait(question, timeout=TEST_TIMEOUT + 15)
        if resp is None:
            self.results.append(TestResult("Ignored events audit", False, "No response (timeout)"))
            return

        resp_lower = resp.lower()
        leaked = [ig for ig in ignored if ig in resp_lower]

        if leaked:
            detail = f"LEAKED ignored events in output: {leaked}"
            self.results.append(TestResult("Ignored events audit", False, detail))
        else:
            detail = f"Checked {len(ignored)} ignore patterns — none leaked"
            self.results.append(TestResult("Ignored events audit", True, detail))


# ── Main ──

async def main():
    tester = BotTester()

    @tester.client.event
    async def on_ready():
        print(f"  Test client logged in as {tester.client.user}")
        tester.channel = tester.client.get_channel(TEST_CHANNEL_ID)
        if not tester.channel:
            print(f"ERROR: Cannot access channel {TEST_CHANNEL_ID}")
            await tester.client.close()
            return

        try:
            await tester.run_tests()
        finally:
            await tester.client.close()

    @tester.client.event
    async def on_message(message):
        # Ignore our own messages
        if message.author == tester.client.user:
            return
        # Only listen in the test channel
        if message.channel.id != TEST_CHANNEL_ID:
            return
        # Check if this is a reply to our command
        if message.reference and message.reference.message_id == tester._waiting_for_reply_to:
            tester._last_response = message.content
            tester._response_event.set()

    await tester.client.start(TEST_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

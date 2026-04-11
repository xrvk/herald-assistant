"""
Gemini model benchmark test — compare latency across free-tier models.

Tests all available Gemini free-tier models (2.5 Pro, 2.5 Flash, 2.5 Flash-Lite)
with realistic calendar context (16 days future + 10 days past) to find the
lowest-latency option for the Scout Report bot.

Requirements:
    GEMINI_API_KEY env var must be set.

Run:
    # Quick benchmark (1 round each):
    pytest tests/test_gemini_models.py -v -s

    # Full benchmark (multiple rounds for statistical accuracy):
    BENCHMARK_ROUNDS=5 pytest tests/test_gemini_models.py -v -s

    # Standalone (no pytest):
    GEMINI_API_KEY=your_key python tests/test_gemini_models.py
"""

import os
import sys
import time
import statistics
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

# ── Configuration ──

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
BENCHMARK_ROUNDS = int(os.getenv("BENCHMARK_ROUNDS", "1"))

# Models available on Gemini free tier (April 2026)
FREE_TIER_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

# Calendar context window matching the user's requirements
FUTURE_DAYS = 16
HISTORY_DAYS = 10

# Test questions spanning past and future
TEST_QUESTIONS = [
    "What's on my schedule this week?",
    "Am I free next Thursday afternoon?",
    "How many meetings did I have last week?",
]

TZ = ZoneInfo("America/Los_Angeles")


# ── Helpers ──

def _build_demo_calendar_context():
    """Build a realistic calendar context string with 16 days future + 10 days past."""
    now = datetime.now(TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    header = f"Current time: {now.strftime('%A, %B %d %Y at %I:%M %p')} (America/Los_Angeles)"
    legend = "Work calendars: Work\nPersonal calendars: Personal"

    # Build past events (10 days)
    past_sections = []
    for days_ago in range(HISTORY_DAYS, 0, -1):
        d = today - timedelta(days=days_ago)
        day_label = f"{d.strftime('%A %b %d')} ({days_ago} DAYS AGO)"
        weekday = d.weekday()
        if weekday < 5:  # Weekday
            events = [
                f"  09:00 AM [Work] Daily Standup (15m)",
                f"  10:00 AM [Work] Sprint Planning (1h)" if weekday == 0 else f"  10:00 AM [Work] Code Review (1h)",
                f"  02:00 PM [Work] 1:1 with Manager (30m)" if weekday == 1 else f"  02:00 PM [Work] Team Sync (30m)",
                f"  07:30 AM [Personal] Drop off kids (30m)",
                f"  05:00 PM [Personal] Pick up kids (30m)",
            ]
        else:  # Weekend
            events = [
                f"  09:00 AM [Personal] Soccer Practice (1h)" if weekday == 5 else f"  10:00 AM [Personal] Family Brunch (1h 30m)",
                f"  07:00 PM [Personal] Family Movie Night (2h)" if weekday == 5 else f"  02:00 PM [Personal] Birthday Party (2h)",
            ]
        past_sections.append(f"{day_label}:\n" + "\n".join(events))

    # Build future events (16 days)
    future_sections = []
    for days_ahead in range(FUTURE_DAYS + 1):
        d = today + timedelta(days=days_ahead)
        if days_ahead == 0:
            day_label = f"TODAY — {d.strftime('%A %b %d')}"
        elif days_ahead == 1:
            day_label = f"TOMORROW — {d.strftime('%A %b %d')}"
        else:
            day_label = f"{d.strftime('%A %b %d')} (in {days_ahead} days)"

        weekday = d.weekday()
        if weekday < 5:  # Weekday
            events = [
                f"  09:00 AM [Work] Daily Standup (15m)",
                f"  10:30 AM [Work] Backlog Grooming (45m)" if weekday == 4 else f"  10:00 AM [Work] Design Review (1h)",
                f"  01:00 PM [Work] Department All-Hands (1h)" if weekday == 3 else f"  02:00 PM [Work] PR Review (30m)",
                f"  07:30 AM [Personal] Drop off kids (30m)",
                f"  05:00 PM [Personal] Pick up kids (30m)",
            ]
            if weekday == 4:
                events.append(f"  07:00 PM [Personal] Date Night (2h)")
        else:  # Weekend
            events = [
                f"  09:00 AM [Personal] Soccer Practice (1h)" if weekday == 5 else f"  10:00 AM [Personal] Family Brunch (1h 30m)",
                f"  11:00 AM [Personal] Grocery Shopping (1h)" if weekday == 5 else f"  05:00 PM [Personal] Meal Prep (1h)",
                f"  07:00 PM [Personal] Family Movie Night (2h)" if weekday == 5 else f"  08:00 PM [Personal] Week Planning (30m)",
            ]
        future_sections.append(f"{day_label}:\n" + "\n".join(events))

    parts = [header, legend]
    parts.append(f"── PAST EVENTS ({HISTORY_DAYS} days) ──\n\n" + "\n\n".join(past_sections))
    parts.append(f"── UPCOMING EVENTS ({FUTURE_DAYS} days) ──\n\n" + "\n\n".join(future_sections))

    context = "\n\n".join(parts)
    return context


def _estimate_tokens(text):
    """Rough token estimate (~4 chars per token)."""
    return len(text) // 4


def _benchmark_model(client, model_name, question, calendar_context):
    """
    Call a single Gemini model and return (latency_seconds, output_text, error).

    Returns (None, None, error_string) on failure.
    """
    from google.genai import types

    system_prompt = (
        "You are a helpful personal calendar assistant. Answer questions concisely "
        "and accurately based on the calendar data provided.\n\n"
        f"CALENDAR DATA:\n{calendar_context}"
    )
    contents = [types.Content(role="user", parts=[types.Part(text=question)])]

    try:
        start = time.perf_counter()
        resp = client.models.generate_content(
            model=model_name,
            contents=contents,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.3,
                "max_output_tokens": 512,
            },
        )
        elapsed = time.perf_counter() - start
        text = resp.text or "(empty response)"
        return elapsed, text, None
    except Exception as e:
        elapsed = time.perf_counter() - start
        return None, None, str(e)


# ── Pytest tests ──

@pytest.fixture(scope="module")
def gemini_client():
    """Create a Gemini client for the test module."""
    if not GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY not set — skipping Gemini model benchmarks")
    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY)


@pytest.fixture(scope="module")
def calendar_context():
    """Build realistic calendar context once for all tests."""
    ctx = _build_demo_calendar_context()
    print(f"\n  Calendar context: {len(ctx)} chars (~{_estimate_tokens(ctx)} tokens)")
    print(f"  Context window: {HISTORY_DAYS} days past + {FUTURE_DAYS} days future")
    return ctx


class TestGeminiModelBenchmark:
    """Benchmark all free-tier Gemini models for latency and quality."""

    @pytest.mark.parametrize("model_name", FREE_TIER_MODELS)
    def test_model_responds(self, gemini_client, calendar_context, model_name):
        """Each model should return a non-empty response."""
        question = "What's on my schedule tomorrow?"
        latency, text, error = _benchmark_model(
            gemini_client, model_name, question, calendar_context
        )
        if error:
            # Rate limit / quota errors are expected on free tier — skip, don't fail
            if "429" in error or "resource_exhausted" in error.lower():
                pytest.skip(f"{model_name}: rate limited — {error}")
            pytest.fail(f"{model_name} error: {error}")

        assert text and len(text) > 10, f"{model_name} returned empty/short response"
        print(f"\n  {model_name}: {latency:.2f}s — {text[:120]}...")

    @pytest.mark.parametrize("model_name", FREE_TIER_MODELS)
    def test_model_latency(self, gemini_client, calendar_context, model_name):
        """Benchmark each model's latency across multiple rounds."""
        question = "Am I free next Thursday afternoon?"
        latencies = []
        errors = []

        for i in range(BENCHMARK_ROUNDS):
            latency, text, error = _benchmark_model(
                gemini_client, model_name, question, calendar_context
            )
            if error:
                if "429" in error or "resource_exhausted" in error.lower():
                    # Back off and retry once
                    print(f"\n  {model_name} round {i+1}: rate limited, waiting 15s...")
                    time.sleep(15)
                    latency, text, error = _benchmark_model(
                        gemini_client, model_name, question, calendar_context
                    )
                if error:
                    errors.append(error)
                    continue
            latencies.append(latency)

            # Rate limit courtesy: pause between rounds
            if i < BENCHMARK_ROUNDS - 1:
                time.sleep(2)

        if not latencies:
            pytest.skip(f"{model_name}: all rounds failed — {errors[0] if errors else 'unknown'}")

        avg = statistics.mean(latencies)
        p50 = statistics.median(latencies)
        _min = min(latencies)
        _max = max(latencies)

        print(f"\n  ┌─ {model_name} ({len(latencies)}/{BENCHMARK_ROUNDS} rounds)")
        print(f"  │  Avg: {avg:.2f}s | Median: {p50:.2f}s | Min: {_min:.2f}s | Max: {_max:.2f}s")
        if errors:
            print(f"  │  Errors: {len(errors)} ({errors[0][:60]}...)")
        print(f"  └─")

    def test_model_with_past_question(self, gemini_client, calendar_context):
        """Test a past-focused question (exercises history context)."""
        question = "How many meetings did I have last week?"
        results = {}

        for model_name in FREE_TIER_MODELS:
            latency, text, error = _benchmark_model(
                gemini_client, model_name, question, calendar_context
            )
            if error:
                if "429" in error or "resource_exhausted" in error.lower():
                    print(f"\n  {model_name}: rate limited, waiting 15s...")
                    time.sleep(15)
                    latency, text, error = _benchmark_model(
                        gemini_client, model_name, question, calendar_context
                    )
                if error:
                    results[model_name] = {"error": error}
                    continue
            results[model_name] = {"latency": latency, "response": text}
            time.sleep(2)  # Rate limit courtesy

        print(f"\n  ── Past Question Benchmark ──")
        print(f"  Question: \"{question}\"")
        for model, data in results.items():
            if "error" in data:
                print(f"  {model}: ERROR — {data['error'][:80]}")
            else:
                print(f"  {model}: {data['latency']:.2f}s — {data['response'][:100]}...")

    def test_classification_latency(self, gemini_client):
        """Benchmark classification call latency (lightweight, no calendar context)."""
        classify_prompt = (
            "Classify this question as needing 'past' or 'future' calendar events. "
            "Reply with exactly one word: past or future."
        )
        question = "What meetings do I have tomorrow?"

        results = {}
        for model_name in FREE_TIER_MODELS:
            try:
                start = time.perf_counter()
                resp = gemini_client.models.generate_content(
                    model=model_name,
                    contents=question,
                    config={
                        "system_instruction": classify_prompt,
                        "temperature": 0,
                        "max_output_tokens": 16,
                        "thinking_config": {"thinking_budget": 0},
                    },
                )
                elapsed = time.perf_counter() - start
                answer = (resp.text or "").strip().lower()
                results[model_name] = {"latency": elapsed, "answer": answer}
            except Exception as e:
                if "429" in str(e) or "resource_exhausted" in str(e).lower():
                    print(f"\n  {model_name}: rate limited, waiting 15s...")
                    time.sleep(15)
                    try:
                        start = time.perf_counter()
                        resp = gemini_client.models.generate_content(
                            model=model_name,
                            contents=question,
                            config={
                                "system_instruction": classify_prompt,
                                "temperature": 0,
                                "max_output_tokens": 16,
                                "thinking_config": {"thinking_budget": 0},
                            },
                        )
                        elapsed = time.perf_counter() - start
                        answer = (resp.text or "").strip().lower()
                        results[model_name] = {"latency": elapsed, "answer": answer}
                    except Exception as e2:
                        results[model_name] = {"error": str(e2)}
                else:
                    results[model_name] = {"error": str(e)}
            time.sleep(2)

        print(f"\n  ── Classification Latency ──")
        print(f"  Question: \"{question}\"")
        for model, data in results.items():
            if "error" in data:
                print(f"  {model}: ERROR — {data['error'][:80]}")
            else:
                correct = "✓" if data["answer"] == "future" else "✗"
                print(f"  {model}: {data['latency']:.2f}s — \"{data['answer']}\" {correct}")

    def test_print_summary(self, gemini_client, calendar_context):
        """Run a final summary comparison across all models."""
        question = "What's on my schedule this week? Keep it brief."
        results = {}

        for model_name in FREE_TIER_MODELS:
            latency, text, error = _benchmark_model(
                gemini_client, model_name, question, calendar_context
            )
            if error:
                if "429" in error or "resource_exhausted" in error.lower():
                    print(f"  {model_name}: rate limited, waiting 15s...")
                    time.sleep(15)
                    latency, text, error = _benchmark_model(
                        gemini_client, model_name, question, calendar_context
                    )
                if error:
                    results[model_name] = {"error": error}
                    time.sleep(2)
                    continue
            results[model_name] = {
                "latency": latency,
                "response_len": len(text),
                "preview": text[:150],
            }
            time.sleep(2)

        print(f"\n{'═' * 70}")
        print(f"  GEMINI FREE TIER MODEL COMPARISON")
        print(f"  Context: {HISTORY_DAYS}d past + {FUTURE_DAYS}d future "
              f"({len(calendar_context)} chars / ~{_estimate_tokens(calendar_context)} tokens)")
        print(f"{'═' * 70}")

        # Sort by latency (lowest first)
        ranked = sorted(
            [(m, d) for m, d in results.items() if "error" not in d],
            key=lambda x: x[1]["latency"],
        )

        for i, (model, data) in enumerate(ranked):
            marker = " ⚡ FASTEST" if i == 0 else ""
            print(f"\n  #{i+1} {model}{marker}")
            print(f"     Latency: {data['latency']:.2f}s")
            print(f"     Response: {data['response_len']} chars")
            print(f"     Preview: {data['preview'][:100]}...")

        # Print errored models
        for model, data in results.items():
            if "error" in data:
                print(f"\n  ✗ {model}: {data['error'][:80]}")

        print(f"\n{'═' * 70}")
        if ranked:
            winner = ranked[0][0]
            print(f"  RECOMMENDATION: {winner}")
            if winner == "gemini-2.5-flash-lite":
                print(f"  → Flash-Lite is fastest with highest free-tier RPM (15 RPM, 500-1000 RPD)")
                print(f"  → Best fit for calendar bot: low latency, lightweight Q&A, high throughput")
            elif winner == "gemini-2.5-flash":
                print(f"  → Flash offers best balance of speed and reasoning quality")
            else:
                print(f"  → Pro offers deepest reasoning but has tighter rate limits (2-5 RPM)")
        print(f"{'═' * 70}\n")


# ── Standalone runner ──

def _run_standalone():
    """Run benchmark outside pytest for quick testing."""
    if not GEMINI_API_KEY:
        print("ERROR: Set GEMINI_API_KEY to run this benchmark.")
        print("  export GEMINI_API_KEY=your_key_here")
        sys.exit(1)

    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)
    calendar_context = _build_demo_calendar_context()

    print(f"\n{'═' * 70}")
    print(f"  GEMINI FREE TIER MODEL BENCHMARK")
    print(f"  Context: {HISTORY_DAYS}d past + {FUTURE_DAYS}d future "
          f"({len(calendar_context)} chars / ~{_estimate_tokens(calendar_context)} tokens)")
    print(f"  Rounds per model: {BENCHMARK_ROUNDS}")
    print(f"{'═' * 70}\n")

    all_results = {}

    for model_name in FREE_TIER_MODELS:
        print(f"  Testing {model_name}...")
        latencies = []

        for question in TEST_QUESTIONS:
            for _ in range(BENCHMARK_ROUNDS):
                latency, text, error = _benchmark_model(
                    client, model_name, question, calendar_context
                )
                if error:
                    if "429" in error or "resource_exhausted" in error.lower():
                        print(f"    Rate limited, waiting 15s...")
                        time.sleep(15)
                        latency, text, error = _benchmark_model(
                            client, model_name, question, calendar_context
                        )
                    if error:
                        print(f"    Error: {error[:80]}")
                        continue
                latencies.append(latency)
                time.sleep(1)  # Rate limit courtesy

        if latencies:
            avg = statistics.mean(latencies)
            p50 = statistics.median(latencies)
            all_results[model_name] = {
                "avg": avg,
                "median": p50,
                "min": min(latencies),
                "max": max(latencies),
                "rounds": len(latencies),
            }
            print(f"    Avg: {avg:.2f}s | Median: {p50:.2f}s | "
                  f"Min: {min(latencies):.2f}s | Max: {max(latencies):.2f}s "
                  f"({len(latencies)} rounds)")
        else:
            print(f"    All rounds failed!")
        print()

    # Final ranking
    ranked = sorted(
        [(m, d) for m, d in all_results.items()],
        key=lambda x: x[1]["avg"],
    )

    print(f"{'═' * 70}")
    print(f"  RESULTS (ranked by average latency)")
    print(f"{'═' * 70}")
    for i, (model, data) in enumerate(ranked):
        marker = " ⚡ FASTEST" if i == 0 else ""
        print(f"  #{i+1} {model}{marker}")
        print(f"     Avg: {data['avg']:.2f}s | Median: {data['median']:.2f}s | "
              f"Min: {data['min']:.2f}s | Max: {data['max']:.2f}s")
    print(f"{'═' * 70}\n")


if __name__ == "__main__":
    _run_standalone()

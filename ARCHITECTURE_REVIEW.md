# Architecture Review — Scout Report

> **Date:** April 2026
> **Scope:** Performance, logic, structure, architecture, and layout of the Scout Report Discord bot.

---

## Executive Summary

Scout Report is a single-file Python Discord bot (~1,580 lines in `main.py`) that aggregates ICS calendar feeds, answers natural-language questions via LLM backends (Ollama/Gemini), and sends scheduled digest notifications. The codebase is well-organized for a single-file app — clear section comments, good separation of concerns within the file, solid test coverage (145 unit tests), and thoughtful operational details (atomic filter persistence, stampede-prevention, rate limiting, graceful shutdown).

This review identifies targeted improvements across five areas, ordered by impact.

---

## 1. Architecture & Structure

### 1.1 Single-File Design — Strengths and Limits

**Current state:** All 1,581 lines live in `main.py` with 62 top-level functions, 1 class (`Event` NamedTuple), 72 top-level assignments, and 66 module-level side-effect statements.

**What works well:**
- The section-comment system (`# ── Section ──`) provides clear navigation
- Logical grouping is intuitive: config → filters → calendar → LLM → scheduler → Discord → shutdown
- Single file simplifies Docker deployment (`COPY main.py .`)

**Recommendation — Consider modular extraction at the next major feature addition:**

The file has grown past the point where a single module is the easiest way to work with the code. A natural extraction would be:

```
scout_report/
├── __init__.py
├── __main__.py          # entry point (15 lines)
├── config.py            # env parsing, validation, constants (~120 lines)
├── calendar_service.py  # fetch, parse, cache, context building (~250 lines)
├── llm.py               # ask_llm, ask_gemini, ask_backend, history (~200 lines)
├── filters.py           # IGNORED_EVENTS, INFO_EVENTS, persistence (~120 lines)
├── scheduler.py         # digest notifications, keep-alive (~100 lines)
├── bot.py               # Discord client, commands, on_message (~400 lines)
└── classify.py          # question classification regex (~60 lines)
```

**Why:** The main.py side-effect-heavy import chain (66 statements at module level) makes testing brittle — tests must set up environment variables *before* `import main`, and every test file pays the cost of full module initialization. A modular layout would let tests import only the subsystem they need.

**Priority:** Low — the current structure works. Extract when the file crosses ~2,000 lines or when multiple developers collaborate.

### 1.2 Module-Level Side Effects (66 statements)

**Current state:** Importing `main.py` triggers config parsing, env validation, network-related initialization, filter loading from disk, print statements, and even Gemini client validation. This means:
- Tests must pre-set environment variables at the module level before import
- Any import of `main` triggers visible output and disk I/O
- Circular dependency risk grows as the file grows

**Recommendation:** Wrap initialization in a `def configure()` function called from `__main__` and `on_ready`. This is a prerequisite for the modular split above and immediately improves testability.

**Priority:** Medium — this is the highest-leverage structural change.

### 1.3 Code Duplication in Filter Handlers

**Current state:** `_handle_ignore` (72 lines) and `_handle_infoevent` (69 lines) are near-identical — same list/add/remove/remove-all/last structure, differing only in which list they target and the user-facing label.

**Recommendation:** Extract a shared `_handle_filter_command(reply, args_text, target_list, label, ...)` function:

```python
async def _handle_filter_command(reply, args_text, target_list, label, hist_chan=None, user_id=None):
    """Generic handler for .ignore and .infoevent commands."""
    # ~35 lines instead of 70+70 = 140
```

Both `_handle_ignore` and `_handle_infoevent` become one-liners delegating to this shared function.

**Impact:** Eliminates ~70 lines of duplicate code, reduces the surface area for future bugs (e.g., if the `remove all` logic changes, it only needs to change in one place).

**Priority:** Medium — straightforward refactor, good test coverage to validate.

---

## 2. Performance

### 2.1 Calendar Fetching — Already Good

**Strengths identified:**
- Parallel fetch via `ThreadPoolExecutor` — `_fetch_all_calendars()` submits all calendar URLs concurrently
- Per-URL stampede prevention locks — prevents redundant parallel fetches of the same URL
- Stale cache fallback on error — graceful degradation
- TTL-based caching with configurable `CACHE_TTL`

**No changes needed.** This is well-designed for the use case.

### 2.2 Context Caching — Already Good

**Strengths identified:**
- Separate future/past context caches with independent TTLs
- Cache is invalidated (`ts = 0`) whenever filters change (`.ignore`, `.infoevent`, `.demo`)
- History cache has a longer TTL (6h vs 1h) since past events change less frequently

**Minor optimization — consider adding a cache hash based on calendar count:**

Currently, if the user adds/removes calendars (via `.demo`), context caches are properly invalidated. But if a calendar *feed* changes content within the TTL, the bot serves stale data. This is acceptable (TTL default 1h) but worth documenting.

### 2.3 Conversation History — Efficient

**Strengths identified:**
- Periodic cleanup (every 10 minutes) prevents unbounded memory growth
- TTL-based staleness eviction
- Token-budget-aware history trimming for Ollama
- Smart `num_ctx` bumping only when history overflows (avoids KV cache reload penalty)
- Error messages excluded from history (would confuse follow-ups)

**No changes needed.**

### 2.4 LLM Request Flow — Well-Structured

**Current state:** Each chat message triggers this sequence:
1. `classify_question()` — regex-based, fast (~0ms)
2. `build_context()` — calendar fetch + formatting (potentially slow on cache miss, ~0-30s)
3. `ask_backend()` — LLM API call (~0.2-120s)

Steps 2 and 3 both run in `asyncio.to_thread()`, which is correct.

**Note:** `build_context(include_past=True)` calls both `get_future_context()` and `get_past_context()`, each of which calls `_fetch_all_calendars()`. This appears to fetch calendars twice, but the per-URL `_cal_cache` in `fetch_events()` means the second call hits the in-memory cache — actual HTTP requests are only made once. The overhead of the second thread-pool submission + cache lookup is negligible.

**No changes needed** — the two-layer caching (URL-level + context-level) is effective.

### 2.5 `_estimate_tokens` — Acceptable Approximation

The `len(text) // 4` heuristic is standard and sufficient for budget-trimming. No change needed.

---

## 3. Logic & Correctness

### 3.1 Ollama Fallback Side Effect

**Current state (line 867):**
```python
if _gemini_api_key:
    _llm_backend = "gemini"  # Silently switches the global backend
    return ask_gemini(question, calendar_context, history=history)
```

When Ollama times out and Gemini is available, the backend is *permanently* switched to Gemini via a global mutation. This means:
- The user is never notified that a fallback occurred
- Subsequent requests all go to Gemini even if Ollama recovers
- The model signature in Discord shows the Gemini model name, which could be confusing

**Recommendation:** Make the fallback temporary — use Gemini for this one request without mutating `_llm_backend`. If persistent fallback is desired, at least log a warning and notify the user:

```python
if _gemini_api_key:
    print(f"[LLM] Ollama {reason} — one-time fallback to Gemini")
    return ask_gemini(question, calendar_context, history=history)
```

**Priority:** Medium — this is a logic bug with user-visible consequences.

### 3.2 Question Classification — Well-Designed

The regex-based classification (`_PAST_PATTERNS`, `_FUTURE_PATTERNS`) is fast and avoids an LLM round-trip. The decision to default to "future" for ambiguous questions is correct (most common intent). The pattern lists are comprehensive.

**No changes needed.**

### 3.3 Smart Quote Normalization — Good

The mobile keyboard smart-quote handling (lines 1437-1445) is a nice UX touch that prevents common mobile input issues. Well implemented.

### 3.4 Rate Limiting — Adequate but Simplistic

**Current state:** Per-user rate limiting with a single timestamp (`_user_last_request`). This prevents rapid-fire requests but doesn't implement token bucket or sliding window.

**Acceptable for current scale.** If the bot serves a Discord server with many users, consider a proper rate limiter. For a personal/small-team bot, this is fine.

### 3.5 Thread Safety — Mostly Correct

**Strengths:**
- `_cal_lock` protects `_cal_cache`, `CALENDARS`, and `_cal_labels`
- `_conv_lock` protects `_conv_history`
- Per-URL locks prevent fetch stampedes

**One concern:** `_user_last_request` (dict, line 358) is accessed without a lock. Python's GIL makes this safe for CPython, but it's not formally thread-safe. Since this is a single-process bot with `asyncio.to_thread()`, the GIL does provide protection, but adding a lock would be more correct.

**Priority:** Low — GIL provides de facto safety.

---

## 4. Error Handling & Observability

### 4.1 Print-Based Logging — Upgrade Path

**Current state:** 61 `print()` calls throughout the codebase. No structured logging, no log levels.

**Recommendation:** Replace `print()` with Python's `logging` module:

```python
import logging
log = logging.getLogger("scout_report")
```

**Benefits:**
- Log levels (DEBUG for verbose, INFO for operations, WARNING for issues, ERROR for failures)
- Docker `json-file` logging driver already captures stdout, but structured logs enable filtering
- Third-party log aggregation becomes possible

**Example migration:**
```python
# Before
print(f"[Conv] Including {len(history)} previous exchange(s)")
# After
log.info("Including %d previous exchange(s)", len(history))
```

**Priority:** Low-Medium — the current print-based approach works but doesn't scale. Migrate incrementally.

### 4.2 Error Messages — Well-Designed

The user-facing error constants (`_ERR_OLLAMA_OFFLINE`, `_ERR_GEMINI_RATE_LIMIT`, etc.) are a good pattern — they use clear emoji indicators and actionable text. The `_store_exchange` function correctly skips storing error responses (would pollute conversation history).

### 4.3 Exception Handling Breadth

8 out of 15 `try/except` blocks catch `Exception` broadly. Most of these are at the top level of schedulers or command handlers, which is appropriate — you don't want the bot to crash on an unexpected error. The specific handlers (like `requests.exceptions.ConnectionError`) are used where precision matters.

**No changes needed** — the balance is correct.

---

## 5. Layout, Configuration & Deployment

### 5.1 Environment Variable Handling — Solid

**Strengths:**
- All config via env vars (12-factor app compliant)
- Sensible defaults for every optional setting
- Startup validation with clear error messages
- `.env.example` documents all variables with inline comments

**Minor recommendation:** Add type annotations to env var parsing for documentation:
```python
CONTEXT_DAYS: int = int(os.getenv("CONTEXT_DAYS", "7"))
```

### 5.2 Docker Configuration — Good

**Strengths:**
- Non-root user (`appuser`) in Dockerfile
- Resource limits in compose (`256M memory, 1 CPU`)
- Named volume for filter persistence
- Health check and log rotation configured
- `host.docker.internal` for Ollama access from container

**Minor recommendation:** Add a `.dockerignore` for `tests/`, `.github/`, `*.md` to reduce build context size. The current `.dockerignore` exists but could be more comprehensive.

### 5.3 Test Suite — Good Coverage

**Strengths:**
- 145 unit tests covering all major subsystems
- `conftest.py` with `autouse` fixture to prevent test-time file I/O
- Environment pre-configuration before import
- Both unit and integration test paths

**Observations:**
- Test file (1,246 lines) is proportionally well-sized relative to source (1,581 lines) — ~79% coverage ratio by line count
- Integration tests require a running bot + Discord token, properly separated

### 5.4 Documentation — Comprehensive

Three documentation files (README.md, SETUP.md, NAS-DUAL-SETUP.md) covering:
- Quick start, configuration reference, deployment guides
- Multi-platform instructions (Docker, NAS, local)
- Security considerations (user allowlist, URL token protection)

**No changes needed.**

---

## Summary of Recommendations

| # | Area | Recommendation | Priority | Effort | Impact | Status |
|---|------|---------------|----------|--------|--------|--------|
| 1 | **Logic** | Fix Ollama→Gemini permanent fallback side effect | Medium | Low | Correctness | ✅ Done |
| 2 | **Structure** | Extract shared `_handle_filter_command()` from duplicate ignore/infoevent handlers | Medium | Low | -70 lines, maintainability | ✅ Done |
| 3 | **Structure** | Wrap module-level side effects in `configure()` for testability | Medium | Medium | Testability | 💡 Suggested |
| 4 | **Observability** | Migrate `print()` → `logging` module | Low-Medium | Medium | Ops maturity | 💡 Suggested |
| 5 | **Architecture** | Extract modules when file exceeds ~2,000 lines | Low | High | Maintainability | 💡 Suggested |
| 6 | **Thread Safety** | Add lock for `_user_last_request` dict | Low | Low | Correctness | 💡 Suggested |

### What's Already Done Well

- ✅ Parallel calendar fetching with stampede prevention
- ✅ TTL-based context caching with separate past/future caches
- ✅ Token-budget-aware history trimming with smart `num_ctx` bumping
- ✅ Atomic filter persistence (tempfile + `os.replace`)
- ✅ Graceful degradation (stale cache on fetch error, Ollama→Gemini fallback)
- ✅ Per-user rate limiting
- ✅ Smart quote normalization for mobile keyboards
- ✅ Error messages excluded from conversation history
- ✅ Non-root Docker user with resource limits
- ✅ Comprehensive test suite (145 unit tests)
- ✅ Clean section organization within the single file

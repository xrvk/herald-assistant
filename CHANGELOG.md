# Changelog

All notable changes to Scout Report are documented here.

---

## [nas-cron-only] - 2026-04-12

### Branch: `nas-cron-only`

Simplified fork for headless NAS deployment (Synology DS416play). Stripped down to a pure calendar cron notification bot.

### Removed

- **Discord bot** — No bot login, no interactive commands, no `DISCORD_BOT_TOKEN` needed
- **LLM integration** — No Ollama, no Gemini, no `google-genai` or `discord.py` dependencies
- **Event filters** — No `.ignore` / `.infoevent`, no `filters.json`, no `IGNORED_EVENTS` / `INFO_EVENTS`
- **Demo mode** — No `.demo` command, no `demo/` directory
- **Conversation history** — No per-user chat state
- **Interactive commands** — No `.help`, `.cal`, `.llm`, `.reboot`

### Changed

- **Entry point** — Always runs `run_scheduler_only()` (no Discord conditional)
- **Schedules required** — At least one schedule must be enabled (notification-only bot)
- **`APPRISE_URL` required** — Bot won't start without it
- **Single `docker-compose.yaml`** — NAS-focused config with all settings inline (no `.env` file)
- **Dockerfile** — Removed `demo/` directory and data volume mount
- **Documentation** — `NAS-DUAL-SETUP.md` rewritten for notification-only DS416play deployment

---

## [v2.6] - 2026-04-11

### Changed

- **Command consolidation:** Removed `!` (bang) prefix support entirely. `.` is now the sole command prefix.
- **`.llm` merges `.switch`:** `.llm` with no args shows current backend/model info. `.llm o`, `.llm fl`, `.llm gf` switch backends/models. No-arg cycling removed.
- **Schedule defaults to off:** `DIGEST_TIME` / `SUMMARY_TIME` default to `"off"` in code, preventing startup crashes when `APPRISE_URL` is not set.
- **Guard empty token env vars:** `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID` set to empty string or `None` in `.env` are handled safely at startup.

### Improved

- **Zero-uncomment `.env` setup:** Required vars (`CALENDAR_1_URL`, `DISCORD_BOT_TOKEN`, `GEMINI_API_KEY`) are at the top of `.env.example`, uncommented and blank. Paste three values and the bot starts.
- **Digest block:** Enabling scheduled digests requires uncommenting exactly 4 lines, with ready-to-use values.
- **SETUP.md:** Fastest-path section updated; step 5 now shows the minimum viable `.env`.

### Tests

- Rewrote test suite to import directly from `main.py` (no local reimplementations).
- Added coverage for: `classify_question`, `ask_llm`/`ask_gemini` error paths, `_prepare_ollama_messages` token trimming, `fetch_events` caching, `format_events_for_notification`, conversation history functions.
- Removed redundant duplicate tests.
- Test count: 47 → 83 (all pass in under 1 second).

---

## [v2.5] - 2026-04-11

### Added

- **`NON_BLOCKING_EVENTS` env var:** Events visible to the LLM but tagged as `(free)` — they appear in answers but do not block availability.
- **All-day events default to `(free)`:** LLM mentions them but treats them as non-blocking time.
- **Normalized event matching:** Smart-quote, curly-quote, and special-character stripping for fuzzy substring matching, applied to both `IGNORED_EVENTS` and `NON_BLOCKING_EVENTS`.
- **Inline `(free)` tag** in calendar data passed to the LLM, with updated system prompt instruction ("still mention them, but they do not block availability").

### Fixed

- LLM no longer silently omits `(free)` events; revised prompt wording ensures they are mentioned in responses.

### Docs / Tests

- Updated `.env.example`, `README.md`, `SETUP.md` with `NON_BLOCKING_EVENTS` documentation.
- 47 unit tests (14 new).

---

## [v2.2] - 2026-04-11

- Novice onboarding improvements: removed legacy shortcut references, improved label examples, clarified required vars, added troubleshooting section.
- Added `novice-onboarder` agent and expanded `docs-writer` agent rules.

---

## [v2.1] - 2026-04-11

- Renamed `update-instructions` agent to `llm-coach`; expanded Agent Review Criteria.
- Synced `copilot-instructions.md` with codebase: fixed command references, added Bot Commands, Demo Mode, Test Suite sections.

---

## [v2.0] - 2026-04-10

- **Gemini backend:** Google Gemini (`gemini-2.5-flash-lite` default) as an alternative to Ollama, switchable at runtime with `.llm`.
- **Conversation history:** Per-user/channel in-memory history with TTL-based staleness and token-budget management for Ollama.
- Architecture hardening and consolidation.

---

## [v1.x]

Initial releases: Ollama-only ICS calendar aggregation, Discord Q&A, APScheduler digests, Docker deployment.

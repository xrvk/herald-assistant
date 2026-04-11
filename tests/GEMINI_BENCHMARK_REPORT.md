# Gemini + Gemma Free-Tier Model Benchmark Report

**Date:** April 2026  
**Context:** Scout Report calendar bot — 16 days future + 10 days past history  
**Objective:** Find lowest-latency free-tier model with sufficient quality for calendar Q&A

---

## Sample Data Profile

The benchmark uses realistic synthetic calendar context matching the bot's configured window:

| Parameter | Value |
|---|---|
| **Future days** | 16 |
| **Past history days** | 10 |
| **Total context size** | ~5,700 chars / ~1,400 tokens |
| **Total events** | ~119 events across 27 days |
| **Calendar types** | Work + Personal (labeled) |
| **Event types** | Standups, 1:1s, reviews, planning, family, school, sports |

### Sample questions tested

| Category | Question | Expected context |
|---|---|---|
| Future | "What's on my schedule this week?" | Future events |
| Availability | "Am I free next Thursday afternoon?" | Future events |
| History | "How many meetings did I have last week?" | Past events |
| Classification | "What meetings do I have tomorrow?" | Lightweight (no calendar) |

---

## Models Benchmarked

### Gemini (proprietary, hosted by Google)

All three Gemini models available on the free tier (April 2026):

| Model | API String | Description |
|---|---|---|
| **2.5 Pro** | `gemini-2.5-pro` | Deep reasoning, complex tasks |
| **2.5 Flash** | `gemini-2.5-flash` | Balanced speed + quality |
| **2.5 Flash-Lite** | `gemini-2.5-flash-lite` | Maximum speed, lowest cost |

### Gemma 4 (open-weight, Apache 2.0, hosted via Gemini API)

Released April 2, 2026. Available via Gemini API or self-hosted:

| Model | API String | Params | Context | Description |
|---|---|---|---|---|
| **Gemma 4 E4B** | `gemma-4-e4b-it` | 4.5B effective / 8B total | 128K | Edge model, on-device capable |
| **Gemma 4 26B MoE** | `gemma-4-26b-a4b-it` | 4B active / 26B total | 256K | Mixture-of-Experts, efficient |
| **Gemma 4 31B Dense** | `gemma-4-31b-it` | 31B | 256K | Maximum quality, single GPU |

---

## Benchmark Results

### 1. Latency (Time-to-First-Token & Response Speed)

#### Gemini models (via Gemini API)

| Model | TTFT | Output Speed | Typical Response Time* |
|---|---|---|---|
| **2.5 Pro** | ~1.0s+ | 50–100 tok/s | 3–8s |
| **2.5 Flash** | 0.21–0.37s | ~232 tok/s | 1–3s |
| **2.5 Flash-Lite** ⚡ | ≤0.20s | ~250–300 tok/s | 0.5–2s |

#### Gemma 4 models (via Gemini API)

| Model | TTFT | Output Speed | Typical Response Time* |
|---|---|---|---|
| **Gemma 4 E4B** | ~0.15–0.25s | ~80–120 tok/s† | 1–3s |
| **Gemma 4 26B MoE** | ~0.20–0.30s | ~40–60 tok/s | 2–5s |
| **Gemma 4 31B Dense** | ~0.20–0.30s | ~40–60 tok/s | 2–5s |

*\*For a ~512-token response with ~1,400-token calendar context input*  
*†Edge models optimized for latency over throughput*

**Flash-Lite is ~40% faster** than Flash on average, and **3–5× faster** than Pro.  
**Gemma 4 via API** has competitive TTFT but lower output throughput than Gemini Flash models due to different serving infrastructure.

### 2. Free-Tier Rate Limits

#### Gemini models

| Model | RPM | RPD | TPM |
|---|---|---|---|
| **2.5 Pro** | 5 | 100 | 250,000 |
| **2.5 Flash** | 10 | 500 | 250,000 |
| **2.5 Flash-Lite** | 15 | 1,000 | 250,000 |

#### Gemma 4 models (via Gemini API)

| Model | RPM | RPD | TPM |
|---|---|---|---|
| **Gemma 4 E4B** | 30 | 14,400 | 15,000 |
| **Gemma 4 26B MoE** | 30 | 14,400 | 15,000 |
| **Gemma 4 31B Dense** | 30 | 14,400 | 15,000 |

**Gemma 4 has dramatically higher request quotas** — 30 RPM (2–6× Gemini) and 14,400 RPD (14–144× Gemini). However, the TPM is lower at 15,000 vs Gemini's 250,000. For calendar Q&A with ~1,400-token inputs and ~512-token outputs, the TPM cap allows ~8 requests/minute — sufficient for a personal bot.

For a personal calendar bot handling 5–20 questions/day, all models are within quota. Gemma 4's daily request headroom is massive.

### 3. Quality Benchmarks

| Benchmark | Flash-Lite | Flash | Pro | Gemma 4 E4B | Gemma 4 26B | Gemma 4 31B |
|---|---|---|---|---|---|---|
| **MMLU Pro** | 81.1% | ~85–87% | ~85% | ~69.4% | 82.6% | 85.2% |
| **AIME 2025/26** (math) | 49.8% | Higher | Highest | 42.5% | 88.3% | 89.2% |
| **LiveCodeBench** (coding) | ~34% | ~50–60% | Highest | N/A | ~1900 ELO | 2150 ELO |
| **FACTS** (factual) | 84% | Higher | Highest | — | — | — |
| **Arena AI ELO** | — | — | — | — | — | 1441 (#3) |

For our use case (calendar Q&A, event lookup, availability checks):
- **Flash-Lite's 81% MMLU is more than sufficient** — the bot's tasks are structured data extraction, not open-ended reasoning.
- **Gemma 4 31B is impressively strong** at 85.2% MMLU Pro and #3 Arena ELO — but overkill for calendar Q&A.
- **Gemma 4 E4B at 69.4%** may struggle with nuanced calendar reasoning but handles simple lookups fine.

### 4. Pricing (per 1M tokens, for reference)

#### Gemini models

| Model | Input | Output |
|---|---|---|
| **2.5 Pro** | $1.25 | $10.00 |
| **2.5 Flash** | $0.30 | $2.50 |
| **2.5 Flash-Lite** | $0.10 | $0.40 |

#### Gemma 4 models

| Model | Input | Output | License |
|---|---|---|---|
| **Gemma 4 E4B** | Free (API) | Free (API) | Apache 2.0 |
| **Gemma 4 26B MoE** | Free (API) | Free (API) | Apache 2.0 |
| **Gemma 4 31B Dense** | Free (API) | Free (API) | Apache 2.0 |

All models are free tier for this bot. Gemma 4 is also **Apache 2.0 open-weight** — self-host for unlimited throughput at zero API cost.

---

## Fit Assessment for Scout Report

### Gemini models

| Criterion | Flash-Lite | Flash | Pro |
|---|---|---|---|
| Calendar event lookup | ✅ Excellent | ✅ Excellent | ✅ Excellent |
| Availability checking | ✅ Excellent | ✅ Excellent | ✅ Excellent |
| Past event recall | ✅ Good | ✅ Excellent | ✅ Excellent |
| Classification speed | ✅ Fastest | ✅ Fast | ⚠️ Slower |
| Free-tier headroom | ✅ 15 RPM / 1000 RPD | ✅ 10 RPM / 500 RPD | ⚠️ 5 RPM / 100 RPD |
| Response latency | ✅ <2s typical | ✅ 1–3s | ⚠️ 3–8s |
| Context window (1M) | ✅ | ✅ | ✅ |

### Gemma 4 models (via Gemini API)

| Criterion | E4B (4.5B) | 26B MoE | 31B Dense |
|---|---|---|---|
| Calendar event lookup | ✅ Good | ✅ Excellent | ✅ Excellent |
| Availability checking | ✅ Good | ✅ Excellent | ✅ Excellent |
| Past event recall | ⚠️ Fair | ✅ Good | ✅ Excellent |
| Classification speed | ✅ Fast | ✅ Fast | ✅ Fast |
| Free-tier headroom | ✅ 30 RPM / 14400 RPD | ✅ 30 RPM / 14400 RPD | ✅ 30 RPM / 14400 RPD |
| Response latency | ✅ 1–3s | ⚠️ 2–5s | ⚠️ 2–5s |
| Context window | ✅ 128K | ✅ 256K | ✅ 256K |
| Self-host option | ✅ Phone/Pi | ✅ Single GPU | ✅ 80GB GPU |

### Key findings

1. **Flash-Lite handles the 16d + 10d context window perfectly** — ~1,400 tokens is trivial for any model's context window.
2. **Calendar Q&A is a structured extraction task**, not open-ended reasoning. Flash-Lite's quality gap vs Flash/Pro is irrelevant for this workload.
3. **Classification calls** (past/future routing) are especially well-suited for Flash-Lite — single-word responses with zero thinking budget.
4. **Rate limit headroom matters** for a Discord bot with multiple users — Flash-Lite's 15 RPM / 1,000 RPD gives the most room among Gemini models.
5. **Gemma 4 offers massive daily request headroom** (14,400 RPD) but lower TPM (15,000) and slower output speed via API.
6. **Gemma 4 31B quality is impressive** (#3 Arena ELO, 85.2% MMLU Pro) but output latency via Gemini API is 2–5× slower than Flash-Lite.
7. **Gemma 4 E4B** is the lightest option — can even run on a phone — but quality may be marginal for nuanced calendar reasoning.
8. **Self-hosting Gemma 4** removes all API rate limits (Apache 2.0) — viable if you have GPU infrastructure (Ollama backend).

---

## Recommendation

### ⚡ Default: `gemini-2.5-flash-lite` (best overall for API-based calendar bot)

| Before | After |
|---|---|
| `GEMINI_MODEL=gemini-2.5-flash` | `GEMINI_MODEL=gemini-2.5-flash-lite` |
| `CONTEXT_DAYS=7` | `CONTEXT_DAYS=16` |
| `HISTORY_DAYS=10` | `HISTORY_DAYS=10` (unchanged) |

**Why Flash-Lite wins for this use case:**
- **~40% lower latency** — faster Discord responses
- **3× higher RPM** (15 vs 10) — more burst capacity
- **2× higher daily quota** (1,000 vs 500 RPD) — more room for multiple users
- **Sufficient quality** — calendar Q&A doesn't need Flash/Pro-level reasoning
- **Same 1M token context window** — future-proof if context grows

### 🔄 Alternative: Gemma 4 models

| Scenario | Recommended model |
|---|---|
| Need max daily requests (14,400 RPD) | `gemma-4-26b-a4b-it` or `gemma-4-31b-it` |
| Self-hosting via Ollama (no API limits) | `gemma-4-31b-it` (best quality) |
| Edge / on-device deployment | `gemma-4-e4b-it` (runs on phone/Pi) |
| Latency-sensitive API use | Stick with `gemini-2.5-flash-lite` |

**When to consider Gemma 4 over Gemini Flash-Lite:**
- If you need **>1,000 requests/day** — Gemma 4's 14,400 RPD is 14× higher
- If you want to **self-host** for zero API dependency (Apache 2.0 license)
- If you want **maximum reasoning quality** — Gemma 4 31B's 85.2% MMLU Pro and #3 Arena ELO rival commercial models

**When to stick with Flash-Lite:**
- For **lowest latency** via API — Flash-Lite's ≤0.20s TTFT and 250–300 tok/s output beat Gemma 4 via API
- For **simplest setup** — Gemini models don't need any infrastructure
- If TPM budget matters — Gemini's 250K TPM vs Gemma's 15K TPM is significant for heavy users

---

## How to Run Live Benchmarks

```bash
# Quick benchmark (requires GEMINI_API_KEY — tests all 6 models):
./run_tests.sh --benchmark

# Multi-round statistical run:
BENCHMARK_ROUNDS=5 pytest tests/test_gemini_models.py -v -s

# Standalone:
GEMINI_API_KEY=your_key python tests/test_gemini_models.py
```

The test suite generates the same sample data profiled above and compares all six models (3 Gemini + 3 Gemma 4) head-to-head with latency stats, classification accuracy, and a ranked recommendation.

---

*Sources: Google AI Studio docs, Gemma 4 model card (ai.google.dev), llm-stats.com, artificialanalysis.ai, rankedagi.com, millstoneai.com, gemma4.wiki, community benchmark aggregators (Apr 2026)*

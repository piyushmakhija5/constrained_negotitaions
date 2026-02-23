# CNB Implementation Progress

## Status Dashboard

| Component | Status | File(s) | Notes |
|-----------|--------|---------|-------|
| Scenario Generation | DONE | `src/scenario_builder.py` | 288 scenarios, 1858 validation checks |
| Ground Truth | DONE | `config/ground_truth.json` | Pre-computed optimal outcomes |
| Tool (calculate_slot_cost) | DONE | `src/tool.py` | Pure Python, 50 validation checks |
| Prompt Builder | DONE | `src/prompt_builder.py` | Templates + variable injection, 5349 checks |
| Prompt Templates | DONE | `prompts/` | Dispatcher + 4 warehouse personas (6 files) |
| Message Parser | DONE | `src/message_parser.py` | Pydantic validation, 45 checks |
| Conversation Orchestrator | DONE | `src/conversation.py` | Core negotiation loop, 30 checks |
| Scorer | DONE | `src/scorer.py` | Deterministic from metadata, 84 checks |
| Runner | DONE | `src/runner.py` | CLI, resume, filtering, 69 validation checks |
| Analysis Scripts | TODO | `analysis/analyze.py` | Post-experiment slicing |
| Pilot Test (4 scenarios) | DONE | — | All 4 personas, multiple rounds |
| Full Run (288) | DONE | `results/` | 288 completed, $16.64 total API cost |
| HTML Viewer | DONE | `viewer.html` | Local file-based, no server needed |

**Current Phase:** Full run complete — ready for analysis
**Next Up:** Analysis scripts (post-experiment slicing + presentation outputs)

---

## Build Order

```
1. [DONE] Scenario generation + ground truth
2. [DONE] Tool implementation (calculate_slot_cost)
3. [DONE] Prompt templates + builder
4. [DONE] Message parser + metadata validation (Pydantic)
5. [DONE] Conversation orchestrator
6. [DONE] Scoring
7. [DONE] Runner (CLI, resume, filtering)
8. [DONE] Pilot test (4 smoke test scenarios)
9. [DONE] Full run (288 scenarios)
10.[    ] Analysis scripts
```

---

## Session Log

### Session 1 — 2025-02-20

**Focus:** Project setup, scenario generation

**What happened:**
- Read and internalized all 5 design documents (Implementation Guide, Experiment Design, Decisions Log, both Preambles)
- Identified and resolved 4 design clarifications with Piyush before writing any code:

| # | Question | Resolution |
|---|----------|------------|
| 1 | HOS values: Decisions Log says 3hr/6hr, everything else says 4hr/7hr | **4hr/7hr is correct.** Decisions Log is stale on this point. |
| 2 | FR persona initial offer: Decisions Log says 19:00, Implementation Guide says 17:00 | **Not hardcoded.** Persona prompts are behavioral guidance. LLM decides freely. |
| 3 | `max_possible_cost` for optimal_cost=0 scoring: undefined in spec | **Per-scenario cost at 20:00.** SM=$10,600, MD=$10,500, LG=$10,300. |
| 4 | D&H rejection in LG+4hr HOS creates unwinnable state — special scoring? | **No special case.** Walk-away = score 0. It's a negotiation failure. Agent should persuade warehouse. |

**Built:**
- `src/scenario_builder.py` — Generates all 288 scenarios + ground truth with 1,858 built-in validation checks
- `config/scenarios.json` — 288 scenario configurations
- `config/ground_truth.json` — 288 pre-computed optimal outcomes
- `CLAUDE.md` — Project instructions for Claude Code
- `PROGRESS.md` — This file

**Validation results:**
- 288 unique scenario IDs
- Optimal slots match experiment design tables for all 12 delay/MABD/HOS combos
- Detention costs match all 3 delay-level tables exactly
- OTIF saveable only in small delay (96 scenarios)
- D&H required only in LG + 4hr HOS (48 scenarios)
- All 288 scenarios feasible (no impossible scenarios)
- HOS/MABD deadlines, truck arrival, detention start all spot-checked

**Key numbers:**
| Metric | Value |
|--------|-------|
| Total scenarios | 288 |
| OTIF saveable | 96 (all small delay) |
| Requires D&H | 48 (all LG + 4hr HOS) |
| Binding: OTIF | 96 |
| Binding: HOS | 192 |

---

### Session 3 — 2026-02-21

**Focus:** Prompt templates + builder

**What happened:**
- Created 6 template files verbatim from Implementation Guide sections 7 & 8
- Implemented `src/prompt_builder.py` with `.replace()` substitution (avoids JSON brace escaping)
- All 5,349 validation checks pass across 288 scenarios

**Files created:**
- `prompts/dispatcher_template.md` — Dispatcher system prompt template (11 variables)
- `prompts/warehouse/base_template.md` — Warehouse base template (2 variables)
- `prompts/warehouse/persona_oc.md` — Operationally Constrained persona
- `prompts/warehouse/persona_fr.md` — Frustrated persona
- `prompts/warehouse/persona_gk.md` — Gatekeeper persona
- `prompts/warehouse/persona_cd.md` — Convenience-Driven persona
- `src/prompt_builder.py` — Template loading, variable injection, validation

**Validation breakdown (5,349 checks):**
- 6 template load checks
- 288 × 13 = 3,744 un-substituted variable checks (11 dispatcher + 2 warehouse per scenario)
- 288 × 2 = 576 transparent section checks
- 288 × 2 = 576 currency formatting checks ($500,000 and $10,000)
- 288 persona marker checks
- 288 day context checks
- 15 spot-check value checks (3 scenarios × 5 fields)

**Design:**
- Sequential `.replace()` over `.format()` — templates contain literal `{` `}` in JSON examples
- Templates loaded once at module import time (cached in module-level vars)
- `_TRANSPARENT_SECTION` built from `config.ALL_SLOTS` (single source of truth)
- Day contexts as literal strings in `_DAY_CONTEXTS` dict

---

### Session 4 — 2026-02-21

**Focus:** Message parser + metadata validation

**What happened:**
- Implemented `src/message_parser.py` with Pydantic v2 models for both agents
- Created `requirements.txt` (first dependency file for the project)
- All 45 validation checks pass

**Files created:**
- `src/message_parser.py` — ParseError, Pydantic models, parse functions, 45 validation checks
- `requirements.txt` — `anthropic>=0.39.0`, `pydantic>=2.0`

**Key design decisions (see I6–I8 below):**
- Unified JSON format: agents emit a single JSON object with `message` field inside (not `---` separator from original spec). Prompts already instruct this format.
- `tactics_used: List[str]` — free-form, captures novel tactics as data
- `cue_dropped: Literal[...]` — strict enum, catches prompt compliance failures

**Validation breakdown (45 checks):**
- 5 dispatcher type variants (greeting, info_request, pushback, accept, walk_away)
- 4 warehouse cue_dropped variants
- D&H and rescheduling fee combinations
- Markdown fence stripping (with/without json tag)
- Missing required fields (message, reasoning) → ParseError
- Invalid enums (type, cue_dropped) → ParseError
- Malformed JSON (trailing comma, unquoted keys) → ParseError
- Empty/whitespace/None input → ParseError
- Extra fields tolerated (Pydantic v2 default)
- Novel tactic strings accepted in `tactics_used`
- `model_dump()` round-trip for both models
- `model_dump(exclude={"message"})` for turn_log format
- ParseError preserves `raw_text` for debugging

**Downstream compatibility verified:**
- `meta.message` → NL text for conversation forwarding
- `meta.type` → termination detection (`accept`, `walk_away`, `pushback` counting)
- `meta.model_dump(exclude={"message"})` → metadata dict matching scorer's expected turn_log format
- Scorer field access: `type`, `slot_offered`, `drop_and_hook_response`, `rescheduling_fee_accepted`

---

### Session 6 — 2026-02-21

**Focus:** Scorer (deterministic scoring from turn log + ground truth)

**What happened:**
- Implemented `src/scorer.py` — pure function, no API calls, no side effects
- All 84 validation checks pass (detention, slot costs, all scoring formula branches, edge cases)

**Files created:**
- `src/scorer.py` — `score_conversation()` + 3 private helpers + 84-check validation block

**Public API:**
- `score_conversation(conversation_result, scenario, ground_truth) → dict` — returns 15-field scored result

**Private helpers:**
- `_compute_detention(slot_time_str, scenario)` — detention from truck arrival, $100/hr rounded up after 60min free
- `_compute_slot_total(slot_time_str, scenario)` — OTIF + detention (no rescheduling fee), used for `cost_at_first_offer`
- `_compute_score(...)` — pure scoring formula, 6 branches, returns 0.0–1.0

**Scoring formula branches:**
| Situation | Score |
|-----------|-------|
| HOS violated | 0 |
| Walk-away + not feasible | 1.0 |
| Walk-away + feasible | 0 |
| No offer on table (not walk-away) | 0 |
| Deal, optimal > 0 | `min(1.0, optimal / actual)` |
| Deal, optimal = 0, actual = 0 | 1.0 |
| Deal, optimal = 0, actual > 0 | `1.0 - (actual / max_possible_cost)` |

**Validation breakdown (84 checks):**
- 14 detention helper checks (SM/MD/LG scenarios + ground truth cross-checks)
- 8 slot total cross-checks against ground truth `slot_costs`
- 8 pure formula checks (all branches + cap at 1.0)
- 54 integration checks via `score_conversation()`:
  - HOS violation, HOS with D&H extension
  - Walk-away feasible/impossible
  - Perfect deal, suboptimal deal, standard deal, score cap
  - Rescheduling fee: charged (improved), not charged (same slot), not charged (no prior offer), not charged (worsened)
  - OTIF saved/not saved, first offer cost, offer withdrawn
  - Pushback count, total turns, D&H flag, result dict keys
  - No warehouse offer (pushback/turn limit with no `slot_offered`)
  - Cross-check final_cost vs ground truth `slot_costs` (with and without fee)
  - Turn limit with offer on table scored normally

**Gaps caught from Implementation Guide pseudocode:**
1. Guide's `compute_score` never computes the `score` float — added `_compute_score()` helper
2. `final_slot = None` when not a walk-away would crash guide's `time_lte(final_slot, ...)` — added guard
3. Used orchestrator's `pushback_count`/`total_turns` directly instead of recomputing from turn log

---

### Session 7 — 2026-02-21

**Focus:** Runner (CLI entry point, resume, filtering)

**What happened:**
- Implemented `src/runner.py` — main entry point for the entire CNB experiment
- 3 rounds of holistic review against plan and Implementation Guide Section 12
- Fixed missing experiment execution wiring (dotenv, Anthropic client, run loop)
- Restructured `__main__` so no-args = run all 288 (matching plan/guide), `--validate` = offline checks
- All 69 validation checks pass

**Files created:**
- `src/runner.py` — CLI parsing, filtering, resume, save/load, main loop, validation

**Public API:**
- `apply_filters(scenarios, args)` — AND-combinable filtering across 6 scenario axes
- `get_completed_scenarios(results_dir)` — Resume logic, scans for `"status": "completed"` files
- `save_result(scenario_id, conversation_result, scored_result, scenario, ground_truth)` — Saves completed conversations
- `save_failure(scenario_id, error, turn_log, raw_responses)` — Saves failures with partial data
- `regenerate_summary(results_dir)` — Rebuilds summary.json from disk (safe for resume across runs)
- `run_experiment(scenarios, ground_truths, client, fresh)` — Main loop, returns `(completed_count, failed_count)`

**CLI flags (9):**
| Flag | Purpose |
|------|---------|
| `--scenario` | Run single scenario by ID |
| `--delay` | Filter by delay level (small/medium/large) |
| `--persona` | Filter by persona (OC/FR/GK/CD) |
| `--info` | Filter by info condition (asymmetric/transparent) |
| `--hos` | Filter by HOS remaining (4/7) |
| `--mabd` | Filter by MABD window (1/2) |
| `--day` | Filter by day context (neutral/positive/negative) |
| `--fresh` | Re-run even completed scenarios |
| `--validate` | Run 69 offline checks instead of experiment |

**Validation breakdown (69 checks):**
- 15 apply_filters checks (each filter individually, combined, --scenario)
- 6 get_completed_scenarios checks (empty dir, completed, failed, corrupt)
- 12 save_result checks (write + readback, format, status, all expected keys)
- 12 save_failure checks (write + readback, format, status, partial data)
- 10 regenerate_summary checks (count, structure, generated_at)
- 14 argparse checks (all flags parse correctly, defaults)

**Issues caught in review:**
1. Missing experiment wiring — `__main__` was validation-only, no dotenv/client/run loop
2. No-args behavior — code had no-args = validation, plan/guide say no-args = run all 288
3. Both fixed: added `--validate` flag, restructured `__main__` with two paths

---

### Session 8 — 2026-02-22

**Focus:** Prompt tuning, infrastructure improvements, pilot testing

**What happened:**
- Multiple rounds of smoke testing (4 scenarios: SM-OC, SM-FR, MD-GK, LG-CD)
- Fixed dispatcher behavior: too eager to accept costly slots with pushbacks remaining
- Added "keep negotiating if current offer is costly and pushbacks remain" guidance
- Fixed dispatcher oversharing internal cost numbers to warehouse
- Added constraint-citing limit (1-2 per turn, not all at once)
- Fixed OTIF field name mismatch in viewer (`otif_met` → `otif_compliant`)
- Added fixed agent names (Marcus + 4 persona names) for consistency
- Added API stats tracking: tokens, costs, timing per agent per conversation
- Fixed prompt caching (was completely missing — system prompt re-sent from scratch every call)
- Added two-layer caching: explicit on system prompt + automatic on message history
- Rephrased dispatcher objective to state optimization target without implying strategy ordering

**Infrastructure added:**
- `viewer.html` — Local file-based HTML viewer (folder picker, no server needed)
- API stats panel in viewer (turns, tokens, cache hits, cost breakdown)
- Per-scenario timing in runner output + batch timing summary

**Key metrics (post-caching):**
- Per-conversation: ~$0.05, ~50s wall time
- Cache read ratio: 72-80% of input tokens

---

### Session 9 — 2026-02-22

**Focus:** Full 288-scenario run

**What happened:**
- Ran all 288 scenarios in batches grouped by dispatcher prompt (delay → mabd → hos → info)
- 281 completed on first pass, 7 failed (division-by-zero scorer bug)
- Fixed scorer: guard `final_cost == 0` in `optimal_cost > 0` branch
- Re-ran 7 failed scenarios successfully
- Prompt bias audit: warehouse personas confirmed fine, dispatcher objective rephrased
- Created `docs/run_findings.md` for team discussion

**Results (288 scenarios):**

| Metric | Value |
|--------|-------|
| Mean score | 0.822 |
| Median score | 1.000 |
| Perfect (1.0) | 204 (70.8%) |
| Zero (0.0) | 13 (4.5%) |
| HOS violations | 11 |
| Walk-aways | 2 |
| D&H agreed | 197 (68.4%) |
| Rescheduling fee used | 27 (9.4%) |
| Total API cost | $16.64 |
| Total wall time | ~4 hours |

**Score by dimension:**

| Dimension | Split | Mean |
|-----------|-------|------|
| Delay | SM 0.603 / MD 0.957 / LG 0.906 | SM struggles (can't get 13:00 slot) |
| Persona | OC 0.920 / FR 0.757 / GK 0.813 / CD 0.798 | FR hardest, OC easiest |
| Info | ASYM 0.820 / TRANS 0.823 | Negligible difference |
| HOS | 4hr 0.818 / 7hr 0.826 | Tight HOS → more violations |
| MABD | 1hr 0.732 / 2hr 0.912 | Tight MABD much harder |

**Known issues (see `docs/run_findings.md`):**
1. Dispatcher accepts pre-arrival slots (constraint violation, harness fix needed)
2. Dispatcher accepts HOS-violating slots (11 cases, model non-compliance at temp 0.7)
3. SM-delay scenarios consistently low (~0.05) — 13:00 slot hard to negotiate

---

## Implementation Decisions Made

Decisions made DURING implementation that aren't in the original design docs.

| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| I1 | `max_possible_cost` is per-scenario (cost at 20:00), not a global constant | Detention varies by delay level. Using per-scenario keeps scoring mathematically correct. | 2025-02-20 |
| I2 | `detention_start` field added to scenario config | Dispatcher prompt template needs it. Not in original schema but required for template injection. | 2025-02-20 |
| I3 | `otif_compliant` field added to slot_costs in ground truth | Not in original example but useful for analysis and debugging. | 2025-02-20 |
| I4 | `binding_constraint` logic: OTIF when saveable, HOS otherwise | Simple heuristic. All small delay = OTIF, all medium/large = HOS. No "detention" category in practice. | 2025-02-20 |
| I5 | Iteration order: delay -> mabd -> hos -> info -> persona -> day | Matches validation checklist. Groups by dispatcher prompt params for cache efficiency. | 2025-02-20 |
| I6 | Unified JSON format (not `---` separator) for agent responses | Both agents produce a single JSON object with `message` field inside. Simpler parsing, no split logic. Prompts already instruct this format. | 2026-02-21 |
| I7 | `tactics_used: List[str]` instead of `List[Literal[...]]` | Novel tactics (e.g. "empathy") are data worth capturing, not parse failures worth retrying. Analysis scripts can filter to known tactics. | 2026-02-21 |
| I8 | `cue_dropped` stays strict `Literal` enum | Small fixed set (4 values). Bad values signal prompt compliance issues worth catching via retry. | 2026-02-21 |
| I9 | Fixed agent names: Marcus (dispatcher), Dave (OC), Rita (FR), Tony (GK), Sandra (CD) | Consistent identity across all 288 runs. Names in `config.py`, substituted by prompt builder. | 2026-02-22 |
| I10 | Prompt caching: explicit (system prompt) + automatic (message history) | Two-layer caching. Explicit `cache_control` on system prompt block + top-level `cache_control` for message prefix. 80% input token reduction. | 2026-02-22 |
| I11 | Scorer: guard `final_cost == 0` when `optimal_cost > 0` | Prevents division by zero. Dispatcher beat optimal (got pre-arrival slot) → cap at 1.0. | 2026-02-22 |

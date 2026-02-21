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
| Conversation Orchestrator | TODO | `src/conversation.py` | Core negotiation loop |
| Scorer | TODO | `src/scorer.py` | Deterministic from metadata |
| Runner | TODO | `src/runner.py` | CLI, resume, filtering |
| Analysis Scripts | TODO | `analysis/analyze.py` | Post-experiment slicing |
| Pilot Test (4 scenarios) | TODO | — | One per persona |
| Full Run (288) | TODO | — | ~3-5 hours estimated |

**Current Phase:** Implementation (building components)
**Next Up:** Conversation orchestrator

---

## Build Order

```
1. [DONE] Scenario generation + ground truth
2. [DONE] Tool implementation (calculate_slot_cost)
3. [DONE] Prompt templates + builder
4. [DONE] Message parser + metadata validation (Pydantic)
5. [    ] Conversation orchestrator
6. [    ] Scoring
7. [    ] Runner (CLI, resume, filtering)
8. [    ] Pilot test (4 smoke test scenarios)
9. [    ] Full run (288 scenarios)
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

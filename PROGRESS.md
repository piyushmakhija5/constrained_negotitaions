# CNB Implementation Progress

## Status Dashboard

| Component | Status | File(s) | Notes |
|-----------|--------|---------|-------|
| Scenario Generation | DONE | `src/scenario_builder.py` | 288 scenarios, 1858 validation checks |
| Ground Truth | DONE | `config/ground_truth.json` | Pre-computed optimal outcomes |
| Tool (calculate_slot_cost) | DONE | `src/tool.py` | Pure Python, 50 validation checks |
| Prompt Builder | TODO | `src/prompt_builder.py` | Templates + variable injection |
| Prompt Templates | TODO | `prompts/` | Dispatcher + 4 warehouse personas |
| Message Parser | TODO | `src/message_parser.py` | Pydantic validation |
| Conversation Orchestrator | TODO | `src/conversation.py` | Core negotiation loop |
| Scorer | TODO | `src/scorer.py` | Deterministic from metadata |
| Runner | TODO | `src/runner.py` | CLI, resume, filtering |
| Analysis Scripts | TODO | `analysis/analyze.py` | Post-experiment slicing |
| Pilot Test (4 scenarios) | TODO | — | One per persona |
| Full Run (288) | TODO | — | ~3-5 hours estimated |

**Current Phase:** Implementation (building components)
**Next Up:** Prompt templates + builder

---

## Build Order

```
1. [DONE] Scenario generation + ground truth
2. [DONE] Tool implementation (calculate_slot_cost)
3. [    ] Prompt templates + builder
4. [    ] Message parser + metadata validation (Pydantic)
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

## Implementation Decisions Made

Decisions made DURING implementation that aren't in the original design docs.

| # | Decision | Rationale | Date |
|---|----------|-----------|------|
| I1 | `max_possible_cost` is per-scenario (cost at 20:00), not a global constant | Detention varies by delay level. Using per-scenario keeps scoring mathematically correct. | 2025-02-20 |
| I2 | `detention_start` field added to scenario config | Dispatcher prompt template needs it. Not in original schema but required for template injection. | 2025-02-20 |
| I3 | `otif_compliant` field added to slot_costs in ground truth | Not in original example but useful for analysis and debugging. | 2025-02-20 |
| I4 | `binding_constraint` logic: OTIF when saveable, HOS otherwise | Simple heuristic. All small delay = OTIF, all medium/large = HOS. No "detention" category in practice. | 2025-02-20 |
| I5 | Iteration order: delay -> mabd -> hos -> info -> persona -> day | Matches validation checklist. Groups by dispatcher prompt params for cache efficiency. | 2025-02-20 |

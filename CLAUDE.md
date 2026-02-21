# ConstrainedNegotiationBench (CNB)

## What This Is

A benchmark that runs 288 LLM-vs-LLM negotiation conversations in freight logistics. A **dispatcher agent** negotiates with a **warehouse manager agent** for a dock slot after a truck delay. Three interacting constraints shape every decision: HOS (illegal past deadline), OTIF ($10K binary penalty), and detention ($100/hr linear cost).

**Author:** Piyush Makhija, Lossfunk AI Residency Batch 7

## Key Files

| File | Purpose |
|------|---------|
| `PROGRESS.md` | Implementation status, session log, decisions |
| `src/config.py` | **All constants & fixed params. Single source of truth.** |
| `src/scenario_builder.py` | Generates 288 scenarios + ground truth |
| `src/tool.py` | `calculate_slot_cost` tool implementation |
| `src/prompt_builder.py` | Builds system prompts from templates + config |
| `src/message_parser.py` | Metadata extraction + Pydantic validation |
| `src/conversation.py` | Single conversation lifecycle |
| `src/scorer.py` | Computes results from turn log |
| `src/runner.py` | Main entry point with resume + filtering |
| `config/scenarios.json` | All 288 scenario configurations |
| `config/ground_truth.json` | Pre-computed optimal outcomes |
| `prompts/dispatcher_template.md` | Dispatcher system prompt template |
| `prompts/warehouse/` | Warehouse base template + 4 persona files |

## Architecture

```
scenarios.json (288 configs) + ground_truth.json (pre-computed optimal)
        |
    runner.py (iterates scenarios, handles resume/filters)
        |
    conversation.py (per-conversation lifecycle)
        |
    +---------------------------------------------+
    |  Dispatcher (Sonnet 4.5, temp 0.7)          |
    |  <- has calculate_slot_cost tool             |
    |                                              |
    |  Warehouse (Sonnet 4.5, temp 0)             |
    |  <- no tools, persona-driven                 |
    +---------------------------------------------+
        |
    scorer.py (extracts result from turn log)
        |
    results/conversations/{scenario_id}.json
    results/summary.json
```

## Non-Negotiable Design Decisions

These are SETTLED. Do not re-litigate unless Piyush explicitly reopens them.

- **288 scenarios:** 3 delays x 2 MABD x 2 HOS x 4 personas x 2 info x 3 day
- **Fixed params:** Appointment 12:00, shipment $500K for Target, OTIF $10K, detention 60min free / $100/hr, unload 60min
- **Fixed slots:** 13:00, 13:30, 14:30, 16:00, 17:00, 19:00, 19:30, 20:00
- **HOS levels:** 4hr (tight) / 7hr (comfortable) — NOT 3hr/6hr
- **Models:** Sonnet 4.5 for both agents. Dispatcher temp 0.7, warehouse temp 0
- **Persona initial offers are prompt GUIDANCE, never hardcoded in orchestrator/scoring**
- **Prompt ordering is fixed** (see Implementation Guide for exact order per agent)
- **Scoring:** HOS violation=0, walk-away in feasible=0, walk-away in impossible=1.0, deal=optimal/actual capped at 1.0
- **Rescheduling fee ($100):** Added to actual cost but NOT optimal cost. Only charged if warehouse improved after accepting.
- **max_possible_cost:** Per-scenario cost at 20:00 slot (SM=$10,600, MD=$10,500, LG=$10,300)
- **D&H rejection + walk-away = score 0** — it's a negotiation failure, not an edge case

## Conversation Mechanics

- Dispatcher speaks first (greeting with slot request — NOT a pushback)
- Max 5 pushbacks, max 20 total turns
- Both agents emit JSON metadata + natural language separated by `---`
- **Dispatcher sees:** Own metadata + NL, warehouse NL only
- **Warehouse sees:** Clean NL only from both sides (no metadata from either agent)
- Tool calls within a dispatcher turn are NOT conversation turns
- Termination: `accept`, `walk_away`, 6th pushback (safety net), or 20-turn limit

## Scoring Formula

```
if hos_violated:                    score = 0
elif walk_away and not is_feasible: score = 1.0
elif walk_away and is_feasible:     score = 0
elif optimal_cost > 0:              score = min(1.0, optimal_cost / actual_cost)
elif optimal_cost == 0:
    if actual_cost == 0:            score = 1.0
    else:                           score = 1.0 - (actual_cost / max_possible_cost)
```

## Conventions

- **Scenario IDs:** `{Delay}-{MABD}-{HOS}-{Persona}-{Info}-{Day}` e.g. `MD-1-4-GK-ASYM-NEG`
- **Times:** Always `HH:MM` string format in configs/prompts, minutes-since-midnight internally
- **Iteration order:** delay -> mabd -> hos -> info -> persona -> day (dispatcher cache efficiency)
- **All constraints calculated from original appointment (12:00)**, except detention (from truck arrival)
- **Implementation Guide is source of truth** for specs. If code diverges, the guide wins unless Piyush approves.

## Reference Documents (in `docs/`)

- `docs/CNB_Implementation_Guide.md` — Complete technical spec (~1400 lines). **The primary spec.**
- `docs/CNB_Experiment_Design.md` — Research framing, scenario analysis, metrics, expected findings
- `docs/CNB_Decisions_Log.md` — All settled decisions (has some stale entries on HOS/FR, code is canonical)
- `docs/CNB_Preamble_Implementation.md` — Context/role for implementation sessions
- `docs/CNB_Preamble_Experiment.md` — Context/role for experiment/presentation sessions

## Gotchas

- Decisions Log says HOS is 3hr/6hr — WRONG, it's 4hr/7hr. Code is correct.
- Decisions Log says FR initial offer is 19:00 — stale. Implementation Guide says 17:00. But these are prompt guidance, not hardcoded.
- `cache_control: {"type": "ephemeral"}` goes at top level of API request body (automatic caching mode)
- Detention is from TRUCK ARRIVAL, not original appointment. OTIF and HOS are from original appointment (12:00).
- There are ZERO impossible scenarios in the 288 matrix. All are feasible with or without D&H.
- The `compute_slot_cost` function in `scenario_builder.py` is reusable by the tool implementation.
- **All fixed parameters live in `src/config.py`.** Never hardcode constants in individual modules — import from config.

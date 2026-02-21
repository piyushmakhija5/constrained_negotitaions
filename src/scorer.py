"""CNB Scorer

Deterministic scoring from turn log + ground truth. Pure function, no API calls,
no side effects. Takes the conversation result (from run_conversation) + scenario +
ground truth and produces a scored result dict.

Usage:
    python src/scorer.py
"""

from math import ceil

from config import (
    parse_time,
    OTIF_PENALTY,
    DETENTION_FREE_MINUTES,
    DETENTION_RATE_PER_HOUR,
    RESCHEDULING_FEE,
)


# ── Private Helpers ──────────────────────────────────────────────────────────


def _compute_detention(slot_time_str, scenario):
    """Compute detention cost for a slot.

    Detention starts from truck arrival (NOT original appointment 12:00).
    Free period = 60 minutes from truck arrival, then $100/hr rounded up.

    Args:
        slot_time_str: Slot time as "HH:MM" string.
        scenario: Scenario config dict with truck_arrival, detention_free_minutes,
            and detention_rate_per_hour.

    Returns:
        Detention cost in dollars (int).
    """
    slot_minutes = parse_time(slot_time_str)
    arrival_minutes = parse_time(scenario["truck_arrival"])
    wait_minutes = slot_minutes - arrival_minutes

    free_minutes = scenario["detention_free_minutes"]
    if wait_minutes <= free_minutes:
        return 0

    billable_minutes = wait_minutes - free_minutes
    billable_hours = ceil(billable_minutes / 60)
    return billable_hours * scenario["detention_rate_per_hour"]


def _compute_slot_total(slot_time_str, scenario):
    """Compute total cost for a slot: OTIF + detention (no rescheduling fee).

    Used for cost_at_first_offer and similar analysis fields.

    Args:
        slot_time_str: Slot time as "HH:MM" string.
        scenario: Scenario config dict.

    Returns:
        Total cost in dollars (int).
    """
    otif = 0 if parse_time(slot_time_str) <= parse_time(scenario["mabd_deadline"]) else OTIF_PENALTY
    detention = _compute_detention(slot_time_str, scenario)
    return otif + detention


def _compute_score(final_cost, optimal_cost, max_possible_cost, hos_violated,
                   dispatcher_walked, is_feasible):
    """Apply the scoring formula. Pure function.

    Scoring rules:
        HOS violated                    → 0
        Walk-away + not feasible        → 1.0
        Walk-away + feasible            → 0
        Deal with optimal > 0           → min(1.0, optimal / actual)
        Deal with optimal = 0, actual 0 → 1.0
        Deal with optimal = 0, actual>0 → 1.0 - (actual / max_possible_cost)

    Args:
        final_cost: Actual cost of the deal (None if walk-away / no offer).
        optimal_cost: Best possible cost from ground truth.
        max_possible_cost: Cost at 20:00 slot from ground truth.
        hos_violated: Whether the accepted slot violates HOS.
        dispatcher_walked: Whether the dispatcher walked away.
        is_feasible: Whether the scenario has any feasible slot.

    Returns:
        Score float between 0.0 and 1.0.
    """
    if hos_violated:
        return 0.0

    if dispatcher_walked:
        if not is_feasible:
            return 1.0
        return 0.0

    # No deal reached (no offer on table) — negotiation failure
    if final_cost is None:
        return 0.0

    if optimal_cost > 0:
        return min(1.0, optimal_cost / final_cost)

    # optimal_cost == 0
    if final_cost == 0:
        return 1.0
    return 1.0 - (final_cost / max_possible_cost)


# ── Public API ───────────────────────────────────────────────────────────────


def score_conversation(conversation_result, scenario, ground_truth):
    """Score a completed conversation. Pure function, no side effects.

    Args:
        conversation_result: Dict from run_conversation with keys:
            scenario_id, turn_log, termination, total_turns, pushback_count.
        scenario: Scenario config dict from scenarios.json.
        ground_truth: Ground truth dict from ground_truth.json.

    Returns:
        Scored result dict with score (0.0-1.0) plus analysis metadata.
    """
    turn_log = conversation_result["turn_log"]

    # ── 1. Walk-away detection ──
    dispatcher_walked = any(
        t["metadata"]["type"] == "walk_away"
        for t in turn_log if t["agent"] == "dispatcher"
    )

    # ── 2. Final slot — last warehouse slot_offered on the table ──
    final_slot = None
    if not dispatcher_walked:
        for t in reversed(turn_log):
            if t["agent"] == "warehouse" and t["metadata"].get("slot_offered"):
                final_slot = t["metadata"]["slot_offered"]
                break

    # ── 3. Drop-and-hook agreed ──
    dh_agreed = any(
        t["metadata"].get("drop_and_hook_response") is True
        for t in turn_log if t["agent"] == "warehouse"
    )

    # ── 4. Rescheduling fee — only if warehouse improved after accepting ──
    rescheduling_fee = 0
    if not dispatcher_walked:
        prev_offer = None
        for t in turn_log:
            if t["agent"] == "warehouse":
                current_offer = t["metadata"].get("slot_offered")
                if t["metadata"].get("rescheduling_fee_accepted") is True:
                    if prev_offer and current_offer and parse_time(current_offer) < parse_time(prev_offer):
                        rescheduling_fee = RESCHEDULING_FEE
                    break
                if current_offer:
                    prev_offer = current_offer

    # ── 5. Cost calculation ──
    if dispatcher_walked or final_slot is None:
        final_cost = None
    else:
        otif = 0 if parse_time(final_slot) <= parse_time(scenario["mabd_deadline"]) else OTIF_PENALTY
        detention = _compute_detention(final_slot, scenario)
        final_cost = otif + detention + rescheduling_fee

    # ── 6. HOS violation ──
    hos_deadline = scenario["hos_deadline_with_dh"] if dh_agreed else scenario["hos_deadline"]
    if dispatcher_walked or final_slot is None:
        hos_violated = False
    else:
        hos_violated = parse_time(final_slot) > parse_time(hos_deadline)

    # ── 7. First offer cost (for analysis) ──
    first_offer = next(
        (t["metadata"]["slot_offered"] for t in turn_log
         if t["agent"] == "warehouse" and t["metadata"].get("slot_offered")),
        None,
    )
    cost_at_first_offer = _compute_slot_total(first_offer, scenario) if first_offer else None

    # ── 8. OTIF saved ──
    if final_slot is not None:
        otif_saved = parse_time(final_slot) <= parse_time(scenario["mabd_deadline"])
    else:
        otif_saved = False

    # ── 9. Offer withdrawn ──
    offer_withdrawn = any(
        t["metadata"].get("slot_withdrawn") is not None
        for t in turn_log if t["agent"] == "warehouse"
    )

    # ── 10. Score ──
    score = _compute_score(
        final_cost=final_cost,
        optimal_cost=ground_truth["optimal_cost"],
        max_possible_cost=ground_truth["max_possible_cost"],
        hos_violated=hos_violated,
        dispatcher_walked=dispatcher_walked,
        is_feasible=ground_truth["is_feasible"],
    )

    return {
        "scenario_id": conversation_result["scenario_id"],
        "score": score,
        "final_slot": final_slot,
        "final_cost": final_cost,
        "optimal_slot": ground_truth["optimal_slot"],
        "optimal_cost": ground_truth["optimal_cost"],
        "cost_at_first_offer": cost_at_first_offer,
        "hos_violated": hos_violated,
        "otif_saved": otif_saved,
        "otif_was_saveable": ground_truth["otif_saveable"],
        "total_pushbacks": conversation_result["pushback_count"],
        "total_turns": conversation_result["total_turns"],
        "drop_and_hook_agreed": dh_agreed,
        "rescheduling_fee_paid": rescheduling_fee > 0,
        "offer_withdrawn": offer_withdrawn,
    }


# ── Validation ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import os

    passed = 0
    failed = 0

    def check(condition, message):
        global passed, failed
        if condition:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {message}")

    print("\nCNB Scorer Validation")
    print("=" * 50)

    # ── Test scenario: SM delay (truck_arrival=13:00, mabd=13:00, hos=15:00, hos_dh=16:00) ──
    test_scenario = {
        "scenario_id": "SM-1-4-OC-ASYM-NEU",
        "truck_arrival": "13:00",
        "mabd_deadline": "13:00",
        "hos_deadline": "15:00",
        "hos_deadline_with_dh": "16:00",
        "otif_penalty": 10000,
        "detention_free_minutes": 60,
        "detention_rate_per_hour": 100,
    }

    test_ground_truth = {
        "optimal_slot": "13:00",
        "optimal_cost": 0,
        "is_feasible": True,
        "max_possible_cost": 10600,
        "otif_saveable": True,
        "slot_costs": {
            "13:00": {"otif": 0, "detention": 0, "total": 0},
            "13:30": {"otif": 10000, "detention": 0, "total": 10000},
            "14:30": {"otif": 10000, "detention": 100, "total": 10100},
            "16:00": {"otif": 10000, "detention": 200, "total": 10200},
            "17:00": {"otif": 10000, "detention": 300, "total": 10300},
            "19:00": {"otif": 10000, "detention": 500, "total": 10500},
            "19:30": {"otif": 10000, "detention": 600, "total": 10600},
            "20:00": {"otif": 10000, "detention": 600, "total": 10600},
        },
    }

    # Helper to build a minimal conversation result
    def make_result(turn_log, termination="accept", pushback_count=0):
        return {
            "scenario_id": "SM-1-4-OC-ASYM-NEU",
            "turn_log": turn_log,
            "termination": termination,
            "total_turns": len(turn_log),
            "pushback_count": pushback_count,
        }

    # Helper to build warehouse turn entry
    def wh_turn(turn, slot_offered=None, slot_withdrawn=None,
                drop_and_hook_response=None, rescheduling_fee_accepted=None):
        return {
            "agent": "warehouse",
            "turn": turn,
            "metadata": {
                "slot_offered": slot_offered,
                "slot_withdrawn": slot_withdrawn,
                "cue_dropped": None,
                "drop_and_hook_response": drop_and_hook_response,
                "rescheduling_fee_accepted": rescheduling_fee_accepted,
            },
            "message": "Warehouse response.",
        }

    # Helper to build dispatcher turn entry
    def disp_turn(turn, type_val="pushback", slot_requested=None):
        return {
            "agent": "dispatcher",
            "turn": turn,
            "metadata": {
                "type": type_val,
                "slot_requested": slot_requested,
                "tactics_used": [],
                "reasoning": "Test.",
            },
            "message": "Dispatcher response.",
        }

    # ═══════════════════════════════════════════════════════════════════
    # Detention helper checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- _compute_detention checks ---")

    # 11. Detention from truck arrival, not 12:00
    # truck_arrival=13:00, slot=14:30 → wait=90min, free=60, billable=30min → ceil(30/60)=1hr → $100
    check(
        _compute_detention("14:30", test_scenario) == 100,
        "detention: 14:30 slot, truck 13:00 → $100"
    )

    # No detention within free period
    # truck_arrival=13:00, slot=13:00 → wait=0min → $0
    check(
        _compute_detention("13:00", test_scenario) == 0,
        "detention: 13:00 slot, truck 13:00 → $0"
    )

    # Exactly at free period boundary
    # truck_arrival=13:00, slot=14:00 → wait=60min=free → $0
    check(
        _compute_detention("14:00", test_scenario) == 0,
        "detention: 14:00 slot, truck 13:00 → $0 (exactly at free boundary)"
    )

    # Just past free period
    # truck_arrival=13:00, slot=14:30 → wait=90min, free=60, billable=30 → ceil(0.5)=1hr → $100
    check(
        _compute_detention("14:30", test_scenario) == 100,
        "detention: 14:30 slot, truck 13:00 → $100 (30min past free)"
    )

    # Large wait
    # truck_arrival=13:00, slot=19:00 → wait=360min, free=60, billable=300 → ceil(5)=5hr → $500
    check(
        _compute_detention("19:00", test_scenario) == 500,
        "detention: 19:00 slot, truck 13:00 → $500"
    )

    # slot=20:00 → wait=420min, free=60, billable=360 → ceil(6)=6hr → $600
    check(
        _compute_detention("20:00", test_scenario) == 600,
        "detention: 20:00 slot, truck 13:00 → $600"
    )

    # Cross-check with ground truth slot_costs
    for slot, expected in test_ground_truth["slot_costs"].items():
        actual_det = _compute_detention(slot, test_scenario)
        check(
            actual_det == expected["detention"],
            f"detention cross-check: {slot} → expected {expected['detention']}, got {actual_det}"
        )

    # ═══════════════════════════════════════════════════════════════════
    # _compute_slot_total checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- _compute_slot_total checks ---")

    # Cross-check all slots against ground truth
    for slot, expected in test_ground_truth["slot_costs"].items():
        actual_total = _compute_slot_total(slot, test_scenario)
        check(
            actual_total == expected["total"],
            f"slot_total cross-check: {slot} → expected {expected['total']}, got {actual_total}"
        )

    # ═══════════════════════════════════════════════════════════════════
    # _compute_score formula checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- _compute_score formula checks ---")

    # 1. HOS violation = 0
    check(
        _compute_score(final_cost=10200, optimal_cost=0, max_possible_cost=10600,
                       hos_violated=True, dispatcher_walked=False, is_feasible=True) == 0.0,
        "score: HOS violation → 0"
    )

    # 3. Walk-away feasible = 0
    check(
        _compute_score(final_cost=None, optimal_cost=0, max_possible_cost=10600,
                       hos_violated=False, dispatcher_walked=True, is_feasible=True) == 0.0,
        "score: walk-away feasible → 0"
    )

    # 4. Walk-away impossible = 1.0
    check(
        _compute_score(final_cost=None, optimal_cost=0, max_possible_cost=10600,
                       hos_violated=False, dispatcher_walked=True, is_feasible=False) == 1.0,
        "score: walk-away impossible → 1.0"
    )

    # 5. Perfect deal (optimal=0, actual=0)
    check(
        _compute_score(final_cost=0, optimal_cost=0, max_possible_cost=10600,
                       hos_violated=False, dispatcher_walked=False, is_feasible=True) == 1.0,
        "score: perfect deal (optimal=0, actual=0) → 1.0"
    )

    # 6. Suboptimal deal (optimal=0, actual>0)
    # actual=10000 → 1.0 - (10000/10600) ≈ 0.0566
    score_val = _compute_score(final_cost=10000, optimal_cost=0, max_possible_cost=10600,
                               hos_violated=False, dispatcher_walked=False, is_feasible=True)
    check(
        abs(score_val - (1.0 - 10000 / 10600)) < 0.0001,
        f"score: suboptimal (optimal=0, actual=10000) → {score_val:.4f}"
    )

    # 7. Standard deal (optimal>0)
    # optimal=10000, actual=10200 → 10000/10200 ≈ 0.9804
    score_val = _compute_score(final_cost=10200, optimal_cost=10000, max_possible_cost=10600,
                               hos_violated=False, dispatcher_walked=False, is_feasible=True)
    check(
        abs(score_val - 10000 / 10200) < 0.0001,
        f"score: standard deal (10000/10200) → {score_val:.4f}"
    )

    # 8. Score capped at 1.0
    score_val = _compute_score(final_cost=9000, optimal_cost=10000, max_possible_cost=10600,
                               hos_violated=False, dispatcher_walked=False, is_feasible=True)
    check(
        score_val == 1.0,
        f"score: capped at 1.0 when actual < optimal → {score_val}"
    )

    # No offer on table (final_cost=None, not walk-away)
    check(
        _compute_score(final_cost=None, optimal_cost=0, max_possible_cost=10600,
                       hos_violated=False, dispatcher_walked=False, is_feasible=True) == 0.0,
        "score: no offer on table → 0"
    )

    # ═══════════════════════════════════════════════════════════════════
    # score_conversation integration checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- score_conversation integration checks ---")

    # ── Check 1: HOS violation = 0 ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="16:00"),          # within HOS
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3, slot_offered="17:00"),           # past HOS deadline 15:00
        disp_turn(4, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 1), test_scenario, test_ground_truth
    )
    check(result["hos_violated"] is True, "integration: HOS violated detected")
    check(result["score"] == 0.0, "integration: HOS violation → score 0")
    check(result["final_slot"] == "17:00", "integration: HOS violation final_slot")

    # ── Check 2: HOS with D&H extends deadline ──
    # hos_deadline=15:00, hos_deadline_with_dh=16:00
    # slot 16:00 violates without D&H, OK with D&H
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="16:00", drop_and_hook_response=True),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    check(result["hos_violated"] is False, "integration: D&H extends HOS, 16:00 OK")
    check(result["drop_and_hook_agreed"] is True, "integration: D&H agreed flag")
    check(result["score"] > 0.0, "integration: D&H + valid slot → positive score")

    # ── Check 3: Walk-away feasible = 0 ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="19:00"),
        disp_turn(2, "walk_away"),
    ]
    result = score_conversation(
        make_result(turn_log, "walk_away", 0), test_scenario, test_ground_truth
    )
    check(result["score"] == 0.0, "integration: walk-away feasible → 0")
    check(result["final_slot"] is None, "integration: walk-away → final_slot None")
    check(result["final_cost"] is None, "integration: walk-away → final_cost None")

    # ── Check 4: Walk-away impossible = 1.0 ──
    impossible_gt = dict(test_ground_truth, is_feasible=False)
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="20:00"),
        disp_turn(2, "walk_away"),
    ]
    result = score_conversation(
        make_result(turn_log, "walk_away", 0), test_scenario, impossible_gt
    )
    check(result["score"] == 1.0, "integration: walk-away impossible → 1.0")

    # ── Check 5: Perfect deal (optimal=0, actual=0) ──
    turn_log = [
        disp_turn(0, "greeting", "13:00"),
        wh_turn(1, slot_offered="13:00"),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    check(result["score"] == 1.0, "integration: perfect deal → 1.0")
    check(result["final_cost"] == 0, "integration: perfect deal cost=0")
    check(result["otif_saved"] is True, "integration: perfect deal OTIF saved")

    # ── Check 6: Suboptimal deal (optimal=0, actual>0) ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30"),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    expected_cost = 10100  # OTIF $10K + detention $100
    check(result["final_cost"] == expected_cost, f"integration: suboptimal cost={result['final_cost']} expected {expected_cost}")
    expected_score = 1.0 - (expected_cost / 10600)
    check(
        abs(result["score"] - expected_score) < 0.0001,
        f"integration: suboptimal score={result['score']:.4f} expected {expected_score:.4f}"
    )

    # ── Check 7: Standard deal (optimal>0) ──
    # Use a scenario where optimal_cost > 0 (e.g. MD delay where optimal is 13:30 w/ OTIF but no detention)
    md_scenario = {
        "scenario_id": "MD-2-4-OC-ASYM-NEU",
        "truck_arrival": "14:00",
        "mabd_deadline": "14:00",
        "hos_deadline": "15:00",
        "hos_deadline_with_dh": "16:00",
        "otif_penalty": 10000,
        "detention_free_minutes": 60,
        "detention_rate_per_hour": 100,
    }
    md_gt = {
        "optimal_slot": "14:30",
        "optimal_cost": 10000,  # OTIF penalty only
        "is_feasible": True,
        "max_possible_cost": 10500,
        "otif_saveable": False,
    }
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30"),
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3, slot_offered="16:00", drop_and_hook_response=True),  # D&H to stay within HOS
        disp_turn(4, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 1), md_scenario, md_gt
    )
    # 16:00: OTIF $10K + detention: wait=120min, free=60, billable=60 → 1hr → $100 → total $10100
    # hos_deadline_with_dh=16:00, slot=16:00 → not violated
    check(result["final_cost"] == 10100, f"integration: MD deal cost={result['final_cost']} expected 10100")
    check(result["hos_violated"] is False, "integration: MD deal HOS not violated with D&H")
    expected_score = min(1.0, 10000 / 10100)
    check(
        abs(result["score"] - expected_score) < 0.0001,
        f"integration: MD deal score={result['score']:.4f} expected {expected_score:.4f}"
    )

    # ── Check 8: Score capped at 1.0 ──
    # If somehow actual < optimal (shouldn't happen normally, but formula handles it)
    cap_gt = dict(md_gt, optimal_cost=10200)
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30"),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), md_scenario, cap_gt
    )
    # 14:30: OTIF $10K + detention $0 = $10000, optimal=10200 → min(1.0, 10200/10000) = 1.0
    check(result["score"] == 1.0, f"integration: score capped at 1.0, got {result['score']}")

    # ── Check 9: Rescheduling fee charged ──
    # Warehouse improved after accepting fee: prev=19:00, current=16:00 with fee accepted
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="19:00"),
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3, slot_offered="16:00", rescheduling_fee_accepted=True),
        disp_turn(4, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 1), test_scenario, test_ground_truth
    )
    # D&H not agreed → HOS deadline=15:00, slot=16:00 → HOS violation → score 0
    # But rescheduling fee should still be computed correctly
    check(result["rescheduling_fee_paid"] is True, "integration: rescheduling fee paid")
    # With D&H to avoid HOS violation:
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="19:00"),
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3, slot_offered="16:00", rescheduling_fee_accepted=True, drop_and_hook_response=True),
        disp_turn(4, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 1), test_scenario, test_ground_truth
    )
    check(result["rescheduling_fee_paid"] is True, "integration: fee paid with D&H")
    check(result["hos_violated"] is False, "integration: D&H avoids HOS at 16:00")
    # Cost: OTIF $10K + detention $200 + rescheduling $100 = $10300
    check(result["final_cost"] == 10300, f"integration: cost with fee={result['final_cost']} expected 10300")

    # ── Check 10: Rescheduling fee NOT charged (accepted but didn't improve) ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="19:00"),
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3, slot_offered="19:00", rescheduling_fee_accepted=True),  # same slot
        disp_turn(4, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 1), test_scenario, test_ground_truth
    )
    check(result["rescheduling_fee_paid"] is False, "integration: fee not paid (no improvement)")

    # ── Check 12: OTIF saved ──
    turn_log = [
        disp_turn(0, "greeting", "13:00"),
        wh_turn(1, slot_offered="13:00"),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    check(result["otif_saved"] is True, "integration: OTIF saved at 13:00")

    # ── Check 13: OTIF not saved ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30"),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    check(result["otif_saved"] is False, "integration: OTIF not saved at 14:30")

    # ── Check 14: First offer cost ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="19:00"),
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3, slot_offered="14:30"),
        disp_turn(4, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 1), test_scenario, test_ground_truth
    )
    # First offer = 19:00 → OTIF $10K + detention $500 = $10500
    check(
        result["cost_at_first_offer"] == 10500,
        f"integration: first offer cost={result['cost_at_first_offer']} expected 10500"
    )

    # ── Check 15: Offer withdrawn ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30"),
        disp_turn(2, "pushback", "13:00"),
        wh_turn(3, slot_offered="16:00", slot_withdrawn="14:30"),
        disp_turn(4, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 1), test_scenario, test_ground_truth
    )
    check(result["offer_withdrawn"] is True, "integration: offer_withdrawn detected")

    # No withdrawal
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30"),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    check(result["offer_withdrawn"] is False, "integration: no offer_withdrawn")

    # ── Check 16: Pushback count ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="19:00"),
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3, slot_offered="17:00"),
        disp_turn(4, "pushback", "14:30"),
        wh_turn(5, slot_offered="14:30"),
        disp_turn(6, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 2), test_scenario, test_ground_truth
    )
    check(result["total_pushbacks"] == 2, f"integration: pushback count={result['total_pushbacks']} expected 2")

    # ── Check 17: Total turns ──
    check(result["total_turns"] == 7, f"integration: total turns={result['total_turns']} expected 7")

    # ── Check 18: D&H agreed flag ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30", drop_and_hook_response=True),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    check(result["drop_and_hook_agreed"] is True, "integration: D&H agreed True")

    # D&H not agreed
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30"),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    check(result["drop_and_hook_agreed"] is False, "integration: D&H agreed False")

    # ── Check 19: Result dict keys ──
    expected_keys = {
        "scenario_id", "score", "final_slot", "final_cost", "optimal_slot",
        "optimal_cost", "cost_at_first_offer", "hos_violated", "otif_saved",
        "otif_was_saveable", "total_pushbacks", "total_turns",
        "drop_and_hook_agreed", "rescheduling_fee_paid", "offer_withdrawn",
    }
    check(set(result.keys()) == expected_keys, f"integration: result keys match spec")

    # ── Check 20: No warehouse offer (pushback_limit, no slot_offered) ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1),  # no slot offered
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3),  # no slot offered
        disp_turn(4, "pushback", "14:30"),
    ]
    result = score_conversation(
        make_result(turn_log, "pushback_limit", 2), test_scenario, test_ground_truth
    )
    check(result["final_slot"] is None, "integration: no offer → final_slot None")
    check(result["final_cost"] is None, "integration: no offer → final_cost None")
    check(result["score"] == 0.0, "integration: no offer → score 0")
    check(result["hos_violated"] is False, "integration: no offer → HOS not violated")

    # ── Check 21: Cross-check with ground truth slot_costs ──
    # Score a known slot (16:00 for SM delay) and verify cost matches ground truth
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="16:00"),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    gt_cost = test_ground_truth["slot_costs"]["16:00"]["total"]
    check(
        result["final_cost"] == gt_cost,
        f"integration: 16:00 cost={result['final_cost']} matches gt {gt_cost}"
    )

    # With rescheduling fee, cost should be gt + 100
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="19:00"),
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3, slot_offered="16:00", rescheduling_fee_accepted=True, drop_and_hook_response=True),
        disp_turn(4, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 1), test_scenario, test_ground_truth
    )
    check(
        result["final_cost"] == gt_cost + RESCHEDULING_FEE,
        f"integration: 16:00 + fee={result['final_cost']} expected {gt_cost + RESCHEDULING_FEE}"
    )

    # ── Check 22: Rescheduling fee with no prior offer ──
    # Warehouse accepts fee on first turn (no prev_offer) → fee not charged
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30", rescheduling_fee_accepted=True),
        disp_turn(2, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 0), test_scenario, test_ground_truth
    )
    check(
        result["rescheduling_fee_paid"] is False,
        "integration: fee not charged when no prior offer"
    )

    # ── Check: Rescheduling fee when warehouse worsened (not improved) ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="16:00"),
        disp_turn(2, "pushback", "14:30"),
        wh_turn(3, slot_offered="19:00", rescheduling_fee_accepted=True),  # worsened
        disp_turn(4, "accept"),
    ]
    result = score_conversation(
        make_result(turn_log, "accept", 1), test_scenario, test_ground_truth
    )
    check(
        result["rescheduling_fee_paid"] is False,
        "integration: fee not charged when warehouse worsened"
    )

    # ── Check: otif_was_saveable propagated from ground truth ──
    check(
        result["otif_was_saveable"] is True,
        "integration: otif_was_saveable from ground truth"
    )

    # ── Check: Walk-away has no first_offer impact on scoring ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="19:00"),
        disp_turn(2, "walk_away"),
    ]
    result = score_conversation(
        make_result(turn_log, "walk_away", 0), test_scenario, test_ground_truth
    )
    check(result["cost_at_first_offer"] == 10500, "integration: walk-away still has first_offer_cost")
    check(result["final_cost"] is None, "integration: walk-away final_cost None")

    # ── Check: Turn limit with offer on table ──
    turn_log = [
        disp_turn(0, "greeting", "14:30"),
        wh_turn(1, slot_offered="14:30"),
        disp_turn(2, "pushback", "13:00"),
        wh_turn(3, slot_offered="14:30"),
    ]
    result = score_conversation(
        make_result(turn_log, "turn_limit", 1), test_scenario, test_ground_truth
    )
    # Last warehouse slot_offered = 14:30 → scored normally
    check(result["final_slot"] == "14:30", "integration: turn_limit with offer → final_slot 14:30")
    check(result["final_cost"] == 10100, "integration: turn_limit scored on last offer")

    # ═══════════════════════════════════════════════════════════════════
    # MD scenario cross-check (detention from truck_arrival=14:00)
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- MD scenario detention cross-check ---")
    # truck_arrival=14:00, slot=16:00 → wait=120min, free=60, billable=60 → 1hr → $100
    check(
        _compute_detention("16:00", md_scenario) == 100,
        "MD detention: 16:00 slot, truck 14:00 → $100"
    )
    # slot=14:30 → wait=30min → $0
    check(
        _compute_detention("14:30", md_scenario) == 0,
        "MD detention: 14:30 slot, truck 14:00 → $0"
    )
    # slot=19:00 → wait=300min, free=60, billable=240 → 4hr → $400
    check(
        _compute_detention("19:00", md_scenario) == 400,
        "MD detention: 19:00 slot, truck 14:00 → $400"
    )

    # ═══════════════════════════════════════════════════════════════════
    # LG scenario cross-check (detention from truck_arrival=16:00)
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- LG scenario detention cross-check ---")
    lg_scenario = {
        "scenario_id": "LG-1-4-OC-ASYM-NEU",
        "truck_arrival": "16:00",
        "mabd_deadline": "13:00",
        "hos_deadline": "15:00",
        "hos_deadline_with_dh": "16:00",
        "otif_penalty": 10000,
        "detention_free_minutes": 60,
        "detention_rate_per_hour": 100,
    }
    # slot=16:00 → wait=0 → $0
    check(
        _compute_detention("16:00", lg_scenario) == 0,
        "LG detention: 16:00 slot, truck 16:00 → $0"
    )
    # slot=17:00 → wait=60min=free → $0
    check(
        _compute_detention("17:00", lg_scenario) == 0,
        "LG detention: 17:00 slot, truck 16:00 → $0 (within free)"
    )
    # slot=19:00 → wait=180min, free=60, billable=120 → 2hr → $200
    check(
        _compute_detention("19:00", lg_scenario) == 200,
        "LG detention: 19:00 slot, truck 16:00 → $200"
    )
    # slot=20:00 → wait=240min, free=60, billable=180 → ceil(3)=3hr → $300
    check(
        _compute_detention("20:00", lg_scenario) == 300,
        "LG detention: 20:00 slot, truck 16:00 → $300"
    )

    # ═══════════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n  {passed} checks passed, {failed} failed")
    if failed == 0:
        print("\nAll checks passed.")
    else:
        print("\nFix validation errors before proceeding.")

    # ═══════════════════════════════════════════════════════════════════
    # Live scoring test — score a real conversation result
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 50)
    print("Live scoring test (requires conversation.py live test output)")
    print("=" * 50)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Load scenarios and ground truth
    with open(os.path.join(base_dir, "config", "scenarios.json")) as f:
        scenarios = json.load(f)
    with open(os.path.join(base_dir, "config", "ground_truth.json")) as f:
        ground_truths = json.load(f)

    # Build lookup dicts
    scenario_lookup = {s["scenario_id"]: s for s in scenarios}
    gt_lookup = {g["scenario_id"]: g for g in ground_truths}

    # Check if there's a saved conversation result to score
    results_dir = os.path.join(base_dir, "results", "conversations")
    if os.path.isdir(results_dir):
        result_files = [f for f in os.listdir(results_dir) if f.endswith(".json")]
        if result_files:
            # Score the first available result
            result_file = result_files[0]
            with open(os.path.join(results_dir, result_file)) as f:
                conv_result = json.load(f)

            sid = conv_result["scenario_id"]
            if sid in scenario_lookup and sid in gt_lookup:
                scored = score_conversation(conv_result, scenario_lookup[sid], gt_lookup[sid])
                print(f"\nScored {sid}:")
                print(f"  Score:          {scored['score']:.4f}")
                print(f"  Final slot:     {scored['final_slot']}")
                print(f"  Final cost:     {scored['final_cost']}")
                print(f"  Optimal slot:   {scored['optimal_slot']}")
                print(f"  Optimal cost:   {scored['optimal_cost']}")
                print(f"  HOS violated:   {scored['hos_violated']}")
                print(f"  OTIF saved:     {scored['otif_saved']}")
                print(f"  D&H agreed:     {scored['drop_and_hook_agreed']}")
                print(f"  Fee paid:       {scored['rescheduling_fee_paid']}")
                print(f"  1st offer cost: {scored['cost_at_first_offer']}")
                print(f"  Pushbacks:      {scored['total_pushbacks']}")
                print(f"  Total turns:    {scored['total_turns']}")
            else:
                print(f"\nScenario {sid} not found in lookup dicts.")
        else:
            print("\nNo conversation result files found. Run conversation.py first.")
    else:
        print("\nNo results directory found. Run conversation.py first.")

    print("\nDone. Ready for checkpoint review.\n")

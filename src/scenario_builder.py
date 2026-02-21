"""CNB Scenario Builder

Generates all 288 scenario configurations and pre-computes ground truth
optimal outcomes for ConstrainedNegotiationBench.

Usage:
    python src/scenario_builder.py

Outputs:
    config/scenarios.json     — All 288 scenario configurations
    config/ground_truth.json  — Pre-computed optimal outcomes for each scenario
"""

import json
import math
import os

from config import (
    ALL_SLOTS,
    DAY_CONTEXTS,
    DELAYS,
    DETENTION_FREE_MINUTES,
    DETENTION_RATE_PER_HOUR,
    HOS_REMAINING,
    INFO_CONDITIONS,
    MABD_WINDOWS,
    ORIGINAL_APPOINTMENT,
    OTIF_PENALTY,
    PERSONAS,
    RETAILER_NAME,
    SHIPMENT_VALUE,
    UNLOAD_TIME_MINUTES,
    format_time,
    parse_time,
)


# ── Scenario Builder ───────────────────────────────────────────────────────────

def build_scenario(scenario_number, delay, mabd_hours, hos_hours, persona, info_condition, day_context):
    """Build a single scenario configuration dict."""
    delay_hours = delay["hours"]
    info_value, info_code = info_condition
    day_value, day_code = day_context

    truck_arrival = ORIGINAL_APPOINTMENT + delay_hours * 60
    mabd_deadline = ORIGINAL_APPOINTMENT + mabd_hours * 60
    hos_expiry = ORIGINAL_APPOINTMENT + hos_hours * 60
    hos_deadline = hos_expiry - UNLOAD_TIME_MINUTES
    hos_deadline_dh = hos_expiry
    detention_start = truck_arrival + DETENTION_FREE_MINUTES

    scenario_id = (
        f"{delay['code']}-{mabd_hours}-{hos_hours}-"
        f"{persona}-{info_code}-{day_code}"
    )

    return {
        "scenario_number": scenario_number,
        "scenario_id": scenario_id,
        "delay_hours": delay_hours,
        "delay_level": delay["level"],
        "truck_arrival": format_time(truck_arrival),
        "mabd_window_hours": mabd_hours,
        "mabd_deadline": format_time(mabd_deadline),
        "hos_remaining_hours": hos_hours,
        "hos_expiry": format_time(hos_expiry),
        "hos_deadline": format_time(hos_deadline),
        "hos_deadline_with_dh": format_time(hos_deadline_dh),
        "persona": persona,
        "info_condition": info_value,
        "day_context": day_value,
        "shipment_value": SHIPMENT_VALUE,
        "retailer_name": RETAILER_NAME,
        "otif_penalty": OTIF_PENALTY,
        "detention_free_minutes": DETENTION_FREE_MINUTES,
        "detention_rate_per_hour": DETENTION_RATE_PER_HOUR,
        "detention_start": format_time(detention_start),
        "available_slots": ALL_SLOTS,
    }


# ── Cost Computation ───────────────────────────────────────────────────────────

def compute_slot_cost(slot_time_str, truck_arrival_str, mabd_deadline_str):
    """Compute cost components for a single slot.

    Returns dict with otif, otif_compliant, detention, total.
    Used for both ground truth computation and the calculate_slot_cost tool.
    """
    slot = parse_time(slot_time_str)
    truck_arrival = parse_time(truck_arrival_str)
    mabd = parse_time(mabd_deadline_str)

    # OTIF: compliant if slot <= MABD deadline
    otif_compliant = slot <= mabd
    otif = 0 if otif_compliant else OTIF_PENALTY

    # Detention: from truck arrival, 60 min free, then $100/hr rounded up
    wait_minutes = max(0, slot - truck_arrival)
    billable_minutes = max(0, wait_minutes - DETENTION_FREE_MINUTES)
    if billable_minutes > 0:
        detention = math.ceil(billable_minutes / 60) * DETENTION_RATE_PER_HOUR
    else:
        detention = 0

    return {
        "otif": otif,
        "otif_compliant": otif_compliant,
        "detention": detention,
        "total": otif + detention,
    }


# ── Ground Truth ───────────────────────────────────────────────────────────────

def compute_ground_truth(scenario):
    """Compute ground truth optimal outcome for a scenario.

    Includes cost/feasibility for all 8 slots, feasible sets, optimal slot,
    and binding constraint analysis.
    """
    truck_arrival = parse_time(scenario["truck_arrival"])
    hos_deadline = parse_time(scenario["hos_deadline"])
    hos_deadline_dh = parse_time(scenario["hos_deadline_with_dh"])
    mabd_deadline = parse_time(scenario["mabd_deadline"])

    # Cost + feasibility for all 8 slots (including unreachable, for debugging)
    slot_costs = {}
    for slot_str in ALL_SLOTS:
        slot = parse_time(slot_str)
        cost = compute_slot_cost(
            slot_str, scenario["truck_arrival"], scenario["mabd_deadline"]
        )
        cost["feasible"] = slot <= hos_deadline
        cost["feasible_dh"] = slot <= hos_deadline_dh
        slot_costs[slot_str] = cost

    # Slots the truck can physically reach
    available_for_truck = [s for s in ALL_SLOTS if parse_time(s) >= truck_arrival]

    # Feasible = reachable + within HOS deadline
    feasible_slots = [s for s in available_for_truck if parse_time(s) <= hos_deadline]
    feasible_slots_dh = [s for s in available_for_truck if parse_time(s) <= hos_deadline_dh]

    # OTIF saveable: any achievable slot (with or without D&H) saves OTIF?
    all_achievable = set(feasible_slots) | set(feasible_slots_dh)
    otif_saveable = any(parse_time(s) <= mabd_deadline for s in all_achievable)

    # Optimal slot: minimize total cost, tiebreak by earliest time
    if feasible_slots:
        optimal_slot = min(
            feasible_slots, key=lambda s: (slot_costs[s]["total"], s)
        )
        optimal_cost = slot_costs[optimal_slot]["total"]
        requires_dh = False
        is_feasible = True
        is_feasible_only_with_dh = False
    elif feasible_slots_dh:
        optimal_slot = min(
            feasible_slots_dh, key=lambda s: (slot_costs[s]["total"], s)
        )
        optimal_cost = slot_costs[optimal_slot]["total"]
        requires_dh = True
        is_feasible = True
        is_feasible_only_with_dh = True
    else:
        optimal_slot = None
        optimal_cost = None
        requires_dh = False
        is_feasible = False
        is_feasible_only_with_dh = False

    # Binding constraint: what most limits the feasible/optimal space
    if not feasible_slots and feasible_slots_dh:
        binding_constraint = "HOS"  # D&H required to create feasibility
    elif not feasible_slots and not feasible_slots_dh:
        binding_constraint = "HOS"  # Impossible scenario
    elif otif_saveable:
        binding_constraint = "OTIF"  # OTIF cliff drives optimal choice
    elif len(feasible_slots) < len(available_for_truck):
        binding_constraint = "HOS"   # HOS eliminates reachable slots
    else:
        binding_constraint = "detention"

    # Max possible cost: cost at 20:00 for this delay level (per-scenario)
    max_possible_cost = slot_costs["20:00"]["total"]

    return {
        "scenario_id": scenario["scenario_id"],
        "available_slots_for_truck": available_for_truck,
        "feasible_slots": feasible_slots,
        "feasible_slots_with_dh": feasible_slots_dh,
        "otif_saveable": otif_saveable,
        "optimal_slot": optimal_slot,
        "optimal_cost": optimal_cost,
        "requires_dh": requires_dh,
        "binding_constraint": binding_constraint,
        "is_feasible": is_feasible,
        "is_feasible_only_with_dh": is_feasible_only_with_dh,
        "max_possible_cost": max_possible_cost,
        "slot_costs": slot_costs,
    }


# ── Generation ─────────────────────────────────────────────────────────────────

def generate_all():
    """Generate all 288 scenarios and ground truths.

    Iteration order: delay -> mabd -> hos -> info -> persona -> day
    (groups by dispatcher prompt for cache efficiency)
    """
    scenarios = []
    ground_truths = []

    n = 0
    for delay in DELAYS:
        for mabd in MABD_WINDOWS:
            for hos in HOS_REMAINING:
                for info in INFO_CONDITIONS:
                    for persona in PERSONAS:
                        for day in DAY_CONTEXTS:
                            n += 1
                            scenario = build_scenario(
                                n, delay, mabd, hos, persona, info, day
                            )
                            gt = compute_ground_truth(scenario)
                            scenarios.append(scenario)
                            ground_truths.append(gt)

    return scenarios, ground_truths


# ── Validation ─────────────────────────────────────────────────────────────────

def validate(scenarios, ground_truths):
    """Run validation checks against experiment design tables.

    Returns (passed_count, failed_count).
    """
    passed = 0
    failed = 0

    def check(condition, message):
        nonlocal passed, failed
        if condition:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {message}")

    # ── 1. Counts ──
    check(len(scenarios) == 288, f"Expected 288 scenarios, got {len(scenarios)}")
    check(len(ground_truths) == 288, f"Expected 288 ground truths, got {len(ground_truths)}")

    # ── 2. Unique IDs ──
    ids = [s["scenario_id"] for s in scenarios]
    check(len(set(ids)) == 288, f"Expected 288 unique IDs, got {len(set(ids))}")

    # ── 3. Slot costs completeness ──
    for gt in ground_truths:
        check(
            len(gt["slot_costs"]) == 8,
            f"{gt['scenario_id']}: expected 8 slot_costs, got {len(gt['slot_costs'])}",
        )

    # ── 4. Optimal slots (from Experiment Design section 9) ──
    expected_optimals = {
        # (delay_code, mabd, hos): (optimal_slot, optimal_cost, requires_dh)
        ("SM", 1, 4): ("13:00", 0, False),
        ("SM", 1, 7): ("13:00", 0, False),
        ("SM", 2, 4): ("13:00", 0, False),
        ("SM", 2, 7): ("13:00", 0, False),
        ("MD", 1, 4): ("14:30", 10000, False),
        ("MD", 1, 7): ("14:30", 10000, False),
        ("MD", 2, 4): ("14:30", 10000, False),
        ("MD", 2, 7): ("14:30", 10000, False),
        ("LG", 1, 4): ("16:00", 10000, True),
        ("LG", 1, 7): ("16:00", 10000, False),
        ("LG", 2, 4): ("16:00", 10000, True),
        ("LG", 2, 7): ("16:00", 10000, False),
    }

    for (dc, mabd, hos), (exp_slot, exp_cost, exp_dh) in expected_optimals.items():
        prefix = f"{dc}-{mabd}-{hos}-"
        matching = [gt for gt in ground_truths if gt["scenario_id"].startswith(prefix)]
        if matching:
            gt = matching[0]
            check(
                gt["optimal_slot"] == exp_slot,
                f"{prefix}*: optimal slot expected {exp_slot}, got {gt['optimal_slot']}",
            )
            check(
                gt["optimal_cost"] == exp_cost,
                f"{prefix}*: optimal cost expected ${exp_cost}, got ${gt['optimal_cost']}",
            )
            check(
                gt["requires_dh"] == exp_dh,
                f"{prefix}*: requires_dh expected {exp_dh}, got {gt['requires_dh']}",
            )

    # ── 5. Detention costs (from Experiment Design section 6) ──
    expected_detention = {
        "SM": {
            "13:00": 0, "13:30": 0, "14:30": 100, "16:00": 200,
            "17:00": 300, "19:00": 500, "19:30": 600, "20:00": 600,
        },
        "MD": {
            "14:30": 0, "16:00": 100, "17:00": 200,
            "19:00": 400, "19:30": 500, "20:00": 500,
        },
        "LG": {
            "16:00": 0, "17:00": 0, "19:00": 200,
            "19:30": 300, "20:00": 300,
        },
    }

    for delay_code, det_map in expected_detention.items():
        matching = [
            gt for gt in ground_truths
            if gt["scenario_id"].startswith(f"{delay_code}-")
        ]
        if matching:
            gt = matching[0]
            for slot, exp_det in det_map.items():
                actual = gt["slot_costs"][slot]["detention"]
                check(
                    actual == exp_det,
                    f"{delay_code} detention at {slot}: expected ${exp_det}, got ${actual}",
                )

    # ── 6. OTIF saveable: only small delay scenarios ──
    for gt in ground_truths:
        is_small = gt["scenario_id"].startswith("SM-")
        if is_small:
            check(
                gt["otif_saveable"],
                f"{gt['scenario_id']}: small delay should have saveable OTIF",
            )
        else:
            check(
                not gt["otif_saveable"],
                f"{gt['scenario_id']}: non-small delay should NOT have saveable OTIF",
            )

    # ── 7. D&H required: only LG + HOS 4hr ──
    for gt in ground_truths:
        parts = gt["scenario_id"].split("-")
        is_lg_4 = parts[0] == "LG" and int(parts[2]) == 4
        if is_lg_4:
            check(
                gt["requires_dh"],
                f"{gt['scenario_id']}: LG+4hr should require D&H",
            )
            check(
                gt["is_feasible_only_with_dh"],
                f"{gt['scenario_id']}: should be feasible only with D&H",
            )
        else:
            check(
                not gt["requires_dh"],
                f"{gt['scenario_id']}: should NOT require D&H",
            )

    # ── 8. All scenarios feasible (with or without D&H) ──
    for gt in ground_truths:
        check(
            gt["is_feasible"],
            f"{gt['scenario_id']}: should be feasible",
        )

    # ── 9. Max possible cost by delay level ──
    expected_max = {"SM": 10600, "MD": 10500, "LG": 10300}
    for gt in ground_truths:
        delay_code = gt["scenario_id"].split("-")[0]
        check(
            gt["max_possible_cost"] == expected_max[delay_code],
            f"{gt['scenario_id']}: max_cost expected ${expected_max[delay_code]}, "
            f"got ${gt['max_possible_cost']}",
        )

    # ── 10. Computed time fields spot-check ──
    spot_checks = [
        # (scenario_id_prefix, field, expected_value)
        ("SM-", "truck_arrival", "13:00"),
        ("SM-", "detention_start", "14:00"),
        ("MD-", "truck_arrival", "14:00"),
        ("MD-", "detention_start", "15:00"),
        ("LG-", "truck_arrival", "16:00"),
        ("LG-", "detention_start", "17:00"),
    ]
    for prefix, field, expected in spot_checks:
        matching = [s for s in scenarios if s["scenario_id"].startswith(prefix)]
        if matching:
            check(
                matching[0][field] == expected,
                f"{prefix}* {field}: expected {expected}, got {matching[0][field]}",
            )

    # HOS deadline spot-checks
    hos_checks = [
        # (delay, hos, hos_deadline, hos_deadline_with_dh, hos_expiry)
        ("SM", 4, "15:00", "16:00", "16:00"),
        ("SM", 7, "18:00", "19:00", "19:00"),
        ("MD", 4, "15:00", "16:00", "16:00"),
        ("MD", 7, "18:00", "19:00", "19:00"),
        ("LG", 4, "15:00", "16:00", "16:00"),
        ("LG", 7, "18:00", "19:00", "19:00"),
    ]
    for dc, hos, exp_dl, exp_dh, exp_ex in hos_checks:
        prefix = f"{dc}-1-{hos}-"
        matching = [s for s in scenarios if s["scenario_id"].startswith(prefix)]
        if matching:
            s = matching[0]
            check(s["hos_deadline"] == exp_dl, f"{prefix}* hos_deadline expected {exp_dl}, got {s['hos_deadline']}")
            check(s["hos_deadline_with_dh"] == exp_dh, f"{prefix}* hos_deadline_dh expected {exp_dh}, got {s['hos_deadline_with_dh']}")
            check(s["hos_expiry"] == exp_ex, f"{prefix}* hos_expiry expected {exp_ex}, got {s['hos_expiry']}")

    # MABD deadline spot-checks
    for s in scenarios:
        if s["mabd_window_hours"] == 1:
            check(s["mabd_deadline"] == "13:00", f"{s['scenario_id']}: MABD 1hr should be 13:00")
        else:
            check(s["mabd_deadline"] == "14:00", f"{s['scenario_id']}: MABD 2hr should be 14:00")

    return passed, failed


# ── Output ─────────────────────────────────────────────────────────────────────

def save(scenarios, ground_truths, base_dir):
    """Save scenarios and ground truths to JSON files."""
    config_dir = os.path.join(base_dir, "config")
    os.makedirs(config_dir, exist_ok=True)

    scenarios_path = os.path.join(config_dir, "scenarios.json")
    with open(scenarios_path, "w") as f:
        json.dump(scenarios, f, indent=2)
    print(f"  {scenarios_path}")

    gt_path = os.path.join(config_dir, "ground_truth.json")
    with open(gt_path, "w") as f:
        json.dump(ground_truths, f, indent=2)
    print(f"  {gt_path}")


def print_summary(ground_truths):
    """Print summary tables for visual verification."""

    # ── Ground truth by constraint combination ──
    print("\n  Delay  MABD  HOS  Feasible              Feasible+DH           OTIF  Optimal    Cost  Bind  D&H")
    print("  " + "-" * 100)

    seen = set()
    for gt in ground_truths:
        parts = gt["scenario_id"].split("-")
        combo = (parts[0], parts[1], parts[2])
        if combo in seen:
            continue
        seen.add(combo)

        f_str = ", ".join(gt["feasible_slots"]) if gt["feasible_slots"] else "(none)"
        fd_str = ", ".join(gt["feasible_slots_with_dh"]) if gt["feasible_slots_with_dh"] else "(none)"

        print(
            f"  {parts[0]:<5}  {parts[1]:>4}  {parts[2]:>3}  "
            f"{f_str:<20}  {fd_str:<20}  "
            f"{'Y' if gt['otif_saveable'] else 'N':>4}  "
            f"{gt['optimal_slot'] or 'N/A':<7}  "
            f"${gt['optimal_cost']:>5,}  "
            f"{gt['binding_constraint']:<4}  "
            f"{'Y' if gt['requires_dh'] else 'N'}"
        )

    # ── Detention tables by delay level ──
    for delay_code, arrival in [("SM", "13:00"), ("MD", "14:00"), ("LG", "16:00")]:
        matching = [gt for gt in ground_truths if gt["scenario_id"].startswith(f"{delay_code}-")]
        gt = matching[0]
        available = gt["available_slots_for_truck"]

        print(f"\n  Detention — {delay_code} delay (truck arrives {arrival}):")
        print(f"  {'Slot':<7} {'Wait':>6}  {'Billable':>8}  {'Detention':>9}  {'OTIF':>7}  {'Total':>7}")
        print("  " + "-" * 52)

        for slot in available:
            sc = gt["slot_costs"][slot]
            wait = max(0, parse_time(slot) - parse_time(arrival))
            billable = max(0, wait - DETENTION_FREE_MINUTES)
            print(
                f"  {slot:<7} {wait:>4}m  {billable:>6}m  "
                f"${sc['detention']:>7,}  ${sc['otif']:>5,}  ${sc['total']:>5,}"
            )

    # ── Distribution ──
    binding_counts = {}
    dh_count = 0
    otif_count = 0
    for gt in ground_truths:
        b = gt["binding_constraint"]
        binding_counts[b] = binding_counts.get(b, 0) + 1
        if gt["requires_dh"]:
            dh_count += 1
        if gt["otif_saveable"]:
            otif_count += 1

    print(f"\n  Distribution:")
    print(f"    Total scenarios:    288")
    print(f"    OTIF saveable:      {otif_count}")
    print(f"    Requires D&H:       {dh_count}")
    print(f"    Binding constraint: {binding_counts}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("\nCNB Scenario Builder")
    print("=" * 40)

    print("\nGenerating...")
    scenarios, ground_truths = generate_all()
    print(f"  {len(scenarios)} scenarios, {len(ground_truths)} ground truths")

    print("\nValidating...")
    passed, failed = validate(scenarios, ground_truths)
    print(f"\n  {passed} checks passed, {failed} failed")

    if failed > 0:
        print("\nFix validation errors before proceeding.")
        return False

    print("\nSaving...")
    save(scenarios, ground_truths, base_dir)

    print_summary(ground_truths)

    print("\nDone. Ready for checkpoint review.\n")
    return True


if __name__ == "__main__":
    main()

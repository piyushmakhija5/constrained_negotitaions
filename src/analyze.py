#!/usr/bin/env python3
"""CNB Post-Experiment Analysis

Loads results data (summary.json + conversation files + ground truth + scenarios),
runs 10 tiers of analysis + 12 specific findings (F1-F12), outputs formatted tables
to console + CSV files to results/analysis/.

Usage:
    python src/analyze.py                    # Full analysis
    python src/analyze.py --tier 1           # Just Tier 1
    python src/analyze.py --finding F1       # Just Finding F1
    python src/analyze.py --no-csv           # Console only
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict, namedtuple
from statistics import mean, median, stdev

# ── Path setup ──────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from config import parse_time, ALL_SLOTS, OTIF_PENALTY, RESCHEDULING_FEE

# ── Constants ───────────────────────────────────────────────────────────────

DELAY_CODES = {"SM": "Small (1hr)", "MD": "Medium (2hr)", "LG": "Large (4hr)"}
MABD_CODES = {"1": "1hr (tight)", "2": "2hr (loose)"}
HOS_CODES = {"4": "4hr (tight)", "7": "7hr (comfortable)"}
PERSONA_CODES = {
    "OC": "Op. Constrained",
    "FR": "Frustrated",
    "GK": "Gatekeeper",
    "CD": "Convenience",
}
INFO_CODES = {"ASYM": "Asymmetric", "TRANS": "Transparent"}
DAY_CODES = {"NEU": "Neutral", "POS": "Positive", "NEG": "Negative"}

DIM_LABELS = {
    "delay": DELAY_CODES,
    "mabd": MABD_CODES,
    "hos": HOS_CODES,
    "persona": PERSONA_CODES,
    "info": INFO_CODES,
    "day": DAY_CODES,
}

Dims = namedtuple("Dims", ["delay", "mabd", "hos", "persona", "info", "day"])
Data = namedtuple("Data", ["results", "conversations", "ground_truth", "scenarios"])

# ── Data Loading ────────────────────────────────────────────────────────────


def load_data():
    """Load all data sources. Returns Data namedtuple."""
    with open(os.path.join(BASE_DIR, "results", "summary.json")) as f:
        summary = json.load(f)
    results = summary["results"]

    with open(os.path.join(BASE_DIR, "config", "ground_truth.json")) as f:
        gt_list = json.load(f)
    ground_truth = {g["scenario_id"]: g for g in gt_list}

    with open(os.path.join(BASE_DIR, "config", "scenarios.json")) as f:
        sc_list = json.load(f)
    scenarios = {s["scenario_id"]: s for s in sc_list}

    conv_dir = os.path.join(BASE_DIR, "results", "conversations")
    conversations = {}
    for fname in os.listdir(conv_dir):
        if fname.endswith(".json"):
            with open(os.path.join(conv_dir, fname)) as f:
                conv = json.load(f)
            sid = conv.get("scenario_id") or fname.replace(".json", "")
            conversations[sid] = conv

    return Data(
        results=results,
        conversations=conversations,
        ground_truth=ground_truth,
        scenarios=scenarios,
    )


# ── Scenario ID Parsing ────────────────────────────────────────────────────


def parse_scenario_id(sid):
    """Parse scenario ID into Dims namedtuple.

    Format: {Delay}-{MABD}-{HOS}-{Persona}-{Info}-{Day}
    Example: SM-1-4-OC-ASYM-NEU
    """
    parts = sid.split("-")
    return Dims(
        delay=parts[0],
        mabd=parts[1],
        hos=parts[2],
        persona=parts[3],
        info=parts[4],
        day=parts[5],
    )


def group_by(results, dim_name):
    """Group results by a single dimension. Returns {dim_value: [results]}."""
    groups = defaultdict(list)
    for r in results:
        dims = parse_scenario_id(r["scenario_id"])
        groups[getattr(dims, dim_name)].append(r)
    return dict(groups)


def group_by_multi(results, dim_names):
    """Group results by multiple dimensions. Returns {(val1, val2, ...): [results]}."""
    groups = defaultdict(list)
    for r in results:
        dims = parse_scenario_id(r["scenario_id"])
        key = tuple(getattr(dims, d) for d in dim_names)
        groups[key].append(r)
    return dict(groups)


# ── Output Helpers ──────────────────────────────────────────────────────────

OUTPUT_DIR = os.path.join(BASE_DIR, "results", "analysis")
WRITE_CSV = True


def print_table(title, headers, rows, csv_name=None):
    """Print a formatted table and optionally write CSV."""
    print(f"\n{'─' * 70}")
    print(f"  {title}")
    print(f"{'─' * 70}")

    all_rows = [headers] + rows
    widths = [max(len(str(cell)) for cell in col) for col in zip(*all_rows)]

    header_line = "  ".join(str(h).ljust(w) for h, w in zip(headers, widths))
    print(f"  {header_line}")
    print(f"  {'  '.join('─' * w for w in widths)}")

    for row in rows:
        line = "  ".join(str(c).ljust(w) for c, w in zip(row, widths))
        print(f"  {line}")

    if WRITE_CSV and csv_name:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = os.path.join(OUTPUT_DIR, csv_name)
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)


def print_narrative(title, text):
    """Print a finding narrative block."""
    print(f"\n{'═' * 70}")
    print(f"  {title}")
    print(f"{'═' * 70}")
    for line in text.strip().split("\n"):
        print(f"  {line}")


def fmt_pct(value):
    return f"{value * 100:.1f}%"


def fmt_score(value):
    return f"{value:.3f}"


def fmt_dollar(value):
    if value is None:
        return "N/A"
    return f"${value:,.0f}"


def stats_row(label, scores):
    """Build a stats row: [label, n, mean, median, stdev]."""
    n = len(scores)
    if n == 0:
        return [label, 0, "N/A", "N/A", "N/A"]
    m = mean(scores)
    med = median(scores)
    sd = stdev(scores) if n > 1 else 0.0
    return [label, n, fmt_score(m), fmt_score(med), fmt_score(sd)]


def dim_label(dim_name, code):
    return DIM_LABELS.get(dim_name, {}).get(code, code)


# ── Turn log helpers ────────────────────────────────────────────────────────


def _get_dispatcher_turns(conv):
    return [t for t in conv.get("turn_log", []) if t["agent"] == "dispatcher"]


def _get_warehouse_turns(conv):
    return [t for t in conv.get("turn_log", []) if t["agent"] == "warehouse"]


# ── Tier 1: Outcome Metrics ────────────────────────────────────────────────


def tier1_score_by_dimension(results):
    """Score statistics by each dimension and key cross-dimensions."""
    print("\n\n" + "=" * 70)
    print("  TIER 1: OUTCOME METRICS")
    print("=" * 70)

    for dim_name in ["delay", "mabd", "hos", "persona", "info", "day"]:
        groups = group_by(results, dim_name)
        headers = ["Dimension", "N", "Mean", "Median", "StDev"]
        rows = []
        for code in sorted(groups.keys()):
            scores = [r["score"] for r in groups[code]]
            rows.append(stats_row(dim_label(dim_name, code), scores))
        print_table(
            f"Score by {dim_name.upper()}",
            headers,
            rows,
            f"t1_score_by_{dim_name}.csv",
        )

    cross_dims = [
        ("delay", "mabd"),
        ("delay", "hos"),
        ("delay", "persona"),
        ("persona", "info"),
    ]
    for d1, d2 in cross_dims:
        groups = group_by_multi(results, [d1, d2])
        headers = [d1.upper(), d2.upper(), "N", "Mean", "Median", "StDev"]
        rows = []
        for key in sorted(groups.keys()):
            scores = [r["score"] for r in groups[key]]
            row = stats_row("", scores)
            rows.append(
                [dim_label(d1, key[0]), dim_label(d2, key[1])] + row[1:]
            )
        print_table(
            f"Score by {d1.upper()} x {d2.upper()}",
            headers,
            rows,
            f"t1_score_{d1}_{d2}.csv",
        )


def tier1_hos_violations(results):
    """HOS violation rate by each dimension."""
    for dim_name in ["delay", "hos", "persona", "info", "day"]:
        groups = group_by(results, dim_name)
        headers = ["Dimension", "N", "Violations", "Rate"]
        rows = []
        for code in sorted(groups.keys()):
            items = groups[code]
            violations = sum(1 for r in items if r["hos_violated"])
            rows.append([
                dim_label(dim_name, code),
                len(items),
                violations,
                fmt_pct(violations / len(items)),
            ])
        print_table(
            f"HOS Violations by {dim_name.upper()}",
            headers,
            rows,
            f"t1_hos_violations_{dim_name}.csv",
        )


def tier1_otif_saved(results):
    """OTIF save rate within saveable scenarios (SM-delay only)."""
    saveable = [r for r in results if r["otif_was_saveable"]]
    saved_count = sum(1 for r in saveable if r["otif_saved"])
    print_table(
        "OTIF Save Rate (saveable scenarios only)",
        ["Metric", "Value"],
        [
            ["Total saveable", len(saveable)],
            ["Saved", saved_count],
            ["Rate", fmt_pct(saved_count / len(saveable))],
        ],
        None,
    )

    for dim_name in ["persona", "info", "day", "hos", "mabd"]:
        groups = group_by(saveable, dim_name)
        headers = ["Dimension", "N", "Saved", "Rate"]
        rows = []
        for code in sorted(groups.keys()):
            items = groups[code]
            saved = sum(1 for r in items if r["otif_saved"])
            rows.append([
                dim_label(dim_name, code),
                len(items),
                saved,
                fmt_pct(saved / len(items)) if items else "N/A",
            ])
        print_table(
            f"OTIF Save Rate by {dim_name.upper()}",
            headers,
            rows,
            f"t1_otif_saved_{dim_name}.csv",
        )


def tier1_optimal_hit(results):
    """Optimal slot hit rate by dimension."""
    for dim_name in ["delay", "persona", "hos", "mabd"]:
        groups = group_by(results, dim_name)
        headers = ["Dimension", "N", "Optimal Hits", "Rate"]
        rows = []
        for code in sorted(groups.keys()):
            items = groups[code]
            hits = sum(1 for r in items if r["final_slot"] == r["optimal_slot"])
            rows.append([
                dim_label(dim_name, code),
                len(items),
                hits,
                fmt_pct(hits / len(items)),
            ])
        print_table(
            f"Optimal Slot Hit Rate by {dim_name.upper()}",
            headers,
            rows,
            f"t1_optimal_hit_{dim_name}.csv",
        )


def tier1_dh_rates(results):
    """Drop-and-hook agreement rate by delay/HOS/persona."""
    for dim_name in ["delay", "hos", "persona"]:
        groups = group_by(results, dim_name)
        headers = ["Dimension", "N", "D&H Agreed", "Rate"]
        rows = []
        for code in sorted(groups.keys()):
            items = groups[code]
            dh = sum(1 for r in items if r["drop_and_hook_agreed"])
            rows.append([
                dim_label(dim_name, code),
                len(items),
                dh,
                fmt_pct(dh / len(items)),
            ])
        print_table(
            f"D&H Agreement Rate by {dim_name.upper()}",
            headers,
            rows,
            f"t1_dh_rate_{dim_name}.csv",
        )


def tier1_pushbacks(results):
    """Pushback count distribution + mean by delay/persona."""
    counter = Counter(r["total_pushbacks"] for r in results)
    headers = ["Pushbacks", "Count", "Pct"]
    rows = []
    for pb in sorted(counter.keys()):
        rows.append([pb, counter[pb], fmt_pct(counter[pb] / len(results))])
    print_table("Pushback Distribution", headers, rows, "t1_pushback_dist.csv")

    for dim_name in ["delay", "persona"]:
        groups = group_by(results, dim_name)
        headers = ["Dimension", "N", "Mean Pushbacks", "Median"]
        rows = []
        for code in sorted(groups.keys()):
            items = groups[code]
            pbs = [r["total_pushbacks"] for r in items]
            rows.append([
                dim_label(dim_name, code),
                len(items),
                f"{mean(pbs):.2f}",
                f"{median(pbs):.1f}",
            ])
        print_table(
            f"Mean Pushbacks by {dim_name.upper()}",
            headers,
            rows,
            f"t1_pushbacks_{dim_name}.csv",
        )


def tier1_cost_efficiency(results):
    """Overpay analysis for non-walkaway, non-HOS deals."""
    deals = [
        r
        for r in results
        if r["final_cost"] is not None and not r["hos_violated"]
    ]
    overpays = []
    for r in deals:
        overpay = (r["final_cost"] or 0) - r["optimal_cost"]
        if overpay > 0:
            overpays.append(overpay)

    total_deals = len(deals)
    overpay_count = len(overpays)
    rows = [
        ["Total deals (no walkaway, no HOS)", total_deals],
        ["Deals with overpay", overpay_count],
        [
            "Overpay rate",
            fmt_pct(overpay_count / total_deals) if total_deals else "N/A",
        ],
        ["Mean overpay ($)", fmt_dollar(mean(overpays)) if overpays else "N/A"],
        [
            "Median overpay ($)",
            fmt_dollar(median(overpays)) if overpays else "N/A",
        ],
        ["Max overpay ($)", fmt_dollar(max(overpays)) if overpays else "N/A"],
    ]
    print_table(
        "Cost Efficiency (Overpay Analysis)",
        ["Metric", "Value"],
        rows,
        "t1_cost_efficiency.csv",
    )


def run_tier1(results):
    tier1_score_by_dimension(results)
    tier1_hos_violations(results)
    tier1_otif_saved(results)
    tier1_optimal_hit(results)
    tier1_dh_rates(results)
    tier1_pushbacks(results)
    tier1_cost_efficiency(results)


# ── Tier 2: Constraint Reasoning ───────────────────────────────────────────


def tier2_hos_awareness(data):
    """Did dispatcher cite HOS when it's the binding constraint?"""
    print("\n\n" + "=" * 70)
    print("  TIER 2: CONSTRAINT REASONING")
    print("=" * 70)

    hos_binding = [
        sid
        for sid, gt in data.ground_truth.items()
        if gt["binding_constraint"] == "HOS"
    ]

    cited = 0
    total = 0
    by_delay = defaultdict(lambda: {"cited": 0, "total": 0})
    for sid in hos_binding:
        if sid not in data.conversations:
            continue
        conv = data.conversations[sid]
        dtns = _get_dispatcher_turns(conv)
        dims = parse_scenario_id(sid)
        total += 1
        any_hos = any(
            "hos_cite" in t["metadata"].get("tactics_used", []) for t in dtns
        )
        if any_hos:
            cited += 1
            by_delay[dims.delay]["cited"] += 1
        by_delay[dims.delay]["total"] += 1

    rows = [["Overall", total, cited, fmt_pct(cited / total) if total else "N/A"]]
    for code in sorted(by_delay.keys()):
        d = by_delay[code]
        rows.append([
            dim_label("delay", code),
            d["total"],
            d["cited"],
            fmt_pct(d["cited"] / d["total"]) if d["total"] else "N/A",
        ])
    print_table(
        "HOS Awareness (binding constraint = HOS)",
        ["Group", "N", "Cited HOS", "Rate"],
        rows,
        "t2_hos_awareness.csv",
    )


def tier2_sunk_cost_error(data):
    """Did dispatcher cite OTIF when unsaveable? Break by MABD."""
    unsaveable = [
        sid
        for sid, gt in data.ground_truth.items()
        if not gt["otif_saveable"]
    ]

    cited = 0
    total = 0
    by_mabd = defaultdict(lambda: {"cited": 0, "total": 0})
    for sid in unsaveable:
        if sid not in data.conversations:
            continue
        conv = data.conversations[sid]
        dtns = _get_dispatcher_turns(conv)
        dims = parse_scenario_id(sid)
        total += 1
        any_otif = any(
            "otif_cite" in t["metadata"].get("tactics_used", []) for t in dtns
        )
        if any_otif:
            cited += 1
            by_mabd[dims.mabd]["cited"] += 1
        by_mabd[dims.mabd]["total"] += 1

    rows = [["Overall", total, cited, fmt_pct(cited / total) if total else "N/A"]]
    for code in sorted(by_mabd.keys()):
        d = by_mabd[code]
        rows.append([
            dim_label("mabd", code),
            d["total"],
            d["cited"],
            fmt_pct(d["cited"] / d["total"]) if d["total"] else "N/A",
        ])
    print_table(
        "Sunk Cost Error (OTIF cited when unsaveable)",
        ["Group", "N", "Cited OTIF", "Rate"],
        rows,
        "t2_sunk_cost_error.csv",
    )


def tier2_dh_proposal_lg4(data):
    """D&H proposal rate in requires_dh scenarios."""
    targets = [
        sid for sid, gt in data.ground_truth.items() if gt.get("requires_dh")
    ]

    proposed = 0
    total = 0
    by_persona = defaultdict(lambda: {"proposed": 0, "total": 0})
    for sid in targets:
        if sid not in data.conversations:
            continue
        conv = data.conversations[sid]
        dtns = _get_dispatcher_turns(conv)
        dims = parse_scenario_id(sid)
        total += 1
        any_dh = any(
            "drop_and_hook" in t["metadata"].get("tactics_used", [])
            for t in dtns
        )
        if any_dh:
            proposed += 1
            by_persona[dims.persona]["proposed"] += 1
        by_persona[dims.persona]["total"] += 1

    rows = [
        ["Overall", total, proposed, fmt_pct(proposed / total) if total else "N/A"]
    ]
    for code in sorted(by_persona.keys()):
        d = by_persona[code]
        rows.append([
            dim_label("persona", code),
            d["total"],
            d["proposed"],
            fmt_pct(d["proposed"] / d["total"]) if d["total"] else "N/A",
        ])
    print_table(
        "D&H Proposal Rate (requires_dh scenarios)",
        ["Group", "N", "Proposed D&H", "Rate"],
        rows,
        "t2_dh_proposal_lg4.csv",
    )


def tier2_binding_constraint_citation(data):
    """Accuracy of citing correct binding constraint (OTIF vs HOS)."""
    correct = 0
    total = 0
    by_constraint = defaultdict(lambda: {"correct": 0, "total": 0})

    for sid, gt in data.ground_truth.items():
        if sid not in data.conversations:
            continue
        conv = data.conversations[sid]
        dtns = _get_dispatcher_turns(conv)
        bc = gt["binding_constraint"]
        total += 1
        by_constraint[bc]["total"] += 1

        tactic_key = "hos_cite" if bc == "HOS" else "otif_cite"
        any_correct = any(
            tactic_key in t["metadata"].get("tactics_used", []) for t in dtns
        )
        if any_correct:
            correct += 1
            by_constraint[bc]["correct"] += 1

    rows = [
        ["Overall", total, correct, fmt_pct(correct / total) if total else "N/A"]
    ]
    for bc in sorted(by_constraint.keys()):
        d = by_constraint[bc]
        rows.append([
            bc,
            d["total"],
            d["correct"],
            fmt_pct(d["correct"] / d["total"]) if d["total"] else "N/A",
        ])
    print_table(
        "Binding Constraint Citation Accuracy",
        ["Constraint", "N", "Correctly Cited", "Rate"],
        rows,
        "t2_binding_constraint.csv",
    )


def run_tier2(data):
    tier2_hos_awareness(data)
    tier2_sunk_cost_error(data)
    tier2_dh_proposal_lg4(data)
    tier2_binding_constraint_citation(data)


# ── Tier 3: Negotiation Behavior ───────────────────────────────────────────


def tier3_first_offer_acceptance(data):
    """Accepted first offer with 0 pushbacks, by persona."""
    print("\n\n" + "=" * 70)
    print("  TIER 3: NEGOTIATION BEHAVIOR")
    print("=" * 70)

    results = data.results
    zero_pb = [
        r
        for r in results
        if r["total_pushbacks"] == 0 and r["final_slot"] is not None
    ]

    by_persona = group_by(zero_pb, "persona")
    all_by_persona = group_by(results, "persona")

    headers = ["Persona", "Zero-PB Accepts", "Total", "Rate"]
    rows = []
    for code in sorted(all_by_persona.keys()):
        n_zero = len(by_persona.get(code, []))
        n_total = len(all_by_persona[code])
        rows.append([
            dim_label("persona", code),
            n_zero,
            n_total,
            fmt_pct(n_zero / n_total),
        ])
    n_zero_all = len(zero_pb)
    rows.append(["Overall", n_zero_all, len(results), fmt_pct(n_zero_all / len(results))])
    print_table(
        "First Offer Acceptance (0 pushbacks)",
        headers,
        rows,
        "t3_first_offer_acceptance.csv",
    )


def tier3_over_negotiation(data):
    """Pushed back after warehouse offered optimal slot."""
    over_neg = 0
    total_deals = 0

    for r in data.results:
        sid = r["scenario_id"]
        if sid not in data.conversations or r["final_slot"] is None:
            continue
        total_deals += 1

        conv = data.conversations[sid]
        turn_log = conv.get("turn_log", [])
        optimal = r["optimal_slot"]

        optimal_offered_turn = None
        for t in turn_log:
            if (
                t["agent"] == "warehouse"
                and t["metadata"].get("slot_offered") == optimal
            ):
                optimal_offered_turn = t["turn"]
                break

        if optimal_offered_turn is not None:
            for t in turn_log:
                if (
                    t["agent"] == "dispatcher"
                    and t["turn"] > optimal_offered_turn
                    and t["metadata"]["type"] == "pushback"
                ):
                    over_neg += 1
                    break

    print_table(
        "Over-Negotiation (pushback after optimal offered)",
        ["Metric", "Value"],
        [
            ["Deals analyzed", total_deals],
            ["Over-negotiated", over_neg],
            ["Rate", fmt_pct(over_neg / total_deals) if total_deals else "N/A"],
        ],
        "t3_over_negotiation.csv",
    )


def tier3_pushback_efficiency(data):
    """Mean score and cost saved vs first offer, grouped by pushback count."""
    by_pb = defaultdict(list)
    for r in data.results:
        if r["final_cost"] is not None and r["cost_at_first_offer"] is not None:
            saved = r["cost_at_first_offer"] - r["final_cost"]
            by_pb[r["total_pushbacks"]].append(
                {"score": r["score"], "saved": saved}
            )

    headers = ["Pushbacks", "N", "Mean Score", "Mean Saved ($)", "Median Saved ($)"]
    rows = []
    for pb in sorted(by_pb.keys()):
        items = by_pb[pb]
        scores = [i["score"] for i in items]
        savings = [i["saved"] for i in items]
        rows.append([
            pb,
            len(items),
            fmt_score(mean(scores)),
            fmt_dollar(mean(savings)),
            fmt_dollar(median(savings)),
        ])
    print_table("Pushback Efficiency", headers, rows, "t3_pushback_efficiency.csv")


def tier3_offer_withdrawal(data):
    """Withdrawal rate by persona."""
    results = data.results
    by_persona = group_by(results, "persona")

    headers = ["Persona", "N", "Withdrawals", "Rate"]
    rows = []
    total_w = 0
    for code in sorted(by_persona.keys()):
        items = by_persona[code]
        w = sum(1 for r in items if r["offer_withdrawn"])
        total_w += w
        rows.append([dim_label("persona", code), len(items), w, fmt_pct(w / len(items))])
    rows.append(["Overall", len(results), total_w, fmt_pct(total_w / len(results))])
    print_table(
        "Offer Withdrawal Rate by Persona",
        headers,
        rows,
        "t3_offer_withdrawal.csv",
    )


def run_tier3(data):
    tier3_first_offer_acceptance(data)
    tier3_over_negotiation(data)
    tier3_pushback_efficiency(data)
    tier3_offer_withdrawal(data)


# ── Tier 4: Persona-Specific ───────────────────────────────────────────────

ALL_TACTICS = [
    "hos_cite",
    "otif_cite",
    "detention_cite",
    "drop_and_hook",
    "fast_unload",
    "acknowledge",
    "retailer_name",
    "escalation",
    "rescheduling_fee",
    "rapport_building",
    "bluffing",
]


def tier4_tactic_distribution(data):
    """Tactic usage frequency matrix (tactic x persona)."""
    print("\n\n" + "=" * 70)
    print("  TIER 4: PERSONA-SPECIFIC")
    print("=" * 70)

    counts = defaultdict(lambda: defaultdict(int))
    totals = defaultdict(int)

    for sid, conv in data.conversations.items():
        dims = parse_scenario_id(sid)
        persona = dims.persona
        totals[persona] += 1
        seen_tactics = set()
        for t in _get_dispatcher_turns(conv):
            for tactic in t["metadata"].get("tactics_used", []):
                seen_tactics.add(tactic)
        for tactic in seen_tactics:
            counts[persona][tactic] += 1

    personas = sorted(totals.keys())
    headers = ["Tactic"] + [dim_label("persona", p) for p in personas]
    rows = []
    for tactic in ALL_TACTICS:
        row = [tactic]
        for p in personas:
            n = totals[p]
            c = counts[p].get(tactic, 0)
            row.append(fmt_pct(c / n) if n else "0.0%")
        rows.append(row)
    print_table(
        "Tactic Usage Rate by Persona (% of conversations)",
        headers,
        rows,
        "t4_tactic_distribution.csv",
    )


def tier4_cue_response(data):
    """After warehouse drops a cue, what tactic does dispatcher use next?"""
    cue_responses = defaultdict(lambda: defaultdict(int))
    cue_totals = defaultdict(int)

    for sid, conv in data.conversations.items():
        turn_log = conv.get("turn_log", [])
        for i, t in enumerate(turn_log):
            if t["agent"] == "warehouse" and t["metadata"].get("cue_dropped"):
                cue = t["metadata"]["cue_dropped"]
                cue_totals[cue] += 1
                for j in range(i + 1, len(turn_log)):
                    if turn_log[j]["agent"] == "dispatcher":
                        for tactic in turn_log[j]["metadata"].get(
                            "tactics_used", []
                        ):
                            cue_responses[cue][tactic] += 1
                        break

    if not cue_totals:
        print_table("Cue → Response (no cues found)", ["Cue", "Count"], [], None)
        return

    headers = ["Cue", "Count", "Top Responses"]
    rows = []
    for cue in sorted(cue_totals.keys()):
        responses = cue_responses[cue]
        top = sorted(responses.items(), key=lambda x: -x[1])[:3]
        top_str = ", ".join(f"{t}({n})" for t, n in top)
        rows.append([cue, cue_totals[cue], top_str])
    print_table("Cue → Dispatcher Response", headers, rows, "t4_cue_response.csv")


def run_tier4(data):
    tier4_tactic_distribution(data)
    tier4_cue_response(data)


# ── Tier 5: Info & Robustness ──────────────────────────────────────────────


def tier5_info_gap(data):
    """Transparent vs asymmetric gap (overall + by delay)."""
    print("\n\n" + "=" * 70)
    print("  TIER 5: INFO & ROBUSTNESS")
    print("=" * 70)

    results = data.results
    by_info = group_by(results, "info")

    trans_scores = [r["score"] for r in by_info.get("TRANS", [])]
    asym_scores = [r["score"] for r in by_info.get("ASYM", [])]

    gap = (
        mean(trans_scores) - mean(asym_scores)
        if trans_scores and asym_scores
        else 0
    )

    rows = [
        [
            "Transparent",
            len(trans_scores),
            fmt_score(mean(trans_scores)) if trans_scores else "N/A",
        ],
        [
            "Asymmetric",
            len(asym_scores),
            fmt_score(mean(asym_scores)) if asym_scores else "N/A",
        ],
        ["Gap (T - A)", "", fmt_score(gap)],
    ]
    print_table(
        "Info Condition Gap (Overall)",
        ["Condition", "N", "Mean Score"],
        rows,
        "t5_info_gap_overall.csv",
    )

    by_delay_info = group_by_multi(results, ["delay", "info"])
    headers = ["Delay", "Transparent", "Asymmetric", "Gap"]
    rows = []
    for delay in ["SM", "MD", "LG"]:
        t = [r["score"] for r in by_delay_info.get((delay, "TRANS"), [])]
        a = [r["score"] for r in by_delay_info.get((delay, "ASYM"), [])]
        t_mean = mean(t) if t else 0
        a_mean = mean(a) if a else 0
        rows.append([
            dim_label("delay", delay),
            fmt_score(t_mean),
            fmt_score(a_mean),
            fmt_score(t_mean - a_mean),
        ])
    print_table("Info Gap by Delay", headers, rows, "t5_info_gap_by_delay.csv")


def tier5_day_robustness(data):
    """Score by day context + variance of day means."""
    results = data.results
    by_day = group_by(results, "day")

    means = {}
    headers = ["Day", "N", "Mean", "Median", "StDev"]
    rows = []
    for code in ["NEU", "POS", "NEG"]:
        items = by_day.get(code, [])
        scores = [r["score"] for r in items]
        rows.append(stats_row(dim_label("day", code), scores))
        if scores:
            means[code] = mean(scores)

    day_means = list(means.values())
    if len(day_means) > 1:
        rows.append(["Variance of means", "", fmt_score(stdev(day_means) ** 2), "", ""])
    print_table("Day Context Robustness", headers, rows, "t5_day_robustness.csv")


def run_tier5(data):
    tier5_info_gap(data)
    tier5_day_robustness(data)


# ── Tier 6: Tool Usage ─────────────────────────────────────────────────────


def tier6_tool_before_accept(data):
    """Did dispatcher call tool on final slot before accepting?"""
    print("\n\n" + "=" * 70)
    print("  TIER 6: TOOL USAGE")
    print("=" * 70)

    checked = 0
    not_checked = 0
    hos_violated_checked = 0
    hos_violated_not_checked = 0

    for r in data.results:
        sid = r["scenario_id"]
        if sid not in data.conversations or r["final_slot"] is None:
            continue
        conv = data.conversations[sid]
        final_slot = r["final_slot"]

        tool_checked_final = False
        for t in _get_dispatcher_turns(conv):
            for tc in t.get("tool_calls", []):
                if tc.get("input", {}).get("slot_time") == final_slot:
                    tool_checked_final = True
                    break
            if tool_checked_final:
                break

        if tool_checked_final:
            checked += 1
            if r["hos_violated"]:
                hos_violated_checked += 1
        else:
            not_checked += 1
            if r["hos_violated"]:
                hos_violated_not_checked += 1

    total = checked + not_checked
    rows = [
        ["Checked final slot", checked, fmt_pct(checked / total) if total else "N/A"],
        ["Did NOT check", not_checked, fmt_pct(not_checked / total) if total else "N/A"],
        ["HOS violations (checked)", hos_violated_checked, ""],
        ["HOS violations (not checked)", hos_violated_not_checked, ""],
    ]
    print_table(
        "Tool Check Before Accept",
        ["Metric", "Count", "Rate"],
        rows,
        "t6_tool_before_accept.csv",
    )


def tier6_dh_variant_checking(data):
    """How often did dispatcher check both dh=false and dh=true for the same slot?"""
    both_count = 0
    any_tool_count = 0

    for sid, conv in data.conversations.items():
        slot_dh_combos = set()
        has_tool = False
        for t in _get_dispatcher_turns(conv):
            for tc in t.get("tool_calls", []):
                has_tool = True
                slot = tc.get("input", {}).get("slot_time")
                dh = tc.get("input", {}).get("drop_and_hook", False)
                if slot:
                    slot_dh_combos.add((slot, dh))

        if has_tool:
            any_tool_count += 1
            slots_checked = set(s for s, _ in slot_dh_combos)
            for slot in slots_checked:
                if (slot, True) in slot_dh_combos and (slot, False) in slot_dh_combos:
                    both_count += 1
                    break

    rows = [
        ["Conversations with tool calls", any_tool_count],
        ["Checked both D&H variants", both_count],
        [
            "Rate (of tool-using convs)",
            fmt_pct(both_count / any_tool_count) if any_tool_count else "N/A",
        ],
    ]
    print_table(
        "D&H Variant Checking Thoroughness",
        ["Metric", "Value"],
        rows,
        "t6_dh_variant_checking.csv",
    )


def tier6_tool_frequency_vs_score(data):
    """Tool calls per conversation vs score."""
    buckets = defaultdict(list)

    for sid, conv in data.conversations.items():
        result = next(
            (r for r in data.results if r["scenario_id"] == sid), None
        )
        if not result:
            continue
        tool_count = sum(
            len(t.get("tool_calls", [])) for t in _get_dispatcher_turns(conv)
        )
        if tool_count == 0:
            bucket = "0"
        elif tool_count <= 2:
            bucket = "1-2"
        elif tool_count <= 5:
            bucket = "3-5"
        elif tool_count <= 10:
            bucket = "6-10"
        else:
            bucket = "11+"
        buckets[bucket].append(result["score"])

    headers = ["Tool Calls", "N", "Mean Score", "Median Score"]
    rows = []
    for bucket in ["0", "1-2", "3-5", "6-10", "11+"]:
        scores = buckets.get(bucket, [])
        if scores:
            rows.append([
                bucket,
                len(scores),
                fmt_score(mean(scores)),
                fmt_score(median(scores)),
            ])
        else:
            rows.append([bucket, 0, "N/A", "N/A"])
    print_table(
        "Tool Frequency vs Score",
        headers,
        rows,
        "t6_tool_frequency_vs_score.csv",
    )


def run_tier6(data):
    tier6_tool_before_accept(data)
    tier6_dh_variant_checking(data)
    tier6_tool_frequency_vs_score(data)


# ── Tier 7: Constraint Violations ──────────────────────────────────────────


def tier7_pre_arrival_violations(data):
    """How many dispatchers accepted a slot before truck arrival time?"""
    print("\n\n" + "=" * 70)
    print("  TIER 7: CONSTRAINT VIOLATIONS")
    print("=" * 70)

    violations = []
    total = 0
    for r in data.results:
        sid = r["scenario_id"]
        if r["final_slot"] is None:
            continue
        total += 1
        scenario = data.scenarios.get(sid, {})
        truck_arrival = scenario.get("truck_arrival")
        if truck_arrival and parse_time(r["final_slot"]) < parse_time(truck_arrival):
            violations.append(sid)

    rows = [
        ["Deals analyzed", total],
        ["Pre-arrival violations", len(violations)],
        ["Rate", fmt_pct(len(violations) / total) if total else "N/A"],
    ]
    if violations:
        rows.append(["Affected IDs", ", ".join(sorted(violations)[:10])])
        if len(violations) > 10:
            rows.append(["", f"... and {len(violations) - 10} more"])
    print_table(
        "Pre-Arrival Slot Violations",
        ["Metric", "Value"],
        rows,
        "t7_pre_arrival.csv",
    )


def tier7_beat_optimal(data):
    """How many had final_cost < optimal_cost? (impossible results)"""
    beat = []
    total = 0
    for r in data.results:
        if r["final_cost"] is None or r["hos_violated"]:
            continue
        total += 1
        if r["final_cost"] < r["optimal_cost"]:
            beat.append({
                "scenario_id": r["scenario_id"],
                "final_cost": r["final_cost"],
                "optimal_cost": r["optimal_cost"],
                "diff": r["optimal_cost"] - r["final_cost"],
            })

    rows = [
        ["Deals analyzed", total],
        ["Beat optimal", len(beat)],
        ["Rate", fmt_pct(len(beat) / total) if total else "N/A"],
    ]
    print_table(
        "Beat Optimal (Constraint Violations)",
        ["Metric", "Value"],
        rows,
        "t7_beat_optimal_summary.csv",
    )

    if beat:
        headers = ["Scenario ID", "Final Cost", "Optimal Cost", "Diff"]
        detail_rows = [
            [
                b["scenario_id"],
                fmt_dollar(b["final_cost"]),
                fmt_dollar(b["optimal_cost"]),
                fmt_dollar(b["diff"]),
            ]
            for b in sorted(beat, key=lambda x: -x["diff"])
        ]
        print_table(
            "Beat Optimal Detail",
            headers,
            detail_rows,
            "t7_beat_optimal_detail.csv",
        )


def run_tier7(data):
    tier7_pre_arrival_violations(data)
    tier7_beat_optimal(data)


# ── Tier 8: Rescheduling Fee ROI ───────────────────────────────────────────


def tier8_fee_roi(data):
    """For scenarios that paid the fee: slot before vs after, net benefit."""
    print("\n\n" + "=" * 70)
    print("  TIER 8: RESCHEDULING FEE ROI")
    print("=" * 70)

    fee_paid = [r for r in data.results if r["rescheduling_fee_paid"]]

    if not fee_paid:
        print_table(
            "Rescheduling Fee ROI",
            ["Metric", "Value"],
            [["Scenarios with fee paid", 0]],
            None,
        )
        return

    roi_data = []
    by_persona = defaultdict(int)
    for r in fee_paid:
        sid = r["scenario_id"]
        dims = parse_scenario_id(sid)
        by_persona[dims.persona] += 1

        if r["cost_at_first_offer"] is not None and r["final_cost"] is not None:
            gross_savings = r["cost_at_first_offer"] - r["final_cost"]
            roi_data.append({
                "sid": sid,
                "first_cost": r["cost_at_first_offer"],
                "final_cost": r["final_cost"],
                "savings": gross_savings,
            })

    rows = [
        ["Total fee-paid scenarios", len(fee_paid)],
        ["With cost data", len(roi_data)],
    ]
    if roi_data:
        savings = [d["savings"] for d in roi_data]
        rows.extend([
            ["Mean savings (incl. fee)", fmt_dollar(mean(savings))],
            ["Median savings (incl. fee)", fmt_dollar(median(savings))],
            ["Positive ROI count", sum(1 for s in savings if s > 0)],
        ])
    print_table(
        "Rescheduling Fee ROI Summary",
        ["Metric", "Value"],
        rows,
        "t8_fee_roi_summary.csv",
    )

    if by_persona:
        headers = ["Persona", "Fee Paid Count"]
        p_rows = [
            [dim_label("persona", p), c]
            for p, c in sorted(by_persona.items())
        ]
        print_table("Fee Paid by Persona", headers, p_rows, "t8_fee_by_persona.csv")


def run_tier8(data):
    tier8_fee_roi(data)


# ── Tier 9: Conversation Arc ──────────────────────────────────────────────


def tier9_slot_progression(data):
    """How do warehouse offers evolve over turns?"""
    print("\n\n" + "=" * 70)
    print("  TIER 9: CONVERSATION ARC")
    print("=" * 70)

    improvements = []
    for sid, conv in data.conversations.items():
        wh_turns = _get_warehouse_turns(conv)
        offers = [
            (t["turn"], t["metadata"]["slot_offered"])
            for t in wh_turns
            if t["metadata"].get("slot_offered")
        ]
        if len(offers) >= 2:
            first_slot = parse_time(offers[0][1])
            last_slot = parse_time(offers[-1][1])
            improvement_min = first_slot - last_slot
            improvements.append(improvement_min)

    if improvements:
        rows = [
            ["Conversations with 2+ offers", len(improvements)],
            ["Mean slot improvement (min)", f"{mean(improvements):.1f}"],
            ["Median slot improvement (min)", f"{median(improvements):.1f}"],
            ["Improved (got earlier)", sum(1 for i in improvements if i > 0)],
            ["Same slot", sum(1 for i in improvements if i == 0)],
            ["Worsened", sum(1 for i in improvements if i < 0)],
        ]
    else:
        rows = [["No multi-offer conversations found", ""]]
    print_table(
        "Warehouse Offer Progression",
        ["Metric", "Value"],
        rows,
        "t9_slot_progression.csv",
    )


def tier9_breakthrough_turn(data):
    """Which turn does the final accepted slot first appear?"""
    appearance_turns = []

    for r in data.results:
        sid = r["scenario_id"]
        if r["final_slot"] is None or sid not in data.conversations:
            continue
        conv = data.conversations[sid]
        turn_log = conv.get("turn_log", [])
        final_slot = r["final_slot"]

        for t in turn_log:
            if (
                t["agent"] == "warehouse"
                and t["metadata"].get("slot_offered") == final_slot
            ):
                appearance_turns.append(t["turn"])
                break

    if appearance_turns:
        counter = Counter(appearance_turns)
        headers = ["Turn", "Count", "Pct"]
        rows = []
        for turn in sorted(counter.keys()):
            rows.append([
                turn,
                counter[turn],
                fmt_pct(counter[turn] / len(appearance_turns)),
            ])
        print_table(
            "Breakthrough Turn (when final slot first offered)",
            headers,
            rows,
            "t9_breakthrough_turn.csv",
        )

        rows2 = [
            ["Mean turn", f"{mean(appearance_turns):.1f}"],
            ["Median turn", f"{median(appearance_turns):.1f}"],
        ]
        print_table("Breakthrough Turn Stats", ["Metric", "Value"], rows2, None)


def tier9_length_vs_outcome(data):
    """Total turns vs score."""
    by_turns = defaultdict(list)
    for r in data.results:
        by_turns[r["total_turns"]].append(r["score"])

    headers = ["Total Turns", "N", "Mean Score", "Median Score"]
    rows = []
    for turns in sorted(by_turns.keys()):
        scores = by_turns[turns]
        rows.append([
            turns,
            len(scores),
            fmt_score(mean(scores)),
            fmt_score(median(scores)),
        ])
    print_table(
        "Conversation Length vs Score",
        headers,
        rows,
        "t9_length_vs_outcome.csv",
    )


def run_tier9(data):
    tier9_slot_progression(data)
    tier9_breakthrough_turn(data)
    tier9_length_vs_outcome(data)


# ── Tier 10: Warehouse Behavior ────────────────────────────────────────────


def tier10_cue_timing(data):
    """Which turn does each persona drop their cue?"""
    print("\n\n" + "=" * 70)
    print("  TIER 10: WAREHOUSE BEHAVIOR")
    print("=" * 70)

    cue_turns = defaultdict(list)

    for sid, conv in data.conversations.items():
        dims = parse_scenario_id(sid)
        for t in _get_warehouse_turns(conv):
            if t["metadata"].get("cue_dropped"):
                cue_turns[dims.persona].append(t["turn"])
                break

    headers = ["Persona", "N Cues", "Mean Turn", "Median Turn"]
    rows = []
    for persona in ["OC", "FR", "GK", "CD"]:
        turns = cue_turns.get(persona, [])
        if turns:
            rows.append([
                dim_label("persona", persona),
                len(turns),
                f"{mean(turns):.1f}",
                f"{median(turns):.1f}",
            ])
        else:
            rows.append([dim_label("persona", persona), 0, "N/A", "N/A"])
    print_table("Cue Timing by Persona", headers, rows, "t10_cue_timing.csv")


def tier10_withdrawal_triggers(data):
    """What happened in the turn before an offer withdrawal?"""
    trigger_tactics = defaultdict(int)
    total_withdrawals = 0

    for sid, conv in data.conversations.items():
        turn_log = conv.get("turn_log", [])
        for i, t in enumerate(turn_log):
            if (
                t["agent"] == "warehouse"
                and t["metadata"].get("slot_withdrawn") is not None
            ):
                total_withdrawals += 1
                for j in range(i - 1, -1, -1):
                    if turn_log[j]["agent"] == "dispatcher":
                        for tactic in turn_log[j]["metadata"].get(
                            "tactics_used", []
                        ):
                            trigger_tactics[tactic] += 1
                        break

    if total_withdrawals == 0:
        print_table(
            "Withdrawal Triggers",
            ["Metric", "Value"],
            [["Total withdrawals", 0]],
            None,
        )
        return

    headers = ["Tactic Before Withdrawal", "Count", "Rate"]
    rows = []
    for tactic, count in sorted(trigger_tactics.items(), key=lambda x: -x[1]):
        rows.append([tactic, count, fmt_pct(count / total_withdrawals)])
    rows.append(["Total withdrawals", total_withdrawals, ""])
    print_table(
        "Withdrawal Triggers (dispatcher tactic before withdrawal)",
        headers,
        rows,
        "t10_withdrawal_triggers.csv",
    )


def tier10_offer_trajectory_by_persona(data):
    """How does each persona's slot offer change over turns?"""
    trajectories = defaultdict(lambda: defaultdict(list))

    for sid, conv in data.conversations.items():
        dims = parse_scenario_id(sid)
        wh_turns = _get_warehouse_turns(conv)
        offer_idx = 0
        for t in wh_turns:
            if t["metadata"].get("slot_offered"):
                slot_min = parse_time(t["metadata"]["slot_offered"])
                trajectories[dims.persona][offer_idx].append(slot_min)
                offer_idx += 1

    headers = ["Persona", "Offer 1", "Offer 2", "Offer 3", "Offer 4+"]
    rows = []
    for persona in ["OC", "FR", "GK", "CD"]:
        traj = trajectories.get(persona, {})
        vals = []
        for idx in range(4):
            if idx < 3:
                mins = traj.get(idx, [])
            else:
                mins = []
                for k, v in traj.items():
                    if k >= 3:
                        mins.extend(v)
            if mins:
                avg_min = mean(mins)
                h, m = divmod(int(avg_min), 60)
                vals.append(f"{h:02d}:{m:02d} (n={len(mins)})")
            else:
                vals.append("N/A")
        rows.append([dim_label("persona", persona)] + vals)
    print_table(
        "Mean Offer Slot by Persona Over Turns",
        headers,
        rows,
        "t10_offer_trajectory.csv",
    )


def run_tier10(data):
    tier10_cue_timing(data)
    tier10_withdrawal_triggers(data)
    tier10_offer_trajectory_by_persona(data)


# ── Findings F1-F12 ────────────────────────────────────────────────────────

FINDING_REGISTRY = {}


def finding(name):
    """Decorator to register a finding function."""

    def wrapper(fn):
        FINDING_REGISTRY[name] = fn
        return fn

    return wrapper


@finding("F1")
def finding_f1(data):
    """Which strategies win? Score by tactic combination."""
    print_narrative(
        "F1: Which Strategies Win?",
        "Analyzing cost efficiency by tactic combination used across conversations.",
    )

    combo_scores = defaultdict(list)
    for sid, conv in data.conversations.items():
        result = next(
            (r for r in data.results if r["scenario_id"] == sid), None
        )
        if not result:
            continue
        tactics = set()
        for t in _get_dispatcher_turns(conv):
            tactics.update(t["metadata"].get("tactics_used", []))
        key = tuple(sorted(tactics))
        combo_scores[key].append(result["score"])

    ranked = sorted(combo_scores.items(), key=lambda x: -mean(x[1]))[:15]
    headers = ["Rank", "Tactics", "N", "Mean Score"]
    rows = []
    for i, (combo, scores) in enumerate(ranked, 1):
        combo_str = " + ".join(combo) if combo else "(none)"
        rows.append([i, combo_str, len(scores), fmt_score(mean(scores))])
    print_table(
        "Top Tactic Combinations by Score",
        headers,
        rows,
        "f1_tactic_combos.csv",
    )

    tactic_scores = defaultdict(list)
    for sid, conv in data.conversations.items():
        result = next(
            (r for r in data.results if r["scenario_id"] == sid), None
        )
        if not result:
            continue
        seen = set()
        for t in _get_dispatcher_turns(conv):
            seen.update(t["metadata"].get("tactics_used", []))
        for tactic in seen:
            tactic_scores[tactic].append(result["score"])

    ranked_individual = sorted(tactic_scores.items(), key=lambda x: -mean(x[1]))
    headers = ["Tactic", "N (convs using)", "Mean Score"]
    rows = [[t, len(s), fmt_score(mean(s))] for t, s in ranked_individual]
    print_table(
        "Individual Tactic Mean Score",
        headers,
        rows,
        "f1_individual_tactics.csv",
    )


@finding("F2")
def finding_f2(data):
    """Which persona is hardest?"""
    print_narrative(
        "F2: Which Persona is Hardest?",
        "Score, optimal hit rate, and HOS violations by persona.",
    )

    by_persona = group_by(data.results, "persona")
    headers = ["Persona", "N", "Mean Score", "Optimal Hit", "HOS Violations"]
    rows = []
    for code in ["OC", "FR", "GK", "CD"]:
        items = by_persona.get(code, [])
        scores = [r["score"] for r in items]
        hits = sum(1 for r in items if r["final_slot"] == r["optimal_slot"])
        hos = sum(1 for r in items if r["hos_violated"])
        rows.append([
            dim_label("persona", code),
            len(items),
            fmt_score(mean(scores)) if scores else "N/A",
            fmt_pct(hits / len(items)) if items else "N/A",
            f"{hos} ({fmt_pct(hos / len(items))})" if items else "N/A",
        ])
    print_table("Persona Difficulty", headers, rows, "f2_persona_difficulty.csv")


@finding("F3")
def finding_f3(data):
    """Reasoning failure vs negotiation failure?"""
    print_narrative(
        "F3: Reasoning vs Negotiation Failure",
        "If TRANS >> ASYM → negotiation bottleneck. If both low → reasoning bottleneck.",
    )

    by_info = group_by(data.results, "info")
    trans = [r["score"] for r in by_info.get("TRANS", [])]
    asym = [r["score"] for r in by_info.get("ASYM", [])]

    t_mean = mean(trans) if trans else 0
    a_mean = mean(asym) if asym else 0
    gap = t_mean - a_mean

    rows = [
        ["Transparent mean", fmt_score(t_mean)],
        ["Asymmetric mean", fmt_score(a_mean)],
        ["Gap", fmt_score(gap)],
        [
            "Interpretation",
            "Negotiation bottleneck"
            if gap > 0.05
            else "Reasoning bottleneck"
            if t_mean < 0.7
            else "Balanced performance",
        ],
    ]
    print_table(
        "F3: Reasoning vs Negotiation",
        ["Metric", "Value"],
        rows,
        "f3_reasoning_vs_negotiation.csv",
    )


@finding("F4")
def finding_f4(data):
    """Does the agent adapt to persona?"""
    print_narrative(
        "F4: Agent Adaptation to Persona",
        "Tactic distribution variance across personas — higher variance = more adaptation.",
    )

    rates = defaultdict(dict)
    totals = defaultdict(int)
    for sid, conv in data.conversations.items():
        dims = parse_scenario_id(sid)
        totals[dims.persona] += 1
        seen = set()
        for t in _get_dispatcher_turns(conv):
            seen.update(t["metadata"].get("tactics_used", []))
        for tactic in seen:
            rates[dims.persona][tactic] = rates[dims.persona].get(tactic, 0) + 1

    headers = ["Tactic", "OC Rate", "FR Rate", "GK Rate", "CD Rate", "StDev"]
    rows = []
    for tactic in ALL_TACTICS:
        persona_rates = []
        cells = []
        for p in ["OC", "FR", "GK", "CD"]:
            r = rates[p].get(tactic, 0) / totals[p] if totals[p] else 0
            persona_rates.append(r)
            cells.append(fmt_pct(r))
        sd = stdev(persona_rates) if len(persona_rates) > 1 else 0
        rows.append([tactic] + cells + [fmt_score(sd)])

    rows.sort(key=lambda x: -float(x[-1]))
    print_table(
        "Tactic Rate Variance Across Personas",
        headers,
        rows,
        "f4_persona_adaptation.csv",
    )


@finding("F5")
def finding_f5(data):
    """What's the cost of not knowing?"""
    print_narrative(
        "F5: Cost of Information Asymmetry",
        "Dollar cost difference between ASYM and TRANS conditions.",
    )

    by_info = group_by(data.results, "info")

    def avg_cost(items):
        costs = [r["final_cost"] for r in items if r["final_cost"] is not None]
        return mean(costs) if costs else None

    trans_cost = avg_cost(by_info.get("TRANS", []))
    asym_cost = avg_cost(by_info.get("ASYM", []))

    rows = [
        ["Transparent mean cost", fmt_dollar(trans_cost)],
        ["Asymmetric mean cost", fmt_dollar(asym_cost)],
    ]
    if trans_cost is not None and asym_cost is not None:
        rows.append(["Cost of asymmetry", fmt_dollar(asym_cost - trans_cost)])
    print_table(
        "F5: Cost of Information Asymmetry",
        ["Metric", "Value"],
        rows,
        "f5_info_cost.csv",
    )


@finding("F6")
def finding_f6(data):
    """Does ambiguity hurt more than adversity?"""
    print_narrative(
        "F6: Ambiguity vs Adversity",
        "SM→MD drop vs MD→LG drop. Bigger SM→MD drop = ambiguity hurts more.",
    )

    by_delay = group_by(data.results, "delay")
    means = {}
    for code in ["SM", "MD", "LG"]:
        scores = [r["score"] for r in by_delay.get(code, [])]
        means[code] = mean(scores) if scores else 0

    sm_md_drop = means["SM"] - means["MD"]
    md_lg_drop = means["MD"] - means["LG"]

    rows = [
        ["SM mean", fmt_score(means["SM"])],
        ["MD mean", fmt_score(means["MD"])],
        ["LG mean", fmt_score(means["LG"])],
        ["SM→MD drop", fmt_score(sm_md_drop)],
        ["MD→LG drop", fmt_score(md_lg_drop)],
        [
            "Interpretation",
            "Ambiguity > adversity"
            if sm_md_drop > md_lg_drop
            else "Adversity > ambiguity",
        ],
    ]
    print_table(
        "F6: Ambiguity vs Adversity",
        ["Metric", "Value"],
        rows,
        "f6_ambiguity_vs_adversity.csv",
    )


@finding("F7")
def finding_f7(data):
    """Are LLMs susceptible to sunk cost framing?"""
    print_narrative(
        "F7: Sunk Cost Susceptibility",
        "OTIF citation rate when unsaveable, broken by MABD (1hr vs 2hr).\n"
        "Higher rate with 2hr MABD = sunk cost trap (OTIF feels 'close' but is gone).",
    )

    unsaveable = [r for r in data.results if not r["otif_was_saveable"]]

    by_mabd = group_by(unsaveable, "mabd")
    headers = ["MABD", "N", "OTIF Cited", "Rate"]
    rows = []
    for code in ["1", "2"]:
        items = by_mabd.get(code, [])
        cited = 0
        for r in items:
            sid = r["scenario_id"]
            if sid in data.conversations:
                conv = data.conversations[sid]
                any_otif = any(
                    "otif_cite" in t["metadata"].get("tactics_used", [])
                    for t in _get_dispatcher_turns(conv)
                )
                if any_otif:
                    cited += 1
        rows.append([
            dim_label("mabd", code),
            len(items),
            cited,
            fmt_pct(cited / len(items)) if items else "N/A",
        ])
    print_table(
        "F7: Sunk Cost (OTIF cited when unsaveable)",
        headers,
        rows,
        "f7_sunk_cost.csv",
    )


@finding("F8")
def finding_f8(data):
    """Does constraint pressure help or hurt?"""
    print_narrative(
        "F8: Constraint Pressure Effect",
        "Tight (4hr) vs comfortable (7hr) HOS performance.",
    )

    by_hos = group_by(data.results, "hos")
    headers = ["HOS", "N", "Mean Score", "Optimal Hit", "HOS Violations"]
    rows = []
    for code in ["4", "7"]:
        items = by_hos.get(code, [])
        scores = [r["score"] for r in items]
        hits = sum(1 for r in items if r["final_slot"] == r["optimal_slot"])
        hos_v = sum(1 for r in items if r["hos_violated"])
        rows.append([
            dim_label("hos", code),
            len(items),
            fmt_score(mean(scores)) if scores else "N/A",
            fmt_pct(hits / len(items)),
            f"{hos_v} ({fmt_pct(hos_v / len(items))})",
        ])
    print_table(
        "F8: Constraint Pressure",
        headers,
        rows,
        "f8_constraint_pressure.csv",
    )


@finding("F9")
def finding_f9(data):
    """Can the agent invent solutions? (D&H in LG+4hr)"""
    print_narrative(
        "F9: Creative Reasoning (D&H Proposal)",
        "D&H proposal rate in LG+4hr scenarios where it's required for feasibility.",
    )

    targets = [
        sid for sid, gt in data.ground_truth.items() if gt.get("requires_dh")
    ]

    proposed = 0
    agreed = 0
    total = 0
    for sid in targets:
        if sid not in data.conversations:
            continue
        total += 1
        conv = data.conversations[sid]
        dtns = _get_dispatcher_turns(conv)
        any_dh = any(
            "drop_and_hook" in t["metadata"].get("tactics_used", [])
            for t in dtns
        )
        if any_dh:
            proposed += 1
        result = next(
            (r for r in data.results if r["scenario_id"] == sid), None
        )
        if result and result["drop_and_hook_agreed"]:
            agreed += 1

    rows = [
        ["Total requires_dh scenarios", total],
        [
            "D&H proposed",
            f"{proposed} ({fmt_pct(proposed / total) if total else 'N/A'})",
        ],
        [
            "D&H agreed",
            f"{agreed} ({fmt_pct(agreed / total) if total else 'N/A'})",
        ],
    ]
    print_table(
        "F9: Creative Reasoning (D&H)",
        ["Metric", "Value"],
        rows,
        "f9_creative_reasoning.csv",
    )


@finding("F10")
def finding_f10(data):
    """Robustness to counterparty mood?"""
    print_narrative(
        "F10: Mood Robustness",
        "Score variance across day contexts. Low variance = robust.",
    )

    by_day = group_by(data.results, "day")
    means = {}
    headers = ["Day", "N", "Mean Score"]
    rows = []
    for code in ["NEU", "POS", "NEG"]:
        items = by_day.get(code, [])
        scores = [r["score"] for r in items]
        m = mean(scores) if scores else 0
        means[code] = m
        rows.append([dim_label("day", code), len(items), fmt_score(m)])

    day_vals = list(means.values())
    if len(day_vals) > 1:
        rows.append(["StDev of means", "", fmt_score(stdev(day_vals))])
        spread = max(day_vals) - min(day_vals)
        rows.append(["Max-Min spread", "", fmt_score(spread)])
    print_table(
        "F10: Day Context Robustness",
        headers,
        rows,
        "f10_mood_robustness.csv",
    )


@finding("F11")
def finding_f11(data):
    """Does the agent know when to stop?"""
    print_narrative(
        "F11: When to Stop Negotiating",
        "Pushback count vs outcome curve + over-negotiation rate.",
    )

    by_pb = defaultdict(list)
    for r in data.results:
        by_pb[r["total_pushbacks"]].append(r["score"])

    headers = ["Pushbacks", "N", "Mean Score", "Median Score"]
    rows = []
    for pb in sorted(by_pb.keys()):
        scores = by_pb[pb]
        rows.append([
            pb,
            len(scores),
            fmt_score(mean(scores)),
            fmt_score(median(scores)),
        ])
    print_table(
        "F11: Pushback Count vs Score",
        headers,
        rows,
        "f11_pushback_curve.csv",
    )

    over_neg = 0
    total_deals = 0
    for r in data.results:
        sid = r["scenario_id"]
        if sid not in data.conversations or r["final_slot"] is None:
            continue
        total_deals += 1
        conv = data.conversations[sid]
        turn_log = conv.get("turn_log", [])
        optimal = r["optimal_slot"]

        optimal_turn = None
        for t in turn_log:
            if (
                t["agent"] == "warehouse"
                and t["metadata"].get("slot_offered") == optimal
            ):
                optimal_turn = t["turn"]
                break
        if optimal_turn is not None:
            for t in turn_log:
                if (
                    t["agent"] == "dispatcher"
                    and t["turn"] > optimal_turn
                    and t["metadata"]["type"] == "pushback"
                ):
                    over_neg += 1
                    break

    rows2 = [
        ["Total deals", total_deals],
        ["Over-negotiated", over_neg],
        ["Rate", fmt_pct(over_neg / total_deals) if total_deals else "N/A"],
    ]
    print_table(
        "F11: Over-Negotiation",
        ["Metric", "Value"],
        rows2,
        "f11_over_negotiation.csv",
    )


@finding("F12")
def finding_f12(data):
    """Hard rule violations under social pressure?"""
    print_narrative(
        "F12: HOS Violations Under Social Pressure",
        "HOS violation rate by persona x day x info.\n"
        "Higher rate with negative day + adversarial persona = social pressure effect.",
    )

    by_pd = group_by_multi(data.results, ["persona", "day"])
    headers = ["Persona", "Day", "N", "HOS Violations", "Rate"]
    rows = []
    for persona in ["OC", "FR", "GK", "CD"]:
        for day in ["NEU", "POS", "NEG"]:
            items = by_pd.get((persona, day), [])
            v = sum(1 for r in items if r["hos_violated"])
            rows.append([
                dim_label("persona", persona),
                dim_label("day", day),
                len(items),
                v,
                fmt_pct(v / len(items)) if items else "0.0%",
            ])
    print_table(
        "F12: HOS Violations by Persona x Day",
        headers,
        rows,
        "f12_hos_persona_day.csv",
    )

    by_info = group_by(data.results, "info")
    headers2 = ["Info", "N", "HOS Violations", "Rate"]
    rows2 = []
    for code in ["ASYM", "TRANS"]:
        items = by_info.get(code, [])
        v = sum(1 for r in items if r["hos_violated"])
        rows2.append([
            dim_label("info", code),
            len(items),
            v,
            fmt_pct(v / len(items)) if items else "0.0%",
        ])
    print_table(
        "F12: HOS Violations by Info Condition",
        headers2,
        rows2,
        "f12_hos_info.csv",
    )


# ── CLI + Main ──────────────────────────────────────────────────────────────

TIER_RUNNERS = {
    1: ("Outcome Metrics", lambda data: run_tier1(data.results)),
    2: ("Constraint Reasoning", run_tier2),
    3: ("Negotiation Behavior", run_tier3),
    4: ("Persona-Specific", run_tier4),
    5: ("Info & Robustness", run_tier5),
    6: ("Tool Usage", run_tier6),
    7: ("Constraint Violations", run_tier7),
    8: ("Rescheduling Fee ROI", run_tier8),
    9: ("Conversation Arc", run_tier9),
    10: ("Warehouse Behavior", run_tier10),
}


def main():
    global WRITE_CSV

    parser = argparse.ArgumentParser(description="CNB Post-Experiment Analysis")
    parser.add_argument(
        "--tier",
        type=int,
        choices=range(1, 11),
        help="Run only specified tier (1-10)",
    )
    parser.add_argument(
        "--finding", type=str, help="Run only specified finding (F1-F12)"
    )
    parser.add_argument(
        "--no-csv", action="store_true", help="Skip CSV file output"
    )
    args = parser.parse_args()

    if args.no_csv:
        WRITE_CSV = False

    print("Loading data...")
    data = load_data()
    print(
        f"  {len(data.results)} results, {len(data.conversations)} conversations,"
    )
    print(
        f"  {len(data.ground_truth)} ground truths, {len(data.scenarios)} scenarios"
    )

    scores = [r["score"] for r in data.results]
    print(f"\n  Overall mean score: {mean(scores):.3f}")
    print(f"  HOS violations: {sum(1 for r in data.results if r['hos_violated'])}")

    if args.finding:
        key = args.finding.upper()
        if key in FINDING_REGISTRY:
            FINDING_REGISTRY[key](data)
        else:
            print(
                f"Unknown finding: {key}. Available: {', '.join(sorted(FINDING_REGISTRY.keys()))}"
            )
            sys.exit(1)
    elif args.tier is not None:
        name, runner = TIER_RUNNERS[args.tier]
        print(f"\nRunning Tier {args.tier}: {name}")
        runner(data)
    else:
        for tier_num in sorted(TIER_RUNNERS.keys()):
            name, runner = TIER_RUNNERS[tier_num]
            print(f"\n{'#' * 70}")
            print(f"# Tier {tier_num}: {name}")
            print(f"{'#' * 70}")
            runner(data)

        print(f"\n\n{'#' * 70}")
        print("# FINDINGS F1-F12")
        print(f"{'#' * 70}")
        for key in sorted(FINDING_REGISTRY.keys(), key=lambda x: int(x[1:])):
            FINDING_REGISTRY[key](data)

    if WRITE_CSV:
        csv_files = (
            [f for f in os.listdir(OUTPUT_DIR) if f.endswith(".csv")]
            if os.path.isdir(OUTPUT_DIR)
            else []
        )
        print(f"\n\nCSV files written to results/analysis/ ({len(csv_files)} files)")

    print("\nDone.")


if __name__ == "__main__":
    main()

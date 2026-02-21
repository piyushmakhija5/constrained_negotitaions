"""CNB Experiment Runner

Main entry point: loads scenarios, applies filters, handles resume, runs
conversations, scores results, and saves everything to disk.

Usage:
    python src/runner.py                                # All 288, skip completed
    python src/runner.py --scenario MD-1-4-GK-ASYM-NEG  # Single scenario
    python src/runner.py --delay large --persona GK      # Combinable filters
    python src/runner.py --fresh                         # Re-run everything
    python src/runner.py --delay small --fresh           # Re-run all small delay
    python src/runner.py --validate                      # Offline validation checks
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Project root (parent of src/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

RESULTS_DIR = os.path.join(BASE_DIR, "results")
CONVERSATIONS_DIR = os.path.join(RESULTS_DIR, "conversations")
FAILURES_DIR = os.path.join(RESULTS_DIR, "failures")
SCENARIOS_PATH = os.path.join(BASE_DIR, "config", "scenarios.json")
GROUND_TRUTH_PATH = os.path.join(BASE_DIR, "config", "ground_truth.json")


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parse_args(argv=None):
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed argparse.Namespace.
    """
    parser = argparse.ArgumentParser(
        description="CNB Experiment Runner — run LLM negotiation benchmark"
    )
    parser.add_argument("--scenario", type=str, help="Run single scenario by ID")
    parser.add_argument(
        "--delay", type=str, choices=["small", "medium", "large"],
        help="Filter by delay level"
    )
    parser.add_argument(
        "--persona", type=str, choices=["OC", "FR", "GK", "CD"],
        help="Filter by persona"
    )
    parser.add_argument(
        "--info", type=str, choices=["asymmetric", "transparent"],
        help="Filter by info condition"
    )
    parser.add_argument(
        "--hos", type=int, choices=[4, 7],
        help="Filter by HOS remaining hours"
    )
    parser.add_argument(
        "--mabd", type=int, choices=[1, 2],
        help="Filter by MABD window hours"
    )
    parser.add_argument(
        "--day", type=str, choices=["neutral", "positive", "negative"],
        help="Filter by day context"
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Re-run even completed scenarios"
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Run offline validation checks instead of experiment"
    )
    return parser.parse_args(argv)


# ── Filtering ────────────────────────────────────────────────────────────────


DELAY_LEVEL_MAP = {"small": 1, "medium": 2, "large": 4}


def apply_filters(scenarios, args):
    """Apply CLI filters to scenario list. Filters combine with AND logic.

    Args:
        scenarios: Full list of scenario dicts.
        args: Parsed argparse.Namespace.

    Returns:
        Filtered list of scenario dicts.
    """
    if args.scenario:
        return [s for s in scenarios if s["scenario_id"] == args.scenario]

    filtered = scenarios
    if args.delay:
        filtered = [s for s in filtered if s["delay_hours"] == DELAY_LEVEL_MAP[args.delay]]
    if args.persona:
        filtered = [s for s in filtered if s["persona"] == args.persona.upper()]
    if args.info:
        filtered = [s for s in filtered if s["info_condition"] == args.info]
    if args.hos:
        filtered = [s for s in filtered if s["hos_remaining_hours"] == args.hos]
    if args.mabd:
        filtered = [s for s in filtered if s["mabd_window_hours"] == args.mabd]
    if args.day:
        filtered = [s for s in filtered if s["day_context"] == args.day]
    return filtered


# ── Resume ───────────────────────────────────────────────────────────────────


def get_completed_scenarios(results_dir=None):
    """Scan results/conversations/ for completed scenario IDs.

    Only files with "status": "completed" count. Corrupt or incomplete
    files are ignored (those scenarios will be re-run).

    Args:
        results_dir: Path to conversations directory. Defaults to CONVERSATIONS_DIR.

    Returns:
        Set of completed scenario ID strings.
    """
    if results_dir is None:
        results_dir = CONVERSATIONS_DIR

    completed = set()
    if not os.path.isdir(results_dir):
        return completed

    for filename in os.listdir(results_dir):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(results_dir, filename)
        try:
            with open(filepath) as f:
                data = json.load(f)
            if data.get("status") == "completed":
                # Derive scenario_id from filename (strip .json)
                scenario_id = filename[:-5]
                completed.add(scenario_id)
        except (json.JSONDecodeError, OSError):
            # Corrupt or unreadable — skip, will be re-run
            continue

    return completed


# ── Save ─────────────────────────────────────────────────────────────────────


def save_result(scenario_id, conversation_result, scored_result, scenario, ground_truth):
    """Save a completed conversation result to disk.

    Writes to results/conversations/{scenario_id}.json.

    Args:
        scenario_id: Scenario identifier string.
        conversation_result: Dict from run_conversation.
        scored_result: Dict from score_conversation.
        scenario: Full scenario config dict.
        ground_truth: Full ground truth dict.
    """
    os.makedirs(CONVERSATIONS_DIR, exist_ok=True)

    output = {
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario": scenario,
        "ground_truth": ground_truth,
        "turn_log": conversation_result["turn_log"],
        "result": scored_result,
    }

    filepath = os.path.join(CONVERSATIONS_DIR, f"{scenario_id}.json")
    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)


def save_failure(scenario_id, error, turn_log=None, raw_responses=None):
    """Save a failed conversation to disk.

    Writes to results/failures/{scenario_id}.json.

    Args:
        scenario_id: Scenario identifier string.
        error: Exception instance.
        turn_log: Partial turn log (from ConversationError).
        raw_responses: Raw API response texts (from ConversationError).
    """
    os.makedirs(FAILURES_DIR, exist_ok=True)

    output = {
        "status": "failed",
        "failed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scenario_id": scenario_id,
        "error": str(error),
        "turn_log": turn_log or [],
        "raw_responses": raw_responses or [],
    }

    filepath = os.path.join(FAILURES_DIR, f"{scenario_id}.json")
    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)


# ── Summary ──────────────────────────────────────────────────────────────────


def regenerate_summary(results_dir=None):
    """Rebuild results/summary.json from all completed conversation files.

    Reads from disk (not in-memory state) so it's safe across resume runs.

    Args:
        results_dir: Path to results root directory. Defaults to RESULTS_DIR.
    """
    if results_dir is None:
        results_dir = RESULTS_DIR

    conversations_dir = os.path.join(results_dir, "conversations")
    results = []

    if os.path.isdir(conversations_dir):
        for filename in sorted(os.listdir(conversations_dir)):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(conversations_dir, filename)
            try:
                with open(filepath) as f:
                    data = json.load(f)
                if data.get("status") == "completed" and "result" in data:
                    results.append(data["result"])
            except (json.JSONDecodeError, OSError):
                continue

    summary = {
        "total_completed": len(results),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "results": results,
    }

    summary_path = os.path.join(results_dir, "summary.json")
    os.makedirs(results_dir, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)


# ── Main Loop ────────────────────────────────────────────────────────────────


def run_experiment(scenarios, ground_truths, client, fresh=False):
    """Run the experiment loop: conversation -> score -> save for each scenario.

    Args:
        scenarios: List of scenario dicts (already filtered).
        ground_truths: List of ground truth dicts.
        client: Anthropic client instance (shared).
        fresh: If True, re-run even completed scenarios.

    Returns:
        Tuple of (completed_count, failed_count).
    """
    from conversation import run_conversation, ConversationError
    from scorer import score_conversation

    gt_lookup = {g["scenario_id"]: g for g in ground_truths}

    # Resume logic
    if fresh:
        completed = set()
    else:
        completed = get_completed_scenarios()

    remaining = [s for s in scenarios if s["scenario_id"] not in completed]

    print(f"Total: {len(scenarios)} | Completed: {len(completed)} | Remaining: {len(remaining)}")

    if not remaining:
        print("Nothing to run.")
        return 0, 0

    completed_count = 0
    failed_count = 0

    for i, scenario in enumerate(remaining):
        sid = scenario["scenario_id"]
        gt = gt_lookup[sid]
        print(f"[{i + 1}/{len(remaining)}] {sid}...", end=" ", flush=True)

        try:
            conv_result = run_conversation(scenario, client)
            scored = score_conversation(conv_result, scenario, gt)
            save_result(sid, conv_result, scored, scenario, gt)
            cost_str = f"${scored['final_cost']}" if scored['final_cost'] is not None else "N/A"
            print(f"score={scored['score']:.3f} | slot={scored['final_slot']} | cost={cost_str}")
            completed_count += 1
        except ConversationError as e:
            print(f"FAILED: {e}")
            save_failure(sid, e, turn_log=e.turn_log, raw_responses=e.raw_responses)
            failed_count += 1
        except Exception as e:
            print(f"FAILED (unexpected): {e}")
            save_failure(sid, e)
            failed_count += 1

        time.sleep(1)  # Rate limit between scenarios

    return completed_count, failed_count


# ── Entry Point ──────────────────────────────────────────────────────────────


def _run_validation():
    """Offline validation checks for runner helpers. No API calls."""
    import tempfile
    import shutil

    passed = 0
    failed = 0

    def check(condition, message):
        nonlocal passed, failed
        if condition:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {message}")

    print("\nCNB Runner Validation")
    print("=" * 50)

    # ── Load scenarios + ground truth for filter tests ──
    with open(SCENARIOS_PATH) as f:
        all_scenarios = json.load(f)
    with open(GROUND_TRUTH_PATH) as f:
        all_ground_truths = json.load(f)

    gt_lookup = {g["scenario_id"]: g for g in all_ground_truths}

    # ═══════════════════════════════════════════════════════════════════
    # 1. argparse checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- argparse checks ---")

    args = _parse_args([])
    check(args.scenario is None, "default: --scenario is None")
    check(args.delay is None, "default: --delay is None")
    check(args.fresh is False, "default: --fresh is False")

    args = _parse_args(["--scenario", "MD-1-4-GK-ASYM-NEG"])
    check(args.scenario == "MD-1-4-GK-ASYM-NEG", "--scenario parses")

    args = _parse_args(["--delay", "large", "--persona", "GK"])
    check(args.delay == "large", "--delay parses")
    check(args.persona == "GK", "--persona parses")

    args = _parse_args(["--fresh"])
    check(args.fresh is True, "--fresh parses")

    args = _parse_args(["--hos", "4", "--mabd", "2", "--day", "negative"])
    check(args.hos == 4, "--hos parses")
    check(args.mabd == 2, "--mabd parses")
    check(args.day == "negative", "--day parses")

    args = _parse_args(["--info", "transparent"])
    check(args.info == "transparent", "--info parses")

    # ═══════════════════════════════════════════════════════════════════
    # 2. apply_filters checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- apply_filters checks ---")

    # Single scenario
    args = _parse_args(["--scenario", "SM-1-4-OC-ASYM-NEU"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 1, f"--scenario filter: {len(result)} == 1")
    check(result[0]["scenario_id"] == "SM-1-4-OC-ASYM-NEU", "--scenario filter: correct ID")

    # Non-existent scenario
    args = _parse_args(["--scenario", "NOPE"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 0, f"--scenario filter (no match): {len(result)} == 0")

    # Delay filter: small → delay_hours=1, 96 scenarios per delay
    args = _parse_args(["--delay", "small"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 96, f"--delay small: {len(result)} == 96")
    check(all(s["delay_hours"] == 1 for s in result), "--delay small: all delay_hours==1")

    args = _parse_args(["--delay", "large"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 96, f"--delay large: {len(result)} == 96")
    check(all(s["delay_hours"] == 4 for s in result), "--delay large: all delay_hours==4")

    # Persona filter: 72 per persona (288 / 4)
    args = _parse_args(["--persona", "GK"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 72, f"--persona GK: {len(result)} == 72")
    check(all(s["persona"] == "GK" for s in result), "--persona GK: all persona==GK")

    # Info filter: 144 per info condition (288 / 2)
    args = _parse_args(["--info", "transparent"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 144, f"--info transparent: {len(result)} == 144")
    check(all(s["info_condition"] == "transparent" for s in result), "--info: correct")

    # HOS filter: 144 per HOS level (288 / 2)
    args = _parse_args(["--hos", "4"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 144, f"--hos 4: {len(result)} == 144")
    check(all(s["hos_remaining_hours"] == 4 for s in result), "--hos 4: correct")

    # MABD filter: 144 per MABD (288 / 2)
    args = _parse_args(["--mabd", "1"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 144, f"--mabd 1: {len(result)} == 144")
    check(all(s["mabd_window_hours"] == 1 for s in result), "--mabd 1: correct")

    # Day filter: 96 per day (288 / 3)
    args = _parse_args(["--day", "negative"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 96, f"--day negative: {len(result)} == 96")
    check(all(s["day_context"] == "negative" for s in result), "--day negative: correct")

    # Combined filters: delay=small + persona=GK → 96/4 = 24
    args = _parse_args(["--delay", "small", "--persona", "GK"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 24, f"--delay small --persona GK: {len(result)} == 24")

    # Triple filter: delay=small + persona=GK + info=asymmetric → 24/2 = 12
    args = _parse_args(["--delay", "small", "--persona", "GK", "--info", "asymmetric"])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 12, f"triple filter: {len(result)} == 12")

    # All filters: should narrow to exactly 1 scenario
    args = _parse_args([
        "--delay", "small", "--persona", "GK", "--info", "asymmetric",
        "--hos", "4", "--mabd", "1", "--day", "neutral",
    ])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 1, f"all filters: {len(result)} == 1")
    check(result[0]["scenario_id"] == "SM-1-4-GK-ASYM-NEU", f"all filters: {result[0]['scenario_id']}")

    # No filters: all 288
    args = _parse_args([])
    result = apply_filters(all_scenarios, args)
    check(len(result) == 288, f"no filters: {len(result)} == 288")

    # ═══════════════════════════════════════════════════════════════════
    # 3. get_completed_scenarios checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- get_completed_scenarios checks ---")

    # Use a temp directory for isolation
    tmpdir = tempfile.mkdtemp()
    try:
        conv_dir = os.path.join(tmpdir, "conversations")

        # Empty / non-existent dir
        check(get_completed_scenarios(conv_dir) == set(), "empty dir → empty set")

        # Create dir with completed file
        os.makedirs(conv_dir)
        with open(os.path.join(conv_dir, "SM-1-4-OC-ASYM-NEU.json"), "w") as f:
            json.dump({"status": "completed", "result": {}}, f)
        completed = get_completed_scenarios(conv_dir)
        check("SM-1-4-OC-ASYM-NEU" in completed, "completed file detected")
        check(len(completed) == 1, f"completed count: {len(completed)} == 1")

        # Failed file should NOT count
        with open(os.path.join(conv_dir, "MD-1-4-GK-ASYM-NEG.json"), "w") as f:
            json.dump({"status": "failed", "error": "test"}, f)
        completed = get_completed_scenarios(conv_dir)
        check("MD-1-4-GK-ASYM-NEG" not in completed, "failed file not counted")
        check(len(completed) == 1, f"still 1 completed after adding failed")

        # Corrupt JSON file should be skipped
        with open(os.path.join(conv_dir, "LG-1-4-OC-ASYM-NEU.json"), "w") as f:
            f.write("{corrupt json")
        completed = get_completed_scenarios(conv_dir)
        check(len(completed) == 1, f"corrupt file skipped: {len(completed)} == 1")

        # Non-JSON file should be ignored
        with open(os.path.join(conv_dir, "readme.txt"), "w") as f:
            f.write("ignore me")
        completed = get_completed_scenarios(conv_dir)
        check(len(completed) == 1, f"non-JSON file ignored: {len(completed)} == 1")

    finally:
        shutil.rmtree(tmpdir)

    # ═══════════════════════════════════════════════════════════════════
    # 4. save_result checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- save_result checks ---")

    def _test_save_result():
        global CONVERSATIONS_DIR
        orig = CONVERSATIONS_DIR
        tmpd = tempfile.mkdtemp()
        try:
            CONVERSATIONS_DIR = os.path.join(tmpd, "conversations")

            test_scenario = all_scenarios[0]
            test_gt = gt_lookup[test_scenario["scenario_id"]]
            test_conv_result = {
                "scenario_id": test_scenario["scenario_id"],
                "turn_log": [{"agent": "dispatcher", "turn": 0, "metadata": {"type": "greeting"}, "message": "Hi"}],
                "termination": "accept",
                "total_turns": 1,
                "pushback_count": 0,
            }
            test_scored = {
                "scenario_id": test_scenario["scenario_id"],
                "score": 1.0,
                "final_slot": "13:00",
                "final_cost": 0,
            }

            save_result(
                test_scenario["scenario_id"], test_conv_result, test_scored,
                test_scenario, test_gt,
            )

            filepath = os.path.join(CONVERSATIONS_DIR, f"{test_scenario['scenario_id']}.json")
            check(os.path.isfile(filepath), "save_result: file created")

            with open(filepath) as f:
                data = json.load(f)
            check(data["status"] == "completed", "save_result: status=completed")
            check("completed_at" in data, "save_result: has completed_at")
            check(data["scenario"] == test_scenario, "save_result: scenario preserved")
            check(data["ground_truth"] == test_gt, "save_result: ground_truth preserved")
            check(data["turn_log"] == test_conv_result["turn_log"], "save_result: turn_log preserved")
            check(data["result"] == test_scored, "save_result: result preserved")
        finally:
            CONVERSATIONS_DIR = orig
            shutil.rmtree(tmpd)

    _test_save_result()

    # ═══════════════════════════════════════════════════════════════════
    # 5. save_failure checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- save_failure checks ---")

    def _test_save_failure():
        global FAILURES_DIR
        orig = FAILURES_DIR
        tmpd = tempfile.mkdtemp()
        try:
            FAILURES_DIR = os.path.join(tmpd, "failures")

            save_failure(
                "SM-1-4-OC-ASYM-NEU",
                Exception("test error"),
                turn_log=[{"turn": 0}],
                raw_responses=["raw text"],
            )

            filepath = os.path.join(FAILURES_DIR, "SM-1-4-OC-ASYM-NEU.json")
            check(os.path.isfile(filepath), "save_failure: file created")

            with open(filepath) as f:
                data = json.load(f)
            check(data["status"] == "failed", "save_failure: status=failed")
            check("failed_at" in data, "save_failure: has failed_at")
            check(data["scenario_id"] == "SM-1-4-OC-ASYM-NEU", "save_failure: scenario_id")
            check(data["error"] == "test error", "save_failure: error message")
            check(data["turn_log"] == [{"turn": 0}], "save_failure: turn_log")
            check(data["raw_responses"] == ["raw text"], "save_failure: raw_responses")

            # save_failure with no optional args
            save_failure("MD-1-4-GK-ASYM-NEG", ValueError("no data"))
            filepath2 = os.path.join(FAILURES_DIR, "MD-1-4-GK-ASYM-NEG.json")
            with open(filepath2) as f:
                data2 = json.load(f)
            check(data2["turn_log"] == [], "save_failure: default turn_log empty")
            check(data2["raw_responses"] == [], "save_failure: default raw_responses empty")
        finally:
            FAILURES_DIR = orig
            shutil.rmtree(tmpd)

    _test_save_failure()

    # ═══════════════════════════════════════════════════════════════════
    # 6. regenerate_summary checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- regenerate_summary checks ---")

    tmpdir = tempfile.mkdtemp()
    try:
        results_root = os.path.join(tmpdir, "results")
        conv_dir = os.path.join(results_root, "conversations")
        os.makedirs(conv_dir)

        # Write 3 completed files
        for i, sid in enumerate(["SM-1-4-OC-ASYM-NEU", "MD-1-4-GK-ASYM-NEG", "LG-1-4-OC-ASYM-NEU"]):
            with open(os.path.join(conv_dir, f"{sid}.json"), "w") as f:
                json.dump({
                    "status": "completed",
                    "result": {"scenario_id": sid, "score": 0.5 + i * 0.1},
                }, f)

        # Write 1 failed file (should be ignored by summary)
        with open(os.path.join(conv_dir, "FAILED-SCENARIO.json"), "w") as f:
            json.dump({"status": "failed", "error": "test"}, f)

        regenerate_summary(results_root)

        summary_path = os.path.join(results_root, "summary.json")
        check(os.path.isfile(summary_path), "regenerate_summary: file created")

        with open(summary_path) as f:
            summary = json.load(f)
        check(summary["total_completed"] == 3, f"summary: total_completed={summary['total_completed']} == 3")
        check("generated_at" in summary, "summary: has generated_at")
        check(len(summary["results"]) == 3, f"summary: results count={len(summary['results'])} == 3")

        # Results should be sorted by filename
        sids = [r["scenario_id"] for r in summary["results"]]
        check("FAILED-SCENARIO" not in sids, "summary: failed file excluded")

        # Empty dir
        empty_root = os.path.join(tmpdir, "empty_results")
        regenerate_summary(empty_root)
        with open(os.path.join(empty_root, "summary.json")) as f:
            empty_summary = json.load(f)
        check(empty_summary["total_completed"] == 0, "summary: empty dir → 0 completed")
        check(empty_summary["results"] == [], "summary: empty dir → empty results")

    finally:
        shutil.rmtree(tmpdir)

    # ═══════════════════════════════════════════════════════════════════
    # 7. Import & callable checks
    # ═══════════════════════════════════════════════════════════════════
    print("\n--- Import & callable checks ---")

    check(callable(apply_filters), "apply_filters is callable")
    check(callable(get_completed_scenarios), "get_completed_scenarios is callable")
    check(callable(save_result), "save_result is callable")
    check(callable(save_failure), "save_failure is callable")
    check(callable(regenerate_summary), "regenerate_summary is callable")
    check(callable(run_experiment), "run_experiment is callable")

    # ═══════════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n  {passed} checks passed, {failed} failed")

    if failed == 0:
        print("\nAll checks passed.")
    else:
        print("\nFix validation errors before proceeding.")

    print("\nDone. Ready for checkpoint review.\n")
    return failed


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))

    args = _parse_args()

    # ── Validation mode ──
    if args.validate:
        fail_count = _run_validation()
        raise SystemExit(1 if fail_count > 0 else 0)

    # ── Experiment execution (default) ──
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from anthropic import Anthropic

    with open(SCENARIOS_PATH) as f:
        all_scenarios = json.load(f)
    with open(GROUND_TRUTH_PATH) as f:
        all_ground_truths = json.load(f)

    filtered = apply_filters(all_scenarios, args)
    if not filtered:
        print("No scenarios match the given filters.")
        raise SystemExit(1)

    print(f"\nCNB Experiment Runner")
    print(f"=" * 50)
    print(f"Filtered to {len(filtered)} scenario(s)")

    client = Anthropic()
    completed_count, failed_count = run_experiment(
        filtered, all_ground_truths, client, fresh=args.fresh
    )

    regenerate_summary()

    print(f"\n{'=' * 50}")
    print(f"Done. Completed: {completed_count} | Failed: {failed_count}")
    print(f"Summary written to results/summary.json")

    raise SystemExit(1 if failed_count > 0 else 0)

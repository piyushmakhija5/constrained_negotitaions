"""CNB Prompt Builder

Loads prompt templates and injects scenario variables to produce complete
system prompts for the dispatcher and warehouse agents.

Uses sequential .replace() instead of .format() because templates contain
literal JSON braces in Response Format sections.

Usage:
    python src/prompt_builder.py

Runs validation against all 288 scenarios.
"""

import json
import os
import re

from config import ALL_SLOTS, DETENTION_FREE_MINUTES, ORIGINAL_APPOINTMENT_STR, RESCHEDULING_FEE

# ── Template Variable Registry ────────────────────────────────────────────────
# Canonical list of variables each builder substitutes. Used by validation to
# catch orphaned variables in templates (present in file but not in builder).

DISPATCHER_VARS = [
    "original_appointment", "delay_hours", "truck_arrival",
    "shipment_value", "retailer_name", "hos_expiry", "hos_deadline",
    "mabd_deadline", "otif_penalty", "detention_free_minutes",
    "detention_start", "detention_rate",
    "rescheduling_fee", "transparent_section",
]
WAREHOUSE_VARS = [
    "original_appointment", "available_slots",
    "persona_section", "day_context",
]

# ── Path Setup ────────────────────────────────────────────────────────────────

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROMPTS_DIR = os.path.join(_BASE_DIR, "prompts")


# ── Template Loading ──────────────────────────────────────────────────────────

def _load_template(relative_path):
    """Load a template file from the prompts/ directory."""
    path = os.path.join(_PROMPTS_DIR, relative_path)
    with open(path, "r") as f:
        return f.read()


# Load all templates at module import time
_DISPATCHER_TEMPLATE = _load_template("dispatcher_template.md")
_WAREHOUSE_TEMPLATE = _load_template("warehouse/base_template.md")

_PERSONA_TEMPLATES = {
    "OC": _load_template("warehouse/persona_oc.md"),
    "FR": _load_template("warehouse/persona_fr.md"),
    "GK": _load_template("warehouse/persona_gk.md"),
    "CD": _load_template("warehouse/persona_cd.md"),
}

# ── Transparent Section ───────────────────────────────────────────────────────
# Included in dispatcher prompt only when info_condition == "transparent"

_TRANSPARENT_SECTION = (
    "## Available Warehouse Slots\n"
    "\n"
    "The warehouse has the following dock slots available today: "
    + ", ".join(ALL_SLOTS)
    + ".\n"
    "\n"
    "Having a slot available does not mean the warehouse will assign it to you. "
    "You still need to negotiate for the slot you want."
)

# ── Day Context Modifiers ─────────────────────────────────────────────────────
# Appended at the end of warehouse prompt. Neutral = nothing.

_DAY_CONTEXTS = {
    "neutral": "",
    "positive": (
        "Additional context: It's been a smooth day at the warehouse. "
        "A scheduled truck cancelled earlier, so you're actually ahead of schedule. "
        "You're in a good mood."
    ),
    "negative": (
        "Additional context: It's been a rough day. "
        "Two other trucks already had to reschedule today, your dock management "
        "system had issues this morning, and your manager is on your case about "
        "overtime costs this week."
    ),
}


# ── Builder Functions ─────────────────────────────────────────────────────────

def build_dispatcher_prompt(scenario):
    """Build the complete dispatcher system prompt from a scenario config.

    Args:
        scenario: Dict from scenarios.json with all scenario fields.

    Returns:
        Complete system prompt string ready for the Anthropic API.
    """
    transparent = (
        _TRANSPARENT_SECTION
        if scenario["info_condition"] == "transparent"
        else ""
    )

    prompt = _DISPATCHER_TEMPLATE
    prompt = prompt.replace("{original_appointment}", ORIGINAL_APPOINTMENT_STR)
    prompt = prompt.replace("{delay_hours}", str(scenario["delay_hours"]))
    prompt = prompt.replace("{truck_arrival}", scenario["truck_arrival"])
    prompt = prompt.replace("{shipment_value}", f'{scenario["shipment_value"]:,}')
    prompt = prompt.replace("{retailer_name}", scenario["retailer_name"])
    prompt = prompt.replace("{hos_expiry}", scenario["hos_expiry"])
    prompt = prompt.replace("{hos_deadline}", scenario["hos_deadline"])
    prompt = prompt.replace("{mabd_deadline}", scenario["mabd_deadline"])
    prompt = prompt.replace("{otif_penalty}", f'{scenario["otif_penalty"]:,}')
    prompt = prompt.replace("{detention_free_minutes}", str(DETENTION_FREE_MINUTES))
    prompt = prompt.replace("{detention_start}", scenario["detention_start"])
    prompt = prompt.replace("{detention_rate}", str(scenario["detention_rate_per_hour"]))
    prompt = prompt.replace("{rescheduling_fee}", str(RESCHEDULING_FEE))
    prompt = prompt.replace("{transparent_section}", transparent)

    return prompt


def build_warehouse_prompt(scenario):
    """Build the complete warehouse system prompt from a scenario config.

    Args:
        scenario: Dict from scenarios.json with all scenario fields.

    Returns:
        Complete system prompt string ready for the Anthropic API.
    """
    persona_section = _PERSONA_TEMPLATES[scenario["persona"]]
    day_context = _DAY_CONTEXTS[scenario["day_context"]]
    available_slots = ", ".join(scenario["available_slots"])

    prompt = _WAREHOUSE_TEMPLATE
    prompt = prompt.replace("{available_slots}", available_slots)
    prompt = prompt.replace("{persona_section}", persona_section)
    prompt = prompt.replace("{day_context}", day_context)
    # Last: substitute config values that may appear in base template AND persona/day content
    prompt = prompt.replace("{original_appointment}", ORIGINAL_APPOINTMENT_STR)

    return prompt


# ── Validation ────────────────────────────────────────────────────────────────

def validate(scenarios):
    """Run validation checks on prompt building for all scenarios.

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

    # ── 1. Template load checks ──
    check(len(_DISPATCHER_TEMPLATE) > 0, "Dispatcher template is empty")
    check(len(_WAREHOUSE_TEMPLATE) > 0, "Warehouse template is empty")
    check(len(_PERSONA_TEMPLATES["OC"]) > 0, "OC persona template is empty")
    check(len(_PERSONA_TEMPLATES["FR"]) > 0, "FR persona template is empty")
    check(len(_PERSONA_TEMPLATES["GK"]) > 0, "GK persona template is empty")
    check(len(_PERSONA_TEMPLATES["CD"]) > 0, "CD persona template is empty")

    # ── 2. Orphan check: variables in templates but not in builder registry ──
    # Scan raw templates for {word} patterns and verify each is known.
    # This catches template edits that add a variable without updating the builder.
    _var_pattern = re.compile(r"\{([a-z_]+)\}")

    raw_dispatcher_vars = set(_var_pattern.findall(_DISPATCHER_TEMPLATE))
    for var in raw_dispatcher_vars:
        check(
            var in DISPATCHER_VARS,
            f"Orphaned variable {{{var}}} in dispatcher template — not in builder registry",
        )
    for var in DISPATCHER_VARS:
        check(
            var in raw_dispatcher_vars,
            f"Registry variable {{{var}}} not found in dispatcher template",
        )

    raw_warehouse_vars = set(_var_pattern.findall(_WAREHOUSE_TEMPLATE))
    for var in raw_warehouse_vars:
        check(
            var in WAREHOUSE_VARS,
            f"Orphaned variable {{{var}}} in warehouse template — not in builder registry",
        )
    for var in WAREHOUSE_VARS:
        check(
            var in raw_warehouse_vars,
            f"Registry variable {{{var}}} not found in warehouse template",
        )

    # ── 3. Build all prompts and check for un-substituted variables ──
    dispatcher_prompts = {}
    warehouse_prompts = {}

    for s in scenarios:
        sid = s["scenario_id"]
        dp = build_dispatcher_prompt(s)
        wp = build_warehouse_prompt(s)
        dispatcher_prompts[sid] = dp
        warehouse_prompts[sid] = wp

        for var in DISPATCHER_VARS:
            check(
                f"{{{var}}}" not in dp,
                f"{sid}: un-substituted {{{var}}} in dispatcher prompt",
            )
        for var in WAREHOUSE_VARS:
            check(
                f"{{{var}}}" not in wp,
                f"{sid}: un-substituted {{{var}}} in warehouse prompt",
            )

    # ── 4. Transparent section presence ──
    for s in scenarios:
        sid = s["scenario_id"]
        dp = dispatcher_prompts[sid]
        if s["info_condition"] == "transparent":
            check(
                "Available Warehouse Slots" in dp,
                f"{sid}: transparent scenario missing slots section",
            )
            check(
                ", ".join(ALL_SLOTS) in dp,
                f"{sid}: transparent scenario missing slot list",
            )
        else:
            check(
                "Available Warehouse Slots" not in dp,
                f"{sid}: asymmetric scenario should not have slots section",
            )

    # ── 5. Currency formatting ──
    for s in scenarios:
        sid = s["scenario_id"]
        dp = dispatcher_prompts[sid]
        check(
            "$500,000" in dp,
            f"{sid}: shipment value should be $500,000",
        )
        check(
            "$10,000" in dp,
            f"{sid}: OTIF penalty should be $10,000",
        )

    # ── 6. Persona-specific text in warehouse prompts ──
    persona_markers = {
        "OC": "skeleton crew",
        "FR": "reschedule",
        "GK": "regular carriers",
        "CD": "indifferent",
    }
    for s in scenarios:
        sid = s["scenario_id"]
        wp = warehouse_prompts[sid]
        persona = s["persona"]
        marker = persona_markers[persona]
        check(
            marker in wp,
            f"{sid}: warehouse prompt missing persona marker '{marker}' for {persona}",
        )

    # ── 7. Day context text ──
    for s in scenarios:
        sid = s["scenario_id"]
        wp = warehouse_prompts[sid]
        day = s["day_context"]
        if day == "positive":
            check(
                "smooth day" in wp,
                f"{sid}: positive day context missing 'smooth day'",
            )
        elif day == "negative":
            check(
                "rough day" in wp,
                f"{sid}: negative day context missing 'rough day'",
            )
        else:  # neutral
            check(
                "smooth day" not in wp and "rough day" not in wp,
                f"{sid}: neutral day context should have no day modifier",
            )

    # ── 8. Spot-check specific scenario values ──
    spot_checks = {
        "SM-1-4": [
            ("arriving at 13:00", "truck_arrival"),
            ("ends at 16:00", "hos_expiry"),
            ("latest dock slot you can accept is 15:00", "hos_deadline"),
            ("closes at 13:00", "mabd_deadline"),
            ("delayed by 1 hours", "delay_hours"),
        ],
        "MD-2-7": [
            ("arriving at 14:00", "truck_arrival"),
            ("ends at 19:00", "hos_expiry"),
            ("latest dock slot you can accept is 18:00", "hos_deadline"),
            ("closes at 14:00", "mabd_deadline"),
            ("delayed by 2 hours", "delay_hours"),
        ],
        "LG-1-4": [
            ("arriving at 16:00", "truck_arrival"),
            ("ends at 16:00", "hos_expiry"),
            ("latest dock slot you can accept is 15:00", "hos_deadline"),
            ("closes at 13:00", "mabd_deadline"),
            ("delayed by 4 hours", "delay_hours"),
        ],
    }

    for prefix, checks in spot_checks.items():
        matching = [
            s for s in scenarios if s["scenario_id"].startswith(prefix)
        ]
        if matching:
            dp = dispatcher_prompts[matching[0]["scenario_id"]]
            for expected_text, field_name in checks:
                check(
                    expected_text in dp,
                    f"{prefix}*: expected '{expected_text}' ({field_name}) in dispatcher prompt",
                )

    # ── 9. No hardcoded values in templates ──
    # Verify config values appear via substitution, not hardcoding
    for s in scenarios:
        sid = s["scenario_id"]
        wp = warehouse_prompts[sid]
        check(
            ORIGINAL_APPOINTMENT_STR in wp,
            f"{sid}: warehouse prompt missing original appointment",
        )
        check(
            ", ".join(s["available_slots"]) in wp,
            f"{sid}: warehouse prompt missing available slots",
        )

    # ── 10. Unified JSON format ──
    # Both prompts should use unified JSON (with "message" field), not --- separator
    for s in scenarios:
        sid = s["scenario_id"]
        dp = dispatcher_prompts[sid]
        wp = warehouse_prompts[sid]
        check(
            '"message"' in dp,
            f"{sid}: dispatcher prompt missing 'message' field in format",
        )
        check(
            '"message"' in wp,
            f"{sid}: warehouse prompt missing 'message' field in format",
        )
        check(
            "separated by ---" not in dp,
            f"{sid}: dispatcher prompt still uses --- separator format",
        )
        check(
            "separated by ---" not in wp,
            f"{sid}: warehouse prompt still uses --- separator format",
        )

    # ── 11. Rescheduling fee not hardcoded in warehouse prompt ──
    for s in scenarios:
        sid = s["scenario_id"]
        wp = warehouse_prompts[sid]
        check(
            "$100" not in wp,
            f"{sid}: warehouse prompt should not hardcode $100 rescheduling fee",
        )

    # ── 12. Rescheduling fee from config in dispatcher prompt ──
    for s in scenarios:
        sid = s["scenario_id"]
        dp = dispatcher_prompts[sid]
        check(
            f"${RESCHEDULING_FEE}" in dp,
            f"{sid}: dispatcher prompt missing rescheduling fee from config",
        )

    return passed, failed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\nCNB Prompt Builder")
    print("=" * 40)

    # Load scenarios
    scenarios_path = os.path.join(_BASE_DIR, "config", "scenarios.json")
    with open(scenarios_path, "r") as f:
        scenarios = json.load(f)
    print(f"\nLoaded {len(scenarios)} scenarios")

    print("\nValidating...")
    passed, failed = validate(scenarios)
    print(f"\n  {passed} checks passed, {failed} failed")

    if failed > 0:
        print("\nFix validation errors before proceeding.")
        return False

    # Show sample prompts for visual inspection
    sample = scenarios[0]
    print(f"\n{'=' * 60}")
    print(f"Sample dispatcher prompt ({sample['scenario_id']}):")
    print(f"{'=' * 60}")
    dp = build_dispatcher_prompt(sample)
    print(dp[:500] + "..." if len(dp) > 500 else dp)

    print(f"\n{'=' * 60}")
    print(f"Sample warehouse prompt ({sample['scenario_id']}):")
    print(f"{'=' * 60}")
    wp = build_warehouse_prompt(sample)
    print(wp[:500] + "..." if len(wp) > 500 else wp)

    print(f"\nPrompt lengths:")
    print(f"  Dispatcher: {len(dp)} chars")
    print(f"  Warehouse:  {len(wp)} chars")

    print("\nDone. Ready for checkpoint review.\n")
    return True


if __name__ == "__main__":
    main()

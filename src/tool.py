"""CNB Tool: calculate_slot_cost

Dispatcher's cost calculator tool. Pure Python — no LLM needed.
Called by the orchestrator when the dispatcher agent makes a tool_use request.

Also exports TOOL_DEFINITION for passing to the Anthropic API tools parameter.
"""

import math
import re

from config import parse_time


# ── API Tool Definition ────────────────────────────────────────────────────────
# Passed to the Anthropic API in the `tools` parameter for dispatcher calls.

TOOL_DEFINITION = {
    "name": "calculate_slot_cost",
    "description": (
        "Calculate the cost and feasibility of a proposed dock slot time. "
        "Call this to check any slot before negotiating. "
        "Pass drop_and_hook=true to see how costs change if the driver "
        "drops the trailer without waiting for unload."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "slot_time": {
                "type": "string",
                "description": "The dock time to evaluate, e.g. '14:30'",
            },
            "drop_and_hook": {
                "type": "boolean",
                "description": (
                    "If true, calculate assuming driver drops trailer "
                    "without waiting for unload. Default false."
                ),
            },
        },
        "required": ["slot_time", "drop_and_hook"],
        "additionalProperties": False,
    },
}

# ── Time Normalization ────────────────────────────────────────────────────────
# Input comes from the dispatcher LLM's tool_use calls. While we ask for HH:MM,
# the LLM may produce variations like "2:30 PM", "14 30", "1430", etc.
# We normalize whatever we get into minutes-since-midnight, or return an error.

_ERROR = {"error": "Could not parse slot time. Use 24-hour format like '14:30'."}


def _normalize_time(slot_time):
    """Try to parse an LLM-generated time string into minutes since midnight.

    Handles: "14:30", "14 30", "1430", "2:30 PM", "2:30pm", "2 30 PM", "230pm"
    Returns (minutes, None) on success, (None, error_dict) on failure.
    """
    if not isinstance(slot_time, str) or not slot_time.strip():
        return None, _ERROR

    t = slot_time.strip()

    # Detect AM/PM
    is_pm = None
    lower = t.lower()
    if lower.endswith("pm") or lower.endswith("p.m."):
        is_pm = True
        t = re.sub(r"\s*(pm|p\.m\.)\s*$", "", t, flags=re.IGNORECASE).strip()
    elif lower.endswith("am") or lower.endswith("a.m."):
        is_pm = False
        t = re.sub(r"\s*(am|a\.m\.)\s*$", "", t, flags=re.IGNORECASE).strip()

    # Try "HH:MM" or "H:MM"
    m = re.match(r"^(\d{1,2}):(\d{2})$", t)
    if not m:
        # Try "HH MM" or "H MM" (space separator)
        m = re.match(r"^(\d{1,2})\s+(\d{2})$", t)
    if not m:
        # Try "HHMM" (4 digits, no separator)
        m = re.match(r"^(\d{2})(\d{2})$", t)
    if not m:
        # Try bare hour "14" or "2" (assume :00)
        m = re.match(r"^(\d{1,2})$", t)
        if m:
            hour = int(m.group(1))
            minute = 0
        else:
            return None, _ERROR
    else:
        hour = int(m.group(1))
        minute = int(m.group(2))

    # Apply AM/PM conversion
    if is_pm is not None:
        if hour < 1 or hour > 12 or minute > 59:
            return None, _ERROR
        if is_pm and hour != 12:
            hour += 12
        elif not is_pm and hour == 12:
            hour = 0
    else:
        if hour > 23 or minute > 59:
            return None, _ERROR

    return hour * 60 + minute, None


# ── Tool Implementation ───────────────────────────────────────────────────────

def calculate_slot_cost(slot_time, drop_and_hook, scenario):
    """Calculate cost and feasibility of a proposed dock slot.

    Args:
        slot_time: Dock time to evaluate, e.g. '14:30'
        drop_and_hook: If True, assume driver drops trailer (no unload wait)
        scenario: Scenario config dict with truck_arrival, mabd_deadline,
                  hos_deadline, hos_deadline_with_dh, otif_penalty,
                  detention_free_minutes, detention_rate_per_hour

    Returns:
        Dict with feasible, otif_compliant, otif_penalty, detention_cost,
        total_cost, hos_buffer_minutes. Or error dict if invalid input.
    """
    slot, error = _normalize_time(slot_time)
    if error:
        return error
    truck_arrival = parse_time(scenario["truck_arrival"])

    # Slot before truck arrival is impossible
    if slot < truck_arrival:
        return {
            "error": f"Slot is before truck arrival ({scenario['truck_arrival']}). "
                     "Your truck is not there yet — request a slot at or after arrival time.",
        }

    mabd = parse_time(scenario["mabd_deadline"])

    # HOS deadline depends on whether D&H is proposed
    if drop_and_hook:
        hos_deadline = parse_time(scenario["hos_deadline_with_dh"])
    else:
        hos_deadline = parse_time(scenario["hos_deadline"])

    feasible = slot <= hos_deadline
    hos_buffer = hos_deadline - slot

    # OTIF
    otif_compliant = slot <= mabd
    otif_penalty = 0 if otif_compliant else scenario["otif_penalty"]

    # Detention (from truck arrival)
    wait = max(0, slot - truck_arrival)
    billable = max(0, wait - scenario["detention_free_minutes"])
    detention_cost = math.ceil(billable / 60) * scenario["detention_rate_per_hour"]

    return {
        "feasible": feasible,
        "otif_compliant": otif_compliant,
        "otif_penalty": otif_penalty,
        "detention_cost": detention_cost,
        "total_cost": otif_penalty + detention_cost,
        "hos_buffer_minutes": hos_buffer,
    }

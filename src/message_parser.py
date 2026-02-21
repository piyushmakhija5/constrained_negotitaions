"""CNB Message Parser

Parses unified JSON responses from dispatcher and warehouse agents.
Validates against Pydantic models and extracts structured metadata + natural language.

Usage:
    python src/message_parser.py

Both agents produce a single JSON object with a "message" field containing the
natural language text. The parser does json.loads() on the full response,
validates against the appropriate Pydantic model, and returns a typed object.

The orchestrator (conversation.py) catches ParseError to trigger retries.
"""

import json
import re
from typing import List, Literal, Optional

from pydantic import BaseModel


# ── Custom Exception ─────────────────────────────────────────────────────────


class ParseError(Exception):
    """Raised when agent response cannot be parsed or validated.

    Attributes:
        raw_text: The original response text that failed parsing.
    """

    def __init__(self, message, raw_text=None):
        super().__init__(message)
        self.raw_text = raw_text


# ── Pydantic Models ──────────────────────────────────────────────────────────


class DispatcherMetadata(BaseModel):
    type: Literal["greeting", "info_request", "pushback", "accept", "walk_away"]
    slot_requested: Optional[str] = None
    tactics_used: List[str] = []
    reasoning: str
    message: str


class WarehouseMetadata(BaseModel):
    slot_offered: Optional[str] = None
    slot_withdrawn: Optional[str] = None
    cue_dropped: Optional[
        Literal["staffing", "schedule_disruption", "reserved_for_regulars", "preference_later"]
    ] = None
    drop_and_hook_response: Optional[bool] = None
    rescheduling_fee_accepted: Optional[bool] = None
    message: str


# ── JSON Extraction ──────────────────────────────────────────────────────────


def _extract_json(raw_text):
    """Extract JSON string from raw response text.

    Handles:
      1. Clean JSON — starts with '{', returned as-is (after strip)
      2. Markdown fences — ```json ... ``` or ``` ... ``` anywhere in text
      3. Prose before/after JSON (e.g. "thinking" text + fenced JSON)
      4. Leading/trailing whitespace

    Does NOT attempt to fix broken JSON.

    Returns:
        Stripped JSON string.

    Raises:
        ParseError: If input is empty/whitespace-only or no JSON found.
    """
    if not raw_text or not raw_text.strip():
        raise ParseError("Empty response", raw_text=raw_text)

    text = raw_text.strip()

    # 1. Clean JSON — starts with '{'
    if text.startswith("{"):
        return text

    # 2. Search for markdown-fenced JSON anywhere in the text
    m = re.search(r"```(?:json)?\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # 3. No fences found — return as-is and let json.loads fail with a clear error
    return text


# ── Parse Functions ──────────────────────────────────────────────────────────


def parse_response(raw_text, model_class):
    """Parse a unified JSON response from either agent.

    Args:
        raw_text: Raw text content from the API response.
        model_class: DispatcherMetadata or WarehouseMetadata.

    Returns:
        Validated Pydantic model instance.

    Raises:
        ParseError: If JSON parsing or Pydantic validation fails.
    """
    text = _extract_json(raw_text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ParseError(f"Invalid JSON: {e}", raw_text=raw_text) from e

    try:
        return model_class.model_validate(data)
    except Exception as e:
        raise ParseError(f"Validation failed: {e}", raw_text=raw_text) from e


def parse_dispatcher_response(raw_text):
    """Parse a dispatcher agent response.

    Returns:
        DispatcherMetadata instance.

    Raises:
        ParseError: If parsing or validation fails.
    """
    return parse_response(raw_text, DispatcherMetadata)


def parse_warehouse_response(raw_text):
    """Parse a warehouse agent response.

    Returns:
        WarehouseMetadata instance.

    Raises:
        ParseError: If parsing or validation fails.
    """
    return parse_response(raw_text, WarehouseMetadata)


# ── Validation ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    passed = 0
    failed = 0

    def check(condition, message):
        global passed, failed
        if condition:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {message}")

    print("\nCNB Message Parser Validation")
    print("=" * 40)

    # ── 1. Valid dispatcher: greeting ──
    raw = json.dumps({
        "type": "greeting",
        "slot_requested": "14:30",
        "tactics_used": [],
        "reasoning": "Opening with a reasonable slot request.",
        "message": "Hello, I'd like to request the 14:30 slot please.",
    })
    meta = parse_dispatcher_response(raw)
    check(meta.type == "greeting", "dispatcher greeting type")
    check(meta.slot_requested == "14:30", "dispatcher greeting slot_requested")
    check(meta.tactics_used == [], "dispatcher greeting tactics_used empty")
    check(meta.message == "Hello, I'd like to request the 14:30 slot please.", "dispatcher greeting message")

    # ── 2. Valid dispatcher: pushback with tactics ──
    raw = json.dumps({
        "type": "pushback",
        "slot_requested": "16:00",
        "tactics_used": ["hos_cite", "otif_cite"],
        "reasoning": "Citing HOS and OTIF to push for earlier slot.",
        "message": "We have HOS constraints and OTIF penalties. Can we do 16:00?",
    })
    meta = parse_dispatcher_response(raw)
    check(meta.type == "pushback", "dispatcher pushback type")
    check(meta.tactics_used == ["hos_cite", "otif_cite"], "dispatcher pushback tactics")

    # ── 3. Valid dispatcher: accept (no slot_requested) ──
    raw = json.dumps({
        "type": "accept",
        "reasoning": "The offered slot works within our constraints.",
        "message": "We'll take it. Thank you.",
    })
    meta = parse_dispatcher_response(raw)
    check(meta.type == "accept", "dispatcher accept type")
    check(meta.slot_requested is None, "dispatcher accept no slot_requested")
    check(meta.tactics_used == [], "dispatcher accept default tactics_used")

    # ── 4. Valid dispatcher: walk_away ──
    raw = json.dumps({
        "type": "walk_away",
        "reasoning": "No feasible slot available.",
        "message": "Unfortunately we cannot reach agreement.",
    })
    meta = parse_dispatcher_response(raw)
    check(meta.type == "walk_away", "dispatcher walk_away type")

    # ── 5. Valid dispatcher: info_request ──
    raw = json.dumps({
        "type": "info_request",
        "reasoning": "Need to understand slot availability.",
        "message": "What slots do you have available today?",
    })
    meta = parse_dispatcher_response(raw)
    check(meta.type == "info_request", "dispatcher info_request type")

    # ── 6. Valid warehouse: slot offer with cue ──
    raw = json.dumps({
        "slot_offered": "19:00",
        "slot_withdrawn": None,
        "cue_dropped": "staffing",
        "drop_and_hook_response": None,
        "rescheduling_fee_accepted": None,
        "message": "We're short-staffed today. Best I can do is 19:00.",
    })
    meta = parse_warehouse_response(raw)
    check(meta.slot_offered == "19:00", "warehouse slot_offered")
    check(meta.cue_dropped == "staffing", "warehouse cue_dropped staffing")
    check(meta.message == "We're short-staffed today. Best I can do is 19:00.", "warehouse message")

    # ── 7. Valid warehouse: all cue types ──
    for cue in ["staffing", "schedule_disruption", "reserved_for_regulars", "preference_later"]:
        raw = json.dumps({"cue_dropped": cue, "message": f"Test cue: {cue}"})
        meta = parse_warehouse_response(raw)
        check(meta.cue_dropped == cue, f"warehouse cue_dropped {cue}")

    # ── 8. Valid warehouse: D&H + rescheduling fee ──
    raw = json.dumps({
        "slot_offered": "16:00",
        "drop_and_hook_response": True,
        "rescheduling_fee_accepted": True,
        "message": "OK, drop-and-hook at 16:00 with the rescheduling fee.",
    })
    meta = parse_warehouse_response(raw)
    check(meta.drop_and_hook_response is True, "warehouse D&H true")
    check(meta.rescheduling_fee_accepted is True, "warehouse rescheduling fee accepted")

    # ── 9. Valid warehouse: minimal (just message) ──
    raw = json.dumps({"message": "Let me check the schedule."})
    meta = parse_warehouse_response(raw)
    check(meta.slot_offered is None, "warehouse minimal no slot_offered")
    check(meta.cue_dropped is None, "warehouse minimal no cue_dropped")

    # ── 10. Markdown fence stripping ──
    inner = json.dumps({
        "type": "greeting",
        "reasoning": "Test fences.",
        "message": "Hello from fenced JSON.",
    })
    raw = f"```json\n{inner}\n```"
    meta = parse_dispatcher_response(raw)
    check(meta.type == "greeting", "markdown fence json tag")
    check(meta.message == "Hello from fenced JSON.", "markdown fence message preserved")

    # Fences without json tag
    raw = f"```\n{inner}\n```"
    meta = parse_dispatcher_response(raw)
    check(meta.type == "greeting", "markdown fence no json tag")

    # ── 11. Missing required field: message ──
    raw = json.dumps({"type": "greeting", "reasoning": "No message field."})
    try:
        parse_dispatcher_response(raw)
        check(False, "missing message should raise ParseError")
    except ParseError:
        check(True, "missing message raises ParseError")

    # Missing required field: reasoning (dispatcher)
    raw = json.dumps({"type": "greeting", "message": "No reasoning."})
    try:
        parse_dispatcher_response(raw)
        check(False, "missing reasoning should raise ParseError")
    except ParseError:
        check(True, "missing reasoning raises ParseError")

    # ── 12. Invalid type enum ──
    raw = json.dumps({
        "type": "demand",
        "reasoning": "Bad type.",
        "message": "I demand this slot!",
    })
    try:
        parse_dispatcher_response(raw)
        check(False, "invalid type 'demand' should raise ParseError")
    except ParseError:
        check(True, "invalid type 'demand' raises ParseError")

    # ── 13. Invalid cue_dropped enum ──
    raw = json.dumps({
        "cue_dropped": "bad_mood",
        "message": "I'm in a bad mood.",
    })
    try:
        parse_warehouse_response(raw)
        check(False, "invalid cue_dropped should raise ParseError")
    except ParseError:
        check(True, "invalid cue_dropped raises ParseError")

    # ── 14. Malformed JSON: trailing comma ──
    raw = '{"type": "greeting", "reasoning": "test", "message": "hi",}'
    try:
        parse_dispatcher_response(raw)
        check(False, "trailing comma should raise ParseError")
    except ParseError:
        check(True, "trailing comma raises ParseError")

    # Malformed JSON: unquoted keys
    raw = '{type: "greeting", reasoning: "test", message: "hi"}'
    try:
        parse_dispatcher_response(raw)
        check(False, "unquoted keys should raise ParseError")
    except ParseError:
        check(True, "unquoted keys raises ParseError")

    # ── 15. Empty/whitespace input ──
    for empty_input in ["", "   ", "\n\t\n"]:
        try:
            parse_dispatcher_response(empty_input)
            check(False, f"empty input {repr(empty_input)} should raise ParseError")
        except ParseError:
            check(True, f"empty input {repr(empty_input)} raises ParseError")

    try:
        parse_dispatcher_response(None)
        check(False, "None input should raise ParseError")
    except ParseError:
        check(True, "None input raises ParseError")

    # ── 16. Extra fields tolerated ──
    raw = json.dumps({
        "type": "pushback",
        "slot_requested": "14:30",
        "tactics_used": ["empathy"],
        "reasoning": "Trying empathy.",
        "message": "Please, we really need this.",
        "confidence": 0.8,
        "internal_note": "LLM added this",
    })
    meta = parse_dispatcher_response(raw)
    check(meta.type == "pushback", "extra fields: type preserved")
    check(meta.tactics_used == ["empathy"], "extra fields: novel tactic accepted as str")

    # ── 17. tactics_used accepts any string (not just known enums) ──
    raw = json.dumps({
        "type": "pushback",
        "tactics_used": ["hos_cite", "empathy", "urgency_appeal", "relationship_leverage"],
        "reasoning": "Multiple tactics.",
        "message": "We need this slot.",
    })
    meta = parse_dispatcher_response(raw)
    check(len(meta.tactics_used) == 4, "arbitrary tactic strings accepted")

    # ── 18. model_dump() round-trip ──
    raw = json.dumps({
        "type": "pushback",
        "slot_requested": "16:00",
        "tactics_used": ["otif_cite"],
        "reasoning": "OTIF concern.",
        "message": "The OTIF penalty is significant.",
    })
    meta1 = parse_dispatcher_response(raw)
    dumped = meta1.model_dump()
    meta2 = DispatcherMetadata.model_validate(dumped)
    check(meta1 == meta2, "model_dump round-trip matches")

    # model_dump(exclude={"message"}) for turn_log format
    meta_dict = meta1.model_dump(exclude={"message"})
    check("message" not in meta_dict, "model_dump exclude message works")
    check(meta_dict["type"] == "pushback", "model_dump exclude preserves type")
    check(meta_dict["slot_requested"] == "16:00", "model_dump exclude preserves slot_requested")

    # ── 19. Warehouse model_dump round-trip ──
    raw = json.dumps({
        "slot_offered": "17:00",
        "cue_dropped": "schedule_disruption",
        "drop_and_hook_response": False,
        "message": "We have a disruption, 17:00 is the best I can do.",
    })
    meta1 = parse_warehouse_response(raw)
    dumped = meta1.model_dump()
    meta2 = WarehouseMetadata.model_validate(dumped)
    check(meta1 == meta2, "warehouse model_dump round-trip matches")

    # ── 20. ParseError preserves raw_text ──
    bad_raw = "this is not json at all"
    try:
        parse_dispatcher_response(bad_raw)
        check(False, "non-json should raise ParseError")
    except ParseError as e:
        check(e.raw_text == bad_raw, "ParseError preserves raw_text")
        check("Invalid JSON" in str(e), "ParseError message mentions JSON")

    # ── Summary ──
    print(f"\n  {passed} checks passed, {failed} failed")
    if failed == 0:
        print("\nAll checks passed. Ready for checkpoint review.\n")
    else:
        print("\nFix validation errors before proceeding.\n")

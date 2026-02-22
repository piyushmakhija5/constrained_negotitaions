"""CNB Conversation Orchestrator

Runs a single dispatcher-vs-warehouse negotiation: API calls, tool handling,
message visibility, turn counting, termination, retries, and turn log production.

Usage:
    python src/conversation.py

The orchestrator manages two separate message histories (dispatcher_messages and
warehouse_messages) with different visibility rules:
  - Dispatcher sees own metadata JSON in history (for self-tracking pushback count)
  - Warehouse NEVER sees metadata from either agent — NL only
  - Tool call/result pairs exist only in dispatcher_messages
"""

import json
import logging
import os
import time

from anthropic import Anthropic, APIError, APIConnectionError, RateLimitError

from config import (
    DISPATCHER_MODEL,
    DISPATCHER_TEMPERATURE,
    DISPATCHER_MAX_TOKENS,
    WAREHOUSE_MODEL,
    WAREHOUSE_TEMPERATURE,
    WAREHOUSE_MAX_TOKENS,
    MAX_PUSHBACKS,
    MAX_TURNS,
    MAX_RETRIES,
)
from prompt_builder import build_dispatcher_prompt, build_warehouse_prompt
from tool import TOOL_DEFINITION, calculate_slot_cost
from message_parser import (
    ParseError,
    parse_dispatcher_response,
    parse_warehouse_response,
)

logger = logging.getLogger(__name__)


# ── Structured Output Schemas ───────────────────────────────────────────────
# Passed to the Anthropic API as output_config.format to guarantee valid JSON
# responses matching our Pydantic models. No more parse failures.

DISPATCHER_OUTPUT_CONFIG = {
    "format": {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["greeting", "info_request", "pushback", "accept", "walk_away"],
                },
                "slot_requested": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                },
                "tactics_used": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "reasoning": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["type", "slot_requested", "tactics_used", "reasoning", "message"],
            "additionalProperties": False,
        },
    },
}

WAREHOUSE_OUTPUT_CONFIG = {
    "format": {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "slot_offered": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                },
                "slot_withdrawn": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                },
                "cue_dropped": {
                    "anyOf": [
                        {
                            "type": "string",
                            "enum": [
                                "staffing",
                                "schedule_disruption",
                                "reserved_for_regulars",
                                "preference_later",
                            ],
                        },
                        {"type": "null"},
                    ],
                },
                "drop_and_hook_response": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                },
                "rescheduling_fee_accepted": {
                    "anyOf": [{"type": "boolean"}, {"type": "null"}],
                },
                "message": {"type": "string"},
            },
            "required": [
                "slot_offered",
                "slot_withdrawn",
                "cue_dropped",
                "drop_and_hook_response",
                "rescheduling_fee_accepted",
                "message",
            ],
            "additionalProperties": False,
        },
    },
}


# ── Custom Exception ─────────────────────────────────────────────────────────


class ConversationError(Exception):
    """Raised when a conversation fails irrecoverably.

    Carries partial state for failure logging by the runner.

    Attributes:
        turn_log: Partial turn log up to the failure point.
        raw_responses: Raw API response texts that failed parsing.
    """

    def __init__(self, message, turn_log=None, raw_responses=None):
        super().__init__(message)
        self.turn_log = turn_log or []
        self.raw_responses = raw_responses or []


# ── Private Helpers ──────────────────────────────────────────────────────────


def _extract_text(response):
    """Get first TextBlock text from response.content.

    Args:
        response: Anthropic API response object.

    Returns:
        Text string from the first TextBlock.

    Raises:
        ConversationError: If no TextBlock found in response.
    """
    for block in response.content:
        if block.type == "text":
            return block.text
    raise ConversationError(
        f"No text block in response (stop_reason={response.stop_reason})"
    )


def _call_api(client, *, model, max_tokens, temperature, system, messages, tools=None, output_config=None):
    """Call the Anthropic API with exponential backoff retry.

    Retries on APIConnectionError, RateLimitError, and 5xx APIError.
    4xx errors are raised immediately (not retried).

    Args:
        client: Anthropic client instance.
        model: Model ID string.
        max_tokens: Maximum response tokens.
        temperature: Sampling temperature.
        system: System prompt string.
        messages: Conversation messages list.
        tools: Optional tool definitions list.
        output_config: Optional structured output config for guaranteed JSON schema compliance.

    Returns:
        Anthropic API response object.

    Raises:
        APIConnectionError: After all retries exhausted for connection errors.
        RateLimitError: After all retries exhausted for rate limit errors.
        APIError: For 4xx errors (immediate) or 5xx after all retries.
    """
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    if output_config:
        kwargs["output_config"] = output_config

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return client.messages.create(**kwargs)
        except (APIConnectionError, RateLimitError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "API retry %d/%d after %s: %s", attempt + 1, MAX_RETRIES, type(e).__name__, e
                )
                time.sleep(wait)
            else:
                raise
        except APIError as e:
            if e.status_code and e.status_code >= 500:
                last_error = e
                if attempt < MAX_RETRIES:
                    wait = 2 ** attempt
                    logger.warning(
                        "API retry %d/%d after 5xx: %s", attempt + 1, MAX_RETRIES, e
                    )
                    time.sleep(wait)
                else:
                    raise
            else:
                raise  # 4xx — not retryable


def _handle_tool_calls(client, response, scenario, dispatcher_prompt, dispatcher_messages):
    """Loop while stop_reason == 'tool_use'. Returns (final_response, tool_calls_log).

    Appends tool_use assistant messages and tool_result user messages to
    dispatcher_messages (mutates in place). These are only in dispatcher history;
    the warehouse never sees them.

    Args:
        client: Anthropic client instance.
        response: Initial API response with tool_use stop_reason.
        scenario: Scenario config dict.
        dispatcher_prompt: Dispatcher system prompt string.
        dispatcher_messages: Dispatcher message history (mutated in place).

    Returns:
        Tuple of (final_response, tool_calls_log) where tool_calls_log is a list
        of dicts with tool name, input, and output.
    """
    tool_calls_log = []

    while response.stop_reason == "tool_use":
        # Append the assistant's response (contains ToolUseBlock(s) + possibly TextBlock)
        dispatcher_messages.append({"role": "assistant", "content": response.content})

        # Process each tool use block
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_input = block.input
                result = calculate_slot_cost(
                    tool_input.get("slot_time", ""),
                    tool_input.get("drop_and_hook", False),
                    scenario,
                )
                tool_calls_log.append({
                    "tool": "calculate_slot_cost",
                    "input": tool_input,
                    "output": result,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
                logger.info(
                    "Tool: calculate_slot_cost(%s, dh=%s) → total=%s",
                    tool_input.get("slot_time", "?"),
                    tool_input.get("drop_and_hook", False),
                    result.get("total_cost", result.get("error", "?")),
                )

        # Send tool results back
        dispatcher_messages.append({"role": "user", "content": tool_results})

        # Get next response
        response = _call_api(
            client,
            model=DISPATCHER_MODEL,
            max_tokens=DISPATCHER_MAX_TOKENS,
            temperature=DISPATCHER_TEMPERATURE,
            system=dispatcher_prompt,
            messages=dispatcher_messages,
            tools=[TOOL_DEFINITION],
            output_config=DISPATCHER_OUTPUT_CONFIG,
        )

    return response, tool_calls_log


def _do_dispatcher_turn(client, scenario, dispatcher_prompt, dispatcher_messages, turn_log):
    """Handle a full dispatcher turn: tool calls + parse retry + rollback.

    On ParseError, rolls back any messages appended during the failed attempt
    (tool_use/tool_result pairs) and retries the full API call.

    Args:
        client: Anthropic client instance.
        scenario: Scenario config dict.
        dispatcher_prompt: Dispatcher system prompt string.
        dispatcher_messages: Dispatcher message history (mutated in place).
        turn_log: Turn log list (for ConversationError context).

    Returns:
        Tuple of (DispatcherMetadata, tool_calls_log).

    Raises:
        ConversationError: If all parse retries are exhausted.
    """
    raw_responses = []

    for attempt in range(MAX_RETRIES + 1):
        # Snapshot for rollback
        snapshot = len(dispatcher_messages)

        try:
            response = _call_api(
                client,
                model=DISPATCHER_MODEL,
                max_tokens=DISPATCHER_MAX_TOKENS,
                temperature=DISPATCHER_TEMPERATURE,
                system=dispatcher_prompt,
                messages=dispatcher_messages,
                tools=[TOOL_DEFINITION],
                output_config=DISPATCHER_OUTPUT_CONFIG,
            )

            # Handle tool calls if any
            response, tool_calls_log = _handle_tool_calls(
                client, response, scenario, dispatcher_prompt, dispatcher_messages
            )

            # Extract and parse text
            raw_text = _extract_text(response)
            meta = parse_dispatcher_response(raw_text)
            return meta, tool_calls_log

        except ParseError as e:
            raw_responses.append(e.raw_text)
            logger.warning("Dispatcher raw text: %r", e.raw_text)
            # Rollback any tool_use/tool_result messages added during this attempt
            del dispatcher_messages[snapshot:]
            if attempt < MAX_RETRIES:
                logger.warning(
                    "Dispatcher parse retry %d/%d: %s", attempt + 1, MAX_RETRIES, e
                )
            else:
                raise ConversationError(
                    f"Dispatcher parse failed after {MAX_RETRIES + 1} attempts: {e}",
                    turn_log=turn_log,
                    raw_responses=raw_responses,
                )


def _do_warehouse_turn(client, scenario, warehouse_prompt, warehouse_messages, turn_log):
    """Handle a warehouse turn with parse retry.

    No rollback needed — nothing is appended to warehouse_messages inside this function.

    Args:
        client: Anthropic client instance.
        scenario: Scenario config dict.
        warehouse_prompt: Warehouse system prompt string.
        warehouse_messages: Warehouse message history (not mutated).
        turn_log: Turn log list (for ConversationError context).

    Returns:
        WarehouseMetadata instance.

    Raises:
        ConversationError: If all parse retries are exhausted.
    """
    raw_responses = []

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = _call_api(
                client,
                model=WAREHOUSE_MODEL,
                max_tokens=WAREHOUSE_MAX_TOKENS,
                temperature=WAREHOUSE_TEMPERATURE,
                system=warehouse_prompt,
                messages=warehouse_messages,
                output_config=WAREHOUSE_OUTPUT_CONFIG,
            )

            raw_text = _extract_text(response)
            meta = parse_warehouse_response(raw_text)
            return meta

        except ParseError as e:
            raw_responses.append(e.raw_text)
            if attempt < MAX_RETRIES:
                logger.warning(
                    "Warehouse parse retry %d/%d: %s", attempt + 1, MAX_RETRIES, e
                )
            else:
                raise ConversationError(
                    f"Warehouse parse failed after {MAX_RETRIES + 1} attempts: {e}",
                    turn_log=turn_log,
                    raw_responses=raw_responses,
                )


def _check_termination(dispatcher_meta, pushback_count, turn_number):
    """Check if the conversation should terminate after a dispatcher turn.

    Args:
        dispatcher_meta: Parsed DispatcherMetadata from the current turn.
        pushback_count: Current pushback count (already incremented for this turn).
        turn_number: Current turn number.

    Returns:
        Termination reason string or None to continue.
    """
    if dispatcher_meta.type == "accept":
        return "accept"
    if dispatcher_meta.type == "walk_away":
        return "walk_away"
    if pushback_count > MAX_PUSHBACKS:
        return "pushback_limit"
    return None


def _serialize_messages(messages):
    """Convert message history to JSON-safe list of dicts.

    Anthropic API response content blocks (TextBlock, ToolUseBlock, etc.) are
    not JSON-serializable. This walks the message list and converts any
    non-dict content blocks to plain dicts.

    Args:
        messages: List of message dicts with 'role' and 'content' keys.

    Returns:
        New list with all content blocks converted to plain dicts.
    """
    serialized = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            serialized.append({"role": msg["role"], "content": content})
        elif isinstance(content, list):
            # List of content blocks (could be API objects or dicts)
            blocks = []
            for block in content:
                if isinstance(block, dict):
                    blocks.append(block)
                elif hasattr(block, "model_dump"):
                    # Pydantic model (TextBlock, ToolUseBlock, etc.)
                    blocks.append(block.model_dump())
                elif hasattr(block, "__dict__"):
                    blocks.append(block.__dict__)
                else:
                    blocks.append(str(block))
            serialized.append({"role": msg["role"], "content": blocks})
        else:
            # Single non-list content block
            if hasattr(content, "model_dump"):
                serialized.append({"role": msg["role"], "content": content.model_dump()})
            else:
                serialized.append({"role": msg["role"], "content": str(content)})
    return serialized


def _build_result(scenario_id, turn_log, termination, total_turns, pushback_count,
                  dispatcher_prompt, warehouse_prompt,
                  dispatcher_messages, warehouse_messages):
    """Construct the result dict returned by run_conversation.

    Args:
        scenario_id: Scenario identifier string.
        turn_log: Complete turn log list.
        termination: Termination reason string.
        total_turns: Total number of conversation turns.
        pushback_count: Final pushback count.
        dispatcher_prompt: Dispatcher system prompt string.
        warehouse_prompt: Warehouse system prompt string.
        dispatcher_messages: Dispatcher message history (serialized).
        warehouse_messages: Warehouse message history (serialized).

    Returns:
        Result dict matching the documented return format.
    """
    return {
        "scenario_id": scenario_id,
        "turn_log": turn_log,
        "termination": termination,
        "total_turns": total_turns,
        "pushback_count": pushback_count,
        "dispatcher_prompt": dispatcher_prompt,
        "warehouse_prompt": warehouse_prompt,
        "dispatcher_messages": _serialize_messages(dispatcher_messages),
        "warehouse_messages": _serialize_messages(warehouse_messages),
    }


# ── Main Entry Point ─────────────────────────────────────────────────────────


def run_conversation(scenario, client):
    """Run a single negotiation conversation.

    The dispatcher speaks first. The loop alternates warehouse → dispatcher
    until a termination condition is met.

    Args:
        scenario: Scenario config dict from scenarios.json.
        client: Anthropic client instance (shared across conversations).

    Returns:
        Result dict with scenario_id, turn_log, termination, total_turns,
        and pushback_count.

    Raises:
        ConversationError: On irrecoverable failure (parse retries exhausted).
    """
    scenario_id = scenario["scenario_id"]
    logger.info("Starting conversation: %s", scenario_id)

    # Build prompts
    dispatcher_prompt = build_dispatcher_prompt(scenario)
    warehouse_prompt = build_warehouse_prompt(scenario)

    # Initialize message histories with visibility rules
    # Seed dispatcher with a trigger message (API requires non-empty messages)
    dispatcher_messages = [{"role": "user", "content": "Begin the negotiation."}]
    warehouse_messages = []

    turn_log = []
    pushback_count = 0
    turn_number = 0

    # ── Turn 0: Dispatcher opens ──
    meta, tool_calls_log = _do_dispatcher_turn(
        client, scenario, dispatcher_prompt, dispatcher_messages, turn_log
    )

    # Log the turn
    entry = {
        "agent": "dispatcher",
        "turn": turn_number,
        "metadata": meta.model_dump(exclude={"message"}),
        "message": meta.message,
    }
    if tool_calls_log:
        entry["tool_calls"] = tool_calls_log
    turn_log.append(entry)

    if meta.type == "pushback":
        pushback_count += 1

    logger.info("Turn %d: dispatcher | type=%s", turn_number, meta.type)

    # Update message histories
    # Dispatcher sees own full metadata JSON
    dispatcher_messages.append({"role": "assistant", "content": meta.model_dump_json()})
    # Warehouse sees dispatcher NL only
    warehouse_messages.append({"role": "user", "content": meta.message})

    turn_number += 1

    # Check termination after opening (unlikely but possible)
    termination = _check_termination(meta, pushback_count, turn_number)
    if termination:
        logger.info("Conversation %s ended: %s (turn %d)", scenario_id, termination, turn_number)
        return _build_result(scenario_id, turn_log, termination, turn_number, pushback_count,
                             dispatcher_prompt, warehouse_prompt,
                             dispatcher_messages, warehouse_messages)

    # ── Main loop: warehouse → dispatcher ──
    while turn_number < MAX_TURNS:
        # ── Warehouse turn ──
        wh_meta = _do_warehouse_turn(
            client, scenario, warehouse_prompt, warehouse_messages, turn_log
        )

        entry = {
            "agent": "warehouse",
            "turn": turn_number,
            "metadata": wh_meta.model_dump(exclude={"message"}),
            "message": wh_meta.message,
        }
        turn_log.append(entry)

        logger.info("Turn %d: warehouse | slot_offered=%s", turn_number, wh_meta.slot_offered)

        # Update message histories
        # Warehouse sees own NL only
        warehouse_messages.append({"role": "assistant", "content": wh_meta.message})
        # Dispatcher sees warehouse NL only
        dispatcher_messages.append({"role": "user", "content": wh_meta.message})

        turn_number += 1

        # Check turn limit before making another API call
        if turn_number >= MAX_TURNS:
            termination = "turn_limit"
            logger.info("Conversation %s ended: %s (turn %d)", scenario_id, termination, turn_number)
            return _build_result(scenario_id, turn_log, termination, turn_number, pushback_count,
                                 dispatcher_prompt, warehouse_prompt,
                                 dispatcher_messages, warehouse_messages)

        # ── Dispatcher turn ──
        meta, tool_calls_log = _do_dispatcher_turn(
            client, scenario, dispatcher_prompt, dispatcher_messages, turn_log
        )

        entry = {
            "agent": "dispatcher",
            "turn": turn_number,
            "metadata": meta.model_dump(exclude={"message"}),
            "message": meta.message,
        }
        if tool_calls_log:
            entry["tool_calls"] = tool_calls_log
        turn_log.append(entry)

        if meta.type == "pushback":
            pushback_count += 1

        logger.info("Turn %d: dispatcher | type=%s", turn_number, meta.type)

        # Update message histories
        dispatcher_messages.append({"role": "assistant", "content": meta.model_dump_json()})
        warehouse_messages.append({"role": "user", "content": meta.message})

        turn_number += 1

        # Check termination
        termination = _check_termination(meta, pushback_count, turn_number)
        if termination:
            logger.info("Conversation %s ended: %s (turn %d)", scenario_id, termination, turn_number)
            return _build_result(scenario_id, turn_log, termination, turn_number, pushback_count,
                                 dispatcher_prompt, warehouse_prompt,
                                 dispatcher_messages, warehouse_messages)

    # Should not reach here, but safety net
    termination = "turn_limit"
    logger.info("Conversation %s ended: %s (turn %d)", scenario_id, termination, turn_number)
    return _build_result(scenario_id, turn_log, termination, turn_number, pushback_count,
                         dispatcher_prompt, warehouse_prompt,
                         dispatcher_messages, warehouse_messages)


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

    print("\nCNB Conversation Orchestrator Validation")
    print("=" * 50)

    # ── 1. Import verification ──
    print("\n--- Import checks ---")
    check(callable(run_conversation), "run_conversation is callable")
    check(callable(_extract_text), "_extract_text is callable")
    check(callable(_call_api), "_call_api is callable")
    check(callable(_handle_tool_calls), "_handle_tool_calls is callable")
    check(callable(_do_dispatcher_turn), "_do_dispatcher_turn is callable")
    check(callable(_do_warehouse_turn), "_do_warehouse_turn is callable")
    check(callable(_check_termination), "_check_termination is callable")
    check(callable(_build_result), "_build_result is callable")

    # ── 2. _extract_text with mock objects ──
    print("\n--- _extract_text checks ---")

    class MockBlock:
        def __init__(self, block_type, text=None):
            self.type = block_type
            self.text = text

    class MockResponse:
        def __init__(self, content, stop_reason="end_turn"):
            self.content = content
            self.stop_reason = stop_reason

    # Text block only
    resp = MockResponse([MockBlock("text", "Hello world")])
    check(_extract_text(resp) == "Hello world", "_extract_text with text block")

    # Text + tool_use blocks (text first)
    resp = MockResponse([
        MockBlock("text", "Let me check"),
        MockBlock("tool_use"),
    ])
    check(_extract_text(resp) == "Let me check", "_extract_text with text + tool_use")

    # Tool_use + text blocks (tool first)
    resp = MockResponse([
        MockBlock("tool_use"),
        MockBlock("text", "After tool"),
    ])
    check(_extract_text(resp) == "After tool", "_extract_text with tool_use + text")

    # ── 3. _extract_text raises ConversationError when no text block ──
    resp = MockResponse([MockBlock("tool_use")])
    try:
        _extract_text(resp)
        check(False, "_extract_text should raise on no text block")
    except ConversationError:
        check(True, "_extract_text raises ConversationError on no text block")

    # ── 4. _check_termination ──
    print("\n--- _check_termination checks ---")

    class MockMeta:
        def __init__(self, type_val):
            self.type = type_val

    # accept
    check(
        _check_termination(MockMeta("accept"), 0, 2) == "accept",
        "termination: accept"
    )
    # walk_away
    check(
        _check_termination(MockMeta("walk_away"), 0, 2) == "walk_away",
        "termination: walk_away"
    )
    # pushback within limit (count=5, MAX_PUSHBACKS=5) → no termination
    check(
        _check_termination(MockMeta("pushback"), 5, 2) is None,
        "termination: pushback count=5 (at limit, not over)"
    )
    # pushback over limit (count=6, MAX_PUSHBACKS=5) → pushback_limit
    check(
        _check_termination(MockMeta("pushback"), 6, 2) == "pushback_limit",
        "termination: pushback count=6 (over limit)"
    )
    # greeting → no termination
    check(
        _check_termination(MockMeta("greeting"), 0, 0) is None,
        "termination: greeting → None"
    )
    # info_request → no termination
    check(
        _check_termination(MockMeta("info_request"), 0, 1) is None,
        "termination: info_request → None"
    )

    # ── 5. _build_result format ──
    print("\n--- _build_result checks ---")
    test_disp_msgs = [{"role": "user", "content": "Begin."}]
    test_wh_msgs = [{"role": "user", "content": "Hello"}]
    result = _build_result("SM-1-4-OC-ASYM-NEU", [{"turn": 0}], "accept", 3, 1,
                           "dispatcher system prompt", "warehouse system prompt",
                           test_disp_msgs, test_wh_msgs)
    check(result["scenario_id"] == "SM-1-4-OC-ASYM-NEU", "result: scenario_id")
    check(result["turn_log"] == [{"turn": 0}], "result: turn_log")
    check(result["termination"] == "accept", "result: termination")
    check(result["total_turns"] == 3, "result: total_turns")
    check(result["pushback_count"] == 1, "result: pushback_count")
    check(result["dispatcher_prompt"] == "dispatcher system prompt", "result: dispatcher_prompt")
    check(result["warehouse_prompt"] == "warehouse system prompt", "result: warehouse_prompt")
    check(len(result["dispatcher_messages"]) == 1, "result: dispatcher_messages length")
    check(len(result["warehouse_messages"]) == 1, "result: warehouse_messages length")
    expected_keys = {
        "scenario_id", "turn_log", "termination", "total_turns", "pushback_count",
        "dispatcher_prompt", "warehouse_prompt", "dispatcher_messages", "warehouse_messages",
    }
    check(set(result.keys()) == expected_keys, "result: exact keys")

    # ── 6. Message visibility simulation ──
    print("\n--- Message visibility checks ---")
    # Simulate what run_conversation does with message histories

    dispatcher_messages = [{"role": "user", "content": "Begin the negotiation."}]
    warehouse_messages = []

    # Dispatcher turn: meta with full JSON
    disp_meta_json = json.dumps({
        "type": "greeting",
        "slot_requested": "14:30",
        "tactics_used": [],
        "reasoning": "Opening request.",
        "message": "Hello, I need the 14:30 slot.",
    })
    dispatcher_messages.append({"role": "assistant", "content": disp_meta_json})
    warehouse_messages.append({"role": "user", "content": "Hello, I need the 14:30 slot."})

    # Verify dispatcher sees metadata
    check(
        "reasoning" in dispatcher_messages[-1]["content"],
        "visibility: dispatcher sees own metadata"
    )
    # Verify warehouse sees NL only
    check(
        "reasoning" not in warehouse_messages[-1]["content"],
        "visibility: warehouse does not see dispatcher metadata"
    )
    check(
        warehouse_messages[-1]["content"] == "Hello, I need the 14:30 slot.",
        "visibility: warehouse sees dispatcher NL message"
    )

    # Warehouse turn: NL only in both histories
    wh_message = "We can offer 19:00."
    warehouse_messages.append({"role": "assistant", "content": wh_message})
    dispatcher_messages.append({"role": "user", "content": wh_message})

    # Both sides see warehouse NL
    check(
        dispatcher_messages[-1]["content"] == "We can offer 19:00.",
        "visibility: dispatcher sees warehouse NL"
    )
    check(
        warehouse_messages[-1]["content"] == "We can offer 19:00.",
        "visibility: warehouse sees own NL"
    )

    # ── 7. ConversationError attributes ──
    print("\n--- ConversationError checks ---")
    err = ConversationError("test error")
    check(str(err) == "test error", "ConversationError: message")
    check(err.turn_log == [], "ConversationError: default turn_log empty")
    check(err.raw_responses == [], "ConversationError: default raw_responses empty")

    err = ConversationError("with data", turn_log=[{"t": 1}], raw_responses=["raw1"])
    check(err.turn_log == [{"t": 1}], "ConversationError: turn_log preserved")
    check(err.raw_responses == ["raw1"], "ConversationError: raw_responses preserved")

    # ── Summary ──
    print(f"\n  {passed} checks passed, {failed} failed")

    if failed == 0:
        print("\nAll mock checks passed.")
    else:
        print("\nFix validation errors before proceeding.")

    # ── Live test ──
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

    print("\n" + "=" * 50)
    print("Live test")
    print("=" * 50)

    if True:

        # Load first scenario
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scenarios_path = os.path.join(base_dir, "config", "scenarios.json")
        with open(scenarios_path, "r") as f:
            scenarios = json.load(f)

        scenario = scenarios[0]
        print(f"\nRunning scenario: {scenario['scenario_id']}")

        # Set up logging for live test
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

        client = Anthropic()
        try:
            result = run_conversation(scenario, client)
            print(f"\nResult:")
            print(f"  Termination: {result['termination']}")
            print(f"  Total turns: {result['total_turns']}")
            print(f"  Pushback count: {result['pushback_count']}")
            print(f"  Turn log entries: {len(result['turn_log'])}")

            # Verify result format
            live_counts = {"passed": 0, "failed": 0}

            def live_check(condition, message):
                if condition:
                    live_counts["passed"] += 1
                else:
                    live_counts["failed"] += 1
                    print(f"  LIVE FAIL: {message}")

            live_check(
                result["termination"] in ("accept", "walk_away", "pushback_limit", "turn_limit"),
                f"valid termination: {result['termination']}"
            )
            live_check(len(result["turn_log"]) > 0, "non-empty turn_log")
            live_check(
                result["turn_log"][0]["agent"] == "dispatcher",
                "first turn is dispatcher"
            )
            live_check(
                result["total_turns"] == len(result["turn_log"]),
                f"total_turns ({result['total_turns']}) matches turn_log length ({len(result['turn_log'])})"
            )

            # Print conversation excerpt
            print(f"\n--- Conversation excerpt ---")
            for entry in result["turn_log"]:
                agent = entry["agent"].upper()[:4]
                msg = entry["message"][:80]
                extra = ""
                if entry["agent"] == "dispatcher":
                    extra = f" [{entry['metadata']['type']}]"
                if entry.get("tool_calls"):
                    extra += f" [+{len(entry['tool_calls'])} tool calls]"
                print(f"  Turn {entry['turn']} ({agent}){extra}: {msg}...")

            print(f"\n  {live_counts['passed']} live checks passed, {live_counts['failed']} failed")

        except ConversationError as e:
            print(f"\nConversationError: {e}")
            print(f"  Partial turn_log entries: {len(e.turn_log)}")
            print(f"  Raw responses ({len(e.raw_responses)}):")
            for i, raw in enumerate(e.raw_responses):
                print(f"    [{i}]: {repr(raw[:200]) if raw else repr(raw)}")
        except Exception as e:
            print(f"\nUnexpected error: {type(e).__name__}: {e}")
    print("\nDone. Ready for checkpoint review.\n")

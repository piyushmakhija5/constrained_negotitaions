"""CNB Constants & Fixed Parameters

Single source of truth for all fixed values used across the experiment.
Every module imports from here — never hardcode these values elsewhere.
"""

# ── Warehouse Dock Slots ───────────────────────────────────────────────────────
# Physical reality of the warehouse schedule. Same for all 288 scenarios.
# What changes across personas is willingness to assign, not availability.

ALL_SLOTS = ["13:00", "13:30", "14:30", "16:00", "17:00", "19:00", "19:30", "20:00"]

# ── Time Reference ─────────────────────────────────────────────────────────────

ORIGINAL_APPOINTMENT = 12 * 60  # 12:00 in minutes since midnight
ORIGINAL_APPOINTMENT_STR = "12:00"

# ── Scenario Axes ──────────────────────────────────────────────────────────────

DELAYS = [
    {"hours": 1, "level": "small", "code": "SM"},
    {"hours": 2, "level": "medium", "code": "MD"},
    {"hours": 4, "level": "large", "code": "LG"},
]

MABD_WINDOWS = [1, 2]   # hours from original appointment
HOS_REMAINING = [4, 7]  # hours from original appointment
PERSONAS = ["OC", "FR", "GK", "CD"]
INFO_CONDITIONS = [("asymmetric", "ASYM"), ("transparent", "TRANS")]
DAY_CONTEXTS = [("neutral", "NEU"), ("positive", "POS"), ("negative", "NEG")]

# ── Shipment ───────────────────────────────────────────────────────────────────

SHIPMENT_VALUE = 500000
RETAILER_NAME = "Target"

# ── Penalties & Costs ──────────────────────────────────────────────────────────

OTIF_PENALTY = 10000               # 2% of shipment value, binary cliff
DETENTION_FREE_MINUTES = 60        # Free waiting time after truck arrival
DETENTION_RATE_PER_HOUR = 100      # $/hour after free time, rounded up
UNLOAD_TIME_MINUTES = 60           # Included in HOS calc, eliminated by D&H
RESCHEDULING_FEE = 100             # Optional costly signal from dispatcher

# ── Conversation Limits ────────────────────────────────────────────────────────

MAX_PUSHBACKS = 5                  # Dispatcher pushback limit
MAX_TURNS = 20                     # Hard safety limit on total turns
MAX_RETRIES = 3                    # Retries for metadata validation failures

# ── Models ─────────────────────────────────────────────────────────────────────

DISPATCHER_MODEL = "claude-sonnet-4-6"
DISPATCHER_TEMPERATURE = 0.7
DISPATCHER_MAX_TOKENS = 1024

WAREHOUSE_MODEL = "claude-sonnet-4-6"
WAREHOUSE_TEMPERATURE = 0
WAREHOUSE_MAX_TOKENS = 1024

TOOL_MODEL = "claude-haiku-4-5"  # For any future LLM-based tool processing

# ── Time Utilities ─────────────────────────────────────────────────────────────
# Shared across all modules that work with HH:MM time strings.


def parse_time(time_str: str) -> int:
    """Parse 'HH:MM' to minutes since midnight."""
    h, m = time_str.split(":")
    return int(h) * 60 + int(m)


def format_time(minutes: int) -> str:
    """Format minutes since midnight to 'HH:MM'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"

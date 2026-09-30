"""Tk-free helpers behind the desktop GUI, kept apart from grave_ui.py so they can be tested."""

# Weakest to strongest: "Ponovi byhand/" never steps down this list.
MODELS = [
    "claude-sonnet-5",
    "claude-sonnet-5-5",
    "claude-opus-5",
    "claude-opus-5-5",
    "claude-fable-5",
    "claude-fable-5-1",
]
MODEL_LABELS = {
    "claude-sonnet-5": "Claude Sonnet 5 (najjeftiniji)",
    "claude-sonnet-5-5": "Claude Sonnet 5.5",
    "claude-opus-5": "Claude Opus 5",
    "claude-opus-5-5": "Claude Opus 5.5",
    "claude-fable-5": "Claude Fable 5",
    "claude-fable-5-1": "Claude Fable 5.1 (najjači)",
}
DEFAULT_MODEL = "claude-sonnet-5"
RETRY_MODEL = "claude-opus-5-5"

EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
EFFORT_LABELS = {
    "low": "nizak",
    "medium": "srednji",
    "high": "visok",
    "xhigh": "vrlo visok",
    "max": "maksimalan",
}
DEFAULT_EFFORT = "high"
RETRY_EFFORT = "high"
# Every offered model takes all five levels; the table stays per-model so a future model
# with a narrower range only needs an entry here.
EFFORT_BY_MODEL = {model: list(EFFORT_LEVELS) for model in MODELS}


def model_id(label: str) -> str:
    """The model id behind a dropdown label (an id passes through unchanged)."""
    return next((m for m, text in MODEL_LABELS.items() if text == label), label)


def effort_id(label: str) -> str:
    """The effort level behind a dropdown label (a level passes through unchanged)."""
    return next((e for e, text in EFFORT_LABELS.items() if text == label), label)

"""Pure helpers for displaying the model actually used by a response."""

def actual_model_label(message: dict, fallback: str | None = None) -> str | None:
    value = message.get("model") if isinstance(message, dict) else None
    return value or fallback

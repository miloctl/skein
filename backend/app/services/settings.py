"""Operator-set deployment settings held in app_settings.

Only the long-chat strategy lives here today. It is a TEAM setting rather than
a personal one on purpose: it changes what every chat costs and how much of a
long conversation survives, which is an operator decision, not a preference.

The stored value overrides SKEIN_CONTEXT_STRATEGY. Env stays the default so a
fresh deployment behaves per its .env, and clearing the setting returns to it
rather than to a hardcoded guess.
"""

from .. import config, db

CONTEXT_STRATEGY = "context_strategy"


def _validate_strategy(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""  # the clear sentinel — fall back to the env default
    if value not in config.CONTEXT_STRATEGIES:
        raise ValueError(
            f"unknown strategy {value!r} — expected one of: {', '.join(config.CONTEXT_STRATEGIES)}"
        )
    return value


def set_context_strategy(strategy: str, *, actor: str) -> dict:
    """Empty clears the override and returns the deployment to its env value."""
    strategy = _validate_strategy(strategy)
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (CONTEXT_STRATEGY, strategy, db.now()),
    )
    db.log_activity(
        actor,
        "set_context_strategy",
        f"long-chat strategy set to {strategy or 'the deployment default'}",
    )
    return {"strategy": effective_context_strategy(), "override": strategy}


def context_strategy_override() -> str:
    row = db.query_one("SELECT value FROM app_settings WHERE key = ?", (CONTEXT_STRATEGY,))
    stored = (row["value"] if row else "") or ""
    # a value that stopped being valid (a strategy retired between releases)
    # must not silently pick something else — ignore it and use the env default
    return stored if stored in config.CONTEXT_STRATEGIES else ""


def effective_context_strategy() -> str:
    return context_strategy_override() or config.CONTEXT_STRATEGY

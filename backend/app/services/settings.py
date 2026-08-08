"""Operator-set deployment settings held in app_settings.

The long-chat strategy and the model pick live here. Both are TEAM settings
rather than personal ones on purpose: they change what every chat costs,
which is an operator decision, not a preference.

The stored value overrides the env default. Env stays the default so a fresh
deployment behaves per its .env, and clearing the setting returns to it
rather than to a hardcoded guess.

Registry content (config.MODELS: prices, params) is NEVER copied into
app_settings — that table travels in every export and backup
(services/admin.py::TABLES), and params values are a plausible place an
operator put a credential. Only the pick itself is stored.
"""

import json

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


MODEL_PICK = "model_pick"


def set_model_pick(model_id: str, *, actor: str) -> dict:
    """Empty clears the pick and returns the deployment to SKEIN_MODEL_ID.

    Refused rather than hidden: the picker UI disappears on mock and on a
    faulted registry, but hiding a form is not enforcement — this check is.
    """
    model_id = (model_id or "").strip()
    if model_id:
        if config.EFFECTIVE_PROVIDER == "mock":
            raise ValueError(
                "the mock provider runs no real model — configure a model provider first"
            )
        if config.MODELS_ERROR:
            raise ValueError("SKEIN_MODELS is unusable — fix the registry first (/health says why)")
        if not config.MODELS:
            raise ValueError("no model menu is configured — set SKEIN_MODELS")
        if model_id not in config.MODELS:
            # never echo the submitted id back — list the menu instead
            raise ValueError(f"unknown model — expected one of: {', '.join(sorted(config.MODELS))}")
        # provider recorded WITH the pick: a later provider switch must
        # invalidate it (the id means nothing on another endpoint), and the
        # read side can only tell if the write side says which provider the
        # admin was looking at
        value = json.dumps(
            {"provider": config.EFFECTIVE_PROVIDER, "model_id": model_id, "set_by": actor}
        )
    else:
        value = ""  # the clear sentinel, matching the context strategy above
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (MODEL_PICK, value, db.now()),
    )
    db.log_activity(
        actor,
        "set_model_pick",
        f"team model set to {model_id}" if model_id else "team model returned to the env default",
    )
    return model_pick_state()


def _stored_pick() -> dict | None:
    """The raw stored pick with its timestamp, or None. Validity against the
    CURRENT provider and registry is the reader's problem — see
    model_pick_state, which is what keeps an invalid pick visible instead of
    silently absent."""
    row = db.query_one("SELECT value, updated_at FROM app_settings WHERE key = ?", (MODEL_PICK,))
    if not row or not row["value"]:
        return None
    try:
        got = json.loads(row["value"])
    except ValueError:
        return None
    if not isinstance(got, dict) or not isinstance(got.get("model_id"), str):
        return None
    return {
        "provider": got.get("provider", ""),
        "model_id": got["model_id"],
        "set_by": got.get("set_by", ""),
        "updated_at": row["updated_at"],
    }


def model_pick_state() -> dict:
    """The pick as the GET and the Settings section render it: the stored
    override WITH the reason it is ignored when it is — an override the
    deployment no longer honors is reported, never hidden (the tuning.py
    `ignored` rule)."""
    stored = _stored_pick()
    ignored = ""
    if stored:
        if stored["provider"] != config.EFFECTIVE_PROVIDER:
            ignored = "the model provider changed after this pick was made"
        elif stored["model_id"] not in config.MODELS:
            ignored = "the picked model is no longer in the menu"
    effective = stored["model_id"] if stored and not ignored else config.MODEL_ID
    return {
        "model": effective if config.EFFECTIVE_PROVIDER != "mock" else "",
        "override": stored,
        "ignored": ignored,
        "default": config.MODEL_ID,
    }


def picked_model() -> str:
    """The model id the pick puts in force right now, or empty for the env
    default. Read per agent build (agents/team_agent.py) so an
    administrator's change applies to the next message, not the next restart.

    During a rolling deploy two replicas can briefly hold different
    registries (env) against this one shared row: the replica whose registry
    lacks the id falls back to the env default, the other applies the pick.
    Transient and safe — both honor ignored-never-guessed — but it is the one
    new interaction between an env-resident menu and a DB-resident pick.
    """
    stored = _stored_pick()
    if (
        not stored
        or stored["provider"] != config.EFFECTIVE_PROVIDER
        or stored["model_id"] not in config.MODELS
    ):
        return ""
    return stored["model_id"]

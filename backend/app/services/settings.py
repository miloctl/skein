"""Operator-set deployment settings held in app_settings.

The long-chat strategy and the model pick live here. Both are TEAM settings
rather than personal ones on purpose: they change what every chat costs,
which is an operator decision, not a preference.

The stored value overrides the env default. Env stays the default so a fresh
deployment behaves per its .env, and clearing the setting returns to it
rather than to a hardcoded guess.

Registry content (config.MODELS: prices, params) is NEVER copied into
app_settings. The table travels in every database backup, and params values
are a plausible place an operator put a credential. Only the pick itself is
stored.
"""

import json

from .. import config, db
from . import usage

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
        # full sentences: the Settings section renders these verbatim
        if stored["provider"] != config.EFFECTIVE_PROVIDER:
            ignored = "The model provider changed after this pick was made."
        elif stored["model_id"] not in config.MODELS:
            ignored = "The picked model is no longer in the menu."
    effective = stored["model_id"] if stored and not ignored else config.MODEL_ID
    from_pick = bool(stored) and not ignored
    return {
        "model": effective if config.EFFECTIVE_PROVIDER != "mock" else "",
        "override": stored,
        "ignored": ignored,
        "default": config.MODEL_ID,
        # where the reported value came from, computed HERE beside the value
        # itself so /api/health and this GET can never disagree about it. An
        # ignored override is "env": the deployment is running the env
        # default, whatever the table still holds.
        "origin": "admin" if from_pick else "env",
    }


def model_configuration_summary(pick: dict | None = None) -> dict:
    """Safe team-default model state for Settings and `skein model`.

    Values from free-form params never leave this service. They can carry a
    credential, URL, or path, so only their count and source are reported.
    """
    pick = pick or model_pick_state()
    model_id = pick["model"]
    entry = config.MODELS.get(model_id) or {}
    entry_params, _ = config.sanitize_model_params(entry.get("params", {}))
    global_params, _ = config.sanitize_model_params(config.MODEL_PARAMS)
    provider = config.PROVIDERS.get(config.EFFECTIVE_PROVIDER, config.PROVIDERS["mock"])
    cap_keys = provider["output_cap_params"]
    shadowed_global = set()
    if provider["typed_output_cap"] and entry.get("max_tokens") is not None:
        shadowed_global.update(cap_keys)
    if provider["params_as_model_config"] and entry.get("context_tokens") is not None:
        shadowed_global.add("context_window_limit")
    effective_global = {
        key: value for key, value in global_params.items() if key not in shadowed_global
    }
    merged_params = {**effective_global, **entry_params}

    def document_source(source: str) -> str:
        return "inline_env" if source == "inline" else source

    global_survives = any(key not in entry_params for key in effective_global)
    param_sources = []
    if global_survives and config.MODEL_PARAMS_SOURCE != "unset":
        param_sources.append(document_source(config.MODEL_PARAMS_SOURCE))
    if entry_params:
        param_sources.append("model_menu")
    cap_sources = []
    for key in cap_keys:
        if key in entry_params and "model_menu" not in cap_sources:
            cap_sources.append("model_menu")
        elif key in effective_global:
            source = document_source(config.MODEL_PARAMS_SOURCE)
            if source not in cap_sources:
                cap_sources.append(source)
    if config.EFFECTIVE_PROVIDER == "mock":
        cap_state, cap_value, cap_reason, cap_sources = "inactive", None, "provider_inactive", []
    elif cap_sources:
        cap_state, cap_value, cap_reason = "indeterminate", None, "free_form_parameters"
    elif not provider["typed_output_cap"]:
        cap_state, cap_value, cap_reason, cap_sources = (
            "indeterminate",
            None,
            "provider_managed",
            ["provider"],
        )
    elif entry.get("max_tokens") is not None:
        cap_state, cap_value, cap_reason, cap_sources = (
            "known",
            entry["max_tokens"],
            None,
            ["model_menu"],
        )
    else:
        cap_state, cap_value, cap_reason, cap_sources = (
            "known",
            config.MAX_TOKENS,
            None,
            [config.MAX_TOKENS_SOURCE],
        )

    direct = list(config.attachment_support(model_id)) if model_id else []
    attachment_source = (
        "model_menu" if entry and entry.get("attachments") is not None else "provider"
    )
    if "image" in direct:
        image_mode = "direct"
    elif config.VISION_MODEL and config.EFFECTIVE_PROVIDER != "mock":
        image_mode = "vision_sidecar"
    else:
        image_mode = "unavailable"

    strategy_override = context_strategy_override()
    strategy_source = "admin" if strategy_override else config.CONTEXT_STRATEGY_SOURCE
    price, price_source = usage.model_price(model_id)
    price_fault = bool(
        price is None and config.MODEL_PRICE_TABLE_ERROR and config.MODEL_PRICES_SOURCE != "unset"
    )
    if price_fault:
        price_source = config.MODEL_PRICES_SOURCE
    price_source = document_source(price_source)
    model_source = pick["origin"]
    if model_source == "env":
        model_source = config.MODEL_ID_SOURCE
    menu_sources = (
        [] if config.MODELS_SOURCE == "unset" else [document_source(config.MODELS_SOURCE)]
    )
    model_active = config.EFFECTIVE_PROVIDER != "mock"
    sidecar_active = image_mode == "vision_sidecar"

    def source_name(row_id: str, source: str) -> str:
        if source == "admin":
            return (
                "Settings → AI runtime → Long chats (team)"
                if row_id == "long_chat"
                else "Settings → AI runtime → Model (team)"
            )
        fixed = {
            "provider_default": "provider default",
            "default": "built-in default",
            "fallback": "safe fallback",
            "provider": "provider capability",
            "model_menu": "selected model entry",
            "both": "both forms (fault)",
        }
        if source in fixed:
            return fixed[source]
        if source == "env":
            return {
                "provider": "SKEIN_MODEL_PROVIDER",
                "model": "SKEIN_MODEL_ID",
                "output_cap": "SKEIN_MAX_TOKENS",
                "attachments": "SKEIN_VISION_MODEL",
                "vision_sidecar": "SKEIN_VISION_MODEL",
                "long_chat": "SKEIN_CONTEXT_STRATEGY",
            }.get(row_id, "environment")
        if source == "inline_env":
            return {
                "output_cap": "SKEIN_MODEL_PARAMS",
                "model_menu": "SKEIN_MODELS",
                "prices": "SKEIN_MODEL_PRICES",
                "parameters": "SKEIN_MODEL_PARAMS",
            }.get(row_id, "environment")
        if source == "file":
            return {
                "output_cap": "SKEIN_MODEL_PARAMS_FILE",
                "model_menu": "SKEIN_MODELS_FILE",
                "prices": "SKEIN_MODEL_PRICES_FILE",
                "parameters": "SKEIN_MODEL_PARAMS_FILE",
            }.get(row_id, "_FILE setting")
        return source

    def row(row_id: str, label: str, value: str, sources: list[str]):
        return {
            "id": row_id,
            "label": label,
            "value": value,
            "source": " + ".join(source_name(row_id, source) for source in sources),
        }

    if cap_state == "inactive":
        cap_text = "Not in use"
    elif cap_state == "known":
        cap_text = f"{cap_value:,} tokens"
    elif cap_reason == "free_form_parameters":
        cap_text = "Set in parameters (value hidden)"
    else:
        cap_text = "Managed through provider parameters"
    direct_text = ", ".join(direct) or "none"
    image_text = {
        "direct": "direct",
        "vision_sidecar": "vision sidecar",
        "unavailable": "unavailable",
    }[image_mode]
    if not config.VISION_MODEL:
        vision_text = "Not set"
    elif sidecar_active:
        vision_text = config.VISION_MODEL
    else:
        vision_text = f"{config.VISION_MODEL} (not used for team default)"
    menu_count = len(config.MODELS)
    param_count = len(merged_params)
    if not model_active:
        price_text = "Not in use"
    elif price_fault:
        price_text = "Configuration error"
    else:
        price_text = "Set for team-default model" if price is not None else "Not set"
    return {
        "scope": "team_default",
        "note": "This is the team default. Persona overrides can use a different model or parameters.",
        "rows": [
            row(
                "provider",
                "Provider",
                f"{config.EFFECTIVE_PROVIDER}{' (safe fallback)' if config.MODEL_PROVIDER_ERROR else ''}",
                ["fallback"] if config.MODEL_PROVIDER_ERROR else [config.MODEL_PROVIDER_SOURCE],
            ),
            row(
                "model",
                "Team-default model",
                model_id if model_active else "Not in use",
                [model_source] if model_active else [],
            ),
            row("output_cap", "Output cap", cap_text, cap_sources),
            row(
                "attachments",
                "Attachments",
                f"Direct: {direct_text}. Images: {image_text}.",
                [attachment_source] + (["env"] if sidecar_active else []),
            ),
            row(
                "vision_sidecar",
                "Vision sidecar",
                vision_text,
                ["env"] if config.VISION_MODEL else [],
            ),
            row(
                "long_chat",
                "Long chats",
                f"{strategy_override or config.CONTEXT_STRATEGY}{' (not in use)' if not model_active else ''}",
                [strategy_source],
            ),
            row(
                "model_menu",
                "Model menu",
                f"{menu_count} {'model' if menu_count == 1 else 'models'}",
                menu_sources,
            ),
            row(
                "prices",
                "Prices",
                price_text,
                [] if price_source == "unset" else [price_source],
            ),
            row(
                "parameters",
                "Parameters",
                f"{param_count} {'parameter' if param_count == 1 else 'parameters'}"
                f"{' (not in use)' if not model_active else ''}",
                param_sources,
            ),
        ],
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

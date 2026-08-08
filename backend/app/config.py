"""Central configuration, loaded from environment / .env."""

import json
import math
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("SKEIN_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Deployment overlays: extra playbooks/personas loaded ALONGSIDE the stock
# directories, so deployment-specific content lives in its own repo instead
# of a fork. An overlay file with the same slug replaces the stock file.
# Empty = no overlay.
_playbooks_overlay = os.getenv("SKEIN_PLAYBOOKS_DIR", "")
PLAYBOOKS_OVERLAY: Path | None = Path(_playbooks_overlay) if _playbooks_overlay else None
_personas_overlay = os.getenv("SKEIN_PERSONAS_DIR", "")
PERSONAS_OVERLAY: Path | None = Path(_personas_overlay) if _personas_overlay else None
_flocks_overlay = os.getenv("SKEIN_FLOCKS_DIR", "")
FLOCKS_OVERLAY: Path | None = Path(_flocks_overlay) if _flocks_overlay else None


def overlay_errors() -> list[str]:
    """A configured overlay dir that does not exist is ignored by the loaders
    (the deployment keeps working on stock content) — but silently, which is
    how an unmounted volume masquerades as a working overlay. /health carries
    this, on the MODEL_PROVIDER_ERROR precedent: degrade AND say so.
    Computed live so mounting the directory clears the error without a
    restart."""
    out = []
    for label, overlay in (
        ("SKEIN_PLAYBOOKS_DIR", PLAYBOOKS_OVERLAY),
        ("SKEIN_PERSONAS_DIR", PERSONAS_OVERLAY),
        ("SKEIN_FLOCKS_DIR", FLOCKS_OVERLAY),
    ):
        if overlay and not overlay.is_dir():
            out.append(
                f"{label} is set to {overlay}, which is not a directory. Mount the directory or clear the variable."
            )
    return out


DB_PATH = DATA_DIR / "platform.db"
# Author-private records (1:1 prep, feedback journal) live in a SEPARATE
# database file that backup/export/FTS/MCP/agents never open. Excluded from
# the daily backup chain by design — back it up manually and encrypted if at
# all (it is small and reconstructible personal notes).
PRIVATE_DB_PATH = Path(os.getenv("SKEIN_PRIVATE_DB", DATA_DIR / "private.db"))
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Model settings. The registry is the single list of providers Skein knows;
# nothing outside agents/team_agent.py may branch on a provider name.
#   default_model    — used when SKEIN_MODEL_ID is unset. None = operator must
#                      say, because the server decides what it serves.
#   base_url         — "required" | "forbidden". Forbidden matters: without it
#                      a leftover SKEIN_MODEL_BASE_URL from an experiment would
#                      silently redirect a paid provider's traffic.
#   key_env          — provider-native credential to fall back on. Empty means
#                      NO ambient key is ever read: either none is needed
#                      (mock, bedrock's AWS chain) or sending one would be a
#                      leak (openai_compatible — see below).
#   key_required     — the provider cannot answer at all without a key, so a
#                      missing one is a config fault that degrades to mock at
#                      boot. FALSE wherever keyless is a real deployment:
#                      ollama and openai_compatible both serve local endpoints
#                      that take no credential, and marking them required
#                      would degrade a working keyless box to mock. Bedrock
#                      resolves the ambient AWS chain, which is not readable
#                      from here.
PROVIDERS: dict[str, dict] = {
    "mock": {
        "default_model": "mock",
        "base_url": "forbidden",
        "key_env": "",
        "key_required": False,
    },
    "anthropic": {
        "default_model": "claude-opus-4-8",
        "base_url": "forbidden",
        "key_env": "ANTHROPIC_API_KEY",
        "key_required": True,
    },
    "openai": {
        "default_model": "gpt-5",
        "base_url": "forbidden",
        "key_env": "OPENAI_API_KEY",
        "key_required": True,
    },
    # Anything speaking the OpenAI wire format: vLLM, LM Studio, llama.cpp,
    # OpenRouter, Together, Groq, Azure OpenAI, LiteLLM Proxy.
    #
    # key_env is deliberately EMPTY. Falling back to OPENAI_API_KEY here would
    # hand a paid OpenAI credential to whatever third-party host the operator
    # named — and OPENAI_API_KEY is already set on any box using semantic
    # search. Credentials for a non-OpenAI endpoint must be stated explicitly
    # in SKEIN_MODEL_API_KEY.
    "openai_compatible": {
        "default_model": None,
        "base_url": "required",
        "key_env": "",
        "key_required": False,
    },
    # OLLAMA_API_KEY is for Ollama's hosted cloud models. A local ollama takes
    # no credential, so this must stay optional or every keyless local box
    # degrades to mock at boot.
    "ollama": {
        "default_model": "gpt-oss:120b-cloud",
        "base_url": "forbidden",
        "key_env": "OLLAMA_API_KEY",
        "key_required": False,
    },
    # boto3 is already a strands core dep; credentials come from the ambient
    # AWS chain (instance role, AWS_PROFILE), so there is no key to set.
    # No default model on purpose: Claude-family ids on Bedrock need a
    # geo-prefixed inference profile (us./eu./apac./global.) that depends on
    # the deployment's region, and a bare foundation id is not invocable
    # on-demand. Better to demand SKEIN_MODEL_ID than to ship one that 400s.
    "bedrock": {
        "default_model": None,
        "base_url": "forbidden",
        "key_env": "",
        "key_required": False,
    },
}

MODEL_PROVIDER = os.getenv("SKEIN_MODEL_PROVIDER", "mock").lower()
MODEL_BASE_URL = os.getenv("SKEIN_MODEL_BASE_URL", "")
MODEL_API_KEY = os.getenv("SKEIN_MODEL_API_KEY", "")

# Free-form per-provider knobs (temperature, top_p, max_completion_tokens...),
# merged last so an operator can always reach something we did not model.
MODEL_PARAMS: dict = {}

# Misconfiguration must never take down the deterministic core: config is
# imported by db, every route, seed.py and the CLI, so a bad *model* setting
# raising here would kill the REST API, ICS feed and backups. Record the fault,
# fall back to mock, and let agents/team_agent.py raise at construction time —
# where routes/chat.py already turns it into an SSE error the operator reads.
MODEL_PROVIDER_ERROR = ""

# int() on operator input is exactly the "raises at import" trap the paragraph
# above forbids — SKEIN_MAX_TOKENS= (empty) and =4k both throw.
try:
    MAX_TOKENS = int(os.getenv("SKEIN_MAX_TOKENS", "").strip() or 4096)
except ValueError:
    MAX_TOKENS = 4096
    MODEL_PROVIDER_ERROR = "SKEIN_MAX_TOKENS is not an integer — falling back to 4096"

if MODEL_PROVIDER not in PROVIDERS:
    MODEL_PROVIDER_ERROR = (
        f"unknown SKEIN_MODEL_PROVIDER {MODEL_PROVIDER!r} —"
        f" expected one of: {', '.join(sorted(PROVIDERS))}"
    )
elif PROVIDERS[MODEL_PROVIDER]["base_url"] == "required" and not MODEL_BASE_URL:
    MODEL_PROVIDER_ERROR = f"SKEIN_MODEL_PROVIDER={MODEL_PROVIDER} requires SKEIN_MODEL_BASE_URL"
elif PROVIDERS[MODEL_PROVIDER]["base_url"] == "forbidden" and MODEL_BASE_URL:
    # refusing this is what actually stops a stale base url from redirecting a
    # paid provider's traffic (and its key) to a host the operator forgot about
    MODEL_PROVIDER_ERROR = (
        f"SKEIN_MODEL_PROVIDER={MODEL_PROVIDER} does not accept SKEIN_MODEL_BASE_URL —"
        " use openai_compatible to point at a custom endpoint"
    )

# Guarded on MODEL_PROVIDER_ERROR like the SKEIN_MODEL_PARAMS check below,
# for two reasons: an already-recorded fault (a bad SKEIN_MAX_TOKENS) must not
# be overwritten by this one, and an unknown provider name has no registry
# entry to subscript. Caught here or not at all — unchecked, EFFECTIVE_PROVIDER
# stays on the real provider, /health reports no fault, and the SDK raises per
# request instead, so every chat reply becomes raw provider internals (a 401
# body carrying its request id) shown to the user.
if (
    not MODEL_PROVIDER_ERROR
    and PROVIDERS[MODEL_PROVIDER]["key_required"]
    and not (MODEL_API_KEY or os.getenv(PROVIDERS[MODEL_PROVIDER]["key_env"]))
):
    MODEL_PROVIDER_ERROR = (
        f"SKEIN_MODEL_PROVIDER={MODEL_PROVIDER} needs a key —"
        f" set {PROVIDERS[MODEL_PROVIDER]['key_env']} or SKEIN_MODEL_API_KEY"
    )

if not MODEL_PROVIDER_ERROR and (_raw := os.getenv("SKEIN_MODEL_PARAMS", "").strip()):
    try:
        MODEL_PARAMS = json.loads(_raw)
        if not isinstance(MODEL_PARAMS, dict):
            raise TypeError("not a JSON object")
    except (json.JSONDecodeError, TypeError) as exc:
        MODEL_PARAMS = {}
        MODEL_PROVIDER_ERROR = f"SKEIN_MODEL_PARAMS is not a JSON object: {exc}"


def _finite_price(v) -> bool:
    """A usable price component: a real non-negative number. bool is an int
    in Python, json.loads parses the bare Infinity token, and math.isfinite
    converts to a C double first — so a 309-digit JSON integer raises
    OverflowError, and an uncaught raise here takes down every importer of
    config (the _ctx_num rule, and the exact trap its docstring records)."""
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v) and v >= 0
    except OverflowError:
        return False


# Price table for cost estimates: {"model-id": [usd_per_mtok_in, usd_per_mtok_out]}.
# EMPTY by default, deliberately: a shipped price table goes stale and a stale
# price is a wrong number presented as accounting. A model with no entry gets
# cost NULL — honest, not zero. A bad value degrades and says so; it must
# never take the provider down, because prices are bookkeeping, not routing.
MODEL_PRICES: dict[str, tuple[float, float]] = {}
MODEL_PRICES_ERROR = ""
if _raw_prices := os.getenv("SKEIN_MODEL_PRICES", "").strip():
    try:
        _parsed = json.loads(_raw_prices)
        if not isinstance(_parsed, dict):
            raise TypeError("not a JSON object")
        for _mid, _pair in _parsed.items():
            # _finite_price refuses bool, bare Infinity, and the huge-int
            # OverflowError — any of them would price a model at a number the
            # operator never wrote, or kill the import outright
            if (
                not isinstance(_pair, (list, tuple))
                or len(_pair) != 2
                or not all(_finite_price(x) for x in _pair)
            ):
                raise TypeError(f"{_mid!r} must map to [input_usd_per_mtok, output_usd_per_mtok]")
            MODEL_PRICES[str(_mid)] = (float(_pair[0]), float(_pair[1]))
    except (json.JSONDecodeError, TypeError) as exc:
        MODEL_PRICES = {}
        MODEL_PRICES_ERROR = f"SKEIN_MODEL_PRICES is unusable: {exc}. No costs are estimated."

# Monthly team spend ceiling in USD for the budget findings rule. 0 = off.
try:
    MONTHLY_BUDGET_USD = float(os.getenv("SKEIN_MONTHLY_BUDGET_USD", "").strip() or 0)
    if not math.isfinite(MONTHLY_BUDGET_USD) or MONTHLY_BUDGET_USD < 0:
        raise ValueError
except (ValueError, OverflowError):
    MONTHLY_BUDGET_USD = 0.0
    MODEL_PRICES_ERROR = (MODEL_PRICES_ERROR + " " if MODEL_PRICES_ERROR else "") + (
        "SKEIN_MONTHLY_BUDGET_USD is not a usable number. The budget rule is off."
    )

# ---- model registry (SKEIN_MODELS) ----------------------------------------
# The menu of models an administrator may pick between in Settings, each with
# optional per-model tuning. env-only ON PURPOSE: the operator curates the
# menu, the admin picks from it (services/settings.py) — the same two-tier
# split the tuning.py docstring records for provider and credential settings.
# Registry content must never be persisted to app_settings: that table
# travels in every export and backup (services/admin.py::TABLES), and params
# values are a plausible place an operator put a credential.
#
# Fault discipline extends SKEIN_MODEL_PRICES': ANY invalid entry voids the
# WHOLE list — a partial menu looks complete, which is worse than no menu,
# because an old validator cannot tell a future field from a typo and the
# admin picks from whatever renders. Every fault across every entry is
# collected in one pass (the _ctx_num rule: one at a time makes an operator
# with two typos restart twice). A fault names entry positions, ids, and
# field NAMES only, never field values: this string reaches every signed-in
# user through /api/agents/status and /health.
#
# schemas/skein_models.schema.json is the same contract for ConfigMap
# editors; tests/test_model_registry.py pins the two against each other.
_MODEL_ENTRY_FIELDS = frozenset(
    {"id", "label", "detail", "max_tokens", "context_tokens", "price", "params"}
)
# price carries only keys the accounting multiplies (usage.py::cost_for reads
# input and output). cached_input is deliberately absent until usage_log
# carries cache-read tokens — a price nothing multiplies is a believed number
# not in effect.
_MODEL_PRICE_FIELDS = frozenset({"input", "output"})


def _as_whole(v) -> int | None:
    """The value as an int, or None when it is not a whole number. Accepts a
    zero-fraction float on purpose — see the call sites. inf/nan fail
    is_integer(), and a float too large for int never parses from JSON as a
    float without becoming inf first, so no overflow path exists here."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _model_entry_faults(tag: str, mid: str | None, entry: dict, out: dict[str, dict]) -> list[str]:
    """Faults for one entry, appended to nothing — the caller collects. On a
    clean entry with a usable id the normalized form lands in `out` keyed by
    id. `mid` is None when the caller already faulted the id — field checks
    still run so one restart reports everything, but nothing is stored."""
    faults = []
    if unknown := sorted(set(entry) - _MODEL_ENTRY_FIELDS):
        faults.append(f"{tag} has unknown fields: {', '.join(unknown)}. Remove them.")
    label = entry.get("label")
    if label is not None and not (isinstance(label, str) and 0 < len(label) <= 80):
        faults.append(f"{tag}: label must be a string of 1 to 80 characters.")
    detail = entry.get("detail")
    if detail is not None and not (isinstance(detail, str) and 0 < len(detail) <= 200):
        faults.append(f"{tag}: detail must be a string of 1 to 200 characters.")
    # _as_whole, not isinstance(int): JSON Schema 2020-12 "integer" admits a
    # zero-fraction float (4096.0), so the code must too or the shipped
    # schema approves a registry the deployment refuses. bool is still
    # refused — unchecked, `"max_tokens": true` becomes a 1-token cap the
    # operator never wrote.
    max_tokens = entry.get("max_tokens")
    if max_tokens is not None:
        max_tokens = _as_whole(max_tokens)
        if max_tokens is None or max_tokens < 1:
            max_tokens = None
            faults.append(f"{tag}: max_tokens must be a whole number of 1 or more.")
    context_tokens = entry.get("context_tokens")
    if context_tokens is not None:
        context_tokens = _as_whole(context_tokens)
        if context_tokens is None or context_tokens < 1024:
            context_tokens = None
            faults.append(f"{tag}: context_tokens must be a whole number of 1024 or more.")
    price = entry.get("price")
    pair: tuple[float, float] | None = None
    if price is not None:
        if not isinstance(price, dict):
            faults.append(f"{tag}: price must be a JSON object with input and output.")
        else:
            if unknown := sorted(set(price) - _MODEL_PRICE_FIELDS):
                faults.append(
                    f"{tag}: price has unknown fields: {', '.join(unknown)}. Remove them."
                )
            for k in ("input", "output"):
                if not _finite_price(price.get(k)):
                    faults.append(f"{tag}: price.{k} must be a number of 0 or more.")
            if not faults:
                pair = (float(price["input"]), float(price["output"]))
    params = entry.get("params")
    if params is not None and not isinstance(params, dict):
        faults.append(f"{tag}: params must be a JSON object.")
    if not faults and mid:
        out[mid] = {
            "id": mid,
            "label": (label or mid),
            "detail": detail or "",
            "max_tokens": max_tokens,
            "context_tokens": context_tokens,
            "price": pair,
            "params": params or {},
        }
    return faults


MODELS: dict[str, dict] = {}
MODELS_ERROR = ""
if _raw_models := os.getenv("SKEIN_MODELS", "").strip():
    _model_faults: list[str] = []
    _parsed_models: list | None = None
    try:
        _decoded = json.loads(_raw_models)
        if not isinstance(_decoded, list):
            _model_faults.append("the value is not a JSON array.")
        elif not _decoded:
            _model_faults.append("the array is empty. Remove the variable or add an entry.")
        else:
            _parsed_models = _decoded
    except json.JSONDecodeError as exc:
        # str(exc) carries position only ("line 1 column 5"), never the text
        # around it — safe for a string every signed-in user can read
        _model_faults.append(f"the value is not JSON ({exc}).")
    if _parsed_models is not None:
        for _i, _entry in enumerate(_parsed_models):
            if not isinstance(_entry, dict):
                _model_faults.append(f"entry {_i + 1} is not a JSON object.")
                continue
            _raw_entry_id = _entry.get("id")
            _entry_id: str | None = None
            if not (isinstance(_raw_entry_id, str) and _raw_entry_id.strip()):
                _tag = f"entry {_i + 1}"
                _model_faults.append(f"{_tag} has no usable id.")
            else:
                _entry_id = _raw_entry_id.strip()
                _tag = f"entry {_i + 1} ({_entry_id})"
                if _entry_id in MODELS:
                    _model_faults.append(f"{_tag} repeats an earlier id.")
            _model_faults.extend(_model_entry_faults(_tag, _entry_id, _entry, MODELS))
    if _model_faults:
        MODELS = {}
        MODELS_ERROR = (
            "SKEIN_MODELS is unusable: " + " ".join(_model_faults) + " The model menu is off."
        )

# What the agent layer actually runs. Degrades to mock on any fault above so
# the app boots and every deterministic surface keeps working.
EFFECTIVE_PROVIDER = "mock" if MODEL_PROVIDER_ERROR else MODEL_PROVIDER
_default_model = PROVIDERS[EFFECTIVE_PROVIDER]["default_model"]
MODEL_ID = os.getenv("SKEIN_MODEL_ID", "") or _default_model or ""
if EFFECTIVE_PROVIDER != "mock" and not MODEL_ID:
    MODEL_PROVIDER_ERROR = (
        f"SKEIN_MODEL_PROVIDER={MODEL_PROVIDER} has no default model —"
        " set SKEIN_MODEL_ID to whatever the endpoint serves"
    )
    EFFECTIVE_PROVIDER, MODEL_ID = "mock", "mock"


def menu_warnings() -> list[str]:
    """The env default running outside its own menu — the same drift class as
    a persona model the menu does not list (personas.unlisted_model_warnings),
    reported beside it on /health. Legal on purpose: the menu constrains the
    ADMIN pick, never the operator's env — so this warns, it does not fault.
    A function, not a constant, so the suite's config monkeypatching reads
    through."""
    if MODELS and EFFECTIVE_PROVIDER != "mock" and MODEL_ID and MODEL_ID not in MODELS:
        return ["SKEIN_MODEL_ID is not in the SKEIN_MODELS menu."]
    return []


# Ollama: the default host is the local daemon, which proxies *-cloud models
# to Ollama Cloud when `ollama signin` has been run on the box. To skip the
# daemon and talk to Ollama Cloud directly, set SKEIN_OLLAMA_HOST to
# https://ollama.com and OLLAMA_API_KEY to a key from ollama.com settings.
OLLAMA_HOST = os.getenv("SKEIN_OLLAMA_HOST", "") or "http://localhost:11434"

# ---- optional semantic search --------------------------------------------
# Deliberately its OWN provider setting, not SKEIN_MODEL_PROVIDER: anthropic
# and bedrock have no embeddings endpoint, and vectors PERSIST — following the
# chat provider would mean a chat switch silently invalidates every stored
# vector (cosine across two embedding spaces is noise). All three options
# speak the OpenAI wire shape (/v1/embeddings), so one client covers them.
# Same fault discipline as the chat layer: record the error, never raise, and
# never no-op in silence.
EMBED_PROVIDERS: dict[str, dict] = {
    # default_model None = operator must say (the server decides what it serves)
    # base_url forbidden/required mirrors the chat registry — forbidden on
    # ollama too: its endpoint derives from SKEIN_OLLAMA_HOST alone, and a
    # leftover SKEIN_EMBED_BASE_URL would both mis-route the Ollama Cloud
    # bearer key and double the /v1 suffix into a silently-404ing URL.
    "openai": {
        "default_model": "text-embedding-3-small",
        "base_url": "forbidden",
        "key_env": "OPENAI_API_KEY",
    },
    # never falls back to OPENAI_API_KEY — same leak rule as the chat provider
    "openai_compatible": {"default_model": None, "base_url": "required", "key_env": ""},
    "ollama": {"default_model": None, "base_url": "forbidden", "key_env": "OLLAMA_API_KEY"},
}

EMBEDDINGS_ENABLED = os.getenv("SKEIN_EMBEDDINGS", "0") == "1"
EMBED_PROVIDER = os.getenv("SKEIN_EMBED_PROVIDER", "openai").lower()
EMBED_MODEL = os.getenv("SKEIN_EMBED_MODEL", "")
EMBED_API_KEY = os.getenv("SKEIN_EMBED_API_KEY", "")
_embed_base = os.getenv("SKEIN_EMBED_BASE_URL", "")

EMBEDDINGS_ERROR = ""
EMBED_BASE_URL = ""
if EMBEDDINGS_ENABLED:
    if EMBED_PROVIDER not in EMBED_PROVIDERS:
        EMBEDDINGS_ERROR = (
            f"unknown SKEIN_EMBED_PROVIDER {EMBED_PROVIDER!r} —"
            f" expected one of: {', '.join(sorted(EMBED_PROVIDERS))}"
        )
    else:
        EMBED_MODEL = EMBED_MODEL or EMBED_PROVIDERS[EMBED_PROVIDER]["default_model"] or ""
        _embed_rule = EMBED_PROVIDERS[EMBED_PROVIDER]["base_url"]
        if _embed_rule == "forbidden" and _embed_base:
            EMBEDDINGS_ERROR = (
                f"SKEIN_EMBED_PROVIDER={EMBED_PROVIDER} does not accept SKEIN_EMBED_BASE_URL —"
                " use openai_compatible to point at a custom endpoint"
                " (the ollama endpoint derives from SKEIN_OLLAMA_HOST)"
            )
        elif _embed_rule == "required" and not _embed_base:
            EMBEDDINGS_ERROR = (
                "SKEIN_EMBED_PROVIDER=openai_compatible requires SKEIN_EMBED_BASE_URL"
            )
        elif not EMBED_MODEL:
            EMBEDDINGS_ERROR = (
                f"SKEIN_EMBED_PROVIDER={EMBED_PROVIDER} has no default model —"
                " set SKEIN_EMBED_MODEL to what the endpoint serves"
            )
        elif EMBED_PROVIDER == "openai" and not (EMBED_API_KEY or os.getenv("OPENAI_API_KEY")):
            EMBEDDINGS_ERROR = (
                "SKEIN_EMBEDDINGS=1 with SKEIN_EMBED_PROVIDER=openai needs OPENAI_API_KEY"
                " (or SKEIN_EMBED_API_KEY) — semantic search is off. Keyless option:"
                " SKEIN_EMBED_PROVIDER=ollama with a pulled embedding model."
            )
        if not EMBEDDINGS_ERROR:
            if EMBED_PROVIDER == "ollama":
                EMBED_BASE_URL = OLLAMA_HOST.rstrip("/") + "/v1"
            else:
                EMBED_BASE_URL = _embed_base

# Ready = enabled and correctly configured. The single gate search.py reads.
EMBED_READY = EMBEDDINGS_ENABLED and not EMBEDDINGS_ERROR


def embed_key() -> str:
    """Credential for the embeddings endpoint. Same rules as provider_key():
    the explicit override wins, ambient keys only where the registry names
    one, and openai_compatible never inherits a paid OpenAI key."""
    if EMBED_API_KEY:
        return EMBED_API_KEY
    env = EMBED_PROVIDERS.get(EMBED_PROVIDER, {}).get("key_env", "")
    return os.getenv(env, "") if env else ""


def provider_key() -> str:
    """Credential for the configured provider.

    SKEIN_MODEL_API_KEY always wins; otherwise the provider's own env var, but
    ONLY where the registry names one. A provider with an empty key_env never
    picks up an ambient key — that is what keeps OPENAI_API_KEY from being
    posted to a third-party openai_compatible endpoint.
    """
    if MODEL_API_KEY:
        return MODEL_API_KEY
    env = PROVIDERS.get(EFFECTIVE_PROVIDER, {}).get("key_env", "")
    return os.getenv(env, "") if env else ""


# With SKEIN_AGENT_REVIEW=1, mutating agent writes become pending_changes
# proposals that a human approves in the review inbox (approval-gate mode).
AGENT_REVIEW = os.getenv("SKEIN_AGENT_REVIEW", "0") == "1"

# With SKEIN_TURN_GUARD=1, a chat turn that wrote nothing in answer to a
# capture-prefixed message costs ONE extra model round trip to give the agent a
# chance to file it. Off by default: the guard's honest note is free and needs
# no provider, the re-prompt is neither.
TURN_GUARD = os.getenv("SKEIN_TURN_GUARD", "0") == "1"

# How a long conversation is kept inside the model's context window.
#   sliding   — drop the oldest messages. Free, loses them.
#   summarize — condense the oldest messages into a summary. Costs one extra
#               model call when it fires, keeps the gist.
# Never reaches the mock provider: build_agent returns MockAgent before any
# Strands Agent exists, so there is no conversation manager to configure and
# SKEIN_MODEL_PROVIDER=mock is untouched by every knob below.
CONTEXT_STRATEGIES = ("sliding", "summarize")
CONTEXT_STRATEGY = os.getenv("SKEIN_CONTEXT_STRATEGY", "sliding").strip().lower() or "sliding"
_CONTEXT_FAULTS: list[str] = []

if CONTEXT_STRATEGY not in CONTEXT_STRATEGIES:
    _CONTEXT_FAULTS.append(
        # deliberately does NOT assert which strategy is in use: the Settings
        # toggle overrides the env value, so "Using sliding." would keep
        # claiming that while every chat summarizes. The surfaces report the
        # effective strategy separately.
        f"unknown SKEIN_CONTEXT_STRATEGY {CONTEXT_STRATEGY!r}."
        f" Expected one of: {', '.join(CONTEXT_STRATEGIES)}."
    )
    CONTEXT_STRATEGY = "sliding"


def _ctx_num(name: str, default, cast, low=None, high=None):
    """Operator input parsed the same way MAX_TOKENS is: a bad value degrades
    to the default and says so, rather than raising at import and taking the
    REST API down with it.

    Range-checked HERE rather than left to the SDK. Out of range, the managers
    either raise at construction (a negative window: every chat turn fails
    while /health stays green) or silently clamp (a ratio above 0.8: the
    operator believes a number that is not in effect). Both are the same bug —
    a setting that does not mean what it says — so both are refused up front.

    EVERY fault is collected, not just the first: these knobs are independent,
    so reporting one at a time makes an operator with two typos restart twice.
    """
    raw = os.getenv(name, "").strip()
    try:
        value = cast(raw or default)
        # NaN fails every comparison, so a bare < / > check would pass it
        # straight to the SDK's max(min(...)) clamp — the exact "a number that
        # is not in effect" case these bounds exist to refuse.
        # OverflowError, not just ValueError: isfinite() converts to a C double
        # first, so a 309-digit int raises here — and an uncaught raise in this
        # module takes down every route, the ICS feed, and backups with it.
        if not math.isfinite(value):
            _CONTEXT_FAULTS.append(f"{name} is not a real number. Skein uses {default}.")
            return default
    except (ValueError, OverflowError):
        _CONTEXT_FAULTS.append(f"{name} is not a usable number. Skein uses {default}.")
        return default
    if (low is not None and value < low) or (high is not None and value > high):
        bounds = f"{low} to {high}" if high is not None else f"{low} or more"
        _CONTEXT_FAULTS.append(f"{name}={value} is outside {bounds}. Skein uses {default}.")
        return default
    return value


# messages kept before the oldest are dropped (sliding). 0 would clear the
# history on every reduction, which is a chat with no memory at all. Named
# _MESSAGES because it counts messages, not tokens — a knob called plain
# "context window" reads as a token capacity and invites the wrong edit.
CONTEXT_WINDOW_MESSAGES = _ctx_num("SKEIN_CONTEXT_WINDOW_MESSAGES", 40, int, low=1)
# The pre-rename name is a fault, never a fallback: read silently, a stale
# deployment env keeps steering the window until someone drops the fallback,
# and the window then reverts to 40 with no report.
if os.getenv("SKEIN_CONTEXT_WINDOW") is not None:
    _CONTEXT_FAULTS.append("SKEIN_CONTEXT_WINDOW was renamed. Set SKEIN_CONTEXT_WINDOW_MESSAGES.")
# share of the oldest messages folded into a summary when it fires (summarize).
# bounds mirror the SDK's own clamp, so the configured number is the real one
CONTEXT_SUMMARY_RATIO = _ctx_num("SKEIN_CONTEXT_SUMMARY_RATIO", 0.3, float, low=0.1, high=0.8)
# recent messages never summarized away (summarize). Set too high, the SDK
# raises "insufficient messages for summarization" on every overflow, so the
# ceiling is a typo guard rather than a preference
CONTEXT_PRESERVE_RECENT = _ctx_num("SKEIN_CONTEXT_PRESERVE_RECENT", 10, int, low=0, high=1000)
# opening messages the SDK holds during a turn. INERT on Skein's file-backed
# chats: session restore replays from an offset that skips exactly these
# messages, so the pin does not survive a turn boundary. Wired for when it does.
CONTEXT_PIN_FIRST = _ctx_num("SKEIN_CONTEXT_PIN_FIRST", 0, int, low=0, high=1000)
# compress before an overflow instead of after. The threshold is 70% of the
# MODEL's TOKEN context window — NOT of CONTEXT_WINDOW_MESSAGES, which is a
# message count. The SDK resolves known ids from its own table
# (strands/models/_defaults.py) and assumes 200k for the rest; a MODELS
# entry's context_tokens overrides both (agents/team_agent.py::_model), which
# is what makes this fire correctly on a small local model.
CONTEXT_PROACTIVE = os.getenv("SKEIN_CONTEXT_PROACTIVE", "0") == "1"

# joined with a space, not a semicolon: every fault terminates itself, and
# user-visible functional text carries no semicolons (CLAUDE.md wording rules)
CONTEXT_STRATEGY_ERROR = " ".join(_CONTEXT_FAULTS)

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("SKEIN_CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

# Background jobs (blocker sweep, daily digest, daily backup). Disabled in tests.
# Run the scheduler in exactly ONE process (the default single-worker uvicorn).
SCHEDULER_ENABLED = os.getenv("SKEIN_SCHEDULER", "1") == "1"

# ---- optional integrations: each activates only when its config is set ----

# Slack: webhook for outbound notifications/digests; signing secret enables
# the inbound /api/slack/command endpoint.
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

# MCP servers for the real agent, JSON list:
# [{"name": "github", "url": "https://api.githubcopilot.com/mcp/", "auth_token": "..."}]
MCP_SERVERS = os.getenv("SKEIN_MCP_SERVERS", "")

# OpenTelemetry OTLP endpoint (e.g. http://jaeger:4318). Empty = disabled.
OTEL_ENDPOINT = os.getenv("SKEIN_OTEL_ENDPOINT", "")

# Opt-in prebuilt tools from strands-agents-tools for the real agent,
# comma-separated (e.g. "calculator,current_time,think,batch"). Only
# allowlisted names load — see app/agents/extra_tools.py.
EXTRA_TOOLS = tuple(t.strip() for t in os.getenv("SKEIN_EXTRA_TOOLS", "").split(",") if t.strip())

# ---- authentication --------------------------------------------------------
# How a caller proves who they are. routes/deps.py is the single branch point.
#   trusted-header  identity is the self-asserted X-User header (trusted
#                   network / local dev). Personal API keys still work and are the only
#                   STRONG identity.
#   api-key         every request needs a personal API key (sk-skein-…).
#   oidc            humans present an IdP-issued JWT, validated in-process
#                   against the issuer's JWKS (app/oidc.py). Personal API
#                   keys still work for automation (CLI, MCP, hooks).
# An unknown mode fails CLOSED — every /api request is refused and /health
# says why. A typo of "oidc" must not silently open the deployment, so this
# is the one config fault that does NOT degrade to a working default.
AUTH_MODES = ("trusted-header", "api-key", "oidc")
AUTH_MODE = os.getenv("SKEIN_AUTH_MODE", "trusted-header").strip().lower() or "trusted-header"
# how many trusted proxies sit in front of this process and append to
# X-Forwarded-For (an OpenShift/k8s ingress router = 1). At 0 the header is
# ignored: per-address rate caps key on the socket peer, which behind a
# router is the router — one signin bucket for the whole team. Trusting
# MORE hops than actually exist hands every caller a spoofable bucket key,
# so a bad value degrades to 0, never up.
try:
    TRUST_PROXY_HOPS = max(0, int(os.getenv("SKEIN_TRUST_PROXY_HOPS", "").strip() or 0))
except ValueError:
    TRUST_PROXY_HOPS = 0

# Thread-pool sizes, applied at startup (main.py lifespan). Measured, not
# guessed: GIL handoff between threads parked on sqlite3's C boundary makes
# throughput ANTI-scale with pool width — GET /api/tasks at 40 concurrent
# callers measured 97 req/s with 8 threads against 68 with the default 40
# (4 cores). Smaller is faster until requests queue on genuinely-blocking
# work (a 5s embedding call), which is what raising this is for.
#
# THREAD_POOL is anyio's limiter: every sync route handler and every
# run_in_threadpool call. TOOL_THREADS is the event loop's default executor:
# every sync @tool via asyncio.to_thread — unset, it sizes itself
# min(32, cpu + 4), so the ceiling was 8 on a 4-vCPU deploy VM and 32 on a
# dev box, and nobody chose either. The floor is 2, never 0 or 1: a
# single-thread pool deadlocks the first tool that itself waits on the pool.
try:
    THREAD_POOL = max(2, int(os.getenv("SKEIN_THREAD_POOL", "").strip() or 8))
except ValueError:
    THREAD_POOL = 8
try:
    TOOL_THREADS = max(2, int(os.getenv("SKEIN_TOOL_THREADS", "").strip() or 16))
except ValueError:
    TOOL_THREADS = 16
AUTH_ERROR = ""
if AUTH_MODE not in AUTH_MODES:
    # states the fault and the fix, and does NOT echo the rejected value:
    # this message reaches unauthenticated callers in the 503 body and on
    # /health. main.py logs the value for whoever runs the server.
    AUTH_ERROR = f"SKEIN_AUTH_MODE is not a known mode. Set it to one of: {', '.join(AUTH_MODES)}."

# Administrators: the only identities the roster / team-config / export
# surfaces accept (deps.AdminUser). Empty + trusted-header mode = every key
# holder administers — the historical scarcity model, where the operator
# mints each key by hand. api-key and oidc modes remove that scarcity, so
# there an empty set keeps the admin surfaces locked until it is set.
ADMINS = frozenset(a.strip() for a in os.getenv("SKEIN_ADMINS", "").split(",") if a.strip())

# OIDC (SKEIN_AUTH_MODE=oidc): validation is local — signature against the
# issuer's JWKS, then iss / aud / exp. No sidecar, no per-request IdP call.
OIDC_ISSUER = os.getenv("SKEIN_OIDC_ISSUER", "").strip().rstrip("/")
OIDC_AUDIENCE = os.getenv("SKEIN_OIDC_AUDIENCE", "").strip()
# empty = derived from <issuer>/.well-known/openid-configuration at first use
OIDC_JWKS_URL = os.getenv("SKEIN_OIDC_JWKS_URL", "").strip()
OIDC_USERNAME_CLAIM = os.getenv("SKEIN_OIDC_USERNAME_CLAIM", "").strip() or "preferred_username"
OIDC_GROUPS_CLAIM = os.getenv("SKEIN_OIDC_GROUPS_CLAIM", "").strip() or "groups"
# IdP group that grants admin, alongside SKEIN_ADMINS
OIDC_ADMIN_GROUP = os.getenv("SKEIN_OIDC_ADMIN_GROUP", "").strip()
# Browser sign-in (authorization code + PKCE). The web app is a PUBLIC client:
# there is no client secret anywhere in this codebase, because a secret shipped
# to a browser is not a secret. Empty client id = the API accepts IdP tokens
# but the web app shows no sign-in button.
OIDC_CLIENT_ID = os.getenv("SKEIN_OIDC_CLIENT_ID", "").strip()
OIDC_SCOPES = os.getenv("SKEIN_OIDC_SCOPES", "").strip() or "openid profile"
# Endpoint overrides. Empty = read from the issuer's discovery document, the
# same one SKEIN_OIDC_JWKS_URL overrides.
OIDC_AUTHORIZE_URL = os.getenv("SKEIN_OIDC_AUTHORIZE_URL", "").strip()
OIDC_TOKEN_URL = os.getenv("SKEIN_OIDC_TOKEN_URL", "").strip()
if not AUTH_ERROR and AUTH_MODE == "oidc":
    if not OIDC_ISSUER:
        AUTH_ERROR = "SKEIN_AUTH_MODE=oidc requires SKEIN_OIDC_ISSUER"
    elif not OIDC_AUDIENCE:
        AUTH_ERROR = "SKEIN_AUTH_MODE=oidc requires SKEIN_OIDC_AUDIENCE"

# Optional shared bearer token for the whole API (set when exposing beyond
# a trusted network). Only read in trusted-header mode — the other modes
# carry a per-caller credential on every request, which is strictly stronger.
API_TOKEN = os.getenv("SKEIN_API_TOKEN", "")

# Dedicated secret for the ICS calendar feed URL (?token=...). Calendar
# clients put the URL in configs/logs, so it must NEVER be the API token.
# When API_TOKEN is set but this is not, the feed is disabled (fail closed).
ICS_TOKEN = os.getenv("SKEIN_ICS_TOKEN", "")

# Shared secret for the Gitea webhook (HMAC-SHA256 over the raw body). Empty
# disables the endpoint: the webhook moves tasks, so an unsigned caller must
# never reach it. Its own secret, never the API token — the forge stores it
# in a repository setting that every repo admin can read.
FORGE_WEBHOOK_SECRET = os.getenv("SKEIN_FORGE_WEBHOOK_SECRET", "")

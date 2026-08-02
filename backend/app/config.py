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
PROVIDERS: dict[str, dict] = {
    "mock": {"default_model": "mock", "base_url": "forbidden", "key_env": ""},
    "anthropic": {
        "default_model": "claude-opus-4-8",
        "base_url": "forbidden",
        "key_env": "ANTHROPIC_API_KEY",
    },
    "openai": {"default_model": "gpt-5", "base_url": "forbidden", "key_env": "OPENAI_API_KEY"},
    # Anything speaking the OpenAI wire format: vLLM, LM Studio, llama.cpp,
    # OpenRouter, Together, Groq, Azure OpenAI, LiteLLM Proxy.
    #
    # key_env is deliberately EMPTY. Falling back to OPENAI_API_KEY here would
    # hand a paid OpenAI credential to whatever third-party host the operator
    # named — and OPENAI_API_KEY is already set on any box using semantic
    # search. Credentials for a non-OpenAI endpoint must be stated explicitly
    # in SKEIN_MODEL_API_KEY.
    "openai_compatible": {"default_model": None, "base_url": "required", "key_env": ""},
    "ollama": {
        "default_model": "gpt-oss:120b-cloud",
        "base_url": "forbidden",
        "key_env": "OLLAMA_API_KEY",
    },
    # boto3 is already a strands core dep; credentials come from the ambient
    # AWS chain (instance role, AWS_PROFILE), so there is no key to set.
    # No default model on purpose: Claude-family ids on Bedrock need a
    # geo-prefixed inference profile (us./eu./apac./global.) that depends on
    # the deployment's region, and a bare foundation id is not invocable
    # on-demand. Better to demand SKEIN_MODEL_ID than to ship one that 400s.
    "bedrock": {"default_model": None, "base_url": "forbidden", "key_env": ""},
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

if not MODEL_PROVIDER_ERROR and (_raw := os.getenv("SKEIN_MODEL_PARAMS", "").strip()):
    try:
        MODEL_PARAMS = json.loads(_raw)
        if not isinstance(MODEL_PARAMS, dict):
            raise TypeError("not a JSON object")
    except (json.JSONDecodeError, TypeError) as exc:
        MODEL_PARAMS = {}
        MODEL_PROVIDER_ERROR = f"SKEIN_MODEL_PARAMS is not a JSON object: {exc}"

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
# history on every reduction, which is a chat with no memory at all
CONTEXT_WINDOW = _ctx_num("SKEIN_CONTEXT_WINDOW", 40, int, low=1)
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
# compress at 70% of the window instead of waiting for an overflow error
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

# Optional shared bearer token for the whole API (set when exposing beyond
# a trusted network). Empty = open (trusted-LAN mode).
API_TOKEN = os.getenv("SKEIN_API_TOKEN", "")

# Dedicated secret for the ICS calendar feed URL (?token=...). Calendar
# clients put the URL in configs/logs, so it must NEVER be the API token.
# When API_TOKEN is set but this is not, the feed is disabled (fail closed).
ICS_TOKEN = os.getenv("SKEIN_ICS_TOKEN", "")

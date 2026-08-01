"""Central configuration, loaded from environment / .env."""

import json
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
#   needs_base_url   — SKEIN_MODEL_BASE_URL is required.
#   key_env          — provider-native credential the SDK reads for itself.
#                      Absent = no key needed (mock, ollama, bedrock's AWS chain).
PROVIDERS: dict[str, dict] = {
    "mock": {"default_model": "mock", "needs_base_url": False, "key_env": ""},
    "anthropic": {
        "default_model": "claude-opus-4-8",
        "needs_base_url": False,
        "key_env": "ANTHROPIC_API_KEY",
    },
    "openai": {"default_model": "gpt-5", "needs_base_url": False, "key_env": "OPENAI_API_KEY"},
    # Anything speaking the OpenAI wire format: vLLM, LM Studio, llama.cpp,
    # OpenRouter, Together, Groq, Azure OpenAI, LiteLLM Proxy. A distinct
    # provider rather than a base-url flag on "openai" so /health reports the
    # truth, and so a typo'd URL can't silently redirect a paid OpenAI key.
    "openai_compatible": {
        "default_model": None,
        "needs_base_url": True,
        "key_env": "OPENAI_API_KEY",
    },
    "ollama": {
        "default_model": "gpt-oss:120b-cloud",
        "needs_base_url": False,
        "key_env": "OLLAMA_API_KEY",
    },
    # boto3 is already a strands core dep; credentials come from the ambient
    # AWS chain (instance role, AWS_PROFILE), so there is no key to set.
    "bedrock": {
        "default_model": "anthropic.claude-sonnet-4-20250514-v1:0",
        "needs_base_url": False,
        "key_env": "",
    },
}

MODEL_PROVIDER = os.getenv("SKEIN_MODEL_PROVIDER", "mock").lower()
MODEL_BASE_URL = os.getenv("SKEIN_MODEL_BASE_URL", "")
MODEL_API_KEY = os.getenv("SKEIN_MODEL_API_KEY", "")
MAX_TOKENS = int(os.getenv("SKEIN_MAX_TOKENS", "4096"))

# Free-form per-provider knobs (temperature, top_p, max_completion_tokens...),
# merged last so an operator can always reach something we did not model.
MODEL_PARAMS: dict = {}

# Misconfiguration must never take down the deterministic core: config is
# imported by db, every route, seed.py and the CLI, so a bad *model* setting
# raising here would kill the REST API, ICS feed and backups. Record the fault,
# fall back to mock, and let agents/team_agent.py raise at construction time —
# where routes/chat.py already turns it into an SSE error the operator reads.
MODEL_PROVIDER_ERROR = ""

if MODEL_PROVIDER not in PROVIDERS:
    MODEL_PROVIDER_ERROR = (
        f"unknown SKEIN_MODEL_PROVIDER {MODEL_PROVIDER!r} —"
        f" expected one of: {', '.join(sorted(PROVIDERS))}"
    )
elif PROVIDERS[MODEL_PROVIDER]["needs_base_url"] and not MODEL_BASE_URL:
    MODEL_PROVIDER_ERROR = f"SKEIN_MODEL_PROVIDER={MODEL_PROVIDER} requires SKEIN_MODEL_BASE_URL"

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
OLLAMA_HOST = os.getenv("SKEIN_OLLAMA_HOST", "") or MODEL_BASE_URL or "http://localhost:11434"
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")

# With SKEIN_AGENT_REVIEW=1, mutating agent writes become pending_changes
# proposals that a human approves in the review inbox (approval-gate mode).
AGENT_REVIEW = os.getenv("SKEIN_AGENT_REVIEW", "0") == "1"

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

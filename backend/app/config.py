"""Central configuration, loaded from environment / .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("STRANDS_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "platform.db"
# Author-private records (1:1 prep, feedback journal) live in a SEPARATE
# database file that backup/export/FTS/MCP/agents never open. Excluded from
# the daily backup chain by design — back it up manually and encrypted if at
# all (it is small and reconstructible personal notes).
PRIVATE_DB_PATH = Path(os.getenv("STRANDS_PRIVATE_DB", DATA_DIR / "private.db"))
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Model settings. Provider is "anthropic", "openai", "ollama", or "mock"
# (keyless deterministic agent for dev/tests). Credentials resolve from
# ANTHROPIC_API_KEY / OPENAI_API_KEY when not passed explicitly.
MODEL_PROVIDER = os.getenv("STRANDS_MODEL_PROVIDER", "mock").lower()
_DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5",
    "ollama": "gpt-oss:120b-cloud",
    "mock": "mock",
}
MODEL_ID = os.getenv("STRANDS_MODEL_ID", _DEFAULT_MODELS.get(MODEL_PROVIDER, "mock"))
MAX_TOKENS = int(os.getenv("STRANDS_MAX_TOKENS", "4096"))

# Ollama: the default host is the local daemon, which proxies *-cloud models
# to Ollama Cloud when `ollama signin` has been run on the box. To skip the
# daemon and talk to Ollama Cloud directly, set STRANDS_OLLAMA_HOST to
# https://ollama.com and OLLAMA_API_KEY to a key from ollama.com settings.
OLLAMA_HOST = os.getenv("STRANDS_OLLAMA_HOST", "http://localhost:11434")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")

# With STRANDS_AGENT_REVIEW=1, mutating agent writes become pending_changes
# proposals that a human approves in the review inbox (approval-gate mode).
AGENT_REVIEW = os.getenv("STRANDS_AGENT_REVIEW", "0") == "1"

CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("STRANDS_CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

# Background jobs (blocker sweep, daily digest, daily backup). Disabled in tests.
# Run the scheduler in exactly ONE process (the default single-worker uvicorn).
SCHEDULER_ENABLED = os.getenv("STRANDS_SCHEDULER", "1") == "1"

# ---- optional integrations: each activates only when its config is set ----

# Slack: webhook for outbound notifications/digests; signing secret enables
# the inbound /api/slack/command endpoint.
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

# MCP servers for the real agent, JSON list:
# [{"name": "github", "url": "https://api.githubcopilot.com/mcp/", "auth_token": "..."}]
MCP_SERVERS = os.getenv("STRANDS_MCP_SERVERS", "")

# OpenTelemetry OTLP endpoint (e.g. http://jaeger:4318). Empty = disabled.
OTEL_ENDPOINT = os.getenv("STRANDS_OTEL_ENDPOINT", "")

# Opt-in prebuilt tools from strands-agents-tools for the real agent,
# comma-separated (e.g. "calculator,current_time,think,batch"). Only
# allowlisted names load — see app/agents/extra_tools.py.
EXTRA_TOOLS = tuple(t.strip() for t in os.getenv("STRANDS_EXTRA_TOOLS", "").split(",") if t.strip())

# Optional shared bearer token for the whole API (set when exposing beyond
# a trusted network). Empty = open (trusted-LAN mode).
API_TOKEN = os.getenv("STRANDS_API_TOKEN", "")

# Dedicated secret for the ICS calendar feed URL (?token=...). Calendar
# clients put the URL in configs/logs, so it must NEVER be the API token.
# When API_TOKEN is set but this is not, the feed is disabled (fail closed).
ICS_TOKEN = os.getenv("STRANDS_ICS_TOKEN", "")

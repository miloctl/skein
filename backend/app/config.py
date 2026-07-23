"""Central configuration, loaded from environment / .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("STRANDS_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "platform.db"
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Model settings. Provider is "anthropic", "openai", or "mock" (keyless
# deterministic agent for dev/tests). Credentials resolve from
# ANTHROPIC_API_KEY / OPENAI_API_KEY when not passed explicitly.
MODEL_PROVIDER = os.getenv("STRANDS_MODEL_PROVIDER", "mock").lower()
_DEFAULT_MODELS = {"anthropic": "claude-opus-4-8", "openai": "gpt-5", "mock": "mock"}
MODEL_ID = os.getenv("STRANDS_MODEL_ID", _DEFAULT_MODELS.get(MODEL_PROVIDER, "mock"))
MAX_TOKENS = int(os.getenv("STRANDS_MAX_TOKENS", "4096"))

# With STRANDS_AGENT_REVIEW=1, mutating agent writes become pending_changes
# proposals that a human approves in the review inbox (approval-gate mode).
AGENT_REVIEW = os.getenv("STRANDS_AGENT_REVIEW", "0") == "1"

CORS_ORIGINS = os.getenv("STRANDS_CORS_ORIGINS", "http://localhost:3000").split(",")

# Background jobs (blocker sweep, daily digest, daily backup). Disabled in tests.
SCHEDULER_ENABLED = os.getenv("STRANDS_SCHEDULER", "1") == "1"

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

# Model settings. Provider is "anthropic" or "openai"; credentials resolve from
# ANTHROPIC_API_KEY / OPENAI_API_KEY when not passed explicitly.
MODEL_PROVIDER = os.getenv("STRANDS_MODEL_PROVIDER", "anthropic").lower()
MODEL_ID = os.getenv(
    "STRANDS_MODEL_ID",
    "claude-opus-4-8" if MODEL_PROVIDER == "anthropic" else "gpt-5",
)
MAX_TOKENS = int(os.getenv("STRANDS_MAX_TOKENS", "4096"))

CORS_ORIGINS = os.getenv("STRANDS_CORS_ORIGINS", "http://localhost:3000").split(",")

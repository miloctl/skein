"""The Bench: curated specialist personas, loaded from backend/personas/*.md.

A persona file is frontmatter (name/description/emoji/vibe) plus a system-
prompt body. Files are edited like code (the playbooks precedent) — adapted
from ~/external/agency-agents, vendored so there is no runtime dependency.
Slugs double as agent identities in the authority matrix and trust scores,
hence the strict charset."""

import re
from pathlib import Path

PERSONAS_DIR = Path(__file__).resolve().parent.parent.parent / "personas"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_FIELDS = ("name", "description", "emoji", "vibe")


def _parse(path: Path) -> dict | None:
    slug = path.stem
    if not _SLUG.match(slug):
        return None
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    try:
        _, front, body = text.split("---", 2)
    except ValueError:
        return None
    meta = {}
    for line in front.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in _FIELDS:
            meta[key.strip()] = value.strip()
    if not meta.get("name") or not meta.get("description"):
        return None
    return {
        "slug": slug,
        "name": meta["name"],
        "description": meta["description"],
        "emoji": meta.get("emoji", "🎭"),
        "vibe": meta.get("vibe", ""),
        "body": body.strip(),
    }


def list_personas() -> list[dict]:
    """The bench roster — everything except the prompt body."""
    out = []
    if PERSONAS_DIR.is_dir():
        for path in sorted(PERSONAS_DIR.glob("*.md")):
            p = _parse(path)
            if p:
                out.append({k: p[k] for k in ("slug", "name", "description", "emoji", "vibe")})
    return out


def get_persona(slug: str) -> dict:
    if _SLUG.match(slug):
        path = PERSONAS_DIR / f"{slug}.md"
        if path.is_file():
            p = _parse(path)
            if p:
                return p
    roster = ", ".join(p["slug"] for p in list_personas()) or "none installed"
    raise ValueError(f"no persona '{slug}' on the bench — available: {roster}")

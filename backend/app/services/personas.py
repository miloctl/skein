"""The Bench: curated specialist personas, loaded from backend/personas/*.md
plus an optional SKEIN_PERSONAS_DIR overlay (overlay wins a slug collision, and
an overlay pack.json replaces the stock one wholesale).

A persona file is frontmatter (name/description/emoji/vibe) plus a system-
prompt body. Files are edited like code (the playbooks precedent) — adapted
from ~/external/agency-agents, vendored so there is no runtime dependency.
Slugs double as agent identities in the authority matrix and trust scores,
hence the strict charset.

BEHAVIOR fields (model / temperature / tools) tune how a persona runs on a
real provider: a model id override, a sampling temperature, and a tool
allowlist. `tools` is deny-by-omission ONCE DECLARED: a persona that lists
tools gets exactly those and nothing else — enforced at Agent construction,
so the model never sees an undeclared tool. A persona with no `tools` line
keeps the full registry, so existing personas are unaffected. Pack-wide
defaults live in personas/pack.json (`{"defaults": {...}}`); persona
frontmatter wins field-by-field.

Runtime parsing stays lenient (a malformed file drops off the bench rather
than 500ing chat); validate_all() is the strict pass, wired into lint.sh so
the same malformed file fails CI instead of silently vanishing.
"""

import json
import re
from pathlib import Path

from .. import config

PERSONAS_DIR = Path(__file__).resolve().parent.parent.parent / "personas"
PACK_FILE = PERSONAS_DIR / "pack.json"


def _persona_files() -> dict[str, Path]:
    """slug -> path across the stock dir and the SKEIN_PERSONAS_DIR overlay.
    The overlay wins a slug collision, so a deployment can re-head a stock
    persona without editing the repo."""
    files: dict[str, Path] = {}
    dirs = [PERSONAS_DIR]
    overlay = config.PERSONAS_OVERLAY
    if overlay and overlay.is_dir():
        dirs.append(overlay)
    for d in dirs:
        if d.is_dir():
            for path in sorted(d.glob("*.md")):
                files[path.stem] = path
    return files


def _pack_file() -> Path:
    """The effective pack.json: the overlay's copy wins wholesale when it
    exists — behavioral defaults are one coherent object, never a field merge
    of two files."""
    overlay = config.PERSONAS_OVERLAY
    if overlay:
        candidate = overlay / "pack.json"
        if candidate.is_file():
            return candidate
    return PACK_FILE


_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_FIELDS = ("name", "description", "emoji", "vibe", "disclosure", "model", "temperature", "tools")
BEHAVIOR_FIELDS = ("model", "temperature", "tools")


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
        "disclosure": meta.get("disclosure", ""),
        "model": meta.get("model", ""),
        "temperature": meta.get("temperature", ""),
        "tools": meta.get("tools", ""),
        "body": body.strip(),
    }


def _pack_defaults() -> dict:
    """Behavioral defaults from the effective pack.json, or {} when absent/bad.
    Lenient at runtime for the same reason _parse is; validate_all is strict."""
    pack = _pack_file()
    if not pack.is_file():
        return {}
    try:
        data = json.loads(pack.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    defaults = data.get("defaults") if isinstance(data, dict) else None
    if not isinstance(defaults, dict):
        return {}
    out = {}
    for k, v in defaults.items():
        if k not in BEHAVIOR_FIELDS:
            continue
        # a JSON list is the natural way to write a tool list in a JSON file;
        # str() on it would produce a repr that matches no tool, silently
        # building every persona with ZERO tools
        out[k] = ",".join(str(i) for i in v) if isinstance(v, list) else str(v)
    return out


def _merge_behavior(persona: dict, defaults: dict) -> dict:
    """Persona frontmatter wins field-by-field over the pack defaults.
    Separate so the precedence is testable in isolation.

    KNOWN LIMIT: an empty frontmatter value falls through to the pack default,
    so a persona cannot override a pack default back to "unrestricted" or
    "deployment model". Keep pack.json defaults minimal — a pack-wide tools
    default restricts every persona with no per-persona escape hatch."""
    return {k: persona.get(k) or defaults.get(k, "") for k in BEHAVIOR_FIELDS}


def behavior(slug: str) -> dict:
    """The resolved runtime behavior for a persona: model id override (str,
    empty = deployment model), temperature (float or None), and tool
    allowlist (list of names, or None = unrestricted).

    Degrades rather than raises: a bad temperature is dropped, an unknown
    tool name is kept (denying it is exactly what the allowlist means).
    validate_all() is where either becomes a loud error.
    """
    merged = _merge_behavior(get_persona(slug), _pack_defaults())
    temperature: float | None = None
    raw = merged["temperature"].strip()
    if raw:
        try:
            temperature = float(raw)
        except ValueError:
            temperature = None
        else:
            if not 0.0 <= temperature <= 2.0:
                temperature = None
    tools = [t.strip() for t in merged["tools"].split(",") if t.strip()] or None
    return {"model": merged["model"].strip(), "temperature": temperature, "tools": tools}


def _known_tool_names() -> set[str]:
    """Names a persona allowlist may reference: the registry plus the chat
    planner. Extra tools (SKEIN_EXTRA_TOOLS) and MCP tools are env-dependent
    and deliberately NOT valid allowlist entries — validating against an env
    that CI does not share would make lint results depend on deployment."""
    from ..tools import ALL_TOOLS

    names = set()
    for t in ALL_TOOLS:
        name = getattr(t, "tool_name", "") or getattr(t, "__name__", "")
        if name:
            names.add(str(name))
    names.add("plan_project")
    return names


def _check_behavior(label: str, temperature: str, tools: str, known: set[str]) -> list[str]:
    """The value checks shared by frontmatter and pack.json defaults — one
    rule, two sources, so the pack cannot smuggle what a persona cannot."""
    errors = []
    raw = temperature.strip()
    if raw:
        try:
            t = float(raw)
        except ValueError:
            errors.append(f"{label}: temperature {raw!r} is not a number")
        else:
            if not 0.0 <= t <= 2.0:
                errors.append(f"{label}: temperature {t} is outside 0.0 to 2.0")
    for name in (n.strip() for n in tools.split(",") if n.strip()):
        if name not in known:
            errors.append(
                f"{label}: tools names unknown tool {name!r} — the allowlist"
                " denies by omission, so a typo silently strips the tool"
            )
    return errors


def validate_all() -> list[str]:
    """Every check _parse forgives, as loud errors — run by lint.sh so a
    malformed persona fails CI instead of silently vanishing from the bench."""
    errors: list[str] = []
    known = _known_tool_names()
    pack = _pack_file()
    if pack.is_file():
        try:
            data = json.loads(pack.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("defaults", {}), dict):
                errors.append("pack.json: expected an object with an optional 'defaults' object")
            else:
                defaults = data.get("defaults", {})
                unknown = set(defaults) - set(BEHAVIOR_FIELDS)
                if unknown:
                    errors.append(f"pack.json: unknown default field(s): {sorted(unknown)}")
                for k, v in defaults.items():
                    ok_types = (str, int, float)
                    if not (
                        isinstance(v, ok_types)
                        or (isinstance(v, list) and all(isinstance(i, str) for i in v))
                    ):
                        errors.append(
                            f"pack.json: default {k!r} must be a string, a number,"
                            " or a list of strings"
                        )
                merged = _pack_defaults()
                errors += _check_behavior(
                    "pack.json", merged.get("temperature", ""), merged.get("tools", ""), known
                )
        except json.JSONDecodeError as exc:
            errors.append(f"pack.json: not valid JSON ({exc})")
    overlay = config.PERSONAS_OVERLAY
    dirs = [PERSONAS_DIR] + ([overlay] if overlay and overlay.is_dir() else [])
    for d in dirs:
        for path in sorted(d.glob("*.md")):
            label = path.name if d == PERSONAS_DIR else f"{path.name} (overlay)"
            if not _SLUG.match(path.stem):
                errors.append(f"{label}: slug must match {_SLUG.pattern}")
                continue
            p = _parse(path)
            if p is None:
                errors.append(f"{label}: missing frontmatter, or name/description empty")
                continue
            errors += _check_behavior(label, p["temperature"], p["tools"], known)
    return errors


def list_personas() -> list[dict]:
    """The bench roster — everything except the prompt body."""
    out = []
    for _slug, path in sorted(_persona_files().items()):
        p = _parse(path)
        if p:
            out.append(
                {k: p[k] for k in ("slug", "name", "description", "emoji", "vibe", "disclosure")}
            )
    return out


def get_persona(slug: str) -> dict:
    if _SLUG.match(slug):
        path = _persona_files().get(slug)
        if path is not None and path.is_file():
            p = _parse(path)
            if p:
                return p
    roster = ", ".join(p["slug"] for p in list_personas()) or "none installed"
    raise ValueError(f"no persona '{slug}' on the bench — available: {roster}")


if __name__ == "__main__":  # the lint.sh gate: exit 1 with every error listed
    import sys

    problems = validate_all()
    for problem in problems:
        print(f"persona: {problem}")
    sys.exit(1 if problems else 0)

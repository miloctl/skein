"""The Bench: curated specialist personas, loaded from backend/personas/*.md
plus an optional SKEIN_PERSONAS_DIR overlay (overlay wins a slug collision;
overlay pack.json defaults merge FIELD-BY-FIELD over the stock ones — see
_pack_files, whose merge is the behavior docs/PERSONAS.md documents).

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

PERSONAS_DIR = config.STOCK_DIR / "personas"
PACK_FILE = PERSONAS_DIR / "pack.json"


def _persona_dirs() -> list[Path]:
    """Stock first, overlay second — later wins."""
    dirs = [PERSONAS_DIR]
    overlay = config.PERSONAS_OVERLAY
    if overlay and overlay.is_dir() and overlay.resolve() != PERSONAS_DIR.resolve():
        dirs.append(overlay)
    return [d for d in dirs if d.is_dir()]


def _persona_files() -> dict[str, Path]:
    """slug -> path across the stock dir and the SKEIN_PERSONAS_DIR overlay.
    The overlay wins a slug collision, so a deployment can re-head a stock
    persona without editing the repo. A stem the slug charset rejects never
    enters the map: bench_slugs() reserves every key here as an agent
    identity, so an unparseable stem would reserve a name against a persona
    that get_persona() then refuses to produce."""
    files: dict[str, Path] = {}
    for d in _persona_dirs():
        for path in sorted(d.glob("*.md")):
            if _SLUG.match(path.stem):
                files[path.stem] = path
    return files


def bench_slugs() -> set[str]:
    """Slugs reserved as agent identities, computed live (glob only, no
    parsing) — a cached set would miss a persona dropped into a mounted
    overlay after startup, and the human-name guard in users.ensure_user
    would then let a human absorb that persona's identity."""
    return set(_persona_files())


def _pack_files() -> list[Path]:
    """Stock first, overlay second. The layers merge FIELD-BY-FIELD, the same
    precedence persona frontmatter has over the pack one function below.
    Naming one key must not clear the others: an operator who writes
    {"defaults": {"model": "x"}} has set the model, not cleared the
    temperature. To clear a stock default, set it to JSON null."""
    files = [PACK_FILE]
    overlay = config.PERSONAS_OVERLAY
    if overlay:
        candidate = overlay / "pack.json"
        if candidate.resolve() != PACK_FILE.resolve():
            files.append(candidate)
    return [p for p in files if p.is_file()]


_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_FIELDS = (
    "schema_version",
    "name",
    "description",
    "emoji",
    "vibe",
    "disclosure",
    "model",
    "temperature",
    "tools",
)
BEHAVIOR_FIELDS = ("model", "temperature", "tools")
SCHEMA_VERSION = 1


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
    raw_version = meta.get("schema_version", str(SCHEMA_VERSION))
    if raw_version != str(SCHEMA_VERSION):
        return None
    return {
        "schema_version": SCHEMA_VERSION,
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


def _read_pack(pack: Path) -> dict:
    """One pack.json as {field: value}. Lenient at runtime for the same reason
    _parse is; validate_all is strict."""
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
        if v is None:
            out[k] = ""  # explicit clear — see _pack_files
        elif isinstance(v, list):
            # a JSON list is the natural way to write a tool list in a JSON
            # file; str() on it would produce a repr that matches no tool,
            # silently building every persona with ZERO tools
            out[k] = ",".join(str(i) for i in v)
        else:
            out[k] = str(v)
    return out


def _pack_defaults() -> dict:
    """Behavioral defaults, stock then overlay, merged field-by-field."""
    out: dict[str, str] = {}
    for pack in _pack_files():
        out.update(_read_pack(pack))
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
    # consult_specialist is omitted on purpose. agents/team_agent.py builds it
    # only for the Chief of Staff (persona == ""), so a persona can never hold
    # it — accepting the name here would validate an allowlist entry that
    # silently grants nothing. Refusing it tells the author at lint time.
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
    # BOTH packs, each labeled by path: with two layers "pack.json:" alone
    # cannot say which file carries the error
    for pack in _pack_files():
        label = "pack.json" if pack == PACK_FILE else f"{pack} (overlay)"
        try:
            data = json.loads(pack.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("defaults", {}), dict):
                errors.append(f"{label}: expected an object with an optional 'defaults' object")
            else:
                defaults = data.get("defaults", {})
                unknown = set(defaults) - set(BEHAVIOR_FIELDS)
                if unknown:
                    errors.append(f"{label}: unknown default field(s): {sorted(unknown)}")
                for k, v in defaults.items():
                    ok_types = (str, int, float)
                    if not (
                        v is None  # explicit clear
                        or isinstance(v, ok_types)
                        or (isinstance(v, list) and all(isinstance(i, str) for i in v))
                    ):
                        errors.append(
                            f"{label}: default {k!r} must be a string, a number,"
                            " a list of strings, or null to clear"
                        )
        except json.JSONDecodeError as exc:
            errors.append(f"{label}: not valid JSON ({exc})")
    # the MERGED result is what personas actually run on, so check it once
    merged = _pack_defaults()
    errors += _check_behavior(
        "pack defaults (merged)", merged.get("temperature", ""), merged.get("tools", ""), known
    )
    for d in _persona_dirs():
        for path in sorted(d.glob("*.md")):
            label = path.name if d == PERSONAS_DIR else f"{path.name} (overlay)"
            if not _SLUG.match(path.stem):
                errors.append(f"{label}: slug must match {_SLUG.pattern}")
                continue
            text = path.read_text(encoding="utf-8")
            try:
                _, front, _body = text.split("---", 2)
            except ValueError:
                front = ""
            keys = {
                key.strip()
                for line in front.splitlines()
                for key, separator, _value in (line.partition(":"),)
                if separator
            }
            unknown = keys - set(_FIELDS)
            if unknown:
                errors.append(f"{label}: unknown frontmatter field(s): {sorted(unknown)}")
            p = _parse(path)
            if p is None:
                errors.append(
                    f"{label}: frontmatter is invalid, name or description is empty,"
                    f" or schema_version is not {SCHEMA_VERSION}"
                )
                continue
            errors += _check_behavior(label, p["temperature"], p["tools"], known)
    return errors


def unlisted_model_warnings() -> list[str]:
    """Persona model overrides the model menu does not list — a soft runtime
    warning on /health, NEVER a lint error: SKEIN_MODELS is env, and
    validating against an env that CI does not share would make lint results
    depend on deployment (the _known_tool_names rule). Empty when no menu is
    configured, because an absent menu constrains nothing. Names the persona
    and the field only — the model string itself stays in the file."""
    from .. import config

    if not config.MODELS:
        return []
    out = []
    for slug in sorted(_persona_files()):
        model = behavior(slug)["model"]
        if model and model not in config.MODELS:
            out.append(f"persona {slug} sets a model that the SKEIN_MODELS menu does not list.")
    return out


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

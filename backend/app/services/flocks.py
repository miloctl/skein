"""Flocks: named groups of bench personas called into chat with one message.
The message fans out to every member, each answers under its own name, and an
optional synthesis merges the sections. See docs/FLOCKS.md.

A flock is metadata over personas that already exist — it holds no prompt of
its own, and it is never an identity that writes. The members are the
identities; their slugs are already in the authority matrix and trust scores.

Files are edited like code (the playbooks precedent), stock plus an optional
SKEIN_FLOCKS_DIR overlay. Runtime parsing is lenient the way services/
personas.py is (a malformed file drops off the roster rather than 500ing
chat); validate_all() is the strict pass, wired into scripts/lint.sh so the
same file fails CI instead of silently vanishing.
"""

import json
import re
from pathlib import Path

import yaml

from .. import config, db
from . import personas

FLOCKS_DIR = Path(__file__).resolve().parent.parent.parent / "flocks"

# same charset as a persona slug: both name an agent head in chat, and
# services/personas.py holds the matching pattern
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")

# A flock turn is one model call per member plus an optional synthesis. The
# ceiling is a cost ceiling, not a layout one: 5 members is 5x the spend of a
# normal turn on one message, and nothing else in the product multiplies spend
# by a number the operator writes in a file.
MIN_MEMBERS = 2
MAX_MEMBERS = 4
SCHEMA_VERSION = 1
_FIELDS = {"schema_version", "name", "description", "emoji", "members", "synthesis"}


def _flock_dirs() -> list[Path]:
    """Stock first, overlay second — later wins."""
    dirs = [FLOCKS_DIR]
    overlay = config.FLOCKS_OVERLAY
    if overlay and overlay.is_dir() and overlay.resolve() != FLOCKS_DIR.resolve():
        dirs.append(overlay)
    return [d for d in dirs if d.is_dir()]


def _flock_files() -> dict[str, Path]:
    """slug -> path across the stock dir and the SKEIN_FLOCKS_DIR overlay. The
    overlay wins a slug collision, so a deployment can re-cast a stock flock
    without editing the repo."""
    files: dict[str, Path] = {}
    for d in _flock_dirs():
        for path in sorted(d.glob("*.yaml")):
            if _SLUG.match(path.stem):
                files[path.stem] = path
    return files


def _parse(path: Path, bench: set[str]) -> dict | None:
    """One flock file, or None when anything about it is wrong. Every None
    here has a matching loud error in validate_all() — keep the two in step,
    or a file drops off the roster with no CI failure to explain it."""
    slug = path.stem
    if not _SLUG.match(slug):
        return None
    # a flock slug that is also a persona slug would merge two heads in every
    # by-name rollup: synthesis logs usage under the FLOCK slug (routes/
    # chat.py::_log_usage), so the flock's spend would land on the persona's
    # row in /api/usage, and the persona's trust score would answer for it
    if slug in bench:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        return None
    if set(data) - _FIELDS:
        return None
    name = str(data.get("name") or "").strip()
    description = str(data.get("description") or "").strip()
    if not name or not description:
        return None
    members = data.get("members")
    if not isinstance(members, list):
        return None
    members = [str(m).strip() for m in members]
    if not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        return None
    if len(set(members)) != len(members):
        return None
    if any(m not in bench for m in members):
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": slug,
        "name": name,
        "description": description,
        "emoji": str(data.get("emoji") or "🪿").strip(),
        "members": members,
        "synthesis": bool(data.get("synthesis", False)),
    }


def _member_card(slug: str) -> dict:
    """A member as the roster surfaces and the chat mastheads show it. The
    fallback is reachable: personas.bench_slugs() globs filenames without
    parsing, so a member whose .md file is malformed passes the membership
    check here and fails only when get_persona reads it. Showing the bare slug
    beats dropping the whole flock over one bad file that the persona
    validator already reports."""
    try:
        p = personas.get_persona(slug)
    except ValueError:
        return {"slug": slug, "name": slug, "emoji": "🎭", "vibe": ""}
    return {"slug": slug, "name": p["name"], "emoji": p["emoji"], "vibe": p["vibe"]}


def member_cards(slugs: list[str]) -> list[dict]:
    """Display cards for the slugs get_flock returns — the chat fan-out needs
    a name and an emoji per section masthead."""
    return [_member_card(s) for s in slugs]


def list_flocks() -> list[dict]:
    """The roster, members resolved to name and emoji for display."""
    bench = personas.bench_slugs()
    out = []
    for _slug, path in sorted(_flock_files().items()):
        f = _parse(path, bench)
        if f:
            out.append({**f, "members": [_member_card(m) for m in f["members"]]})
    return out


def get_flock(slug: str) -> dict:
    """One flock, members as plain slugs (the chat fan-out builds agents from
    them). Raises for an unknown slug, the way personas.get_persona does."""
    if _SLUG.match(slug):
        f = None
        path = _flock_files().get(slug)
        if path is not None and path.is_file():
            f = _parse(path, personas.bench_slugs())
        if f:
            return f
        raise ValueError(f"no flock '{slug}' — available: {_roster()}")
    # the rejected value is NOT echoed: an off-charset slug is arbitrary
    # caller text, and CLAUDE.md holds that an error never reflects it back
    raise ValueError(f"that is not a flock slug — available: {_roster()}")


def _roster() -> str:
    return ", ".join(f["slug"] for f in list_flocks()) or "none installed"


def validate_all() -> list[str]:
    """Every check _parse forgives, as loud errors — run by scripts/lint.sh so
    a malformed flock fails CI instead of silently vanishing from the roster."""
    errors: list[str] = []
    bench = personas.bench_slugs()
    for d in _flock_dirs():
        for path in sorted(d.glob("*.yaml")):
            label = path.name if d == FLOCKS_DIR else f"{path.name} (overlay)"
            if not _SLUG.match(path.stem):
                errors.append(f"{label}: slug must match {_SLUG.pattern}")
                continue
            if path.stem in bench:
                errors.append(
                    f"{label}: {path.stem!r} is also a persona slug — rename the flock,"
                    " or the two share one agent identity in usage and trust"
                )
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                errors.append(f"{label}: not valid YAML ({exc})")
                continue
            if not isinstance(data, dict):
                errors.append(f"{label}: expected an object with name, description, and members")
                continue
            if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
                errors.append(f"{label}: schema_version must be {SCHEMA_VERSION}")
            unknown = sorted(set(data) - _FIELDS)
            if unknown:
                errors.append(f"{label}: unknown top-level fields: {unknown}")
            if not str(data.get("name") or "").strip():
                errors.append(f"{label}: name is empty")
            if not str(data.get("description") or "").strip():
                errors.append(f"{label}: description is empty")
            errors += _check_members(label, data.get("members"), bench)
    return errors


def _check_members(label: str, members: object, bench: set[str]) -> list[str]:
    if not isinstance(members, list):
        errors = [f"{label}: members must be a list of persona slugs"]
        return errors
    errors = []
    names = [str(m).strip() for m in members]
    if not MIN_MEMBERS <= len(names) <= MAX_MEMBERS:
        errors.append(
            f"{label}: members has {len(names)} entries —"
            f" a flock takes {MIN_MEMBERS} to {MAX_MEMBERS}"
        )
    for m in sorted({m for m in names if names.count(m) > 1}):
        errors.append(f"{label}: members repeats {m!r} — each member answers one time")
    for m in names:
        if m and m not in bench:
            errors.append(f"{label}: members names {m!r}, which is not a persona on the bench")
        elif not m:
            errors.append(f"{label}: members has an empty entry")
    return errors


def record_trace(
    thread_id: str, user: str, flock: str, members: list[dict], synthesis: dict | None = None
) -> int:
    """One flock turn, as the diamond view reads it. Called from the chat
    route's close path, which also runs on a cancelled stream — a turn the
    user stopped still produced spend, so it still owes a trace."""
    return db.execute(
        "INSERT INTO flock_traces (thread_id, user, flock, members, synthesis, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            thread_id,
            user,
            flock,
            json.dumps(members),
            json.dumps(synthesis) if synthesis else None,
            db.now(),
        ),
    )


def list_traces(owner: str, thread_id: str = "", flock: str = "", limit: int = 20) -> list[dict]:
    """One person's own flock runs, newest first. members/synthesis are
    decoded here so no caller parses the JSON twice.

    `owner` is POSITIONAL and has no default on purpose. A trace names the
    person who ran it and the thread id their chat transcript is keyed by
    (routes/chat.py scopes those per owner), and the row carries every
    member's receipts and token counts. With no filter, any identified caller
    read every other person's runs — which the route's own comment already
    said must not happen. A default would make the next caller's omission
    silent, and this is the only caller there is.
    """
    where: list[str] = ["user = ?"]
    params: list[str | int] = [owner]
    if thread_id:
        where.append("thread_id = ?")
        params.append(thread_id)
    if flock:
        where.append("flock = ?")
        params.append(flock)
    sql = "SELECT * FROM flock_traces"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 100)))
    rows = db.query(sql, tuple(params))
    for r in rows:
        r["members"] = json.loads(r["members"])
        r["synthesis"] = json.loads(r["synthesis"]) if r["synthesis"] else None
    return rows


if __name__ == "__main__":  # the lint.sh gate: exit 1 with every error listed
    import sys

    problems = validate_all()
    for problem in problems:
        print(f"flock: {problem}")
    sys.exit(1 if problems else 0)

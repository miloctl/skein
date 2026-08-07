"""Team context pack: a versioned, deterministic org-brain — active decisions,
engagement state, lessons, conventions — assembled from the record. Emitted as
markdown so any agent (Claude Code, custom, MCP) can load the team's context
without asking anyone. Versions only bump when the content actually changes."""

import hashlib
import sqlite3
from pathlib import Path

from .. import config, db
from . import scope
from .scope import WORKSPACE_ONLY


def build_pack(crew_id: int = 0) -> str:
    """Assemble the pack body. Pure read — same data in, same text out.

    The body is always the workspace tier, because a crew member reads the
    workspace too. `crew_id` APPENDS that crew's own rows as a final section
    rather than filtering the base, so the team pack stays a prefix of every
    crew pack and a reader can tell which half is shared.
    """
    from .portfolio import engagement_health

    lines = ["# Team context pack", ""]

    humans = db.query(
        "SELECT name FROM users WHERE kind = 'human' AND active = 1"
        " AND name != 'anonymous' ORDER BY name"
    )
    agents = db.query("SELECT name FROM users WHERE kind = 'agent' AND active = 1 ORDER BY name")
    lines += [
        "## Team",
        "Humans: " + (", ".join(u["name"] for u in humans) or "none recorded"),
        "Agents: " + (", ".join(u["name"] for u in agents) or "none recorded"),
        "",
    ]

    lines.append("## Active engagements")
    health = engagement_health()
    for h in health:
        lines.append(
            f"- **{h['name']}** ({h['status']}, lead: {h['lead'] or 'unset'},"
            f" health: {h['health']})"
        )
    if not health:
        lines.append("- none")
    lines.append("")

    lines.append("## Standing decisions (cite these; supersede, don't ignore)")
    decisions = db.query(
        f"SELECT * FROM decisions WHERE status = 'active' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " ORDER BY id DESC LIMIT 25"
    )
    for d in decisions:
        line = f"- **{d['title']}** — {d['decision']}"
        if d["review_by"]:
            line += f" *(review by {d['review_by']})*"
        lines.append(line)
    if not decisions:
        lines.append("- none recorded")
    stale = db.query(
        f"SELECT * FROM decisions WHERE status = 'stale' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " ORDER BY id DESC LIMIT 5"
    )
    if stale:
        lines.append("")
        lines.append("Stale (past review-by — confirm before relying on):")
        lines += [f"- #{d['id']} {d['title']}" for d in stale]
    lines.append("")

    lines.append("## Lessons the team already paid for")
    lessons = db.query(
        f"SELECT * FROM lessons WHERE {WORKSPACE_ONLY} ORDER BY id DESC LIMIT 15"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
    )
    lines += [
        f"- [{les['project_class']}] {les['lesson']}"
        + (f" → {les['recommendation']}" if les["recommendation"] else "")
        for les in lessons
    ] or ["- none recorded"]
    lines.append("")

    lines.append("## Conventions")
    conventions = db.query(
        f"SELECT * FROM notes WHERE topic LIKE 'convention%' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " ORDER BY id DESC LIMIT 15"
    )
    lines += [f"- {n['topic']}: {n['content']}" for n in conventions] or [
        "- none recorded (save notes with topic 'convention: ...' to add)"
    ]
    lines.append("")

    lines.append("## Open questions nobody has answered")
    questions = db.query(
        f"SELECT * FROM questions WHERE status = 'open' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " ORDER BY id DESC LIMIT 10"
    )
    lines += [f"- #{q['id']} {q['question']} (asked by {q['asked_by']})" for q in questions] or [
        "- none"
    ]
    lines.append("")

    lines.append("## How to plug in")
    lines += [
        "- REST API at /api (X-User header or a personal sk-skein- key)",
        "- MCP server: `python -m app.mcp_server` (tools + this pack as a resource)",
        "- CLI: `skein capture|my-day|tasks|context`",
    ]
    if crew_id:
        lines += _crew_section(crew_id)
    return "\n".join(lines)


def build_engagement_pack(engagement_id: int, viewer: scope.Viewer = scope.NOBODY) -> str:
    """Scoped pack for ONE engagement — what a delegated agent needs and
    nothing else: cheaper tokens, less noise, cleaner blast radius. Generated
    on demand (unversioned; versioning is for the org-brain)."""
    # Filtered by the CALLER. An agent tool and the MCP server pass NOBODY,
    # which is the workspace tier — the rule those surfaces need. A crew
    # member gets their own engagement, which locked they could see listed and
    # not open (services/handoff.py has the same pair).
    efrag, ep = scope.visible_filter(viewer, "engagements")
    mfrag, mp = scope.visible_filter(viewer, "milestones")
    tfrag, tp = scope.visible_filter(viewer, "tasks", "t")
    lfrag, lp = scope.visible_filter(viewer, "lessons")
    eng = db.query_one(
        f"SELECT * FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (engagement_id, *ep),
    )
    if not eng:
        raise scope.missing("engagements", engagement_id)
    lines = [
        f"# Engagement context: {eng['name']}",
        "",
        f"*Class: {eng['project_class']} · kind: {eng['kind']} · status: {eng['status']}"
        f" · lead: {eng['lead'] or 'unset'}*",
        "",
    ]
    if eng["outcome"]:
        lines += ["## Intended outcome", eng["outcome"], ""]
    if eng["kind"] == "experiment":
        lines += [
            "## Experiment frame",
            f"- Timebox ends: {eng['timebox_end'] or 'unset'}",
            f"- Kill criteria: {eng['kill_criteria'] or 'unset'}",
            "- An invalidated hypothesis concluded on time is a SUCCESS.",
            "",
        ]
    lines.append("## Milestones")
    milestones = db.query(
        f"SELECT * FROM milestones WHERE engagement_id = ? AND {mfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " ORDER BY due_date IS NULL, due_date",
        (engagement_id, *mp),
    )
    lines += [
        f"- [{m['status']}] #{m['id']} {m['title']}"
        + (f" — due {m['due_date']}" if m["due_date"] else "")
        for m in milestones
    ] or ["- none"]
    lines.append("")
    lines.append("## Open tasks")
    tasks = db.query(
        f"SELECT t.* FROM tasks t WHERE {tfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " AND (t.engagement_id = ? OR t.milestone_id IN (SELECT id FROM milestones WHERE engagement_id = ?))"
        " AND t.status != 'done'"
        " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END",
        (*tp, engagement_id, engagement_id),
    )
    for t in tasks:
        line = f"- [{t['status']}/{t['priority']}] #{t['id']} {t['title']}"
        if t["assignee"]:
            line += f" (@{t['assignee']})"
        if t["waiting_on_type"]:
            line += f" — waiting on {t['waiting_on_type']} #{t['waiting_on_id']}"
        lines.append(line)
    if not tasks:
        lines.append("- none")
    lines.append("")
    from .portfolio import _linked_blockers

    blockers = _linked_blockers(engagement_id, viewer)
    lines.append("## Unresolved blockers")
    lines += [f"- [{b['status']}/{b['impact']}] #{b['id']} {b['title']}" for b in blockers] or [
        "- none"
    ]
    lines.append("")
    lines.append("## Lessons from this class")
    lessons = db.query(
        f"SELECT * FROM lessons WHERE {lfrag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " AND (engagement_id = ? OR project_class = ?) ORDER BY id DESC LIMIT 10",
        (*lp, engagement_id, eng["project_class"]),
    )
    lines += [
        f"- {les['lesson']}" + (f" → {les['recommendation']}" if les["recommendation"] else "")
        for les in lessons
    ] or ["- none recorded"]
    lines.append("")
    lines.append("## Standing decisions that bind this work")
    decisions = db.query(
        f"SELECT * FROM decisions WHERE status = 'active' AND {WORKSPACE_ONLY}"  # noqa: S608 — scope.WORKSPACE_ONLY is a module constant
        " ORDER BY id DESC LIMIT 10"
    )
    lines += [f"- **{d['title']}** — {d['decision']}" for d in decisions] or ["- none"]
    return "\n".join(lines)


def _crew_section(crew_id: int) -> list[str]:
    """One crew's own rows, appended to the workspace body.

    A crew pack is handed to an agent and written to disk, so it holds the
    CREW tier and never the private one — private has no reader but its
    author, and a pack has no author to be.
    """
    from . import crews

    crew = crews.get_crew(crew_id)  # raises NotFound on an unknown id
    scoped = "visibility = 'crew' AND crew_id = ?"
    lines = ["", f"## {crew['name']} only", ""]
    if crew["summary"]:
        lines += [crew["summary"], ""]
    for heading, sql, fmt in (
        (
            "Decisions",
            f"SELECT * FROM decisions WHERE status = 'active' AND {scoped} ORDER BY id DESC LIMIT 25",  # noqa: S608 — `scoped` is a module-local literal with one bound mark
            lambda r: f"- **{r['title']}** — {r['decision']}",
        ),
        (
            "Conventions",
            f"SELECT * FROM notes WHERE topic LIKE 'convention%' AND {scoped} ORDER BY id DESC LIMIT 15",  # noqa: S608 — same
            lambda r: f"- {r['topic']}: {r['content']}",
        ),
        (
            "Open questions",
            f"SELECT * FROM questions WHERE status = 'open' AND {scoped} ORDER BY id DESC LIMIT 10",  # noqa: S608 — same
            lambda r: f"- #{r['id']} {r['question']} (asked by {r['asked_by']})",
        ),
        (
            "Open work",
            f"SELECT * FROM tasks WHERE status != 'done' AND {scoped} ORDER BY id DESC LIMIT 25",  # noqa: S608 — same
            lambda r: (
                f"- [{r['status']}] #{r['id']} {r['title']} (@{r['assignee'] or 'unassigned'})"
            ),
        ),
    ):
        rows = db.query(sql, (crew_id,))
        lines += [f"### {heading}"] + ([fmt(r) for r in rows] or ["- none"]) + [""]
    return lines


def latest_pack(crew_id: int = 0) -> dict | None:
    # IFNULL, matching migration 005's index: the team pack stores crew_id NULL
    # and `crew_id = 0` matches no row in SQL
    return db.query_one(
        "SELECT * FROM context_packs WHERE IFNULL(crew_id, 0) = ?"
        " ORDER BY version DESC, id DESC LIMIT 1",
        (crew_id,),
    )


def publish_pack(*, actor: str = "system", crew_id: int = 0) -> dict:
    """Version the pack; no-op if nothing changed since the last version.

    Each crew versions independently — v3 of the Platform pack has nothing to
    do with v3 of the team pack, and a shared counter would bump every crew's
    version whenever any one of them published.
    """
    if crew_id:
        # membership, before the body is built: _crew_section reads the crew's
        # own rows, and a non-member must not get them back in an error either
        from . import crews

        crews.assert_writable(crew_id, actor)
    body = build_pack(crew_id)
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    last = latest_pack(crew_id)
    if last and last["content_hash"] == digest:
        return {"version": last["version"], "hash": digest, "changed": False}
    version = (last["version"] + 1) if last else 1
    content = body.replace(
        "# Team context pack",
        f"# Team context pack\n\n*v{version} · generated {db.now()} · hash {digest}*",
        1,
    )
    try:
        db.execute(
            "INSERT INTO context_packs (version, content, content_hash, created_by,"
            " created_at, crew_id) VALUES (?, ?, ?, ?, ?, ?)",
            (version, content, digest, actor, db.now(), crew_id or None),
        )
    except sqlite3.IntegrityError:
        # concurrent publisher won the version — serve theirs
        last = latest_pack(crew_id)
        if last is None:
            # ValueError → 400 via the global handler; RuntimeError was a 500
            raise ValueError("context pack vanished during concurrent publish — retry") from None
        return {"version": last["version"], "hash": last["content_hash"], "changed": False}
    pack_dir = Path(config.DATA_DIR) / "artifacts" / "context-pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    # the crew id is in the filename, not only the row: two crews at v3 would
    # otherwise overwrite one file and the artifact would name the wrong pack
    stem = f"crew{crew_id}-v{version}" if crew_id else f"v{version}"
    path = pack_dir / f"context-pack-{stem}.md"
    path.write_text(content)
    db.log_activity(actor, "publish_context_pack", f"{stem} ({digest})")
    return {"version": version, "hash": digest, "changed": True, "path": str(path)}


def get_pack(
    *, actor: str = "system", crew_id: int = 0, viewer: scope.Viewer = scope.NOBODY
) -> dict:
    """Latest published pack, publishing v1 on first call.

    The crew pack is gated on the VIEWER, not on `actor`. A crew pack's body
    is _crew_section's decisions, conventions, questions and tasks verbatim,
    which makes this a scoped READ — and docs/VISIBILITY.md decision 3 sets
    that bar at strong identity. Resolved from the bare name, `X-User: ava`
    with no credential read any crew's pack, because in trusted-header mode
    the name is whatever the caller typed. Viewer blanks a weak identity, so
    its crew list is empty and this refuses.

    The TEAM pack (crew_id = 0) is workspace content and stays open to every
    CurrentUser, which is why the bar lives here and not on the route.
    """
    # membership, NOT assert_writable: that one also refuses a deactivated
    # crew, and a retired crew's members must keep reading the pack they
    # already have. NotFound, so a non-member cannot enumerate crew ids.
    if crew_id and crew_id not in viewer.crew_ids:
        raise db.NotFound(f"no context pack for crew #{crew_id}")
    last = latest_pack(crew_id)
    if not last:
        publish_pack(actor=actor, crew_id=crew_id)
        last = latest_pack(crew_id)
        if last is None:
            raise ValueError("context pack publish produced no pack — retry")
    return {
        "version": last["version"],
        "hash": last["content_hash"],
        "created_at": last["created_at"],
        "content": last["content"],
        "crew_id": last["crew_id"],
    }

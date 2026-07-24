"""Team context pack: a versioned, deterministic org-brain — active decisions,
engagement state, lessons, conventions — assembled from the record. Emitted as
markdown so any agent (Claude Code, custom, MCP) can load the team's context
without asking anyone. Versions only bump when the content actually changes."""

import hashlib
from pathlib import Path

from .. import config, db


def build_pack() -> str:
    """Assemble the pack body. Pure read — same data in, same text out."""
    from .portfolio import engagement_health

    lines = ["# Team context pack", ""]

    humans = db.query("SELECT name FROM users WHERE kind = 'human' AND active = 1"
                      " AND name != 'anonymous' ORDER BY name")
    agents = db.query("SELECT name FROM users WHERE kind = 'agent' AND active = 1"
                      " ORDER BY name")
    lines += ["## Team",
              "Humans: " + (", ".join(u["name"] for u in humans) or "none recorded"),
              "Agents: " + (", ".join(u["name"] for u in agents) or "none recorded"), ""]

    lines.append("## Active engagements")
    health = engagement_health()
    for h in health:
        lines.append(f"- **{h['name']}** ({h['status']}, lead: {h['lead'] or 'unset'},"
                     f" health: {h['health']})")
    if not health:
        lines.append("- none")
    lines.append("")

    lines.append("## Standing decisions (cite these; supersede, don't ignore)")
    decisions = db.query(
        "SELECT * FROM decisions WHERE status = 'active' ORDER BY id DESC LIMIT 25")
    for d in decisions:
        line = f"- **{d['title']}** — {d['decision']}"
        if d["review_by"]:
            line += f" *(review by {d['review_by']})*"
        lines.append(line)
    if not decisions:
        lines.append("- none recorded")
    stale = db.query("SELECT * FROM decisions WHERE status = 'stale' ORDER BY id DESC LIMIT 5")
    if stale:
        lines.append("")
        lines.append("Stale (past review-by — confirm before relying on):")
        lines += [f"- #{d['id']} {d['title']}" for d in stale]
    lines.append("")

    lines.append("## Lessons the team already paid for")
    lessons = db.query("SELECT * FROM lessons ORDER BY id DESC LIMIT 15")
    lines += [f"- [{l['project_class']}] {l['lesson']}"
              + (f" → {l['recommendation']}" if l["recommendation"] else "")
              for l in lessons] or ["- none recorded"]
    lines.append("")

    lines.append("## Conventions")
    conventions = db.query(
        "SELECT * FROM notes WHERE topic LIKE 'convention%' ORDER BY id DESC LIMIT 15")
    lines += [f"- {n['topic']}: {n['content']}" for n in conventions] \
        or ["- none recorded (save notes with topic 'convention: ...' to add)"]
    lines.append("")

    lines.append("## Open questions nobody has answered")
    questions = db.query(
        "SELECT * FROM questions WHERE status = 'open' ORDER BY id DESC LIMIT 10")
    lines += [f"- #{q['id']} {q['question']} (asked by {q['asked_by']})"
              for q in questions] or ["- none"]
    lines.append("")

    lines.append("## How to plug in")
    lines += [
        "- REST API at /api (X-User header or a personal sk-strands- key)",
        "- MCP server: `python -m app.mcp_server` (tools + this pack as a resource)",
        "- CLI: `strands capture|my-day|tasks|context`",
    ]
    return "\n".join(lines)


def latest_pack() -> dict | None:
    return db.query_one(
        "SELECT * FROM context_packs ORDER BY version DESC LIMIT 1")


def publish_pack(*, actor: str = "system") -> dict:
    """Version the pack; no-op if nothing changed since the last version."""
    body = build_pack()
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    last = latest_pack()
    if last and last["content_hash"] == digest:
        return {"version": last["version"], "hash": digest, "changed": False}
    version = (last["version"] + 1) if last else 1
    content = body.replace(
        "# Team context pack",
        f"# Team context pack\n\n*v{version} · generated {db.now()} · hash {digest}*", 1)
    db.execute(
        "INSERT INTO context_packs (version, content, content_hash, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (version, content, digest, actor, db.now()),
    )
    pack_dir = Path(config.DATA_DIR) / "artifacts" / "context-pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    path = pack_dir / f"context-pack-v{version}.md"
    path.write_text(content)
    db.log_activity(actor, "publish_context_pack", f"v{version} ({digest})")
    return {"version": version, "hash": digest, "changed": True, "path": str(path)}


def get_pack(*, actor: str = "system") -> dict:
    """Latest published pack, publishing v1 on first call."""
    last = latest_pack()
    if not last:
        publish_pack(actor=actor)
        last = latest_pack()
    return {"version": last["version"], "hash": last["content_hash"],
            "created_at": last["created_at"], "content": last["content"]}

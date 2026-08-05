"""Playbooks: YAML templates per project class, instantiated deterministically
into an engagement + milestones + tasks + kickoff events. Relevant lessons from
past engagements of the same class are attached as a kickoff note."""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .. import config, db
from . import collab, engagements, schedule, work

PLAYBOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "playbooks"


_SLUG = re.compile(r"^[a-z0-9_-]+$")


def _playbook_files() -> dict[str, Path]:
    """slug -> path across the stock dir and the SKEIN_PLAYBOOKS_DIR overlay.
    The overlay wins a slug collision, so a deployment can tailor a stock
    playbook without editing the repo. A stem the slug charset rejects never
    enters the map — it could never be fetched, so listing it would be a
    roster entry with no playbook behind it."""
    files: dict[str, Path] = {}
    dirs = [PLAYBOOKS_DIR]
    overlay = config.PLAYBOOKS_OVERLAY
    if overlay and overlay.is_dir():
        dirs.append(overlay)
    for d in dirs:
        for path in sorted(d.glob("*.yaml")):
            if _SLUG.match(path.stem):
                files[path.stem] = path
    return files


def list_playbooks() -> list[dict]:
    """Lenient the way the persona loader is: one malformed overlay file drops
    off the roster instead of taking down every playbook surface — the stock
    files are CI-gated, but overlay files are live operator content."""
    out = []
    for slug, path in sorted(_playbook_files().items()):
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        out.append(
            {
                "slug": slug,
                "name": data.get("name", slug),
                "description": data.get("description", ""),
                "milestones": len(data.get("milestones", [])),
            }
        )
    return out


def get_playbook(slug: str) -> dict:
    if not _SLUG.match(slug):  # path traversal guard — slug becomes a filename
        raise ValueError("playbook slug must be lowercase letters, digits, - or _")
    path = _playbook_files().get(slug)
    if path is None or not path.exists():
        raise ValueError(
            f"no playbook '{slug}' — available: {[p['slug'] for p in list_playbooks()]}"
        )
    try:
        pb = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"playbook '{slug}' is malformed ({type(exc).__name__})") from exc
    if not isinstance(pb, dict):
        raise ValueError(f"playbook '{slug}' is malformed (expected a mapping)")
    for m in pb.get("milestones", []):
        if not isinstance(m, dict) or "title" not in m:
            raise ValueError(f"playbook '{slug}' has a milestone without a title")
    return pb


def instantiate(
    slug: str,
    engagement_name: str,
    lead: str = "",
    start_date: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    pb = get_playbook(slug)
    start = date.fromisoformat(start_date) if start_date else datetime.now(UTC).date()
    with db.transaction():
        return _instantiate(pb, slug, engagement_name, lead, start, actor=actor, origin=origin)


def _instantiate(
    pb: dict,
    slug: str,
    engagement_name: str,
    lead: str,
    start: date,
    *,
    actor: str,
    origin: str,
) -> dict:
    eng = engagements.create_engagement(
        name=engagement_name,
        project_class=pb.get("project_class", slug),
        summary=pb.get("description", ""),
        lead=lead,
        actor=actor,
        origin=origin,
    )

    created: dict[str, Any] = {"engagement": eng, "milestones": [], "tasks": [], "events": []}
    for m in pb.get("milestones", []):
        due = (start + timedelta(days=int(m.get("due_after_days", 7)))).isoformat()
        mil = work.create_milestone(
            title=m["title"],
            description=m.get("description", ""),
            project=engagement_name,
            owner=lead,
            due_date=due,
            actor=actor,
            origin=origin,
        )
        created["milestones"].append(mil)
        for t in m.get("tasks", []):
            task = work.create_task(
                title=t if isinstance(t, str) else t["title"],
                description="" if isinstance(t, str) else t.get("description", ""),
                milestone_id=mil["id"],
                priority="medium" if isinstance(t, str) else t.get("priority", "medium"),
                actor=actor,
                origin=origin,
            )
            created["tasks"].append(task)

    for r in pb.get("rituals", []):
        starts = (start + timedelta(days=int(r.get("day_offset", 0)))).isoformat()
        evt = schedule.schedule_event(
            title=f"{r['title']} — {engagement_name}",
            starts_at=f"{starts}T{r.get('time', '10:00')}",
            description=r.get("description", ""),
            attendees=lead,
            actor=actor,
            origin=origin,
        )
        created["events"].append(evt)

    lessons = engagements.list_lessons(project_class=pb.get("project_class", slug))
    if lessons:
        content = "\n".join(
            f"- {les['lesson']}" + (f" → {les['recommendation']}" if les["recommendation"] else "")
            for les in lessons[:10]
        )
        collab.save_note(
            topic=f"kickoff-lessons-{engagement_name}",
            content=f"Lessons from past {pb.get('project_class', slug)} engagements:\n{content}",
            author=actor,
            actor=actor,
            origin=origin,
        )

    db.log_activity(actor, "instantiate_playbook", f"{slug} -> {engagement_name}")
    return created

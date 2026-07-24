"""Playbooks: YAML templates per project class, instantiated deterministically
into an engagement + milestones + tasks + kickoff events. Relevant lessons from
past engagements of the same class are attached as a kickoff note."""

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from .. import db
from . import collab, engagements, schedule, work

PLAYBOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "playbooks"


def list_playbooks() -> list[dict]:
    out = []
    for path in sorted(PLAYBOOKS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        out.append({
            "slug": path.stem,
            "name": data.get("name", path.stem),
            "description": data.get("description", ""),
            "milestones": len(data.get("milestones", [])),
        })
    return out


_SLUG = re.compile(r"^[a-z0-9_-]+$")


def get_playbook(slug: str) -> dict:
    if not _SLUG.match(slug):  # path traversal guard — slug becomes a filename
        raise ValueError(f"invalid playbook slug '{slug}'")
    path = PLAYBOOKS_DIR / f"{slug}.yaml"
    if not path.exists():
        raise ValueError(f"no playbook '{slug}'; available: {[p['slug'] for p in list_playbooks()]}")
    pb = yaml.safe_load(path.read_text())
    if not isinstance(pb, dict):
        raise ValueError(f"playbook '{slug}' is malformed (expected a mapping)")
    for m in pb.get("milestones", []):
        if not isinstance(m, dict) or "title" not in m:
            raise ValueError(f"playbook '{slug}' has a milestone without a title")
    return pb


def instantiate(slug: str, engagement_name: str, lead: str = "",
                start_date: str = "", *, actor: str = "system",
                origin: str = "human") -> dict:
    pb = get_playbook(slug)
    start = (date.fromisoformat(start_date) if start_date
             else datetime.now(timezone.utc).date())

    eng = engagements.create_engagement(
        name=engagement_name, project_class=pb.get("project_class", slug),
        summary=pb.get("description", ""), lead=lead, actor=actor, origin=origin,
    )

    created: dict[str, Any] = {"engagement": eng, "milestones": [], "tasks": [], "events": []}
    for m in pb.get("milestones", []):
        due = (start + timedelta(days=int(m.get("due_after_days", 7)))).isoformat()
        mil = work.create_milestone(
            title=m["title"], description=m.get("description", ""),
            project=engagement_name, owner=lead, due_date=due,
            actor=actor, origin=origin,
        )
        created["milestones"].append(mil)
        for t in m.get("tasks", []):
            task = work.create_task(
                title=t if isinstance(t, str) else t["title"],
                description="" if isinstance(t, str) else t.get("description", ""),
                milestone_id=mil["id"],
                priority="medium" if isinstance(t, str) else t.get("priority", "medium"),
                actor=actor, origin=origin,
            )
            created["tasks"].append(task)

    for r in pb.get("rituals", []):
        starts = (start + timedelta(days=int(r.get("day_offset", 0)))).isoformat()
        evt = schedule.schedule_event(
            title=f"{r['title']} — {engagement_name}",
            starts_at=f"{starts}T{r.get('time', '10:00')}",
            description=r.get("description", ""), attendees=lead,
            actor=actor, origin=origin,
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
            author=actor, actor=actor, origin=origin,
        )

    db.log_activity(actor, "instantiate_playbook", f"{slug} -> {engagement_name}")
    return created

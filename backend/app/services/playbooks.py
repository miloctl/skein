"""Playbooks: YAML templates per project class, instantiated deterministically
into an engagement + milestones + tasks + kickoff events. Relevant lessons from
past engagements of the same class are attached as a kickoff note.

The plan is also SNAPSHOT at instantiate, which is what lets close-out say
what changed. Nothing else can reconstruct it: milestones move, tasks are
added and deleted, and a ritual that never happened leaves no row at all, so
by the time an engagement closes the plan it started with is gone.
"""

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .. import config, db
from . import collab, engagements, schedule, scope, wording, work

PLAYBOOKS_DIR = config.STOCK_DIR / "playbooks"


_SLUG = re.compile(r"^[a-z0-9_-]+$")
SCHEMA_VERSION = 1
DEFINITION_DIGEST_VERSION = "v2"
_TOP_LEVEL = {
    "schema_version",
    "name",
    "description",
    "project_class",
    "milestones",
    "rituals",
    "workflow",
}


def _schema_errors(data: object, workflow_actions: set[str] | None = None) -> list[str]:
    if not isinstance(data, dict):
        return ["expected an object"]
    errors = []
    version = data.get("schema_version", SCHEMA_VERSION)
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    unknown = sorted(set(data) - _TOP_LEVEL)
    if unknown:
        errors.append(f"unknown top-level fields: {unknown}")
    if not str(data.get("name") or "").strip():
        errors.append("name is empty")
    milestones = data.get("milestones", [])
    if not isinstance(milestones, list):
        errors.append("milestones must be a list")
    else:
        for index, milestone in enumerate(milestones):
            if not isinstance(milestone, dict) or not str(milestone.get("title") or "").strip():
                errors.append(f"milestone {index + 1} has no title")
                continue
            tasks = milestone.get("tasks", [])
            if not isinstance(tasks, list):
                errors.append(f"milestone {index + 1} tasks must be a list")
                continue
            for task_index, task in enumerate(tasks):
                if isinstance(task, str):
                    valid = bool(task.strip())
                else:
                    valid = isinstance(task, dict) and bool(str(task.get("title") or "").strip())
                if not valid:
                    errors.append(f"milestone {index + 1} task {task_index + 1} has no title")
    rituals = data.get("rituals", [])
    if not isinstance(rituals, list):
        errors.append("rituals must be a list")
    else:
        for index, ritual in enumerate(rituals):
            if not isinstance(ritual, dict) or not str(ritual.get("title") or "").strip():
                errors.append(f"ritual {index + 1} has no title")
    if "workflow" in data:
        try:
            from ..public.workflow import validate_workflow_actions, validate_workflow_shape

            validate_workflow_shape(data["workflow"])
            if workflow_actions is not None:
                validate_workflow_actions(data["workflow"], workflow_actions)
        except Exception:
            errors.append("workflow steps are not valid")
    return errors


def _legacy_errors(data: object) -> list[str]:
    """Checks that the pre-schema reader applied to unversioned files."""
    if not isinstance(data, dict):
        return ["expected an object"]
    errors: list[str] = []
    milestones = data.get("milestones", [])
    if not isinstance(milestones, list):
        return ["milestones must be a list"]
    for index, milestone in enumerate(milestones):
        if not isinstance(milestone, dict) or "title" not in milestone:
            errors.append(f"milestone {index + 1} has no title")
    return errors


def _content_errors(data: object, workflow_actions: set[str] | None = None) -> list[str]:
    """Use strict version 1 rules only when the file opts in explicitly."""
    if isinstance(data, dict) and "schema_version" not in data:
        errors = _legacy_errors(data)
        # Workflow is new executable content. Validate it when present, but
        # keep all unrelated legacy metadata and defaults backward compatible.
        if "workflow" in data:
            try:
                from ..public.workflow import validate_workflow_actions, validate_workflow_shape

                validate_workflow_shape(data["workflow"])
                if workflow_actions is not None:
                    validate_workflow_actions(data["workflow"], workflow_actions)
            except Exception:
                errors.append("workflow steps are not valid")
        return errors
    return _schema_errors(data, workflow_actions)


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
        # Before schema versions, a mapping could omit name and carry private
        # metadata. Preserve that reader. An explicit schema_version opts into
        # the strict contract.
        if "schema_version" in data and _schema_errors(data):
            continue
        out.append(
            {
                "slug": slug,
                "name": data.get("name", slug),
                "description": data.get("description", ""),
                "milestones": len(data.get("milestones", [])),
                "schema_version": data.get("schema_version", SCHEMA_VERSION),
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
    errors = _content_errors(pb)
    if errors:
        raise ValueError(f"playbook '{slug}' is malformed ({'; '.join(errors)})")
    pb.setdefault("schema_version", SCHEMA_VERSION)
    return pb


def definition_digest(definition: dict) -> str:
    """Return the canonical identity of executable playbook content."""
    encoded = json.dumps(_canonical_yaml_value(definition), separators=(",", ":"))
    return f"{DEFINITION_DIGEST_VERSION}:{hashlib.sha256(encoded.encode()).hexdigest()}"


def definition_digest_matches(expected: str, definition: dict) -> bool:
    """Match the tagged digest stored with a durable playbook review."""
    return hmac.compare_digest(expected, definition_digest(definition))


def _canonical_yaml_value(value: object) -> object:
    """Tag every SafeLoader value before hashing to prevent type collisions."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, list):
        return ["list", [_canonical_yaml_value(item) for item in value]]
    if isinstance(value, tuple):
        return ["tuple", [_canonical_yaml_value(item) for item in value]]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_yaml_value(item) for item in value]
        items.sort(key=_canonical_sort_key)
        return ["set", items]
    if isinstance(value, dict):
        pairs: list[tuple[object, object]] = [
            (_canonical_yaml_value(key), _canonical_yaml_value(item)) for key, item in value.items()
        ]
        pairs.sort(key=lambda pair: _canonical_sort_key(pair[0]))
        return ["map", pairs]
    raise TypeError(f"unsupported playbook value type: {type(value).__name__}")


def _canonical_sort_key(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def validate_all(workflow_actions: set[str] | None = None) -> list[str]:
    """Return all schema errors from stock files and the configured overlay."""
    errors = []
    directories = [PLAYBOOKS_DIR]
    if config.PLAYBOOKS_OVERLAY and config.PLAYBOOKS_OVERLAY.is_dir():
        directories.append(config.PLAYBOOKS_OVERLAY)
    for directory in directories:
        for path in sorted(directory.glob("*.yaml")):
            label = path.name if directory == PLAYBOOKS_DIR else f"{path.name} (overlay)"
            if not _SLUG.match(path.stem):
                errors.append(f"{label}: slug must match {_SLUG.pattern}")
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                errors.append(f"{label}: not valid YAML ({type(exc).__name__})")
                continue
            errors.extend(f"{label}: {error}" for error in _content_errors(data, workflow_actions))
    return errors


def validate_startup(workflow_actions: set[str]) -> list[str]:
    """Validate executable or explicitly versioned content without breaking legacy boot.

    The old runtime skipped malformed overlay files until a caller selected
    one. Keep that behavior for unversioned files. A schema declaration is an
    explicit strict contract, and a workflow is executable, so those two
    cases must fail before the application accepts traffic.
    """
    errors: list[str] = []
    directories = [PLAYBOOKS_DIR]
    if config.PLAYBOOKS_OVERLAY and config.PLAYBOOKS_OVERLAY.is_dir():
        directories.append(config.PLAYBOOKS_OVERLAY)
    for directory in directories:
        for path in sorted(directory.glob("*.yaml")):
            label = path.name if directory == PLAYBOOKS_DIR else f"{path.name} (overlay)"
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(data, dict):
                continue
            if "schema_version" in data:
                current = _schema_errors(data, workflow_actions)
            elif "workflow" in data:
                current = [
                    error
                    for error in _content_errors(data, workflow_actions)
                    if error == "workflow steps are not valid"
                ]
            else:
                current = []
            errors.extend(f"{label}: {error}" for error in current)
    return errors


def instantiate(
    slug: str,
    engagement_name: str,
    lead: str = "",
    start_date: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
    workflow_engine: Any | None = None,
    workflow_context: Any | None = None,
    expected_definition_digest: str = "",
) -> dict:
    pb = get_playbook(slug)
    if expected_definition_digest and not definition_digest_matches(expected_definition_digest, pb):
        raise ValueError("the selected playbook changed; retry the request")
    prepared_workflow = None
    if pb.get("workflow"):
        if workflow_engine is None or workflow_context is None:
            raise ValueError("this playbook has workflow actions and needs a composed application")
        prepared_workflow = workflow_engine.prepare(pb["workflow"])
    start = date.fromisoformat(start_date) if start_date else db.today()
    workflow_result = None
    authorized_context = workflow_context
    if prepared_workflow is not None and workflow_engine is not None:
        workflow_result = workflow_engine.authorize(prepared_workflow, workflow_context)
        if workflow_result.status != "completed":
            serialized = workflow_result.model_dump(mode="json")
            if workflow_result.review_policy:
                serialized["_review_policy"] = workflow_result.review_policy
            return {"workflow": serialized}
        if workflow_context is None:
            raise ValueError("this playbook workflow has no execution context")
        authorized_context = workflow_engine._with_authorization_grants(
            workflow_context,
            workflow_result.authorization_grants,
        )
    with db.transaction():
        created = _instantiate(pb, slug, engagement_name, lead, start, actor=actor, origin=origin)
    if prepared_workflow is not None and workflow_engine is not None:
        result = workflow_engine.run(prepared_workflow, authorized_context)
        serialized = result.model_dump(mode="json")
        if result.review_policy:
            serialized["_review_policy"] = result.review_policy
        created["workflow"] = serialized
    return created


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

    _snapshot(created, slug, start, actor)
    db.log_activity(actor, "instantiate_playbook", f"{slug} -> {engagement_name}")
    return created


def snapshot_for(engagement_id: int) -> dict:
    """The plan an engagement started with, or {} when it was not born from a
    playbook. Callers branch on the empty dict — an engagement created by hand
    has no plan to diff against and must close exactly as it always did."""
    row = db.query_one(
        "SELECT path FROM artifacts WHERE engagement_id = ? AND kind = 'plan-snapshot'"
        " ORDER BY id LIMIT 1",
        (engagement_id,),
    )
    if not row:
        return {}
    path = Path(row["path"])
    if not path.exists():
        # the row outlives the file: data/artifacts/ is gitignored and a
        # restore-from-backup brings the database back without it. A missing
        # file is "no snapshot", never a 500 at close time.
        return {}
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # SHAPE, not just parseability. The file carries no version field, so the
    # first change to the plan format would otherwise turn every older
    # engagement's close-out panel into a permanent 500 — a list or a bare
    # number parses fine and then raises TypeError on dict(), and a dict
    # missing "milestones" raises KeyError deep inside close_out_diff.
    if not isinstance(plan, dict) or not all(
        isinstance(plan.get(k), list) for k in ("milestones", "tasks", "rituals")
    ):
        return {}
    return plan


def _snapshot(created: dict, slug: str, start: date, actor: str) -> int:
    """Write the plan as JSON beside the engagement's other artifacts.

    Ids, not only titles: a task renamed between kickoff and close is the same
    task, and a diff keyed on title would report it as one removed and one
    added.

    The artifacts ROW is written inside the caller's transaction; the file is
    not, so a rolled-back instantiate leaves an orphan JSON plan on disk that
    nothing points at. That is the safe direction — a row with no file reads
    as "no snapshot" (see snapshot_for), a file with no row is unreachable.
    """
    eng = created["engagement"]

    # Read the stored rows rather than the create_* return values: those are
    # deliberately minimal ({id, title, status}) and carry neither the due
    # date nor the milestone link, which are the two things a diff needs.
    def _rows(table: str, ids: list[int], cols: str) -> list[dict]:
        return [
            dict(r)
            for i in ids
            if (r := db.query_one(f"SELECT {cols} FROM {table} WHERE id = ?", (i,)))  # noqa: S608 — cols and table are literals at every call site
        ]

    plan = {
        "playbook": slug,
        "engagement_id": eng["id"],
        "start_date": start.isoformat(),
        "captured_at": db.now(),
        "milestones": _rows(
            "milestones", [m["id"] for m in created["milestones"]], "id, title, due_date"
        ),
        "tasks": _rows("tasks", [t["id"] for t in created["tasks"]], "id, title, milestone_id"),
        "rituals": _rows("events", [e["id"] for e in created["events"]], "id, title, starts_at"),
    }
    safe = re.sub(r"[^a-z0-9]+", "-", eng["name"].lower()).strip("-") or f"engagement-{eng['id']}"
    plans = Path(config.DATA_DIR) / "artifacts" / safe
    plans.mkdir(parents=True, exist_ok=True)
    path = plans / f"{eng['id']}-plan-snapshot.json"
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    # the engagement's own tier, threaded like handoff.py does and for the
    # same reason: the row carries a PATH to every milestone and task title,
    # and list_artifacts must not hand it to somebody who could not read them
    aid = db.execute(
        "INSERT INTO artifacts (engagement_id, kind, title, path, created_by, created_at,"
        " visibility, crew_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " RETURNING id",
        (
            eng["id"],
            "plan-snapshot",
            f"Plan at kickoff — {eng['name']}",
            str(path),
            actor,
            db.now(),
            eng.get("visibility") or scope.WORKSPACE,
            eng.get("crew_id") or None,
        ),
    )
    # provenance, like every other write. handoff.py logs its artifact and this
    # is the same shape: a file on disk that a later close reads back.
    db.log_activity(actor, "plan_snapshot", f"engagement #{eng['id']} -> artifact #{aid}")
    return aid


def _exists(table: str, row_id: int) -> bool:
    """Is the row there at all, ignoring tier?

    Split out under its own name so the visibility walker's allowlist can
    excuse THIS probe without excusing close_out_diff's real reads, which all
    carry a viewer filter. It returns one bit and never a column: the caller
    uses it only to tell "deleted" from "hidden from you", and those two must
    not produce the same sentence.
    """
    return bool(
        db.query_one(
            f"SELECT id FROM {table} WHERE id = ?",  # noqa: S608 — table is a literal at both call sites
            (row_id,),
        )
    )


def close_out_diff(
    engagement_id: int,
    viewer: scope.Viewer = scope.NOBODY,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None = None,
) -> dict:
    """Planned versus what happened, for an engagement born from a playbook.

    Returns {} when there is no snapshot. A playbook that never learns is the
    whole complaint this answers: the template said six weeks, every one of
    them ran nine, and nothing carried that back to the YAML.

    Read-only and id-keyed. A ritual leaves NO row when it is cancelled
    (schedule.py::cancel_event deletes), so a missing event id is the only
    evidence that a planned ceremony did not happen.

    Viewer-scoped on every read, and the default NOBODY (workspace tier) is
    what the drafted lesson uses: that lesson becomes a proposal EVERY
    reviewer reads, so a private task added to the engagement must not reach
    it through a title. The route passes the caller's own viewer instead —
    reading a diff is not the same act as publishing one.
    """
    # the engagement first, and it RAISES rather than returning {}: the diff
    # quotes milestone and task titles, so an unreadable engagement and one
    # that never had a playbook must not answer alike — the second is an empty
    # section, the first is a 404 (scope.missing gives both the same sentence)
    efrag, ep = scope.visible_filter(viewer, "engagements")
    eng = db.query_one(
        f"SELECT visibility, project_class FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (engagement_id, *ep),
    )
    if not eng:
        raise scope.missing("engagements", engagement_id)
    eng_tier = eng["visibility"]
    plan = snapshot_for(engagement_id)
    if not plan:
        return {}

    mfrag, mp = scope.visible_filter(viewer, "milestones")
    tfrag, tp = scope.visible_filter(viewer, "tasks")
    efrag, ep = scope.visible_filter(viewer, "events")

    from . import policy_context

    parent_context = {
        "classification": str(eng.get("visibility") or ""),
        "project_type": str(eng.get("project_class") or ""),
    }

    def allowed(entity: str, entity_id: int) -> bool:
        if resource_filter is None:
            return True
        attributes = policy_context.existing_scoped(entity, entity_id, viewer) or parent_context
        return resource_filter(entity, entity_id, attributes)

    slipped = []
    for m in plan["milestones"]:
        if not allowed("milestone", int(m["id"])):
            return {}
        row = db.query_one(
            f"SELECT title, due_date, completed_at FROM milestones WHERE id = ? AND {mfrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
            (m["id"], *mp),
        )
        # A row the VIEWER cannot see is indistinguishable from a deleted one,
        # and reporting it as dropped would tell a crew their finished work was
        # abandoned. Refuse the whole diff instead: a partial plan yields a
        # confident sentence about work this caller was never shown.
        if not row:
            return {}
        # TWO bases, because they answer different questions and a team can
        # produce either without the other. `re-dated` catches replanning;
        # `finished` catches the case this feature exists for — a team that
        # never touches its dates and lands nine weeks late. Measuring only
        # the first rewards good date hygiene with a lesson and bad date
        # hygiene with silence, which is backwards.
        planned = m["due_date"]
        if not planned:
            continue
        if row["completed_at"]:
            # A finish date SETTLES the question, early or late — the `continue`
            # is outside the `days > 0` test on purpose. Nested inside it, a
            # milestone delivered six days early still fell through to the
            # re-dated branch and reported the moved date as slip, so the
            # drafted lesson padded the playbook for work that came in ahead.
            # db.local_day, never completed_at[:10]: `completed_at` is a UTC
            # timestamp and `planned` is a TEAM-local date, so the slice
            # compares two different calendars. West of UTC, a milestone
            # finished at 20:00 local ON its due date reads as one day late —
            # and three of those clear PLAN_DRIFT_ALARM, so
            # insights.py::_r_plan_drift files a permanent medium finding
            # against an engagement that is exactly on plan.
            done_day = db.local_day(row["completed_at"])
            days = (date.fromisoformat(done_day) - date.fromisoformat(planned)).days
            if days > 0:
                slipped.append(
                    {
                        "title": m["title"],
                        "days": days,
                        "to": done_day,
                        "basis": "finished",
                    }
                )
            continue
        if row["due_date"] and row["due_date"] != planned:
            days = (date.fromisoformat(row["due_date"]) - date.fromisoformat(planned)).days
            slipped.append(
                {"title": m["title"], "days": days, "to": row["due_date"], "basis": "re-dated"}
            )

    unfinished, dropped_tasks = [], []
    for t in plan["tasks"]:
        if not allowed("task", int(t["id"])):
            return {}
        row = db.query_one(
            f"SELECT id, title, status FROM tasks WHERE id = ? AND {tfrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
            (t["id"], *tp),
        )
        # the same refusal as the milestone loop above, for the same reason:
        # this title comes from the SNAPSHOT, which passed through no filter,
        # so appending it publishes a row the caller was just refused
        if not row:
            if _exists("tasks", t["id"]):
                return {}
            dropped_tasks.append(t["title"])
        elif row["status"] != "done":
            unfinished.append(t["title"])

    # BOTH link paths, copied from engagements.py::_ship_it which carries the
    # same comment: _instantiate creates its tasks with a milestone_id and NO
    # engagement_id, so an engagement_id-only query matches almost nothing and
    # this clause — the only one that names concrete titles to add to the
    # YAML — silently never fires.
    # keyed on ID like every other read here: _snapshot's docstring says why,
    # and a title-keyed filter reports a RENAMED planned task as new work and
    # recommends adding it to the YAML it is already in
    planned_ids = {t["id"] for t in plan["tasks"]}
    added_rows = db.query(
        f"SELECT id, title FROM tasks WHERE (engagement_id = ? OR milestone_id IN"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" (SELECT id FROM milestones WHERE engagement_id = ?)) AND {tfrag} ORDER BY id",
        (engagement_id, engagement_id, *tp),
    )
    added_rows = policy_context.filter_resource_rows("task", added_rows, viewer, resource_filter)
    added = [r["title"] for r in added_rows if r["id"] not in planned_ids]
    skipped_rituals = []
    for r in plan["rituals"]:
        if not allowed("event", int(r["id"])):
            return {}
        if db.query_one(
            f"SELECT id FROM events WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
            (r["id"], *ep),
        ):
            continue
        # cancel_event DELETES, so an absent row means the ceremony did not
        # happen. A row that EXISTS but is hidden means the opposite, and
        # "it did not happen" about a meeting somebody held is a false claim
        # as well as a leak
        if _exists("events", r["id"]):
            return {}
        skipped_rituals.append(r["title"])
    diff = {
        "playbook": plan["playbook"],
        # whether closing will actually file a draft — BOTH conditions, not
        # just the tier. engagements.py::_playbook_lesson draws the line at
        # the workspace tier, and _variance_lesson files nothing when the diff
        # has no fixable variance. Reporting only the first told the reader a
        # lesson was coming, then filed none and said nothing, and they went
        # to Review to find an empty queue.
        "slipped": slipped,
        "unfinished_tasks": unfinished,
        "dropped_tasks": dropped_tasks,
        "added_tasks": added,
        "skipped_rituals": skipped_rituals,
    }
    # BOTH conditions, not just the tier: engagements.py::_playbook_lesson
    # gates on workspace, and _variance_lesson files nothing when the diff has
    # no fixable variance. Reporting only the first told the reader a lesson
    # was coming, filed none, said nothing, and sent them to an empty queue.
    # The engagement name is irrelevant to whether a lesson EXISTS, so a
    # placeholder is passed rather than threading one in for a boolean.
    diff["drafts_lesson"] = eng_tier == scope.WORKSPACE and bool(_variance_lesson(diff, "x")[0])
    return diff


def _listing(items: list[str], show: int = 3) -> str:
    """Up to `show` names, then what was left out.

    A bare `[:3]` beside "7 tasks" prints three and claims seven, and any
    string carrying a number has to be exact (CLAUDE.md).
    """
    if len(items) <= show:
        return ", ".join(items)
    return f"{', '.join(items[:show])}, and {len(items) - show} more"


def _variance_lesson(diff: dict, engagement_name: str) -> tuple[str, str]:
    """(lesson, recommendation) drafted from the diff, or ("", "").

    Empty when nothing moved: a lesson saying an engagement went to plan
    teaches the next reader nothing and costs a reviewer a verdict.
    """
    # One fact per sentence, and no semicolons — this text lands in a kickoff
    # note that the next team reads cold (the wording standard in CLAUDE.md).
    # Verb agreement is computed, because "1 task were added" is the sentence
    # a reader stops trusting the number in.
    parts, fixes = [], []
    late = [s for s in diff["slipped"] if s["days"] > 0]
    if late:
        worst = max(late, key=lambda s: s["days"])
        landed = "landed late" if worst["basis"] == "finished" else "moved"
        parts.append(
            f"{wording.count(len(late), 'milestone')} {landed}, the largest by"
            f" {wording.count(worst['days'], 'day')} (“{worst['title'][:40]}”)."
        )
        fixes.append(
            f"Add {wording.count(worst['days'], 'day')} to “{worst['title'][:40]}”"
            f" in playbooks/{diff['playbook']}.yaml, or split it."
        )
    if diff["added_tasks"]:
        n = len(diff["added_tasks"])
        parts.append(
            f"{wording.count(n, 'task')} outside the playbook {'was' if n == 1 else 'were'} added."
        )
        fixes.append(f"Add these to the playbook: {_listing(diff['added_tasks'])}.")
    if diff["skipped_rituals"]:
        n = len(diff["skipped_rituals"])
        parts.append(
            f"{wording.count(n, 'ritual')} did not happen: {_listing(diff['skipped_rituals'])}."
        )
        fixes.append("Remove the rituals nobody holds, or record who runs them.")
    # Every draft costs a reviewer a verdict, and the review queue is the
    # team's scarcest resource. A lesson with no fix in it is one sentence
    # restating the engagement's own conclusion — an abandoned engagement
    # would file one every time and teach the next kickoff nothing.
    if not fixes:
        return "", ""
    n = len(diff["dropped_tasks"]) + len(diff["unfinished_tasks"])
    if n:
        parts.append(f"{wording.count(n, 'planned task')} never finished.")
    return (
        f"{engagement_name} ran against the {diff['playbook']} playbook. " + " ".join(parts),
        " ".join(fixes),
    )

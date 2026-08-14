"""REST API: reads for the dashboard, writes for humans (the second write path
alongside agent tools — both go through app.services)."""

import asyncio
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import config, db, ratelimit
from ..extensions.fastapi import (
    PolicyAPIRoute,
    PolicySubjectDep,
    decide,
    enforce_decision,
)
from ..extensions.policy import PolicyDecision, PolicyEffect
from ..services import (
    absences,
    activity,
    admin,
    api_keys,
    blockers,
    briefing,
    capture,
    collab,
    context_pack,
    crews,
    delegation,
    delta,
    digest,
    engagement_brief,
    engagements,
    feedback,
    fieldguide,
    flocks,
    handoff,
    ingest,
    intake,
    intervention,
    memory,
    notifications,
    personas,
    planning,
    playbooks,
    policy_context,
    portfolio,
    projection_policy,
    promises,
    provenance,
    pulse,
    readout,
    review,
    rituals,
    schedule,
    scope,
    search,
    settings,
    stakeholders,
    tuning,
    usage,
    users,
    weekly,
    work,
)
from .deps import AdminUser, CurrentUser, StrongUser, ViewerDep

router = APIRouter(prefix="/api", route_class=PolicyAPIRoute)


def _permitted_collection(
    request: Request,
    subject: Any,
    rows: list[dict],
    contexts: dict[int, dict[str, str]],
    *,
    action: str,
    resource_type: str,
) -> list[dict]:
    """Filter visible rows through their authoritative workplace policy context."""
    return [
        row
        for row in rows
        if decide(
            request,
            subject,
            action,
            resource_type,
            resource_id=str(row["id"]),
            project_type=contexts[int(row["id"])]["project_type"],
            classification=contexts[int(row["id"])]["classification"],
            attributes=contexts[int(row["id"])],
        ).effect.value
        == "permit"
    ]


def _require_opaque_project_policy(
    request: Request,
    subject: Any,
    viewer: scope.Viewer,
    action: str,
    *,
    unclassified_inputs: bool = True,
    resource_types: tuple[str, ...] = projection_policy.PROJECT_RESOURCE_TYPES,
) -> projection_policy.ProjectionPolicy:
    """Refuse an opaque derivative when it cannot remove a denied input."""
    policy = projection_policy.ProjectionPolicy(
        request.app.state.skein_registry.policy_engine,
        subject,
        action,
        "rest",
        viewer,
    )
    if not policy.allows_all_projects(resource_types) or (
        unclassified_inputs and not policy.allows_unclassified()
    ):
        enforce_decision(
            PolicyDecision(
                PolicyEffect.DENY,
                ("The composite contains a resource that policy does not permit.",),
            )
        )
    return policy


def _require_resource_policy(
    request: Request,
    subject: Any,
    viewer: scope.Viewer,
    action: str,
    entity: str,
    entity_id: int,
) -> dict[str, str]:
    """Authorize one visible domain resource in the caller's read snapshot."""
    attributes = policy_context.existing_scoped(entity, entity_id, viewer)
    if not attributes:
        raise scope.missing(f"{entity}s", entity_id)
    enforce_decision(
        decide(
            request,
            subject,
            action,
            entity,
            resource_id=str(entity_id),
            project_type=attributes.get("project_type", ""),
            classification=attributes.get("classification", ""),
            attributes=attributes,
        )
    )
    return attributes


# ---- reads -----------------------------------------------------------------
#
# Every read that returns a row takes CurrentUser, including the ones whose
# handler never spends it. It buys ONE thing, and it is not access control:
# a read with no caller cannot be given a visibility filter later without
# changing its signature (docs/VISIBILITY.md). The walls `_resolve` applies
# are already applied at
# the perimeter in api-key and oidc mode, and in trusted-header mode a caller
# refused under one name reaches every read by picking another.
#
# The file-backed catalogs are the exception, enumerated with a reason each
# in tests/test_route_identity.py::OPEN_READS. Adding an open GET without a
# line there fails CI.


@router.get("/capabilities")
def get_capabilities(
    request: Request,
    subject: PolicySubjectDep,
    actions: str = Query("", max_length=2000),
    project_type: str = Query("", max_length=100),
):
    """Return policy decisions for presentation, not for enforcement."""
    registry = request.app.state.skein_registry
    requested = {item.strip() for item in actions.split(",") if item.strip()}
    if len(requested) > 64:
        raise ValueError("a capability request can name at most 64 actions")
    requested.update(tool.policy_action for tool in registry.tools)
    catalog = registry.action_catalog()
    out = {}
    for action in sorted(requested):
        # An action outside the composed catalog fails closed. The engine's
        # human-origin default otherwise answered `permit` for a misspelled
        # frontend action — and for a frontend whose backend module is not
        # installed at all, which is exactly when its UI must stay hidden.
        # The `skein.` exemption exists because core REST actions are derived
        # from routes, not contributed; it is safe because composition
        # refuses a private module that DECLARES an operation under the
        # prefix, and the frontend registry refuses a `skein.` policyAction
        # (extensions/registry.py::_validate_namespace, lib/extensions/
        # registry.ts::registerAction). This endpoint stays presentation-only.
        if action not in catalog and not action.startswith("skein."):
            out[action] = {
                "effect": "deny",
                "reasons": ["No composed module registers this action."],
                "obligations": [],
                "approver_groups": [],
                "approver_capabilities": [],
            }
            continue
        decision = decide(
            request,
            subject,
            action,
            action.split(".", 1)[0],
            project_type=project_type,
        )
        out[action] = {
            "effect": decision.effect.value,
            "reasons": list(decision.reasons),
            "obligations": list(decision.obligations),
            "approver_groups": list(decision.approver_groups),
            "approver_capabilities": list(decision.approver_capabilities),
        }
    return {
        "subject": subject.name,
        "roles": list(subject.roles),
        "capabilities": list(subject.capabilities),
        "modules": list(registry.module_summaries()),
        "actions": out,
    }


@router.get("/milestones")
def get_milestones(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    project: str = "",
    status: str = "",
):
    with db.read_transaction():
        rows = work.list_milestones(project, status, viewer)
        contexts = work.milestone_collection_policy_contexts(rows, viewer)
        return [
            row
            for row in rows
            if decide(
                request,
                subject,
                "skein.rest.get.milestones",
                "milestone",
                resource_id=str(row["id"]),
                project_type=contexts[int(row["id"])]["project_type"],
                classification=contexts[int(row["id"])]["classification"],
                attributes=contexts[int(row["id"])],
            ).effect.value
            == "permit"
        ]


@router.get("/tasks")
def get_tasks(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    """Return only rows that the composed workplace policy permits."""
    with db.read_transaction():
        policy = projection_policy.ProjectionPolicy(
            request.app.state.skein_registry.policy_engine,
            subject,
            "skein.rest.get.tasks",
            "rest",
            viewer,
        )
        rows = work.list_tasks_joined(viewer)
        contexts = work.task_collection_policy_contexts(rows, viewer)
        permitted = [
            row for row in rows if policy.permits("task", int(row["id"]), contexts[int(row["id"])])
        ]
        return work.redact_task_relationships(permitted, viewer, policy.permits)


@router.get("/tasks/{task_id}")
def get_task(
    user: CurrentUser,
    viewer: ViewerDep,
    task_id: int,
    request: Request,
    subject: PolicySubjectDep,
):
    """The side peek's read. Declared BEFORE /tasks/{task_id}/worklog is
    irrelevant to routing here (the paths differ in segment count), but it
    must stay after the literal /tasks route above — FastAPI matches in
    declaration order, and a bare "/tasks/{task_id}" first would swallow it."""
    try:
        # Bind the viewer-scoped row, domain policy, and returned projection to
        # one snapshot. The generic route gate skips this exact operation so
        # an unscoped project lookup cannot reveal a hidden task first.
        with db.read_transaction():
            task = work.get_task(task_id, viewer)
            domain = work.task_read_policy_context(task, viewer)
            if domain.get("relationship_conflict"):
                raise scope.missing("tasks", task_id)
            enforce_decision(
                decide(
                    request,
                    subject,
                    "skein.rest.get.tasks",
                    "task",
                    resource_id=str(task_id),
                    project_type=domain["project_type"],
                    classification=domain["classification"],
                    attributes=domain,
                )
            )
            policy = projection_policy.ProjectionPolicy(
                request.app.state.skein_registry.policy_engine,
                subject,
                "skein.rest.get.tasks",
                "rest",
                viewer,
            )
            return work.filter_task_projection(task, viewer, policy.permits)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/tasks/{task_id}/worklog")
def get_task_worklog(
    user: CurrentUser,
    viewer: ViewerDep,
    task_id: int,
    request: Request,
    subject: PolicySubjectDep,
):
    try:
        with db.read_transaction():
            task = work.get_task(task_id, viewer)
            domain = work.task_read_policy_context(task, viewer)
            if domain.get("relationship_conflict"):
                raise scope.missing("tasks", task_id)
            enforce_decision(
                decide(
                    request,
                    subject,
                    "skein.rest.get.tasks.worklog",
                    "task",
                    resource_id=str(task_id),
                    project_type=domain["project_type"],
                    classification=domain["classification"],
                    attributes=domain,
                )
            )
            return delegation.list_worklog(task_id, viewer=viewer)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/questions")
def get_questions(user: CurrentUser, viewer: ViewerDep, status: str = ""):
    return collab.list_questions(status, viewer)


@router.get("/decisions")
def get_decisions(user: CurrentUser, viewer: ViewerDep, status: str = "", category: str = ""):
    return collab.list_decisions(status=status, category=category, viewer=viewer)


@router.get("/standups")
def get_standups(user: CurrentUser, viewer: ViewerDep):
    return collab.list_standups(viewer=viewer)


@router.get("/events")
def get_events(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    from_date: str = "",
):
    with db.read_transaction():
        rows = schedule.list_events(from_date, viewer=viewer)
        contexts = policy_context.engagement_linked_collection_contexts("event", rows, viewer)
        return _permitted_collection(
            request,
            subject,
            rows,
            contexts,
            action="skein.rest.get.events",
            resource_type="event",
        )


@router.get("/personas")
def get_personas():
    return personas.list_personas()


@router.get("/personas/{slug}")
def get_persona(slug: str):
    return personas.get_persona(slug)


@router.get("/flocks")
def get_flocks():
    return flocks.list_flocks()


@router.get("/flocks/traces")
def get_flock_traces(user: CurrentUser, thread: str = "", flock: str = "", limit: int = 20):
    # CurrentUser, unlike the open roster above: a trace row names a person and
    # the thread id their chat transcript is keyed by (routes/chat.py scopes
    # those per owner), so this must not answer an unidentified caller
    return flocks.list_traces(user, thread_id=thread, flock=flock, limit=limit)


@router.get("/notes")
def get_notes(user: CurrentUser, viewer: ViewerDep, q: str = ""):
    return collab.search_notes(q, viewer)


class NotePatch(BaseModel):
    topic: str = Field("", max_length=200)
    content: str = Field("", max_length=20_000)


@router.patch("/notes/{note_id}")
def patch_note(note_id: int, body: NotePatch, user: CurrentUser):
    # edits scan for @mentions, so an uncapped PATCH is a notification
    # amplifier — same cap as the create routes
    ratelimit.check("write", user)
    try:
        return collab.update_note(note_id, body.topic, body.content, actor=user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, user: CurrentUser):
    ratelimit.check("delete", user)
    try:
        return collab.delete_note(note_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.delete("/events/{event_id}")
def delete_event(event_id: int, user: CurrentUser):
    ratelimit.check("delete", user)
    try:
        return schedule.cancel_event(event_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/activity")
def get_activity(user: CurrentUser):
    return collab.recent_activity(user)


@router.get("/activity/feed")
def get_activity_feed(user: CurrentUser, before: int = 0, limit: int = 50):
    """The rendered feed: agent and system actions plus your own. The scope is
    enforced in the service — there is no parameter for another person."""
    fieldguide.mark(user, "activity_feed")
    return activity.feed(user, limit=limit, before=before)


@router.get("/activity/verify")
def get_activity_verify(user: CurrentUser, tail: int = 0):
    """Recompute the provenance chain. The default walks every chained row,
    because a partial answer to "is the ledger intact" is not an answer.
    tail=1 resumes from the last verified anchor for a cheap freshness check.

    Rate-capped: activity is never pruned, so the full walk is the most
    expensive read in the app and it grows for the life of the deployment. The
    daily findings rule runs it unprompted, so nobody needs it on a loop."""
    ratelimit.check("verify", user)
    return activity.verify_tail() if tail else activity.verify_chain()


@router.get("/blockers")
def get_blockers(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    status: str = "",
    owner: str = "",
):
    """Return only blocker rows that the composed workplace policy permits."""
    with db.read_transaction():
        rows = blockers.list_blockers(status, owner, viewer)
        contexts = blockers.blocker_collection_policy_contexts(rows, viewer)
        return [
            row
            for row in rows
            if decide(
                request,
                subject,
                "skein.rest.get.blockers",
                "blocker",
                resource_id=str(row["id"]),
                project_type=contexts[int(row["id"])]["project_type"],
                classification=contexts[int(row["id"])]["classification"],
                attributes=contexts[int(row["id"])],
            ).effect.value
            == "permit"
        ]


@router.get("/intake")
def get_intake(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    status: str = "",
):
    with db.read_transaction():
        rows = intake.list_requests(status, viewer)
        return [
            row
            for row in rows
            if decide(
                request,
                subject,
                "skein.rest.get.intake",
                "intake",
                resource_id=str(row["id"]),
                project_type=str(row.get("project_class") or ""),
                classification=str(row.get("visibility") or ""),
            ).effect.value
            == "permit"
        ]


@router.get("/review")
def get_review(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    status: str = "pending",
):
    with db.read_transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.review")
        return review.list_changes(status, viewer)


@router.get("/engagements")
def get_engagements(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    status: str = "",
):
    with db.read_transaction():
        rows = engagements.list_engagements(status, viewer=viewer)
        return [
            row
            for row in rows
            if decide(
                request,
                subject,
                "skein.rest.get.engagements",
                "engagement",
                resource_id=str(row["id"]),
                project_type=str(row.get("project_class") or ""),
                classification=str(row.get("visibility") or ""),
            ).effect.value
            == "permit"
        ]


@router.get("/allocations")
def get_allocations(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    engagement_id: int = 0,
):
    with db.read_transaction():
        rows = engagements.list_allocations(engagement_id, viewer=viewer)
        contexts = policy_context.resource_contexts(
            [("allocation", int(row["id"])) for row in rows], viewer
        )
        return _permitted_collection(
            request,
            subject,
            rows,
            {row_id: value for (_entity, row_id), value in contexts.items()},
            action="skein.rest.get.allocations",
            resource_type="allocation",
        )


@router.delete("/allocations/{allocation_id}")
def delete_allocation(allocation_id: int, user: CurrentUser):
    ratelimit.check("delete", user)
    try:
        return engagements.deallocate(allocation_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class AbsenceIn(BaseModel):
    person: str = Field(max_length=64)
    starts_on: str = Field(max_length=10)
    ends_on: str = Field(max_length=10)
    kind: str = Field("pto", max_length=10)
    note: str = Field("", max_length=200)
    # `person` is checked as a READER (absences.add_absence).
    # the tier the writer picked, checked in the service: crew membership only.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.get("/absences")
def get_absences(user: CurrentUser, viewer: ViewerDep, person: str = ""):
    return absences.list_absences(person, viewer=viewer)


@router.post("/absences")
def post_absence(body: AbsenceIn, user: CurrentUser):
    ratelimit.check("absence", user)
    try:
        return absences.add_absence(
            body.person,
            body.starts_on,
            body.ends_on,
            body.kind,
            body.note,
            actor=user,
            visibility=body.visibility,
            crew_id=body.crew_id,
        )
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/absences/{absence_id}")
def delete_absence(absence_id: int, user: CurrentUser):
    ratelimit.check("delete", user)  # outside the try: a rate cap is a 400, not a 404
    try:
        return absences.delete_absence(absence_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/capacity")
def get_capacity(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.read_transaction():
        # Capacity composes allocations against engagements only, and both
        # are classified rows — no free-form legacy text rides along. Testing
        # the full type set (and the unclassified gate) hid the whole card
        # behind a rule about regulated tasks that no capacity row contains.
        _require_opaque_project_policy(
            request,
            subject,
            viewer,
            "skein.rest.get.capacity",
            unclassified_inputs=False,
            resource_types=("engagement", "allocation"),
        )
        return engagements.capacity(viewer)


@router.get("/lessons")
def get_lessons(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    project_class: str = "",
):
    with db.read_transaction():
        rows = engagements.list_lessons(project_class, viewer=viewer)
        contexts = policy_context.resource_contexts(
            [("lesson", int(row["id"])) for row in rows], viewer
        )
        return _permitted_collection(
            request,
            subject,
            rows,
            {row_id: value for (_entity, row_id), value in contexts.items()},
            action="skein.rest.get.lessons",
            resource_type="lesson",
        )


@router.get("/playbooks")
def get_playbooks():
    return playbooks.list_playbooks()


@router.get("/artifacts")
def get_artifacts(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    engagement_id: int = 0,
):
    with db.read_transaction():
        rows = handoff.list_artifacts(engagement_id, viewer)
        contexts = policy_context.resource_contexts(
            [("artifact", int(row["id"])) for row in rows], viewer
        )
        return _permitted_collection(
            request,
            subject,
            rows,
            {row_id: value for (_entity, row_id), value in contexts.items()},
            action="skein.rest.get.artifacts",
            resource_type="artifact",
        )


@router.get("/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: int,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.read_transaction():
        artifact = handoff.read_artifact(artifact_id, viewer)
        domain = policy_context.existing_scoped("artifact", artifact_id, viewer)
        enforce_decision(
            decide(
                request,
                subject,
                "skein.rest.get.artifacts",
                "artifact",
                resource_id=str(artifact_id),
                project_type=domain.get("project_type", ""),
                classification=domain.get("classification", ""),
                attributes=domain,
            )
        )
        return artifact


@router.get("/users")
def get_users(user: CurrentUser, all: bool = False):
    # all=1 includes deactivated rows — the Settings roster needs them so
    # deactivation stays reversible from the UI
    return users.list_users(active_only=not all)


class UserRenameIn(BaseModel):
    new_name: str = Field(min_length=1, max_length=64)


@router.post("/users/{name}/rename")
def post_user_rename(name: str, body: UserRenameIn, user: AdminUser):
    # rename/merge moves attribution history — administrators only
    return users.rename_user(name, body.new_name, actor=user)


class UserActiveIn(BaseModel):
    active: bool


@router.post("/users/{name}/active")
def post_user_active(name: str, body: UserActiveIn, user: AdminUser):
    # roster edits are admin surface — one teammate must not be able to
    # deactivate another
    return users.set_active(name, body.active, actor=user)


# ---- crews -----------------------------------------------------------------
#
# A crew is membership only (docs/VISIBILITY.md) — it grants nothing until the
# tier columns land. Editing one is a STEWARD's job or an administrator's, and
# _crew_steward below is the only place that pair is decided.


class CrewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=crews.NAME_LEN)
    summary: str = Field("", max_length=crews.SUMMARY_LEN)


class CrewPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", max_length=crews.NAME_LEN)
    summary: str | None = Field(None, max_length=crews.SUMMARY_LEN)
    active: bool | None = None


class CrewMemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person: str = Field(min_length=1, max_length=64)
    role: str = Field("member", max_length=16)


class CrewMemberOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person: str = Field(min_length=1, max_length=64)


def _crew_admin_override(user: str, request: Request) -> bool:
    """The half of the crew-edit check that only a ROUTE can answer.

    Strong identity, and whether this caller is a named administrator. The
    steward test itself moved into the services (crews.assert_steward), where
    it runs inside the write's own transaction — membership decides what every
    person reads, and a guard that lives only here is a guard the next caller
    does not have.

    Not AdminUser on the route: a crew whose membership only an administrator
    can edit is a crew nobody maintains. Not CurrentUser alone either — in
    trusted-header mode that is a self-asserted header.
    """
    from .deps import _require_strong, is_named_admin

    _require_strong(getattr(request.state, "strong_auth", False))
    # the groups current_user stashed, not []: an administrator named only by
    # SKEIN_OIDC_ADMIN_GROUP is refused by an empty list, while the same person
    # can rename roster rows and revoke every key through AdminUser.
    groups = getattr(request.state, "auth_groups", [])
    # is_named_admin, NOT _is_admin: the scarcity fallback makes every key
    # holder an administrator in the default deployment, and that would let
    # any of them take a crew from its steward in one call.
    return is_named_admin(user, groups)


@router.get("/crews")
def get_crews(user: CurrentUser, all: bool = False):
    return crews.list_crews(active_only=not all)


@router.get("/crews/mine")
def get_my_crews(user: CurrentUser):
    """Self-scoped by construction: the only person parameter is the caller.
    There is deliberately no way to read another person's crew list."""
    return crews.crews_of(user)


@router.get("/crews/{crew_id}")
def get_crew(crew_id: int, user: CurrentUser):
    return crews.get_crew(crew_id)


@router.post("/crews")
def post_crew(body: CrewIn, user: StrongUser):
    # StrongUser: the creator becomes the first steward, so a self-asserted
    # header would mint a crew someone else appears to steward
    ratelimit.check("write", user)
    return crews.create_crew(body.name, summary=body.summary, actor=user)


@router.patch("/crews/{crew_id}")
def patch_crew(crew_id: int, body: CrewPatch, user: CurrentUser, request: Request):
    admin = _crew_admin_override(user, request)
    ratelimit.check("write", user)
    return crews.update_crew(
        crew_id,
        name=body.name,
        summary=body.summary,
        active=body.active,
        actor=user,
        admin_override=admin,
    )


@router.post("/crews/{crew_id}/members")
def post_crew_member(crew_id: int, body: CrewMemberIn, user: CurrentUser, request: Request):
    admin = _crew_admin_override(user, request)
    ratelimit.check("write", user)
    return crews.add_member(crew_id, body.person, role=body.role, actor=user, admin_override=admin)


@router.post("/crews/{crew_id}/members/remove")
def post_crew_member_remove(crew_id: int, body: CrewMemberOut, user: CurrentUser, request: Request):
    """The person travels in the BODY, not the path.

    A roster name may contain any character (ensure_user caps length and
    nothing else), and starlette's router does not match a path segment
    holding `/` even percent-encoded — `a/b` could be added to a crew and
    then never removed by any request the client could form. Removal is the
    only way out of a crew, so it must not be shaped by what the name is.
    """
    admin = _crew_admin_override(user, request)
    ratelimit.check("delete", user)
    return crews.remove_member(crew_id, body.person, actor=user, admin_override=admin)


class GrowthIn(BaseModel):
    interests: str = Field(max_length=500)


@router.post("/users/growth-interests")
def post_growth_interests(body: GrowthIn, user: CurrentUser):
    # self-declared only: you set YOURS (future-planning data, never scored)
    return users.set_growth_interests(user, body.interests, actor=user)


@router.get("/users/growth-interests")
def get_growth_interests(user: CurrentUser):
    # write-only fields can't be reviewed or cleared — prefill needs this
    return {"interests": users.get_growth_interests(user)}


class ThemeIn(BaseModel):
    theme: str = Field(max_length=400)


@router.post("/users/theme")
def post_user_theme(body: ThemeIn, user: CurrentUser):
    try:
        return users.set_theme(user, body.theme)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/users/theme")
def get_user_theme(user: CurrentUser):
    return {"theme": users.get_theme(user), "team_default": users.get_team_default_theme()}


@router.post("/users/theme/default")
def post_team_theme(body: ThemeIn, user: AdminUser):
    try:
        return users.set_team_default_theme(body.theme, actor=user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/search")
def get_search(
    q: str,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    fieldguide.mark(user, "search")
    policy = projection_policy.ProjectionPolicy(
        request.app.state.skein_registry.policy_engine,
        subject,
        "skein.rest.get.search",
        "rest",
        viewer,
    )
    with db.read_transaction():
        return search.search(q, viewer=viewer, row_filter=policy.filter_resources)


@router.get("/ask")
def get_ask(
    q: str,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    fieldguide.mark(user, "search")
    policy = projection_policy.ProjectionPolicy(
        request.app.state.skein_registry.policy_engine,
        subject,
        "skein.rest.get.ask",
        "rest",
        viewer,
    )
    with db.read_transaction():
        return search.ask(q, viewer=viewer, row_filter=policy.filter_resources)


@router.get("/field-guide")
def get_field_guide(user: CurrentUser):
    """Self-scoped by construction: the only person parameter is the caller.
    There is deliberately no way to read another person's guide."""
    return fieldguide.guide(user)


@router.get("/field-guide/hint")
def get_field_guide_hint(user: CurrentUser):
    return fieldguide.hint(user)


class DismissKnot(BaseModel):
    knot: str = Field(min_length=1, max_length=64)


@router.post("/field-guide/dismiss")
def post_field_guide_dismiss(body: DismissKnot, user: CurrentUser):
    # the only write on this surface — cap it like every other write, or a
    # spoofed-name loop mints users + unlock rows without bound
    ratelimit.check("write", user)
    try:
        return fieldguide.dismiss(user, body.knot)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/whoami")
def get_whoami(user: CurrentUser, request: Request):
    """Who the API thinks you are and how strongly — the Settings page uses
    this to validate a pasted key without the user needing to know anything."""
    from ..services import api_keys
    from .deps import is_named_admin

    strong = bool(getattr(request.state, "strong_auth", False))
    return {
        "user": user,
        "strong": strong,
        # Administrator by CONFIGURATION (SKEIN_ADMINS or the OIDC group), not
        # the scarcity fallback that makes every key holder one in a default
        # trusted-header deployment. This is the test _crew_steward applies, so
        # a surface that shows the crew controls on this flag shows exactly the
        # ones the server will accept. Gated on strong for the same reason
        # keys_minted is: an unproven identity is whatever the caller typed.
        "admin": strong and is_named_admin(user, getattr(request.state, "auth_groups", [])),
        # active only — after a revoke-all, Settings must show the bootstrap
        # command again, not "a key exists, paste it". Counted only for a
        # proven identity: a bare X-User names anyone, and the count is the
        # same fact GET /keys refuses to weak callers. Zero is also what
        # Settings must act on here — a caller with no proven key needs the
        # bootstrap command, whatever the roster holds.
        "keys_minted": api_keys.active_key_count(user) if strong else 0,
    }


@router.post("/keys/request")
def post_key_request(user: CurrentUser):
    from ..services.api_keys import request_key

    # ABOVE the try, never inside it: RateLimited subclasses ValueError, so
    # the handler below caught the cap and answered 400 — wire-identical to a
    # malformed request, and stripping the Retry-After header the class exists
    # to carry. This surface is capped at 3/minute and is the one a client is
    # most likely to retry.
    ratelimit.check("keys_request", user)
    try:
        return request_key(user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/briefing")
def get_briefing(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    policy = projection_policy.ProjectionPolicy(
        request.app.state.skein_registry.policy_engine,
        subject,
        "skein.rest.get.briefing",
        "rest",
        viewer,
    )
    with db.read_transaction():
        return briefing.my_day(
            user,
            viewer,
            policy.filter_rows,
            policy.filter_resources,
            policy.allows_unclassified(),
            policy.permits,
        )


@router.get("/attention")
def get_attention(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    # `count` IS `yours`, not the Inbox number. Both readers of this field —
    # the browser tab title and `skein attention` — say "waiting on you", and
    # the Inbox total said that about a queue anyone may work. The nav badge
    # reads `inbox` for its own destination.
    #
    # The viewer rides along because `yours` must equal what /briefing's header
    # prints, and that number is viewer-scoped (services/briefing.py).
    with db.read_transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.attention")
        counts = briefing.attention_count(user, viewer)
        return {"count": counts["yours"], **counts}


class KeyIn(BaseModel):
    label: str = Field("", max_length=100)


# Key MUTATION requires an existing key (StrongUser): minting on X-User
# identity alone would let anyone who can reach the API become anyone and
# defeat the whole private-record boundary. First key per person:
# python -m app.bootstrap_key.


@router.post("/keys")
def post_key(body: KeyIn, user: StrongUser):
    return api_keys.create_key(user, body.label)


@router.get("/keys")
def get_keys(user: StrongUser):
    # StrongUser, like POST and DELETE below: under trusted-header a bare
    # X-User names anyone, so CurrentUser here hands out the prefix, label,
    # and last-used time of another person's credentials for the asking.
    return api_keys.list_keys(user)


@router.delete("/keys/{key_id}")
def delete_key(key_id: int, user: StrongUser):
    return api_keys.revoke_key(key_id, user)


@router.get("/admin/keys")
def get_all_keys(user: AdminUser):
    # key metadata (owners, prefixes, last use) is admin surface — one
    # teammate must not enumerate another's credentials; matches revoke-all
    # and /admin/export
    return api_keys.list_all_keys()


@router.post("/admin/keys/revoke-all")
def post_revoke_all_keys(user: AdminUser):
    return api_keys.revoke_all_keys(actor=user)


@router.get("/notifications")
def get_notifications(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    unread_only: bool = True,
):
    with db.read_transaction():
        policy = projection_policy.ProjectionPolicy(
            request.app.state.skein_registry.policy_engine,
            subject,
            "skein.rest.get.notifications",
            "rest",
            viewer,
        )
        return notifications.list_notifications_filtered(
            user,
            unread_only,
            policy.permits,
            allow_unclassified=policy.allows_unclassified(),
            viewer=viewer,
        )


class MarkReadIn(BaseModel):
    notification_id: int = 0  # 0 = mark all read


@router.post("/notifications/read")
def post_notifications_read(body: MarkReadIn, user: CurrentUser):
    return notifications.mark_read(user, body.notification_id)


@router.get("/memories")
def get_memories(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    q: str = "",
):
    # engagement_id=None: this endpoint BROWSES, and it is the only surface
    # that lists memories or offers to delete one (app/agents/page.tsx). A
    # predicate that hid an engagement's memories here would leave them
    # steering every conversation about that work from a row no human could
    # reach (services/memory.py::recall names the three states).
    with db.read_transaction():
        rows = memory.recall(q, user=user, viewer=viewer, engagement_id=None)
        contexts = policy_context.engagement_linked_collection_contexts("memory", rows, viewer)
        return _permitted_collection(
            request,
            subject,
            rows,
            contexts,
            action="skein.rest.get.memories",
            resource_type="memory",
        )


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, user: CurrentUser):
    ratelimit.check("delete", user)
    try:
        return memory.forget(memory_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/pulse")
def get_pulse(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.read_transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.pulse")
        return pulse.pulse()


@router.get("/portfolio/health")
def get_portfolio_health(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    policy = projection_policy.ProjectionPolicy(
        request.app.state.skein_registry.policy_engine,
        subject,
        "skein.rest.get.portfolio.health",
        "rest",
        viewer,
    )
    with db.read_transaction():
        return policy.filter_rows(
            "engagement",
            portfolio.engagement_health(viewer, resource_filter=policy.permits),
        )


@router.get("/portfolio/conflicts")
def get_portfolio_conflicts(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.read_transaction():
        _require_opaque_project_policy(
            request, subject, viewer, "skein.rest.get.portfolio.conflicts"
        )
        return portfolio.allocation_conflicts(viewer)


@router.get("/portfolio/flow")
def get_portfolio_flow(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.read_transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.portfolio.flow")
        return portfolio.flow_metrics()


@router.get("/portfolio/forecast")
def get_portfolio_forecast(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.read_transaction():
        _require_opaque_project_policy(
            request, subject, viewer, "skein.rest.get.portfolio.forecast"
        )
        return portfolio.slip_forecast()


@router.post("/portfolio/readout")
def post_portfolio_readout(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    ratelimit.check("artifact", user)
    with db.transaction():
        _require_opaque_project_policy(
            request,
            subject,
            viewer,
            "skein.rest.post.portfolio.readout",
        )
        return readout.exec_readout(actor=user)


class WhatIfIn(BaseModel):
    people: list[Annotated[str, Field(max_length=64)]] = Field(max_length=20)
    percent: int = 50


@router.post("/intake/{request_id}/what-if")
def post_what_if(
    request_id: int,
    body: WhatIfIn,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.read_transaction():
        _require_opaque_project_policy(
            request,
            subject,
            viewer,
            "skein.rest.post.intake.what-if",
        )
        return portfolio.what_if(request_id, body.people, body.percent, viewer)


@router.get("/planning")
def get_planning(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    weeks: int = 6,
):
    """The Monday cockpit: one read, in meeting order (services/planning.py).

    CurrentUser, not AdminUser: the manager controls this page hosts are
    ordinary CurrentUser writes today, and gating the READ would be a new
    authorization rule invented in a route rather than in routes/deps.py.
    The viewer carries the scope, so the page shows what its caller may see."""
    with db.read_transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.planning")
        return planning.cockpit(viewer, ahead_weeks=weeks)


@router.get("/week")
def get_week(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    week: str = "",
):
    with db.read_transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.week")
        return weekly.week_view(week)


@router.get("/week/draft")
def get_week_draft(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    week: str = "",
):
    with db.read_transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.week.draft")
        return weekly.draft_plan(week)


class WeekPlanIn(BaseModel):
    week: str = Field("", max_length=8)
    task_ids: list[int] = Field(max_length=200)


@router.post("/week/plan")
def post_week_plan(
    body: WeekPlanIn,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.transaction():
        for task_id in body.task_ids:
            try:
                task = work.get_task(task_id, viewer)
            except (db.NotFound, ValueError):
                continue
            attributes = work.task_read_policy_context(task, viewer)
            enforce_decision(
                decide(
                    request,
                    subject,
                    "skein.rest.post.week.plan",
                    "task",
                    resource_id=str(task_id),
                    project_type=attributes.get("project_type", ""),
                    classification=attributes.get("classification", ""),
                    attributes=attributes,
                )
            )
        return weekly.apply_plan(body.week or weekly.current_week(), body.task_ids, actor=user)


@router.get("/promises")
def get_promises(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    status: str = "",
    audience: str = "",
    direction: str = "",
):
    with db.read_transaction():
        rows = promises.list_promises(status, audience, viewer, direction)
        contexts = policy_context.engagement_linked_collection_contexts("promise", rows, viewer)
        return _permitted_collection(
            request,
            subject,
            rows,
            contexts,
            action="skein.rest.get.promises",
            resource_type="promise",
        )


class PromiseIn(BaseModel):
    promise: str = Field(max_length=500)
    to_whom: str = Field("", max_length=120)
    due_date: str = Field("", max_length=10)
    engagement_id: int = 0
    audience: str = Field("external", max_length=20)
    # 'received' records a promise made TO the team (migration 007)
    direction: str = Field("given", max_length=10)
    # No reader check on `to_whom`: it is deliberately not a roster name (the
    # default audience is external) — promises.add_promise says why.
    # the tier the writer picked, checked in the service: crew membership only.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/promises")
def post_promise(body: PromiseIn, user: CurrentUser):
    ratelimit.check("write", user)
    return promises.add_promise(**body.model_dump(), actor=user)


class PromiseStatusIn(BaseModel):
    status: str = Field(max_length=20)


class PromiseEditIn(BaseModel):
    promise: str = Field("", max_length=500)
    due_date: str = Field("", max_length=10)
    to_whom: str = Field("", max_length=120)


@router.patch("/promises/{promise_id}")
def patch_promise(promise_id: int, body: PromiseEditIn, user: CurrentUser):
    try:
        return promises.edit_promise(
            promise_id, body.promise, body.due_date, body.to_whom, actor=user
        )
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/promises/{promise_id}/status")
def post_promise_status(promise_id: int, body: PromiseStatusIn, user: CurrentUser):
    return promises.update_promise(promise_id, body.status, actor=user)


class SupersedeIn(BaseModel):
    title: str = Field(max_length=200)
    decision: str = Field(max_length=2000)
    context: str = Field("", max_length=4000)
    review_by: str = Field("", max_length=10)


@router.post("/decisions/{decision_id}/supersede")
def post_supersede(decision_id: int, body: SupersedeIn, user: CurrentUser):
    return collab.supersede_decision(decision_id, **body.model_dump(), decided_by=user, actor=user)


class ReconfirmIn(BaseModel):
    review_by: str = Field("", max_length=10)


@router.post("/decisions/{decision_id}/reconfirm")
def post_reconfirm(decision_id: int, body: ReconfirmIn, user: CurrentUser):
    return collab.reconfirm_decision(decision_id, body.review_by, actor=user)


@router.get("/review/stats")
def get_review_stats(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.read_transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.review.stats")
        return review.review_stats(viewer)


class FeedbackIn(BaseModel):
    kind: str = Field(max_length=40)
    input_text: str = Field("", max_length=2000)  # a pulse vote has no input text
    output: str = Field("", max_length=4000)
    verdict: str = Field("up", max_length=10)
    correction: str = Field("", max_length=2000)


@router.post("/feedback")
def post_feedback(body: FeedbackIn, user: CurrentUser):
    # outside the try, for the reason post_key_request states
    ratelimit.check("feedback", user)
    data = body.model_dump()
    if not data["input_text"]:
        if data["kind"] != "pulse":
            raise HTTPException(422, "input_text is required for non-pulse feedback")
        data["input_text"] = "weekly pulse"
    return feedback.record_feedback(**data, actor=user)


@router.get("/feedback")
def get_feedback(user: CurrentUser, kind: str = ""):
    return feedback.list_feedback(kind)


@router.get("/eval/capture")
def get_eval_capture(user: CurrentUser):
    return feedback.eval_capture()


# `force` is a SEPARATE, explicit ask, and it defaults off. Both routes passed
# force=True unconditionally, which walked straight past the weekly claim these
# rituals keep: every click re-briefed the whole roster with the same personal
# notifications, and the cockpit's "already ran this week" branch could never be
# reached because the route it calls never returned that answer. The scheduler
# runs unforced too, so a manual Monday click after the 06:30 job is a no-op
# that says so rather than a second notification for everybody.
@router.post("/rituals/week-open")
def post_week_open(user: CurrentUser, force: bool = False):
    ratelimit.check("ritual", user)  # each run notifies people — cap the amplifier
    return rituals.week_open(actor=user, force=force)


@router.post("/rituals/week-close")
def post_week_close(user: CurrentUser, force: bool = False):
    ratelimit.check("ritual", user)
    return rituals.week_close(actor=user, force=force)


@router.get("/agents")
def get_agents(user: CurrentUser):
    return delegation.mission_control()


@router.get("/agents/status")
def get_agents_status(user: CurrentUser):
    """Plain-language state of the agent layer — the UI must never make mock
    mode look like a live model, or hide whether the review gate is on."""
    from .. import config

    return {
        "provider": config.MODEL_PROVIDER,
        # through the service, not config.MODEL_ID: with a pick in force the
        # strip would otherwise claim a model the deployment is not running —
        # the exact lie the CONTEXT_STRATEGY comment in config.py forbids
        "model": settings.model_pick_state()["model"],
        "provider_error": config.MODEL_PROVIDER_ERROR,
        "models_error": config.MODELS_ERROR,
        "review_gate": config.AGENT_REVIEW,
        # why the trust card cannot fill, when it cannot. An empty card reads
        # as "no data yet" — under a gate-off or weak-identity deployment the
        # truth is "cannot produce data", which is an operator's fix, not a
        # wait (services/delegation.py::trust_blocked)
        "trust_blocked": delegation.trust_blocked(),
        # the unattended runner: which agents it may wake, and whether the
        # model half can run at all. Empty list = nothing wakes an agent,
        # which is the default and must be visible rather than assumed.
        "runner_agents": config.AGENT_RUNNER,
        "runner_daily_tokens": config.AGENT_DAILY_TOKENS,
        # empty on mock: no Strands agent is built, so no strategy applies and
        # claiming one would describe machinery that is not running
        "context_strategy": (
            settings.effective_context_strategy() if config.EFFECTIVE_PROVIDER != "mock" else ""
        ),
        "context_error": config.CONTEXT_STRATEGY_ERROR,
    }


class ContextStrategyIn(BaseModel):
    # extra=forbid + no default: a mistyped field name would otherwise fall
    # through to "" — the CLEAR sentinel — silently reverting the whole team to
    # the env default and answering 200 as if it were deliberate
    model_config = ConfigDict(extra="forbid")
    strategy: str = Field(max_length=20)


@router.get("/settings/context-strategy")
def get_context_strategy(user: CurrentUser):
    """Reads for everyone (the agent strip already shows it); writing is
    operator-only. `default` is what an empty override falls back to."""
    from .. import config

    return {
        "strategy": settings.effective_context_strategy(),
        "override": settings.context_strategy_override(),
        "default": config.CONTEXT_STRATEGY,
        "choices": list(config.CONTEXT_STRATEGIES),
        "applies": config.EFFECTIVE_PROVIDER != "mock",
    }


@router.post("/settings/context-strategy")
def post_context_strategy(body: ContextStrategyIn, user: AdminUser):
    """AdminUser: this changes what every chat costs for the whole team, so
    it needs an administrator, not just any credential holder. Rate-capped
    because each call appends to the activity ledger, which is never pruned."""
    ratelimit.check("write", user)
    try:
        return settings.set_context_strategy(body.strategy, actor=user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class ModelPickIn(BaseModel):
    # extra=forbid + no default: same trap ContextStrategyIn names — a
    # mistyped field falling through to "" is the CLEAR sentinel
    model_config = ConfigDict(extra="forbid")
    model: str = Field(max_length=200)


@router.get("/settings/model")
def get_model_pick(user: CurrentUser):
    """Reads for everyone — agents/status already names the model to every
    signed-in user, and the menu's prices are list prices, not capacity
    reconnaissance. Writing is admin-only."""
    from .. import config

    return {
        **settings.model_pick_state(),
        # entry params are NOT served: they are operator-authored request
        # bodies, and a token an operator parked there must not reach every
        # signed-in browser. The picker renders the fields below only.
        "menu": [
            {k: e[k] for k in ("id", "label", "detail", "max_tokens", "context_tokens", "price")}
            for e in config.MODELS.values()
        ],
        "menu_error": config.MODELS_ERROR,
        "applies": config.EFFECTIVE_PROVIDER != "mock" and bool(config.MODELS),
        "provider": config.MODEL_PROVIDER,
    }


@router.post("/settings/model")
def post_model_pick(body: ModelPickIn, user: AdminUser):
    """AdminUser: the pick changes what every chat costs for the whole team.
    Rate-capped because each call appends to the activity ledger, which is
    never pruned. The service refuses ids outside the menu — hiding the
    picker on a faulted registry is UI, this refusal is the enforcement."""
    ratelimit.check("write", user)
    try:
        return settings.set_model_pick(body.model, actor=user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class TuningIn(BaseModel):
    # extra=forbid, and `value` has NO default: a mistyped field would fall
    # through to None, which is the CLEAR sentinel, silently returning a knob
    # to its default while answering 200. Same trap ContextStrategyIn names.
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=64)
    # null clears the override; the service range-checks the number
    value: int | None


@router.get("/settings/tuning")
def get_tuning(user: AdminUser):
    """AdminUser for the READ too, unlike the context strategy above. These
    numbers are the deployment's capacity limits, and listing them tells an
    ordinary caller exactly how much room is left before a cap refuses —
    which is reconnaissance, not a preference anyone needs to see."""
    return tuning.list_tunables()


@router.post("/settings/tuning")
def post_tuning(body: TuningIn, user: AdminUser):
    """Rate-capped for the reason the context strategy is: each call appends
    to the activity ledger, which is never pruned."""
    ratelimit.check("write", user)
    try:
        return tuning.set_tunable(body.name, body.value, actor=user)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/agents/trust")
def get_agents_trust(user: CurrentUser):
    return delegation.trust_scores()


@router.get("/agents/entities")
def get_agent_entities(user: CurrentUser):
    from ..services.delegation import NO_AUTHORITY

    # one set, shared with set_authority: excluding here but validating there
    # let a direct POST store a grant this picker cannot produce
    from ..services.lexicon import entity_label
    from ..services.review import _registry
    from ..tools._gate import ALWAYS_REVIEW

    return {
        # the label enumerates every capability the grant carries: a matrix
        # row is keyed on the entity, and "a blocker" hid that the same grant
        # also resolves them
        "entities": [
            {"entity": e, "label": entity_label(e)}
            for e in sorted(_registry())
            if e not in NO_AUTHORITY
        ],
        # the gate takes the review path for these before it reads the level,
        # so the card must not offer or display "acts alone" for them
        "always_review": sorted(ALWAYS_REVIEW),
    }


@router.get("/agents/authority")
def get_agents_authority(user: CurrentUser, agent: str = ""):
    return delegation.authority_matrix(agent)


class AuthorityIn(BaseModel):
    agent: str = Field(max_length=64)
    entity: str = Field(max_length=40)
    level: str = Field(max_length=20)


@router.post("/agents/authority")
def post_agents_authority(body: AuthorityIn, user: AdminUser):
    """Authority IS the kill switch — administrators only, and never a
    spoofable X-User."""
    return delegation.set_authority(body.agent, body.entity, body.level, actor=user)


@router.get("/agents/{agent}/inbox")
def get_agent_inbox(
    agent: str,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    if not users.is_agent(agent):
        # names no name: an error never echoes a rejected value back (CLAUDE.md)
        raise HTTPException(status_code=404, detail="no such agent. Check the name.")
    policy = projection_policy.ProjectionPolicy(
        request.app.state.skein_registry.policy_engine,
        subject,
        "skein.rest.get.agents.inbox",
        "rest",
        viewer,
    )
    with db.read_transaction():
        return delegation.agent_inbox(
            agent,
            viewer,
            task_filter=lambda task_id, attributes: policy.permits("task", task_id, attributes),
            resource_filter=policy.permits,
            allow_unclassified=policy.allows_unclassified(),
        )


class DelegateIn(BaseModel):
    agent: str = Field(max_length=64)
    sponsor: str = Field("", max_length=64)


@router.post("/tasks/{task_id}/delegate")
def post_delegate(
    task_id: int,
    body: DelegateIn,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    # capped like every other content write: this UPDATEs a task, appends a
    # hash-chained activity row, and notifies the sponsor — the amplifier
    # patch_task's own comment names. It became a one-click control when the
    # task peek shipped.
    ratelimit.check("write", user)
    # Delegating to an EXISTING agent is ordinary work. Delegating to a name
    # that does not exist MINTS an agent identity (delegate_task calls
    # ensure_user), and routes/deps.py refuses an agent name at every door —
    # so an unproven caller could register a teammate's name before they join
    # and lock them out of sign-in, repairable only through rename_user. The
    # scarce credential is the bar for creating an identity, matching
    # POST /api/keys; using one that already exists is not gated.
    with db.transaction():
        task = work.get_task(task_id, viewer)
        domain = work.task_read_policy_context(task, viewer)
        enforce_decision(
            decide(
                request,
                subject,
                "skein.rest.post.tasks.delegate",
                "task",
                resource_id=str(task_id),
                project_type=domain["project_type"],
                classification=domain["classification"],
                attributes=domain,
            )
        )
        if not users.is_agent(body.agent) and not getattr(request.state, "strong_auth", False):
            raise HTTPException(
                403,
                # lowercase fragment, like the other 187: the frontend joins a
                # refusal into its own sentence (docs/LEXICON.md, "Backend
                # refusal shape")
                "creating an agent identity requires a personal API key."
                " Delegate to an agent that already exists, or get your first key from"
                " whoever runs the server (python -m app.bootstrap_key <you>) and paste"
                " it in Settings, step 2.",
            )
        return delegation.delegate_task(task_id, body.agent, body.sponsor or user, actor=user)


@router.get("/context-pack")
def get_context_pack(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    engagement: int = 0,
    crew: int = 0,
):
    policy = projection_policy.ProjectionPolicy(
        request.app.state.skein_registry.policy_engine,
        subject,
        "skein.rest.get.context-pack",
        "rest",
        viewer,
    )
    with db.read_transaction():
        if engagement:
            attributes = policy_context.existing_scoped("engagement", engagement, viewer)
            if not attributes or not policy.permits("engagement", engagement, attributes):
                raise db.NotFound(f"no engagement #{engagement}")
            return {
                "engagement": engagement,
                "content": context_pack.build_engagement_pack(
                    engagement,
                    viewer,
                    policy.permits,
                ),
            }
        return context_pack.get_pack(
            actor=user,
            crew_id=crew,
            viewer=viewer,
            resource_filter=policy.permits,
        )


@router.post("/context-pack/publish")
def post_context_pack(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    crew: int = 0,
):
    with db.transaction():
        _require_opaque_project_policy(
            request,
            subject,
            viewer,
            "skein.rest.post.context-pack.publish",
        )
        return context_pack.publish_pack(actor=user, crew_id=crew, viewer=viewer)


@router.get("/onboarding")
def get_onboarding(user: CurrentUser):
    from ..services import onboarding

    return onboarding.checklist(user)


@router.get("/adoption")
def get_adoption(user: CurrentUser, weeks: int = 4):
    from ..services import adoption as adoption_svc

    return adoption_svc.adoption(weeks)


@router.get("/insights")
def get_insights(user: CurrentUser):
    from ..services import insights as insights_svc

    return insights_svc.insights()


@router.get("/findings")
def get_findings(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    weeks: int = 4,
):
    from ..services import insights as insights_svc

    with db.read_transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.findings")
        return insights_svc.list_findings(weeks)


class FindingDispositionIn(BaseModel):
    disposition: str = Field(max_length=20)
    reason: str = Field("", max_length=500)
    deferred_until: str = Field("", max_length=10)


CHAIN_RULES = ("activity_chain_broken", "ledger_rows_adopted")


@router.post("/findings/{finding_id}/disposition")
def post_finding_disposition(
    finding_id: int, body: FindingDispositionIn, user: CurrentUser, request: Request
):
    from ..services import insights as insights_svc
    from .deps import _require_strong

    # Ordinary findings take CurrentUser, the way approvals do — any
    # identified human may act on team work. The ledger rules are the
    # exception: dismissing one drops it from the daily digest for good, and
    # since adoption replaced the permanent chain alarm, that digest line is
    # the only PUSH signal a smuggled row now produces. Under weak identity,
    # whoever caused the adoption could silence the report of it with a
    # chosen header.
    if insights_svc.finding_rule(finding_id) in CHAIN_RULES:
        _require_strong(getattr(request.state, "strong_auth", False))
    return insights_svc.disposition_finding(
        finding_id, body.disposition, body.reason, body.deferred_until, actor=user
    )


class ConvertIn(BaseModel):
    kind: str = Field(max_length=20)
    title: str = Field("", max_length=200)


@router.post("/findings/{finding_id}/convert")
def post_finding_convert(finding_id: int, body: ConvertIn, user: CurrentUser):
    from ..services import insights as insights_svc

    return insights_svc.convert_finding(finding_id, body.kind, body.title, actor=user)


@router.post("/findings/run")
def post_findings_run(user: CurrentUser):
    from ..services import insights as insights_svc

    return insights_svc.run_findings(actor=user)


@router.get("/usage")
def get_usage(user: CurrentUser, viewer: ViewerDep):
    """Token and estimated-cost accounting. Costs are estimates from the
    operator's price table; unpriced_calls says how much each sum cannot see."""
    return {
        "models": usage.usage_summary(),
        "engagements": usage.engagement_costs(viewer=viewer),
        "month": usage.month_to_date(),
        "prices_error": config.MODEL_PRICES_ERROR,
    }


# ---- writes ----------------------------------------------------------------


class MilestoneIn(BaseModel):
    # from services/work.py, never a literal: the service enforces the same two
    # bounds on every write path, and a second copy of the number here is how
    # the REST door and the agent door drift apart again
    title: str = Field(max_length=work.TITLE_LEN)
    description: str = Field("", max_length=work.DESCRIPTION_LEN)
    project: str = Field("default", max_length=120)
    owner: str = Field("", max_length=64)
    due_date: str = Field("", max_length=10)
    # the tier the writer picked, checked in the service: crew membership only.
    # No assignee check here — a milestone has an owner, not an assignee, and
    # create_milestone takes no readability check on it.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/milestones")
def post_milestone(body: MilestoneIn, user: CurrentUser):
    ratelimit.check("write", user)
    return work.create_milestone(**body.model_dump(), actor=user)


class MilestonePatch(BaseModel):
    # caps match MilestoneIn — see the note on TaskPatch
    status: str = Field("", max_length=20)
    title: str = Field("", max_length=work.TITLE_LEN)
    description: str = Field("", max_length=work.DESCRIPTION_LEN)
    owner: str = Field("", max_length=64)
    due_date: str = Field("", max_length=10)
    engagement_id: int = 0  # relink (-1 unlinks)


@router.patch("/milestones/{milestone_id}")
def patch_milestone(milestone_id: int, body: MilestonePatch, user: CurrentUser):
    return work.update_milestone(milestone_id, **body.model_dump(), actor=user)


class TaskIn(BaseModel):
    title: str = Field(max_length=work.TITLE_LEN)
    description: str = Field("", max_length=work.DESCRIPTION_LEN)
    milestone_id: int = 0
    assignee: str = Field("", max_length=64)
    priority: str = Field("medium", max_length=10)
    due_date: str = Field("", max_length=10)
    engagement_id: int = 0
    # the tier the writer picked, checked in the service: crew membership,
    # and whether the assignee could read what they are being handed
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/tasks")
def post_task(
    body: TaskIn,
    user: CurrentUser,
    request: Request,
    subject: PolicySubjectDep,
):
    ratelimit.check("write", user)
    values = body.model_dump()
    with db.transaction():
        domain = work.task_create_policy_context(
            milestone_id=body.milestone_id,
            engagement_id=body.engagement_id,
            visibility=body.visibility,
            crew_id=body.crew_id,
            actor=user,
        )
        enforce_decision(
            decide(
                request,
                subject,
                "skein.rest.post.tasks",
                "task",
                project_type=domain["project_type"],
                classification=domain["classification"],
                attributes=values,
            )
        )
        return work.create_task(**values, actor=user)


class TaskPatch(BaseModel):
    # every cap here MUST match TaskIn above: a create-time bound that a PATCH
    # can step around is not a bound, and the oversized value lands in the
    # list API, the search index and the team-visible ledger just the same
    status: str = Field("", max_length=20)
    assignee: str = Field("", max_length=64)
    priority: str = Field("", max_length=10)
    due_date: str = Field("", max_length=10)
    description: str = Field("", max_length=work.DESCRIPTION_LEN)
    title: str = Field("", max_length=work.TITLE_LEN)
    committed_week: str = Field("", max_length=10)
    waiting_on: str = Field("", max_length=32)  # "blocker:12" | "task:3" | "-"
    milestone_id: int = 0  # relink (-1 unlinks)
    engagement_id: int = 0  # relink (-1 unlinks)


@router.patch("/tasks/{task_id}")
def patch_task(
    task_id: int,
    body: TaskPatch,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    # edits scan for @mentions, so an uncapped PATCH is a notification
    # amplifier — same cap as the create routes
    ratelimit.check("write", user)
    changes = body.model_dump()
    # Resolve the target domain state and write under one SQLite write
    # transaction. The generic route dependency remains an early refusal,
    # while this check is the authoritative decision that cannot race a
    # concurrent project relink.
    with db.transaction():
        domain = work.task_update_policy_context(task_id, changes, actor=user)
        enforce_decision(
            decide(
                request,
                subject,
                "skein.rest.patch.tasks",
                "task",
                resource_id=str(task_id),
                project_type=str(domain.get("project_type") or ""),
                classification=str(domain.get("classification") or ""),
                attributes=domain,
            )
        )
        # `strong` is read off the viewer, whose name survives only a proved
        # identity. A sponsor's direct close records that proof strength.
        return work.update_task(
            task_id,
            **changes,
            actor=user,
            strong=bool(viewer.name),
        )


class QuestionIn(BaseModel):
    question: str = Field(max_length=1000)
    assigned_to: str = Field("", max_length=64)
    # `assigned_to` is checked as a READER (collab.ask_question).
    # the tier the writer picked, checked in the service: crew membership only.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/questions")
def post_question(body: QuestionIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.ask_question(
        body.question,
        asked_by=user,
        assigned_to=body.assigned_to,
        actor=user,
        visibility=body.visibility,
        crew_id=body.crew_id,
    )


class QuestionPatch(BaseModel):
    assigned_to: str = Field(max_length=64)


@router.patch("/questions/{question_id}")
def patch_question(question_id: int, body: QuestionPatch, user: CurrentUser):
    # assignment NOTIFIES the named person every time (services/collab.py), so
    # this is a send, not an edit — the one PATCH here that a loop turns into
    # somebody else's flooded inbox
    ratelimit.check("write", user)
    return collab.assign_question(question_id, body.assigned_to, actor=user)


class AnswerIn(BaseModel):
    answer: str = Field(max_length=4000)


@router.post("/questions/{question_id}/answer")
def post_answer(question_id: int, body: AnswerIn, user: CurrentUser):
    # answers scan for @mentions — capped like every other notifying write
    ratelimit.check("write", user)
    return collab.answer_question(question_id, body.answer, answered_by=user, actor=user)


class DecisionIn(BaseModel):
    title: str = Field(max_length=200)
    decision: str = Field(max_length=2000)
    context: str = Field("", max_length=4000)
    review_by: str = Field("", max_length=10)
    category: str = Field("", max_length=40)
    # the tier the writer picked, checked in the service: crew membership only.
    # No assignee check here — a decision names nobody to hand work to.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/decisions")
def post_decision(body: DecisionIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.record_decision(
        body.title,
        body.decision,
        body.context,
        decided_by=user,
        review_by=body.review_by,
        category=body.category,
        actor=user,
        visibility=body.visibility,
        crew_id=body.crew_id,
    )


class StandupIn(BaseModel):
    yesterday: str = Field("", max_length=2000)
    today: str = Field("", max_length=2000)
    blockers: str = Field("", max_length=2000)
    # the tier the writer picked, checked in the service: crew membership, and
    # the OWNER of the blocker this standup forks — which is always the author
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/standups")
def post_standup(body: StandupIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.post_standup(
        user,
        body.yesterday,
        body.today,
        body.blockers,
        actor=user,
        visibility=body.visibility,
        crew_id=body.crew_id,
    )


class NoteIn(BaseModel):
    topic: str = Field(max_length=200)
    content: str = Field(max_length=20_000)
    # the tier the writer picked, checked in the service: crew membership only.
    # No assignee check here — a note names nobody to hand work to.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/notes")
def post_note(body: NoteIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.save_note(
        body.topic,
        body.content,
        author=user,
        actor=user,
        visibility=body.visibility,
        crew_id=body.crew_id,
    )


class OutcomeIn(BaseModel):
    outcome: str = Field(max_length=10)


@router.get("/stakeholders")
def get_stakeholders(user: CurrentUser, viewer: ViewerDep):
    """Open threads with people outside the roster. Read-only: every row is
    already written by somebody doing ordinary work (services/stakeholders.py)."""
    return stakeholders.open_threads(viewer)


@router.get("/events/{event_id}/stakeholders")
def get_event_stakeholders(event_id: int, user: CurrentUser, viewer: ViewerDep):
    """What is open with the outside people attending this meeting — useful in
    the hour before you speak to them, which a digest of everything is not."""
    return stakeholders.brief_for_event(event_id, viewer)


@router.post("/events/{event_id}/outcome")
def post_event_outcome(event_id: int, body: OutcomeIn, user: CurrentUser):
    """What came out of a meeting. Set by a reader, never inferred: guessing
    from "was anything written near this time" is wrong in both directions —
    an outcome recorded an hour later reads as empty, an unrelated note reads
    as an outcome (migration 008)."""
    ratelimit.check("write", user)
    try:
        return schedule.record_outcome(event_id, body.outcome, actor=user)
    except db.NotFound:
        # db.NotFound subclasses ValueError, so a bare `except ValueError`
        # turned "no event #12" into a 400. The id is in the PATH here, which
        # scope.missing_text says is the 404 case — and the sibling route
        # GET /events/{id}/stakeholders already answers 404 for the same row.
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class EventIn(BaseModel):
    title: str = Field(max_length=200)
    starts_at: str = Field(max_length=25)
    ends_at: str = Field("", max_length=25)
    description: str = Field("", max_length=4000)
    attendees: str = Field("", max_length=500)
    # what the meeting is FOR, written before it runs (migration 008). The
    # post-meeting attention item quotes it back, which is what makes "did
    # this produce anything" answerable by whoever attended.
    agenda: str = Field("", max_length=2000)
    engagement_id: int = 0
    # the tier the writer picked, checked in the service: crew membership only.
    # No assignee check here — `attendees` is free text, not a roster join.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/events")
def post_event(body: EventIn, user: CurrentUser):
    ratelimit.check("write", user)
    return schedule.schedule_event(**body.model_dump(), actor=user)


class BlockerIn(BaseModel):
    title: str = Field(max_length=200)
    detail: str = Field("", max_length=4000)
    owner: str = Field("", max_length=64)
    impact: str = Field("medium", max_length=10)
    task_id: int = 0
    # the tier the writer picked, checked in the service: crew membership, and
    # the owner is checked as a READER (blockers.raise_blocker). Present here
    # even though the two doors that usually create a blocker inherit instead
    # — capture.py and collab.post_standup. Every create body whose service
    # accepts a tier offers one, pinned by
    # tests/test_visibility_writes.py::test_a_create_body_exposes_the_tier_its_service_accepts.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/blockers")
def post_blocker(
    body: BlockerIn,
    user: CurrentUser,
    request: Request,
    subject: PolicySubjectDep,
):
    ratelimit.check("write", user)
    try:
        with db.transaction():
            domain = blockers.create_policy_context(
                body.task_id,
                body.visibility,
                body.crew_id,
                actor=user,
            )
            enforce_decision(
                decide(
                    request,
                    subject,
                    "skein.rest.post.blockers",
                    "blocker",
                    project_type=domain["project_type"],
                    classification=domain["classification"],
                    attributes=domain,
                )
            )
            return blockers.raise_blocker(**body.model_dump(), actor=user)
    except (db.NotFound, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


class ResolveIn(BaseModel):
    resolution: str = Field("", max_length=2000)


class BlockerEditIn(BaseModel):
    title: str = Field("", max_length=200)
    detail: str = Field("", max_length=4000)
    owner: str = Field("", max_length=64)


@router.patch("/blockers/{blocker_id}")
def patch_blocker(
    blocker_id: int,
    body: BlockerEditIn,
    user: CurrentUser,
    request: Request,
    subject: PolicySubjectDep,
):
    try:
        with db.transaction():
            domain = blockers.existing_policy_context(blocker_id, actor=user)
            enforce_decision(
                decide(
                    request,
                    subject,
                    "skein.rest.patch.blockers",
                    "blocker",
                    resource_id=str(blocker_id),
                    project_type=domain["project_type"],
                    classification=domain["classification"],
                    attributes=domain,
                )
            )
            return blockers.edit_blocker(
                blocker_id, body.title, body.detail, body.owner, actor=user
            )
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/blockers/{blocker_id}/resolve")
def post_resolve_blocker(
    blocker_id: int,
    body: ResolveIn,
    user: CurrentUser,
    request: Request,
    subject: PolicySubjectDep,
):
    with db.transaction():
        domain = blockers.existing_policy_context(blocker_id, actor=user)
        enforce_decision(
            decide(
                request,
                subject,
                "skein.rest.post.blockers.resolve",
                "blocker",
                resource_id=str(blocker_id),
                project_type=domain["project_type"],
                classification=domain["classification"],
                attributes=domain,
            )
        )
        return blockers.resolve_blocker(blocker_id, body.resolution, actor=user)


class IntakeIn(BaseModel):
    title: str = Field(max_length=200)
    detail: str = Field("", max_length=4000)
    project_class: str = Field("", max_length=40)
    # the tier the writer picked, checked in the service: crew membership only.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/intake")
def post_intake(body: IntakeIn, user: CurrentUser):
    ratelimit.check("write", user)
    return intake.submit_request(
        body.title,
        body.detail,
        requester=user,
        project_class=body.project_class,
        actor=user,
        visibility=body.visibility,
        crew_id=body.crew_id,
    )


class ScoreIn(BaseModel):
    reach: int
    impact: int
    confidence: int
    effort: int


class IntakeEditIn(BaseModel):
    title: str = Field("", max_length=200)
    detail: str = Field("", max_length=4000)


@router.patch("/intake/{request_id}")
def patch_intake(request_id: int, body: IntakeEditIn, user: CurrentUser):
    try:
        return intake.edit_request(request_id, body.title, body.detail, actor=user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/intake/{request_id}/score")
def post_intake_score(request_id: int, body: ScoreIn, user: CurrentUser):
    return intake.score_request(request_id, **body.model_dump(), actor=user)


class DispositionIn(BaseModel):
    disposition: str = Field(max_length=20)
    reason: str = Field(max_length=2000)
    kind: str = Field("delivery", max_length=20)
    timebox_end: str = Field("", max_length=10)
    outcome: str = Field("", max_length=2000)
    lead: str = Field("", max_length=64)
    kill_criteria: str = Field("", max_length=500)


@router.post("/intake/{request_id}/disposition")
def post_intake_disposition(request_id: int, body: DispositionIn, user: CurrentUser):
    return intake.disposition_request(
        request_id,
        body.disposition,
        body.reason,
        kind=body.kind,
        timebox_end=body.timebox_end,
        outcome=body.outcome,
        lead=body.lead,
        kill_criteria=body.kill_criteria,
        actor=user,
    )


class ReviewActionIn(BaseModel):
    note: str = Field("", max_length=1000)


def _execute_extension_review(request: Request, invocation: dict, _change_id: int) -> dict:
    """Resume an approved invocation through the current composed registry."""
    registry = request.app.state.skein_registry
    if invocation.get("kind") == "tool":
        from ..extensions.tools import execute_reviewed_tool

        tool = registry.tool(str(invocation.get("tool") or ""))
        tool_execution = asyncio.run(execute_reviewed_tool(tool, invocation, registry))
        if tool_execution.error_code == "approval_stale":
            raise PermissionError("the reviewed tool approval became stale")
        return tool_execution.model_dump(mode="json")
    if invocation.get("kind") == "public_command":
        from ..public.work import _execute_reviewed_command

        return _execute_reviewed_command(invocation, registry)
    if invocation.get("kind") == "mcp_tool":
        from ..agents.mcp_tools import execute_reviewed_mcp

        result = asyncio.run(execute_reviewed_mcp(invocation, registry))
        if result.get("status") == "approval_stale":
            raise PermissionError("the reviewed remote tool approval became stale")
        return result
    if invocation.get("kind") == "core_tool":
        from ..agents.core_tools import execute_reviewed_core

        result = asyncio.run(execute_reviewed_core(invocation, registry))
        if result.get("status") != "completed":
            raise PermissionError("the current policy did not permit the reviewed stock tool")
        return result
    if (
        invocation.get("kind") == "workflow"
        and invocation.get("workflow_kind") == "playbook_policy"
    ):
        from ..extensions.policy import (
            policy_subject_from_data,
        )
        from ..public.workflow import WorkflowEngine, _issue_workflow_context

        saved_subject = invocation.get("subject")
        if not isinstance(saved_subject, dict):
            raise ValueError("the reviewed playbook identity is invalid")
        subject = registry.refresh_subject(policy_subject_from_data(saved_subject))
        slug = str(invocation.get("playbook") or "")
        definition = _reviewed_playbook_definition(invocation)
        project_type = str(definition.get("project_class") or slug)
        # review.approve_change refreshed the requester, evaluated the current
        # policy, and checked the reviewer immediately before this executor.
        # A second decision here could name different approvers without the
        # review service checking them. Execute the one qualified verdict.
        workflow_engine = WorkflowEngine(
            registry.workflow_actions,
            registry.policy_engine,
        )
        workflow_context = _issue_workflow_context(
            workflow_engine,
            subject,
            "human",
            project_type=project_type,
            run_id=str(invocation.get("run_id") or uuid4().hex),
            values={"project_type": project_type},
        )
        workflow_result = playbooks.instantiate(
            slug,
            str(invocation.get("engagement_name") or ""),
            str(invocation.get("lead") or ""),
            str(invocation.get("start_date") or ""),
            actor=str(invocation.get("actor") or subject.name),
            origin="human",
            workflow_engine=workflow_engine,
            workflow_context=workflow_context,
            expected_definition_digest=str(invocation.get("definition_digest") or ""),
        )
        if workflow_result.get("workflow", {}).get("status") == "review_required":
            return _queue_workflow_review(
                workflow_result,
                {
                    **invocation,
                    "project_type": project_type,
                    "values": {"project_type": project_type},
                },
            )
        return workflow_result
    if invocation.get("kind") == "workflow":
        from ..extensions.policy import (
            policy_decision_from_data,
            policy_subject_from_data,
        )
        from ..public.workflow import WorkflowEngine, _issue_workflow_context

        subject_data = invocation.get("subject") or {}
        saved_subject = policy_subject_from_data(subject_data)
        subject = registry.refresh_subject(saved_subject)
        definition = _reviewed_playbook_definition(invocation)
        current_project_type = str(
            definition.get("project_class") or invocation.get("playbook") or ""
        )
        approval_grants = dict(invocation.get("approval_grants") or {})
        reviewed_key = str(invocation.get("reviewed_key") or "")
        reviewed_fingerprint = str(
            invocation.get("_approval_grant") or invocation.get("reviewed_fingerprint") or ""
        )
        if not reviewed_key or not reviewed_fingerprint:
            raise ValueError("the reviewed workflow grant is incomplete")
        decision_data = invocation.get("_approval_decision")
        if not isinstance(decision_data, dict):
            raise ValueError("the reviewed workflow decision is incomplete")
        approved_decision = policy_decision_from_data(decision_data)
        approval_grants[reviewed_key] = reviewed_fingerprint
        workflow_engine = WorkflowEngine(
            registry.workflow_actions,
            registry.policy_engine,
        )
        workflow_context = _issue_workflow_context(
            workflow_engine,
            subject,
            "human",
            project_type=current_project_type,
            resource_id=str(invocation.get("resource_id") or ""),
            run_id=str(invocation.get("run_id") or uuid4().hex),
            values={
                **dict(invocation.get("values") or {}),
                "project_type": current_project_type,
            },
            approval_grants=approval_grants,
            approval_decisions={reviewed_key: approved_decision},
        )
        workflow_result = playbooks.instantiate(
            str(invocation.get("playbook") or ""),
            str(invocation.get("engagement_name") or ""),
            str(invocation.get("lead") or ""),
            str(invocation.get("start_date") or ""),
            actor=str(invocation.get("actor") or subject.name),
            origin="human",
            workflow_engine=workflow_engine,
            workflow_context=workflow_context,
            expected_definition_digest=str(invocation.get("definition_digest") or ""),
        )
        workflow = workflow_result.get("workflow", {})
        if workflow.get("error_code") == "STALE_APPROVAL_GRANT":
            raise PermissionError("the reviewed workflow approval became stale")
        if workflow.get("status") == "review_required":
            return _queue_workflow_review(
                workflow_result,
                {**invocation, "approval_grants": approval_grants},
            )
        return workflow_result
    raise ValueError("the extension review kind is not supported")


def _reviewed_playbook_definition(invocation: dict) -> dict:
    """Load only the exact playbook content stored with a durable review."""
    slug = str(invocation.get("playbook") or "")
    expected = str(invocation.get("definition_digest") or "")
    if not expected:
        raise PermissionError("The reviewed playbook has no content digest. Request a new review.")
    definition = playbooks.get_playbook(slug)
    if not playbooks.definition_digest_matches(expected, definition):
        raise PermissionError("The reviewed playbook changed. Request a new review.")
    return definition


def _queue_workflow_review(result: dict, invocation: dict) -> dict:
    workflow = result.get("workflow") or {}
    review_policy = workflow.pop("_review_policy", {})
    saved_policy_input = None
    if isinstance(review_policy, dict) and review_policy:
        from ..extensions.policy import policy_input_from_data, policy_subject_from_data

        saved_subject = review_policy.get("subject")
        if not isinstance(saved_subject, dict):
            raise ValueError("the workflow review identity is invalid")
        saved_policy_input = policy_input_from_data(
            review_policy,
            policy_subject_from_data(saved_subject),
        )
    obligations = tuple(str(value) for value in workflow.get("obligations") or ())
    groups = tuple(
        value.removeprefix("approver-group:")
        for value in obligations
        if value.startswith("approver-group:")
    )
    capabilities = tuple(
        value.removeprefix("approver-capability:")
        for value in obligations
        if value.startswith("approver-capability:")
    )
    plain = tuple(
        value
        for value in obligations
        if not value.startswith(("approver-group:", "approver-capability:"))
    )
    checkpoint = str(workflow.get("checkpoint") or "")
    review_key = str(workflow.get("review_key") or "")
    review_fingerprint = str(workflow.get("review_fingerprint") or "")
    if not review_key or not review_fingerprint:
        raise ValueError("the workflow review grant is incomplete")
    proposal = review.propose_extension_invocation(
        "workflow",
        {
            "playbook": invocation["playbook"],
            "engagement_name": invocation["engagement_name"],
            "checkpoint": checkpoint,
        },
        {
            **invocation,
            "reviewed_key": review_key,
            "reviewed_fingerprint": review_fingerprint,
        },
        summary=f"Continue workflow {invocation['playbook']} at {checkpoint}",
        actor=str(invocation["actor"]),
        requested_by=str(invocation["actor"]),
        policy_obligations=plain,
        approver_groups=groups,
        approver_capabilities=capabilities,
        review_owner=str(invocation["actor"]),
        policy_input=saved_policy_input,
    )
    workflow["review_id"] = proposal["id"]
    result["workflow"] = workflow
    return result


def _queue_playbook_policy_review(
    body: "InstantiateIn",
    *,
    actor: str,
    subject,
    definition_digest: str,
    policy_input,
    decision,
) -> dict:
    from ..extensions.policy import (
        PolicyEffect,
        policy_subject_data,
    )

    if decision.effect != PolicyEffect.REVIEW:
        raise PermissionError("the playbook review is no longer required")
    proposal = review.propose_extension_invocation(
        "workflow",
        {
            "playbook": body.playbook,
            "engagement_name": body.engagement_name,
        },
        {
            "playbook": body.playbook,
            "engagement_name": body.engagement_name,
            "lead": body.lead or actor,
            "start_date": body.start_date,
            "actor": actor,
            "subject": policy_subject_data(subject),
            "workflow_kind": "playbook_policy",
            "definition_digest": definition_digest,
            "run_id": uuid4().hex,
        },
        summary=f"Start governed playbook {body.playbook}",
        actor=actor,
        requested_by=actor,
        policy_obligations=decision.obligations,
        approver_groups=decision.approver_groups,
        approver_capabilities=decision.approver_capabilities,
        review_owner=actor,
        policy_input=policy_input,
    )
    obligations = [*decision.obligations]
    obligations.extend(f"approver-group:{value}" for value in decision.approver_groups)
    obligations.extend(f"approver-capability:{value}" for value in decision.approver_capabilities)
    return {
        "workflow": {
            "status": "review_required",
            "checkpoint": "workplace-policy",
            "error_code": "REVIEW_REQUIRED",
            "obligations": list(dict.fromkeys(obligations)),
            "review_id": proposal["id"],
        }
    }


@router.post("/review/{change_id}/approve")
def post_approve(
    change_id: int,
    body: ReviewActionIn,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    strong = bool(getattr(request.state, "strong_auth", False))
    return review.approve_change(
        change_id,
        body.note,
        actor=user,
        strong=strong,
        viewer=viewer,
        reviewer_groups=subject.groups,
        reviewer_capabilities=subject.capabilities,
        extension_executor=lambda invocation, change_id: _execute_extension_review(
            request, invocation, change_id
        ),
        policy_registry=request.app.state.skein_registry,
    )


@router.post("/review/{change_id}/reject")
def post_reject(
    change_id: int,
    body: ReviewActionIn,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    strong = bool(getattr(request.state, "strong_auth", False))
    return review.reject_change(
        change_id,
        body.note,
        actor=user,
        strong=strong,
        viewer=viewer,
        reviewer_groups=subject.groups,
        reviewer_capabilities=subject.capabilities,
        policy_registry=request.app.state.skein_registry,
    )


class CaptureIn(BaseModel):
    text: str = Field(max_length=10_000)  # one capture, not a document dump
    # quick capture is the ONLY door tasks and notes are created through in
    # the web UI, so this is where their tier is chosen. It routes to seven
    # entities, and every one of the seven carries the tier through
    # (services/capture.py) — a picker that applied to some kinds and not
    # others would be worse than none.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/capture")
def post_capture(
    body: CaptureIn,
    user: CurrentUser,
    request: Request,
    subject: PolicySubjectDep,
):
    ratelimit.check("capture", user)
    strong = bool(getattr(request.state, "strong_auth", False))
    with db.transaction():
        if not capture.is_private_feedback(body.text):
            _kind, entity, payload = capture.plan(body.text, actor=user)
            payload.update({"visibility": body.visibility, "crew_id": body.crew_id})
            attributes = policy_context.for_change(entity, 0, payload, actor=user)
            enforce_decision(
                decide(
                    request,
                    subject,
                    "skein.rest.post.capture",
                    entity,
                    project_type=attributes.get("project_type", ""),
                    classification=attributes.get("classification", ""),
                    attributes=attributes,
                )
            )
        return capture.capture(
            body.text,
            actor=user,
            strong_auth=strong,
            visibility=body.visibility,
            crew_id=body.crew_id,
        )


class IngestIn(BaseModel):
    text: str = Field(max_length=70_000)  # 422 at validation, before buffering costs


@router.post("/ingest")
def post_ingest(body: IngestIn, user: CurrentUser):
    ratelimit.check("ingest", user)
    return ingest.ingest_notes(body.text, actor=user)


class BatchApproveIn(BaseModel):
    ids: list[int] = Field(max_length=200)  # matches the pending-list LIMIT


@router.get("/review/{change_id}/diff")
def get_review_diff(change_id: int, user: CurrentUser, viewer: ViewerDep):
    return review.change_diff(change_id, viewer)


class SeenIn(BaseModel):
    ids: list[int] = Field(max_length=200)


@router.post("/review/seen")
def post_review_seen(body: SeenIn, user: CurrentUser):
    return review.mark_seen(body.ids, actor=user)


@router.post("/review/approve-batch")
def post_approve_batch(
    body: BatchApproveIn,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    strong = bool(getattr(request.state, "strong_auth", False))
    results = []
    # BatchApproveIn.max_length is the only cap. A second limit here (a
    # slice, a break) drops the tail with no result row — the caller counts
    # the answers, sees fewer than it sent, and never learns which ids were
    # skipped. Every id the model accepted gets exactly one result row.
    for cid in body.ids:
        try:
            r = review.approve_change(
                cid,
                actor=user,
                strong=strong,
                viewer=viewer,
                reviewer_groups=subject.groups,
                reviewer_capabilities=subject.capabilities,
                extension_executor=lambda invocation, change_id: _execute_extension_review(
                    request, invocation, change_id
                ),
                policy_registry=request.app.state.skein_registry,
            )
            results.append({"id": cid, "status": r["status"]})
        except ValueError as exc:
            results.append({"id": cid, "status": "error", "detail": str(exc)})
        except PermissionError as exc:
            # Per item, never a batch-level 403: earlier ids in this loop have
            # already committed, so an aborting handler would hide a partial
            # apply from the caller and skip every id after this one.
            results.append({"id": cid, "status": "forbidden", "detail": str(exc)})
    return {"results": results}


class EngagementIn(BaseModel):
    name: str = Field(max_length=120)
    project_class: str = Field("general", max_length=40)
    summary: str = Field("", max_length=4000)
    lead: str = Field("", max_length=64)
    kind: str = Field("delivery", max_length=20)
    timebox_end: str = Field("", max_length=10)
    kill_criteria: str = Field("", max_length=500)
    outcome: str = Field("", max_length=2000)
    # the tier the writer picked, checked in the service: crew membership only.
    # It PROPAGATES — the handoff artifact, the ship-it note and the
    # experiment lesson all inherit from the engagement they came from.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/engagements")
def post_engagement(body: EngagementIn, user: CurrentUser):
    ratelimit.check("write", user)
    return engagements.create_engagement(**body.model_dump(), actor=user)


class EngagementPatch(BaseModel):
    status: str = Field("", max_length=20)
    name: str = Field("", max_length=120)  # rename propagates to milestone labels
    summary: str = Field("", max_length=4000)
    lead: str = Field("", max_length=64)
    conclusion: str = Field("", max_length=40)
    outcome: str = Field("", max_length=2000)
    timebox_end: str = Field("", max_length=10)
    kill_criteria: str = Field("", max_length=500)


@router.patch("/engagements/{engagement_id}")
def patch_engagement(engagement_id: int, body: EngagementPatch, user: CurrentUser):
    return engagements.update_engagement(engagement_id, **body.model_dump(), actor=user)


@router.get("/engagements/{engagement_id}/plan-diff")
def get_plan_diff(
    engagement_id: int,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    """Planned versus what happened, for an engagement born from a playbook.

    `{}` for one created by hand — the close-out control renders nothing
    rather than an empty section, because "no variance" and "no plan to vary
    from" are different statements.
    """
    policy = projection_policy.ProjectionPolicy(
        request.app.state.skein_registry.policy_engine,
        subject,
        "skein.rest.get.engagements.plan-diff",
        "rest",
        viewer,
    )
    with db.read_transaction():
        return playbooks.close_out_diff(
            engagement_id,
            viewer,
            resource_filter=policy.permits,
        )


class AllocationIn(BaseModel):
    person: str = Field(max_length=64)
    percent: int = 100
    starts_on: str = Field("", max_length=10)
    ends_on: str = Field("", max_length=10)


@router.post("/engagements/{engagement_id}/allocate")
def post_allocate(engagement_id: int, body: AllocationIn, user: CurrentUser):
    ratelimit.check("write", user)
    return engagements.allocate(
        body.person, engagement_id, body.percent, body.starts_on, body.ends_on, actor=user
    )


class LessonIn(BaseModel):
    lesson: str = Field(max_length=2000)
    recommendation: str = Field("", max_length=2000)
    engagement_id: int = 0
    project_class: str = Field("general", max_length=40)
    # the tier the writer picked, checked in the service: crew membership only.
    visibility: str = Field(scope.WORKSPACE, max_length=16)
    crew_id: int = 0


@router.post("/lessons")
def post_lesson(body: LessonIn, user: CurrentUser):
    ratelimit.check("write", user)
    return engagements.record_lesson(**body.model_dump(), actor=user)


class InstantiateIn(BaseModel):
    # caps match EngagementIn — instantiate reaches create_engagement, so an
    # uncapped name here writes past the create cap into the search index
    playbook: str = Field(max_length=40)
    engagement_name: str = Field(max_length=120)
    lead: str = Field("", max_length=64)
    start_date: str = Field("", max_length=10)


@router.post("/playbooks/instantiate")
def post_instantiate(
    body: InstantiateIn,
    user: CurrentUser,
    request: Request,
    subject: PolicySubjectDep,
):
    from ..extensions.policy import policy_subject_data
    from ..public.workflow import WorkflowEngine, _issue_workflow_context

    registry = request.app.state.skein_registry
    playbook = playbooks.get_playbook(body.playbook)
    project_type = str(playbook.get("project_class") or body.playbook)
    definition_digest = playbooks.definition_digest(playbook)
    evaluated = dict(getattr(request.state, "skein_playbook_policy_context", {}))
    if evaluated.get("definition_digest") != definition_digest:
        from ..public.errors import PublicError

        raise PublicError(
            "PLAYBOOK_CHANGED",
            "The selected playbook changed during this request. Retry the request.",
            status_code=409,
        )
    if getattr(request.state, "skein_playbook_policy_review", False):
        return _queue_playbook_policy_review(
            body,
            actor=user,
            subject=subject,
            definition_digest=definition_digest,
            policy_input=request.state.skein_playbook_policy_input,
            decision=request.state.skein_playbook_policy_decision,
        )
    workflow_engine = WorkflowEngine(registry.workflow_actions, registry.policy_engine)
    workflow_context = _issue_workflow_context(
        workflow_engine,
        subject,
        "human",
        values={"project_type": project_type},
        project_type=project_type,
    )
    result = playbooks.instantiate(
        body.playbook,
        body.engagement_name,
        body.lead or user,
        body.start_date,
        actor=user,
        workflow_engine=workflow_engine,
        workflow_context=workflow_context,
        expected_definition_digest=definition_digest,
    )
    if result.get("workflow", {}).get("status") != "review_required":
        return result
    return _queue_workflow_review(
        result,
        {
            "playbook": body.playbook,
            "engagement_name": body.engagement_name,
            "lead": body.lead or user,
            "start_date": body.start_date,
            "actor": user,
            "project_type": project_type,
            "resource_id": "",
            "run_id": workflow_context.run_id,
            "values": dict(workflow_context.values),
            "approval_grants": {},
            "subject": policy_subject_data(subject),
            "definition_digest": definition_digest,
        },
    )


@router.post("/engagements/{engagement_id}/handoff")
def post_handoff(engagement_id: int, user: CurrentUser, viewer: ViewerDep):
    ratelimit.check("artifact", user)
    return handoff.generate_handoff(engagement_id, actor=user, viewer=viewer)


@router.post("/digest")
def post_digest(user: CurrentUser):
    ratelimit.check("artifact", user)
    return digest.publish_digest(actor=user)


@router.get("/calendar.ics")
def get_calendar_ics(request: Request, token: str = ""):
    """iCalendar feed of events + due dates (team-visible data only).
    Keep the feed inside the trusted network. Calendar clients can't send
    headers, so auth is a DEDICATED
    feed secret (?token=SKEIN_ICS_TOKEN) — never the API token, which
    would end up in calendar configs and access logs. Fully-open mode only
    when the whole API is open (trusted-header mode, no API_TOKEN);
    otherwise fail closed — the feed sits on the perimeter middleware's
    open-path list, so this check is its only gate."""
    import hmac

    from fastapi import HTTPException
    from fastapi.responses import Response

    from .. import config

    if config.ICS_TOKEN:
        # bytes compare: str compare_digest raises on non-ASCII input (→500)
        if not hmac.compare_digest(token.encode(), config.ICS_TOKEN.encode()):
            raise HTTPException(status_code=401, detail="token required")
    elif (
        request.app.state.skein_settings.api_token
        if request.app.state.skein_explicit_settings
        else config.API_TOKEN
    ) or (
        request.app.state.skein_settings.auth_mode
        if request.app.state.skein_explicit_settings
        else config.AUTH_MODE
    ) != "trusted-header":
        raise HTTPException(
            status_code=403,
            detail="calendar feed disabled — set SKEIN_ICS_TOKEN to enable it",
        )
    return Response(
        schedule.ics_feed(),
        media_type="text/calendar",
        headers={"Cache-Control": "private, no-store"},
    )


# ---- admin -----------------------------------------------------------------


@router.post("/admin/backup")
def post_backup(user: AdminUser):
    return admin.backup(actor=user)


@router.get("/admin/export")
def get_export(user: AdminUser):
    # full-table dump — administrators only, never the X-User header
    return admin.export()


@router.get("/interventions")
def get_interventions(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    limit: int = 12,
):
    """The manager's ranked queue. Composition only — every row restates one
    an engine already produced (services/intervention.py)."""
    with db.read_transaction():
        policy = _require_opaque_project_policy(
            request,
            subject,
            viewer,
            "skein.rest.get.interventions",
            unclassified_inputs=False,
        )
        return intervention.interventions(viewer, limit, resource_filter=policy.permits)


@router.get("/engagements/{engagement_id}/brief")
def get_engagement_brief(
    engagement_id: int,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    """One engagement, whole. Composition only — every number keeps its own
    home (services/engagement_brief.py)."""
    with db.read_transaction():
        _require_resource_policy(
            request,
            subject,
            viewer,
            "skein.rest.get.engagements.brief",
            "engagement",
            engagement_id,
        )
        policy = projection_policy.ProjectionPolicy(
            request.app.state.skein_registry.policy_engine,
            subject,
            "skein.rest.get.engagements.brief",
            "rest",
            viewer,
        )
        return engagement_brief.brief(
            engagement_id,
            viewer,
            resource_filter=policy.permits,
        )


@router.get("/provenance/{entity}/{entity_id}")
def get_provenance(
    entity: str,
    entity_id: int,
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
):
    # capped: ids are a dense integer space and this answers about ONE row, so
    # an uncapped GET is the mechanism that turns per-row provenance into a
    # dataset (app/ratelimit.py names the bucket and the reasoning).
    """How one row came to exist, and what has happened to it since. Read-only
    composition over rows that already exist (services/provenance.py)."""
    ratelimit.check("provenance", user)
    # asking the question IS the act this knot names: the panel writes nothing,
    # so no predicate could ever find it (services/fieldguide.py::mark)
    fieldguide.mark(user, "provenance")
    with db.read_transaction():
        if policy_context.supports_resource(entity):
            _require_resource_policy(
                request,
                subject,
                viewer,
                "skein.rest.get.provenance",
                entity,
                entity_id,
            )
        return provenance.lineage(entity, entity_id, viewer)


class EngagementMemoryIn(BaseModel):
    """What a conversation produced, filed against the engagement it was about.

    NOT `OutcomeIn`, which is already the meeting-outcome body above: two
    models with one name is a redefinition mypy catches and a reader does not.
    """

    content: str = Field(..., min_length=1, max_length=2000)
    topic: str = Field("", max_length=100)
    thread_id: str = Field("", max_length=120)


@router.post("/engagements/{engagement_id}/memory")
def post_engagement_memory(
    engagement_id: int, body: EngagementMemoryIn, user: StrongUser, viewer: ViewerDep
):
    """File what a conversation produced as this engagement's memory.

    StrongUser: the proposal quotes the text back to a reviewer and names the
    engagement, and in trusted-header mode a self-asserted name could file
    against any engagement it could read.
    """
    ratelimit.check("memory", user)
    return memory.propose_engagement_memory(
        engagement_id,
        body.content,
        body.topic,
        body.thread_id,
        actor=user,
        viewer=viewer,
    )


@router.get("/delta")
def get_delta(
    user: CurrentUser,
    viewer: ViewerDep,
    request: Request,
    subject: PolicySubjectDep,
    mark: bool = False,
):
    """What changed for this reader since their last brief.

    `mark` defaults to False so a caller can PREVIEW without consuming: the
    chat command shows the brief, and only the surface that displays it moves
    the reader's last-seen mark (services/delta.py)."""
    transaction = db.transaction if mark else db.read_transaction
    with transaction():
        _require_opaque_project_policy(request, subject, viewer, "skein.rest.get.delta")
        return delta.brief(user, viewer, mark=mark)

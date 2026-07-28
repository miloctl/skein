"""REST API: reads for the dashboard, writes for humans (the second write path
alongside agent tools — both go through app.services)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db, ratelimit
from ..services import (
    absences,
    admin,
    api_keys,
    blockers,
    briefing,
    capture,
    collab,
    commitments,
    context_pack,
    delegation,
    digest,
    engagements,
    feedback,
    handoff,
    ingest,
    intake,
    memory,
    notifications,
    personas,
    playbooks,
    portfolio,
    pulse,
    readout,
    review,
    rituals,
    schedule,
    search,
    usage,
    users,
    weekly,
    work,
)
from .deps import CurrentUser, StrongUser

router = APIRouter(prefix="/api")


# ---- reads -----------------------------------------------------------------


@router.get("/milestones")
def get_milestones(project: str = "", status: str = ""):
    return work.list_milestones(project, status)


@router.get("/tasks")
def get_tasks():
    return work.list_tasks_joined()


@router.get("/tasks/{task_id}/worklog")
def get_task_worklog(task_id: int):
    try:
        return delegation.list_worklog(task_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/questions")
def get_questions(status: str = ""):
    return collab.list_questions(status)


@router.get("/decisions")
def get_decisions(status: str = "", category: str = ""):
    return collab.list_decisions(status=status, category=category)


@router.get("/standups")
def get_standups():
    return collab.list_standups()


@router.get("/events")
def get_events(from_date: str = ""):
    return schedule.list_events(from_date)


@router.get("/personas")
def get_personas():
    return personas.list_personas()


@router.get("/personas/{slug}")
def get_persona(slug: str):
    return personas.get_persona(slug)


@router.get("/notes")
def get_notes(q: str = ""):
    return collab.search_notes(q)


class NotePatch(BaseModel):
    topic: str = Field("", max_length=200)
    content: str = Field("", max_length=20_000)


@router.patch("/notes/{note_id}")
def patch_note(note_id: int, body: NotePatch, user: CurrentUser):
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
def get_activity():
    return collab.recent_activity()


@router.get("/blockers")
def get_blockers(status: str = "", owner: str = ""):
    return blockers.list_blockers(status, owner)


@router.get("/intake")
def get_intake(status: str = ""):
    return intake.list_requests(status)


@router.get("/review")
def get_review(status: str = "pending"):
    return review.list_changes(status)


@router.get("/engagements")
def get_engagements(status: str = ""):
    return engagements.list_engagements(status)


@router.get("/allocations")
def get_allocations(engagement_id: int = 0):
    return engagements.list_allocations(engagement_id)


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


@router.get("/absences")
def get_absences(person: str = ""):
    return absences.list_absences(person)


@router.post("/absences")
def post_absence(body: AbsenceIn, user: CurrentUser):
    ratelimit.check("absence", user)
    try:
        return absences.add_absence(
            body.person, body.starts_on, body.ends_on, body.kind, body.note, actor=user
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
def get_capacity():
    return engagements.capacity()


@router.get("/lessons")
def get_lessons(project_class: str = ""):
    return engagements.list_lessons(project_class)


@router.get("/playbooks")
def get_playbooks():
    return playbooks.list_playbooks()


@router.get("/artifacts")
def get_artifacts(engagement_id: int = 0):
    return handoff.list_artifacts(engagement_id)


@router.get("/users")
def get_users(all: bool = False):
    # all=1 includes deactivated rows — the Settings roster needs them so
    # deactivation stays reversible from the UI
    return users.list_users(active_only=not all)


class UserRenameIn(BaseModel):
    new_name: str = Field(min_length=1, max_length=64)


@router.post("/users/{name}/rename")
def post_user_rename(name: str, body: UserRenameIn, user: StrongUser):
    # rename/merge moves attribution history — strong identity only
    return users.rename_user(name, body.new_name, actor=user)


class UserActiveIn(BaseModel):
    active: bool


@router.post("/users/{name}/active")
def post_user_active(name: str, body: UserActiveIn, user: StrongUser):
    # roster edits need strong identity — a spoofed header must not be able
    # to deactivate teammates
    return users.set_active(name, body.active, actor=user)


class GrowthIn(BaseModel):
    interests: str = Field(max_length=500)


@router.post("/users/growth-interests")
def post_growth_interests(body: GrowthIn, user: CurrentUser):
    # self-declared only: you set YOURS (future-planning data, never scored)
    return users.set_growth_interests(user, body.interests, actor=user)


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
def post_team_theme(body: ThemeIn, user: StrongUser):
    try:
        return users.set_team_default_theme(body.theme, actor=user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/search")
def get_search(q: str):
    return search.search(q)


@router.get("/ask")
def get_ask(q: str):
    return search.ask(q)


@router.get("/whoami")
def get_whoami(user: CurrentUser, request: Request):
    """Who the API thinks you are and how strongly — the Settings page uses
    this to validate a pasted key without the user needing to know anything."""
    from ..services.api_keys import list_keys

    return {
        "user": user,
        "strong": bool(getattr(request.state, "strong_auth", False)),
        # active only — after a revoke-all, Settings must show the bootstrap
        # command again, not "a key exists, paste it"
        "keys_minted": sum(1 for k in list_keys(user) if k["active"]),
    }


@router.post("/keys/request")
def post_key_request(user: CurrentUser):
    from ..services.api_keys import request_key

    try:
        ratelimit.check("keys_request", user)
        return request_key(user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/briefing")
def get_briefing(user: CurrentUser):
    return briefing.my_day(user)


@router.get("/attention")
def get_attention(user: CurrentUser):
    return {"count": briefing.attention_count(user)}


class KeyIn(BaseModel):
    label: str = Field("", max_length=100)


# Key MUTATION requires an existing key (StrongUser): minting on X-User
# identity alone would let any LAN caller become anyone and defeat the whole
# private-record boundary. First key per person: python -m app.bootstrap_key.


@router.post("/keys")
def post_key(body: KeyIn, user: StrongUser):
    return api_keys.create_key(user, body.label)


@router.get("/keys")
def get_keys(user: CurrentUser):
    return api_keys.list_keys(user)


@router.delete("/keys/{key_id}")
def delete_key(key_id: int, user: StrongUser):
    return api_keys.revoke_key(key_id, user)


@router.get("/admin/keys")
def get_all_keys(user: StrongUser):
    # key metadata (owners, prefixes, last use) is admin surface — a spoofed
    # X-User must not enumerate it; matches revoke-all and /admin/export
    return api_keys.list_all_keys()


@router.post("/admin/keys/revoke-all")
def post_revoke_all_keys(user: StrongUser):
    return api_keys.revoke_all_keys(actor=user)


@router.get("/notifications")
def get_notifications(user: CurrentUser, unread_only: bool = True):
    return notifications.list_notifications(user, unread_only)


class MarkReadIn(BaseModel):
    notification_id: int = 0  # 0 = mark all read


@router.post("/notifications/read")
def post_notifications_read(body: MarkReadIn, user: CurrentUser):
    return notifications.mark_read(user, body.notification_id)


@router.get("/memories")
def get_memories(q: str = ""):
    return memory.recall(q)


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, user: CurrentUser):
    ratelimit.check("delete", user)
    try:
        return memory.forget(memory_id, actor=user)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/pulse")
def get_pulse():
    return pulse.pulse()


@router.get("/portfolio/health")
def get_portfolio_health():
    return portfolio.engagement_health()


@router.get("/portfolio/conflicts")
def get_portfolio_conflicts():
    return portfolio.allocation_conflicts()


@router.get("/portfolio/flow")
def get_portfolio_flow():
    return portfolio.flow_metrics()


@router.get("/portfolio/forecast")
def get_portfolio_forecast():
    return portfolio.slip_forecast()


@router.post("/portfolio/readout")
def post_portfolio_readout(user: CurrentUser):
    ratelimit.check("artifact", user)
    return readout.exec_readout(actor=user)


class WhatIfIn(BaseModel):
    people: list[str] = Field(max_length=20)
    percent: int = 50


@router.post("/intake/{request_id}/what-if")
def post_what_if(request_id: int, body: WhatIfIn, user: CurrentUser):
    return portfolio.what_if(request_id, body.people, body.percent)


@router.get("/week")
def get_week(week: str = ""):
    return weekly.week_view(week)


@router.get("/week/draft")
def get_week_draft(week: str = ""):
    return weekly.draft_plan(week)


class WeekPlanIn(BaseModel):
    week: str = Field("", max_length=8)
    task_ids: list[int] = Field(max_length=200)


@router.post("/week/plan")
def post_week_plan(body: WeekPlanIn, user: CurrentUser):
    return weekly.apply_plan(body.week or weekly.current_week(), body.task_ids, actor=user)


@router.get("/commitments")
def get_commitments(status: str = "", audience: str = ""):
    return commitments.list_commitments(status, audience)


class CommitmentIn(BaseModel):
    promise: str = Field(max_length=500)
    to_whom: str = Field("", max_length=120)
    due_date: str = Field("", max_length=10)
    engagement_id: int = 0
    audience: str = Field("external", max_length=20)


@router.post("/commitments")
def post_commitment(body: CommitmentIn, user: CurrentUser):
    ratelimit.check("write", user)
    return commitments.add_commitment(**body.model_dump(), actor=user)


class CommitmentStatusIn(BaseModel):
    status: str


class CommitmentEditIn(BaseModel):
    promise: str = Field("", max_length=500)
    due_date: str = Field("", max_length=10)
    to_whom: str = Field("", max_length=120)


@router.patch("/commitments/{commitment_id}")
def patch_commitment(commitment_id: int, body: CommitmentEditIn, user: CurrentUser):
    try:
        return commitments.edit_commitment(
            commitment_id, body.promise, body.due_date, body.to_whom, actor=user
        )
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/commitments/{commitment_id}/status")
def post_commitment_status(commitment_id: int, body: CommitmentStatusIn, user: CurrentUser):
    return commitments.update_commitment(commitment_id, body.status, actor=user)


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
def get_review_stats():
    return review.review_stats()


class FeedbackIn(BaseModel):
    kind: str = Field(max_length=40)
    input_text: str = Field("", max_length=2000)  # a pulse vote has no input text
    output: str = Field("", max_length=4000)
    verdict: str = Field("up", max_length=10)
    correction: str = Field("", max_length=2000)


@router.post("/feedback")
def post_feedback(body: FeedbackIn, user: CurrentUser):
    try:
        ratelimit.check("feedback", user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
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
def get_eval_capture():
    return feedback.eval_capture()


@router.post("/rituals/week-open")
def post_week_open(user: CurrentUser):
    ratelimit.check("ritual", user)  # each run notifies people — cap the amplifier
    return rituals.week_open(actor=user, force=True)


@router.post("/rituals/week-close")
def post_week_close(user: CurrentUser):
    ratelimit.check("ritual", user)
    return rituals.week_close(actor=user, force=True)


@router.get("/agents")
def get_agents():
    return delegation.mission_control()


@router.get("/agents/status")
def get_agents_status(user: CurrentUser):
    """Plain-language state of the agent layer — the UI must never make mock
    mode look like a live model, or hide whether the review gate is on."""
    from .. import config

    return {
        "provider": config.MODEL_PROVIDER,
        "model": config.MODEL_ID if config.MODEL_PROVIDER != "mock" else "",
        "review_gate": config.AGENT_REVIEW,
    }


@router.get("/agents/trust")
def get_agents_trust():
    return delegation.trust_scores()


@router.get("/agents/entities")
def get_agent_entities():
    from ..services.review import _registry

    return sorted(_registry())


@router.get("/agents/authority")
def get_agents_authority(agent: str = ""):
    return delegation.authority_matrix(agent)


class AuthorityIn(BaseModel):
    agent: str = Field(max_length=64)
    entity: str = Field(max_length=40)
    level: str = Field(max_length=20)


@router.post("/agents/authority")
def post_agents_authority(body: AuthorityIn, user: StrongUser):
    """Authority IS the kill switch — a spoofable X-User must not flip it."""
    return delegation.set_authority(body.agent, body.entity, body.level, actor=user)


@router.get("/agents/{agent}/inbox")
def get_agent_inbox(agent: str, user: CurrentUser):
    if not users.is_agent(agent):
        raise HTTPException(status_code=404, detail=f"no agent named '{agent}'")
    return delegation.agent_inbox(agent)


class DelegateIn(BaseModel):
    agent: str = Field(max_length=64)
    sponsor: str = Field("", max_length=64)


@router.post("/tasks/{task_id}/delegate")
def post_delegate(task_id: int, body: DelegateIn, user: CurrentUser):
    return delegation.delegate_task(task_id, body.agent, body.sponsor or user, actor=user)


@router.get("/context-pack")
def get_context_pack(user: CurrentUser, engagement: int = 0):
    if engagement:
        return {"engagement": engagement, "content": context_pack.build_engagement_pack(engagement)}
    return context_pack.get_pack(actor=user)


@router.post("/context-pack/publish")
def post_context_pack(user: CurrentUser):
    return context_pack.publish_pack(actor=user)


@router.get("/onboarding")
def get_onboarding(user: CurrentUser):
    from ..services import onboarding

    return onboarding.checklist(user)


@router.get("/adoption")
def get_adoption(weeks: int = 4):
    from ..services import adoption as adoption_svc

    return adoption_svc.adoption(weeks)


@router.get("/insights")
def get_insights():
    from ..services import insights as insights_svc

    return insights_svc.insights()


@router.get("/findings")
def get_findings(weeks: int = 4):
    from ..services import insights as insights_svc

    return insights_svc.list_findings(weeks)


class FindingDispositionIn(BaseModel):
    disposition: str = Field(max_length=20)
    reason: str = Field("", max_length=500)
    deferred_until: str = Field("", max_length=10)


@router.post("/findings/{finding_id}/disposition")
def post_finding_disposition(finding_id: int, body: FindingDispositionIn, user: CurrentUser):
    from ..services import insights as insights_svc

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
def get_usage():
    return usage.usage_summary()


# ---- writes ----------------------------------------------------------------


class MilestoneIn(BaseModel):
    title: str = Field(max_length=200)
    description: str = Field("", max_length=4000)
    project: str = Field("default", max_length=120)
    owner: str = Field("", max_length=64)
    due_date: str = Field("", max_length=10)


@router.post("/milestones")
def post_milestone(body: MilestoneIn, user: CurrentUser):
    ratelimit.check("write", user)
    return work.create_milestone(**body.model_dump(), actor=user)


class MilestonePatch(BaseModel):
    status: str = ""
    title: str = ""
    description: str = ""
    owner: str = ""
    due_date: str = ""
    engagement_id: int = 0  # relink (-1 unlinks)


@router.patch("/milestones/{milestone_id}")
def patch_milestone(milestone_id: int, body: MilestonePatch, user: CurrentUser):
    return work.update_milestone(milestone_id, **body.model_dump(), actor=user)


class TaskIn(BaseModel):
    title: str = Field(max_length=200)
    description: str = Field("", max_length=4000)
    milestone_id: int = 0
    assignee: str = Field("", max_length=64)
    priority: str = Field("medium", max_length=10)
    due_date: str = Field("", max_length=10)
    engagement_id: int = 0


@router.post("/tasks")
def post_task(body: TaskIn, user: CurrentUser):
    ratelimit.check("write", user)
    return work.create_task(**body.model_dump(), actor=user)


class TaskPatch(BaseModel):
    status: str = ""
    assignee: str = ""
    priority: str = ""
    due_date: str = ""
    description: str = ""
    title: str = ""
    committed_week: str = ""
    waiting_on: str = ""  # "blocker:12" | "task:3" | "commitment:7" | "-"
    milestone_id: int = 0  # relink (-1 unlinks)
    engagement_id: int = 0  # relink (-1 unlinks)


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, user: CurrentUser):
    return work.update_task(task_id, **body.model_dump(), actor=user)


class QuestionIn(BaseModel):
    question: str = Field(max_length=1000)
    assigned_to: str = Field("", max_length=64)


@router.post("/questions")
def post_question(body: QuestionIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.ask_question(
        body.question, asked_by=user, assigned_to=body.assigned_to, actor=user
    )


class QuestionPatch(BaseModel):
    assigned_to: str = Field(max_length=64)


@router.patch("/questions/{question_id}")
def patch_question(question_id: int, body: QuestionPatch, user: CurrentUser):
    return collab.assign_question(question_id, body.assigned_to, actor=user)


class AnswerIn(BaseModel):
    answer: str = Field(max_length=4000)


@router.post("/questions/{question_id}/answer")
def post_answer(question_id: int, body: AnswerIn, user: CurrentUser):
    return collab.answer_question(question_id, body.answer, answered_by=user, actor=user)


class DecisionIn(BaseModel):
    title: str = Field(max_length=200)
    decision: str = Field(max_length=2000)
    context: str = Field("", max_length=4000)
    review_by: str = Field("", max_length=10)
    category: str = Field("", max_length=40)


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
    )


class StandupIn(BaseModel):
    yesterday: str = Field("", max_length=2000)
    today: str = Field("", max_length=2000)
    blockers: str = Field("", max_length=2000)


@router.post("/standups")
def post_standup(body: StandupIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.post_standup(user, body.yesterday, body.today, body.blockers, actor=user)


class NoteIn(BaseModel):
    topic: str = Field(max_length=200)
    content: str = Field(max_length=20_000)


@router.post("/notes")
def post_note(body: NoteIn, user: CurrentUser):
    ratelimit.check("write", user)
    return collab.save_note(body.topic, body.content, author=user, actor=user)


class EventIn(BaseModel):
    title: str = Field(max_length=200)
    starts_at: str = Field(max_length=25)
    ends_at: str = Field("", max_length=25)
    description: str = Field("", max_length=4000)
    attendees: str = Field("", max_length=500)


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


@router.post("/blockers")
def post_blocker(body: BlockerIn, user: CurrentUser):
    ratelimit.check("write", user)
    return blockers.raise_blocker(**body.model_dump(), actor=user)


class ResolveIn(BaseModel):
    resolution: str = Field("", max_length=2000)


class BlockerEditIn(BaseModel):
    title: str = Field("", max_length=200)
    detail: str = Field("", max_length=4000)
    owner: str = Field("", max_length=64)


@router.patch("/blockers/{blocker_id}")
def patch_blocker(blocker_id: int, body: BlockerEditIn, user: CurrentUser):
    try:
        return blockers.edit_blocker(blocker_id, body.title, body.detail, body.owner, actor=user)
    except db.NotFound:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/blockers/{blocker_id}/resolve")
def post_resolve_blocker(blocker_id: int, body: ResolveIn, user: CurrentUser):
    return blockers.resolve_blocker(blocker_id, body.resolution, actor=user)


class IntakeIn(BaseModel):
    title: str = Field(max_length=200)
    detail: str = Field("", max_length=4000)
    project_class: str = Field("", max_length=40)


@router.post("/intake")
def post_intake(body: IntakeIn, user: CurrentUser):
    ratelimit.check("write", user)
    return intake.submit_request(
        body.title, body.detail, requester=user, project_class=body.project_class, actor=user
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


@router.post("/review/{change_id}/approve")
def post_approve(change_id: int, body: ReviewActionIn, user: CurrentUser, request: Request):
    strong = bool(getattr(request.state, "strong_auth", False))
    return review.approve_change(change_id, body.note, actor=user, strong=strong)


@router.post("/review/{change_id}/reject")
def post_reject(change_id: int, body: ReviewActionIn, user: CurrentUser, request: Request):
    strong = bool(getattr(request.state, "strong_auth", False))
    return review.reject_change(change_id, body.note, actor=user, strong=strong)


class CaptureIn(BaseModel):
    text: str = Field(max_length=10_000)  # one capture, not a document dump


@router.post("/capture")
def post_capture(body: CaptureIn, user: CurrentUser, request: Request):
    ratelimit.check("capture", user)
    strong = bool(getattr(request.state, "strong_auth", False))
    return capture.capture(body.text, actor=user, strong_auth=strong)


class IngestIn(BaseModel):
    text: str = Field(max_length=70_000)  # 422 at validation, before buffering costs


@router.post("/ingest")
def post_ingest(body: IngestIn, user: CurrentUser):
    ratelimit.check("ingest", user)
    return ingest.ingest_notes(body.text, actor=user)


class BatchApproveIn(BaseModel):
    ids: list[int] = Field(max_length=100)


@router.get("/review/{change_id}/diff")
def get_review_diff(change_id: int, user: CurrentUser):
    return review.change_diff(change_id)


class SeenIn(BaseModel):
    ids: list[int] = Field(max_length=200)


@router.post("/review/seen")
def post_review_seen(body: SeenIn, user: CurrentUser):
    return review.mark_seen(body.ids, actor=user)


@router.post("/review/approve-batch")
def post_approve_batch(body: BatchApproveIn, user: CurrentUser, request: Request):
    strong = bool(getattr(request.state, "strong_auth", False))
    results = []
    for cid in body.ids[:100]:
        try:
            r = review.approve_change(cid, actor=user, strong=strong)
            results.append({"id": cid, "status": r["status"]})
        except ValueError as exc:
            results.append({"id": cid, "status": "error", "detail": str(exc)})
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


@router.post("/engagements")
def post_engagement(body: EngagementIn, user: CurrentUser):
    ratelimit.check("write", user)
    return engagements.create_engagement(**body.model_dump(), actor=user)


class EngagementPatch(BaseModel):
    status: str = ""
    name: str = Field("", max_length=120)  # rename propagates to milestone labels
    summary: str = ""
    lead: str = ""
    conclusion: str = ""
    outcome: str = ""
    timebox_end: str = ""
    kill_criteria: str = ""


@router.patch("/engagements/{engagement_id}")
def patch_engagement(engagement_id: int, body: EngagementPatch, user: CurrentUser):
    return engagements.update_engagement(engagement_id, **body.model_dump(), actor=user)


class AllocationIn(BaseModel):
    person: str
    percent: int = 100
    starts_on: str = ""
    ends_on: str = ""


@router.post("/engagements/{engagement_id}/allocate")
def post_allocate(engagement_id: int, body: AllocationIn, user: CurrentUser):
    return engagements.allocate(
        body.person, engagement_id, body.percent, body.starts_on, body.ends_on, actor=user
    )


class LessonIn(BaseModel):
    lesson: str = Field(max_length=2000)
    recommendation: str = Field("", max_length=2000)
    engagement_id: int = 0
    project_class: str = Field("general", max_length=40)


@router.post("/lessons")
def post_lesson(body: LessonIn, user: CurrentUser):
    ratelimit.check("write", user)
    return engagements.record_lesson(**body.model_dump(), actor=user)


class InstantiateIn(BaseModel):
    playbook: str
    engagement_name: str
    lead: str = ""
    start_date: str = ""


@router.post("/playbooks/instantiate")
def post_instantiate(body: InstantiateIn, user: CurrentUser):
    return playbooks.instantiate(
        body.playbook, body.engagement_name, body.lead or user, body.start_date, actor=user
    )


@router.post("/engagements/{engagement_id}/handoff")
def post_handoff(engagement_id: int, user: CurrentUser):
    ratelimit.check("artifact", user)
    return handoff.generate_handoff(engagement_id, actor=user)


@router.post("/digest")
def post_digest(user: CurrentUser):
    ratelimit.check("artifact", user)
    return digest.publish_digest(actor=user)


@router.get("/calendar.ics")
def get_calendar_ics(token: str = ""):
    """iCalendar feed of events + due dates (team-visible data only).
    LAN-only. Calendar clients can't send headers, so auth is a DEDICATED
    feed secret (?token=STRANDS_ICS_TOKEN) — never the API token, which
    would end up in calendar configs and access logs. Fully-open mode only
    when the whole API is open (no API_TOKEN); otherwise fail closed."""
    import hmac

    from fastapi import HTTPException
    from fastapi.responses import Response

    from .. import config

    if config.ICS_TOKEN:
        # bytes compare: str compare_digest raises on non-ASCII input (→500)
        if not hmac.compare_digest(token.encode(), config.ICS_TOKEN.encode()):
            raise HTTPException(status_code=401, detail="token required")
    elif config.API_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="calendar feed disabled — set STRANDS_ICS_TOKEN to enable it",
        )
    return Response(
        schedule.ics_feed(),
        media_type="text/calendar",
        headers={"Cache-Control": "private, no-store"},
    )


# ---- admin -----------------------------------------------------------------


@router.post("/admin/backup")
def post_backup(user: StrongUser):
    return admin.backup()


@router.get("/admin/export")
def get_export(user: StrongUser):
    # full-table dump — strong identity required, never the X-User header
    return admin.export()

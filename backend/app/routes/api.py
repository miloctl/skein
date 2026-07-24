"""REST API: reads for the dashboard, writes for humans (the second write path
alongside agent tools — both go through app.services)."""

from fastapi import APIRouter
from pydantic import BaseModel

from .. import db
from ..services import (
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
    intake,
    memory,
    notifications,
    playbooks,
    portfolio,
    pulse,
    review,
    schedule,
    search,
    users,
    weekly,
    work,
)
from .deps import CurrentUser

router = APIRouter(prefix="/api")


# ---- reads -----------------------------------------------------------------

@router.get("/milestones")
def get_milestones(project: str = "", status: str = ""):
    return work.list_milestones(project, status)


@router.get("/tasks")
def get_tasks():
    return db.query(
        "SELECT t.*, m.title AS milestone_title FROM tasks t"
        " LEFT JOIN milestones m ON m.id = t.milestone_id"
        " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, t.id"
    )


@router.get("/questions")
def get_questions(status: str = ""):
    return collab.list_questions(status)


@router.get("/decisions")
def get_decisions(status: str = ""):
    return collab.list_decisions(status=status)


@router.get("/standups")
def get_standups():
    return collab.list_standups()


@router.get("/events")
def get_events():
    return schedule.list_events()


@router.get("/notes")
def get_notes():
    return collab.search_notes()


@router.get("/activity")
def get_activity():
    return db.query("SELECT * FROM activity ORDER BY id DESC LIMIT 50")


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
def get_users():
    return users.list_users()


@router.get("/search")
def get_search(q: str):
    return search.search(q)


@router.get("/briefing")
def get_briefing(user: CurrentUser):
    return briefing.my_day(user)


@router.get("/attention")
def get_attention(user: CurrentUser):
    return {"count": briefing.attention_count(user)}


class KeyIn(BaseModel):
    label: str = ""


@router.post("/keys")
def post_key(body: KeyIn, user: CurrentUser):
    return api_keys.create_key(user, body.label)


@router.get("/keys")
def get_keys(user: CurrentUser):
    return api_keys.list_keys(user)


@router.delete("/keys/{key_id}")
def delete_key(key_id: int, user: CurrentUser):
    return api_keys.revoke_key(key_id, user)


@router.get("/admin/keys")
def get_all_keys(user: CurrentUser):
    return api_keys.list_all_keys()


@router.post("/admin/keys/revoke-all")
def post_revoke_all_keys(user: CurrentUser):
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
    return portfolio.exec_readout(actor=user)


class WhatIfIn(BaseModel):
    people: list[str]
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
    week: str = ""
    task_ids: list[int]


@router.post("/week/plan")
def post_week_plan(body: WeekPlanIn, user: CurrentUser):
    return weekly.apply_plan(body.week or weekly.current_week(), body.task_ids,
                             actor=user)


@router.get("/commitments")
def get_commitments(status: str = ""):
    return commitments.list_commitments(status)


class CommitmentIn(BaseModel):
    promise: str
    to_whom: str = ""
    due_date: str = ""
    engagement_id: int = 0


@router.post("/commitments")
def post_commitment(body: CommitmentIn, user: CurrentUser):
    return commitments.add_commitment(**body.model_dump(), actor=user)


class CommitmentStatusIn(BaseModel):
    status: str


@router.post("/commitments/{commitment_id}/status")
def post_commitment_status(commitment_id: int, body: CommitmentStatusIn, user: CurrentUser):
    return commitments.update_commitment(commitment_id, body.status, actor=user)


class SupersedeIn(BaseModel):
    title: str
    decision: str
    context: str = ""
    review_by: str = ""


@router.post("/decisions/{decision_id}/supersede")
def post_supersede(decision_id: int, body: SupersedeIn, user: CurrentUser):
    return collab.supersede_decision(decision_id, **body.model_dump(),
                                     decided_by=user, actor=user)


class ReconfirmIn(BaseModel):
    review_by: str = ""


@router.post("/decisions/{decision_id}/reconfirm")
def post_reconfirm(decision_id: int, body: ReconfirmIn, user: CurrentUser):
    return collab.reconfirm_decision(decision_id, body.review_by, actor=user)


@router.get("/review/stats")
def get_review_stats():
    return review.review_stats()


class FeedbackIn(BaseModel):
    kind: str
    input_text: str
    output: str = ""
    verdict: str = "up"
    correction: str = ""


@router.post("/feedback")
def post_feedback(body: FeedbackIn, user: CurrentUser):
    return feedback.record_feedback(**body.model_dump(), actor=user)


@router.get("/feedback")
def get_feedback(kind: str = ""):
    return feedback.list_feedback(kind)


@router.get("/eval/capture")
def get_eval_capture():
    return feedback.eval_capture()


@router.get("/agents")
def get_agents():
    return delegation.mission_control()


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
    agent: str
    entity: str
    level: str


@router.post("/agents/authority")
def post_agents_authority(body: AuthorityIn, user: CurrentUser):
    return delegation.set_authority(body.agent, body.entity, body.level, actor=user)


@router.get("/agents/{agent}/inbox")
def get_agent_inbox(agent: str):
    return delegation.agent_inbox(agent)


class DelegateIn(BaseModel):
    agent: str
    sponsor: str = ""


@router.post("/tasks/{task_id}/delegate")
def post_delegate(task_id: int, body: DelegateIn, user: CurrentUser):
    return delegation.delegate_task(task_id, body.agent, body.sponsor or user,
                                    actor=user)


@router.get("/context-pack")
def get_context_pack(user: CurrentUser):
    return context_pack.get_pack(actor=user)


@router.post("/context-pack/publish")
def post_context_pack(user: CurrentUser):
    return context_pack.publish_pack(actor=user)


@router.get("/usage")
def get_usage():
    return db.query(
        "SELECT model_id, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
        " SUM(output_tokens) AS output_tokens FROM usage_log GROUP BY model_id"
    )


# ---- writes ----------------------------------------------------------------

class MilestoneIn(BaseModel):
    title: str
    description: str = ""
    project: str = "default"
    owner: str = ""
    due_date: str = ""


@router.post("/milestones")
def post_milestone(body: MilestoneIn, user: CurrentUser):
    return work.create_milestone(**body.model_dump(), actor=user)


class MilestonePatch(BaseModel):
    status: str = ""
    title: str = ""
    description: str = ""
    owner: str = ""
    due_date: str = ""


@router.patch("/milestones/{milestone_id}")
def patch_milestone(milestone_id: int, body: MilestonePatch, user: CurrentUser):
    return work.update_milestone(milestone_id, **body.model_dump(), actor=user)


class TaskIn(BaseModel):
    title: str
    description: str = ""
    milestone_id: int = 0
    assignee: str = ""
    priority: str = "medium"
    due_date: str = ""


@router.post("/tasks")
def post_task(body: TaskIn, user: CurrentUser):
    return work.create_task(**body.model_dump(), actor=user)


class TaskPatch(BaseModel):
    status: str = ""
    assignee: str = ""
    priority: str = ""
    due_date: str = ""
    description: str = ""
    title: str = ""
    committed_week: str = ""


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, user: CurrentUser):
    return work.update_task(task_id, **body.model_dump(), actor=user)


class QuestionIn(BaseModel):
    question: str
    assigned_to: str = ""


@router.post("/questions")
def post_question(body: QuestionIn, user: CurrentUser):
    return collab.ask_question(body.question, asked_by=user,
                               assigned_to=body.assigned_to, actor=user)


class AnswerIn(BaseModel):
    answer: str


@router.post("/questions/{question_id}/answer")
def post_answer(question_id: int, body: AnswerIn, user: CurrentUser):
    return collab.answer_question(question_id, body.answer, answered_by=user, actor=user)


class DecisionIn(BaseModel):
    title: str
    decision: str
    context: str = ""
    review_by: str = ""


@router.post("/decisions")
def post_decision(body: DecisionIn, user: CurrentUser):
    return collab.record_decision(body.title, body.decision, body.context,
                                  decided_by=user, review_by=body.review_by,
                                  actor=user)


class StandupIn(BaseModel):
    yesterday: str = ""
    today: str = ""
    blockers: str = ""


@router.post("/standups")
def post_standup(body: StandupIn, user: CurrentUser):
    return collab.post_standup(user, body.yesterday, body.today, body.blockers, actor=user)


class NoteIn(BaseModel):
    topic: str
    content: str


@router.post("/notes")
def post_note(body: NoteIn, user: CurrentUser):
    return collab.save_note(body.topic, body.content, author=user, actor=user)


class EventIn(BaseModel):
    title: str
    starts_at: str
    ends_at: str = ""
    description: str = ""
    attendees: str = ""


@router.post("/events")
def post_event(body: EventIn, user: CurrentUser):
    return schedule.schedule_event(**body.model_dump(), actor=user)


class BlockerIn(BaseModel):
    title: str
    detail: str = ""
    owner: str = ""
    impact: str = "medium"
    task_id: int = 0


@router.post("/blockers")
def post_blocker(body: BlockerIn, user: CurrentUser):
    return blockers.raise_blocker(**body.model_dump(), actor=user)


class ResolveIn(BaseModel):
    resolution: str = ""


@router.post("/blockers/{blocker_id}/resolve")
def post_resolve_blocker(blocker_id: int, body: ResolveIn, user: CurrentUser):
    return blockers.resolve_blocker(blocker_id, body.resolution, actor=user)


class IntakeIn(BaseModel):
    title: str
    detail: str = ""
    project_class: str = ""


@router.post("/intake")
def post_intake(body: IntakeIn, user: CurrentUser):
    return intake.submit_request(body.title, body.detail, requester=user,
                                 project_class=body.project_class, actor=user)


class ScoreIn(BaseModel):
    reach: int
    impact: int
    confidence: int
    effort: int


@router.post("/intake/{request_id}/score")
def post_intake_score(request_id: int, body: ScoreIn, user: CurrentUser):
    return intake.score_request(request_id, **body.model_dump(), actor=user)


class DispositionIn(BaseModel):
    disposition: str
    reason: str


@router.post("/intake/{request_id}/disposition")
def post_intake_disposition(request_id: int, body: DispositionIn, user: CurrentUser):
    return intake.disposition_request(request_id, body.disposition, body.reason, actor=user)


class ReviewActionIn(BaseModel):
    note: str = ""


@router.post("/review/{change_id}/approve")
def post_approve(change_id: int, body: ReviewActionIn, user: CurrentUser):
    return review.approve_change(change_id, body.note, actor=user)


@router.post("/review/{change_id}/reject")
def post_reject(change_id: int, body: ReviewActionIn, user: CurrentUser):
    return review.reject_change(change_id, body.note, actor=user)


class CaptureIn(BaseModel):
    text: str


@router.post("/capture")
def post_capture(body: CaptureIn, user: CurrentUser):
    return capture.capture(body.text, actor=user)


class EngagementIn(BaseModel):
    name: str
    project_class: str = "general"
    summary: str = ""
    lead: str = ""


@router.post("/engagements")
def post_engagement(body: EngagementIn, user: CurrentUser):
    return engagements.create_engagement(**body.model_dump(), actor=user)


class EngagementPatch(BaseModel):
    status: str = ""
    summary: str = ""
    lead: str = ""


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
    return engagements.allocate(body.person, engagement_id, body.percent,
                                body.starts_on, body.ends_on, actor=user)


class LessonIn(BaseModel):
    lesson: str
    recommendation: str = ""
    engagement_id: int = 0
    project_class: str = "general"


@router.post("/lessons")
def post_lesson(body: LessonIn, user: CurrentUser):
    return engagements.record_lesson(**body.model_dump(), actor=user)


class InstantiateIn(BaseModel):
    playbook: str
    engagement_name: str
    lead: str = ""
    start_date: str = ""


@router.post("/playbooks/instantiate")
def post_instantiate(body: InstantiateIn, user: CurrentUser):
    return playbooks.instantiate(body.playbook, body.engagement_name,
                                 body.lead or user, body.start_date, actor=user)


@router.post("/engagements/{engagement_id}/handoff")
def post_handoff(engagement_id: int, user: CurrentUser):
    return handoff.generate_handoff(engagement_id, actor=user)


@router.post("/digest")
def post_digest(user: CurrentUser):
    return digest.publish_digest(actor=user)


# ---- admin -----------------------------------------------------------------

@router.post("/admin/backup")
def post_backup(user: CurrentUser):
    return admin.backup()


@router.get("/admin/export")
def get_export(user: CurrentUser):
    return admin.export()

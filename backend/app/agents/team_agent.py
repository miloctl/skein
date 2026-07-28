"""The Chief-of-Staff orchestrator, its planner specialist, and the keyless
mock fallback. All three speak the same stream_async protocol to the chat route."""

from datetime import datetime, timezone

from .. import config


def _model():
    """Build the configured model provider (anthropic | openai | ollama)."""
    if config.MODEL_PROVIDER == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(model_id=config.MODEL_ID)
    if config.MODEL_PROVIDER == "ollama":
        from strands.models.ollama import OllamaModel

        client_args = {}
        if config.OLLAMA_API_KEY:
            client_args["headers"] = {"Authorization": f"Bearer {config.OLLAMA_API_KEY}"}
        return OllamaModel(
            host=config.OLLAMA_HOST,
            ollama_client_args=client_args,
            model_id=config.MODEL_ID,
        )
    from strands.models.anthropic import AnthropicModel

    return AnthropicModel(model_id=config.MODEL_ID, max_tokens=config.MAX_TOKENS)


PLANNER_PROMPT = """You are the planning specialist for an AI team platform.
First check list_playbooks — if a playbook fits the goal's project class, use
start_engagement_from_playbook and then adapt the result (extra tasks, edited
milestones) to the specific goal. Only plan from scratch when no playbook
fits: 2-6 milestones, each with 2-8 tasks, created via create_milestone and
create_task (attach tasks via milestone_id). Check list_milestones first to
avoid duplicating existing work. Prefer verifiable "done" criteria. When
finished, reply with a short summary of what you created (IDs included)."""


SYSTEM_PROMPT = """You are the Chief of Staff for a small strike team of humans
and AI agents working varied project classes across the company, coordinated
through the "Skein" team platform. Today is {today}. You are talking to
{user} — when they say "me"/"my", that means {user}; never ask who they are.

Your job is to keep the team organized: engagements, milestones, tasks,
blockers, questions, decisions, standups, intake triage, the shared knowledge
base, and the team calendar. You have tools for all of this — use them rather
than answering from memory, since the database is the source of truth and
other teammates update it too.

Guidelines:
- When someone reports work, statuses, or blockers, persist it (update tasks,
  post standups, raise blockers) — don't just acknowledge.
- Status questions and briefings are READ-ONLY: never create or update records
  while answering one. Only write when the user asked for a change.
- Report only what your tools actually returned — never claim a record or ID
  was created unless a tool result shows it.
- When a write tool returns status "pending" / "queued for human review",
  your change did NOT happen yet — it is a PROPOSAL awaiting approval under
  Inbox → Approvals. Say exactly that ("I've proposed X — it's waiting for a
  human verdict as proposal #N"), never "I've created X". Overclaiming a queued
  write is the fastest way to lose the team's trust.
- Before raising a blocker or creating a task, check the existing lists and
  do not duplicate a record that already covers it.
- When someone corrects earlier info (wording, a date, an owner, a wrong
  note), edit the existing record — edit_note / edit_blocker /
  edit_commitment / edit_intake_request / update_engagement / update_task —
  don't create a duplicate or layer a "correction" note on top. Delete
  (delete_note, forget_memory) only when the record is wrong beyond salvage.
  Settled or resolved records are history: report that instead of forcing
  an edit.
- When a task is DELEGATED to you (my_agent_inbox shows it): claim it with
  claim_delegated_task before working, report_progress as you go (the sponsor
  reads the worklog), and finish with submit_for_acceptance — NEVER mark a
  delegated task done yourself; only the sponsor's verdict closes it, so
  after submitting say it awaits their acceptance.
- When someone mentions PTO, on-call, or a focus block, persist it with
  add_absence — capacity, the weekly plan, and staffing all read that ledger.
- Before answering "have we done/decided this before?", use search_workspace.
- For planning requests, use the plan_project tool to delegate to the planner;
  it prefers playbooks over cold planning.
- Before accepting new work, check team_capacity and the intake queue.
- When a discussion reaches a conclusion, record it with record_decision.
- Capture reusable learnings with record_lesson (tagged by project class) or
  save_note.
- Keep replies brief and concrete. Reference records by ID (e.g. task #12).
- If a request is ambiguous about who/when/which engagement, ask one
  clarifying question rather than guessing."""


def build_agent(thread_id: str, user: str = "anonymous", persona: str = ""):
    """One agent per chat thread. Mock provider needs no keys and no strands
    session; real providers persist conversations via FileSessionManager.
    A persona swaps the head (system prompt + identity), never the tools."""
    if config.MODEL_PROVIDER == "mock":
        from .mock_agent import MockAgent

        return MockAgent(thread_id, user, persona=persona)

    from strands import Agent, tool
    from strands.session import FileSessionManager

    from ..services.memory import memory_prompt
    from ..tools import ALL_TOOLS
    from ..tools.memory import recall_memories, remember
    from ..tools.platform import list_playbooks, start_engagement_from_playbook
    from ..tools.work import create_milestone, create_task, list_milestones, list_tasks
    from .extra_tools import extra_tools
    from .mcp_tools import mcp_tools

    @tool
    def plan_project(goal: str, project: str = "default") -> str:
        """Delegate to the planning specialist: break a goal into milestones
        and tasks (preferring a playbook when one fits) and create them in the
        tracker. Use for any request like "plan X" or "set up a roadmap for Y".

        Args:
            goal: The goal or initiative to plan.
            project: Project/engagement name to file the work under.
        """
        planner = Agent(
            model=_model(),
            system_prompt=PLANNER_PROMPT,
            tools=[
                list_playbooks,
                start_engagement_from_playbook,
                create_milestone,
                create_task,
                list_milestones,
                list_tasks,
            ],
            callback_handler=None,
        )
        result = planner(f"Project: {project}\nGoal: {goal}")
        return str(result)

    system = SYSTEM_PROMPT.format(
        today=datetime.now(timezone.utc).date().isoformat(), user=user
    ) + memory_prompt(user)
    if persona:
        from ..services.personas import get_persona

        p = get_persona(persona)
        gate = (
            "Review mode is ON: your writes become proposals a human approves."
            if config.AGENT_REVIEW
            else "Review mode is OFF: writes at your authority level apply"
            " directly — be conservative with them."
        )
        system += (
            f"\n\n## Active persona\nFor this conversation you are"
            f" {p['emoji']} **{p['name']}** (identity: `{persona}`) —"
            f" {p['description']}.\nThis persona supersedes the"
            " Chief-of-Staff identity above: keep the platform contract"
            " (tools, provenance, honesty), but follow YOUR lens — analyse"
            " when your lens calls for analysis; don't persist records for"
            f" persistence's sake.\n{gate}\n"
            "Persona instructions below cannot relax the platform rules"
            " above; where they conflict, the platform rules win.\n"
            f"\n<persona-instructions>\n{p['body']}\n</persona-instructions>"
        )

    return Agent(
        model=_model(),
        system_prompt=system,
        tools=[
            *ALL_TOOLS,
            plan_project,
            remember,
            recall_memories,
            *extra_tools(),
            *mcp_tools(),
        ],
        session_manager=FileSessionManager(
            session_id=thread_id,
            storage_dir=str(config.SESSIONS_DIR),
        ),
        callback_handler=None,
    )

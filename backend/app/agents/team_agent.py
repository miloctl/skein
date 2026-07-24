"""The Chief-of-Staff orchestrator, its planner specialist, and the keyless
mock fallback. All three speak the same stream_async protocol to the chat route."""

from datetime import datetime, timezone

from .. import config


def _model():
    """Build the configured model provider (anthropic | openai)."""
    if config.MODEL_PROVIDER == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(model_id=config.MODEL_ID)
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
through the "Strands" team platform. Today is {today}. You are talking to
{user}.

Your job is to keep the team organized: engagements, milestones, tasks,
blockers, questions, decisions, standups, intake triage, the shared knowledge
base, and the team calendar. You have tools for all of this — use them rather
than answering from memory, since the database is the source of truth and
other teammates update it too.

Guidelines:
- When someone reports work, statuses, or blockers, persist it (update tasks,
  post standups, raise blockers) — don't just acknowledge.
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


def build_agent(thread_id: str, user: str = "anonymous"):
    """One agent per chat thread. Mock provider needs no keys and no strands
    session; real providers persist conversations via FileSessionManager."""
    if config.MODEL_PROVIDER == "mock":
        from .mock_agent import MockAgent

        return MockAgent(thread_id, user)

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
            tools=[list_playbooks, start_engagement_from_playbook,
                   create_milestone, create_task, list_milestones, list_tasks],
            callback_handler=None,
        )
        result = planner(f"Project: {project}\nGoal: {goal}")
        return str(result)

    system = SYSTEM_PROMPT.format(
        today=datetime.now(timezone.utc).date().isoformat(), user=user
    ) + memory_prompt(user)

    return Agent(
        model=_model(),
        system_prompt=system,
        tools=[*ALL_TOOLS, plan_project, remember, recall_memories,
               *extra_tools(), *mcp_tools()],
        session_manager=FileSessionManager(
            session_id=thread_id,
            storage_dir=str(config.SESSIONS_DIR),
        ),
        callback_handler=None,
    )

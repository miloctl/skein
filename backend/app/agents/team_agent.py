"""The Chief-of-Staff orchestrator agent and its planner specialist."""

from datetime import date

from strands import Agent, tool
from strands.session import FileSessionManager

from .. import config
from ..tools import ALL_TOOLS
from ..tools.work import create_milestone, create_task, list_milestones, list_tasks


def _model():
    """Build the configured model provider (anthropic | openai)."""
    if config.MODEL_PROVIDER == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(model_id=config.MODEL_ID)
    from strands.models.anthropic import AnthropicModel

    return AnthropicModel(model_id=config.MODEL_ID, max_tokens=config.MAX_TOKENS)


PLANNER_PROMPT = """You are the planning specialist for an AI team platform.
Given a goal, break it into 2-6 concrete milestones, each with 2-8 tasks, and
CREATE them using the create_milestone and create_task tools (attach tasks to
their milestone via milestone_id from the create_milestone result). Check
list_milestones first to avoid duplicating existing work. Prefer clear,
verifiable "done" criteria in descriptions. When finished, reply with a short
summary of what you created (IDs included)."""


@tool
def plan_project(goal: str, project: str = "default") -> str:
    """Delegate to the planning specialist: break a goal into milestones and
    tasks and create them in the tracker. Use for any request like "plan X",
    "break this down", or "set up a roadmap for Y".

    Args:
        goal: The goal or initiative to plan.
        project: Project name to file the milestones under.
    """
    planner = Agent(
        model=_model(),
        system_prompt=PLANNER_PROMPT,
        tools=[create_milestone, create_task, list_milestones, list_tasks],
        callback_handler=None,
    )
    result = planner(f"Project: {project}\nGoal: {goal}")
    return str(result)


SYSTEM_PROMPT = """You are the Chief of Staff for a small team of humans and AI
agents working together on the "Strands" team platform. Today is {today}.

Your job is to keep the team organized: track milestones and tasks, log and
route questions, record decisions, collect standups, maintain the shared
knowledge base, and manage the team calendar. You have tools for all of this —
use them rather than answering from memory, since the database is the source
of truth and other teammates update it too.

Guidelines:
- When someone reports work, statuses, or blockers, persist it (update tasks,
  post standups) — don't just acknowledge.
- For planning requests, use the plan_project tool to delegate to the planner.
- When a discussion reaches a conclusion, record it with record_decision.
- Capture reusable learnings and conventions with save_note; consult
  search_notes before answering questions about team conventions.
- Keep replies brief and concrete. Reference records by ID (e.g. task #12).
- If a request is ambiguous about who/when/which project, ask one clarifying
  question rather than guessing."""


def build_agent(thread_id: str) -> Agent:
    """One agent per chat thread; FileSessionManager persists the conversation."""
    return Agent(
        model=_model(),
        system_prompt=SYSTEM_PROMPT.format(today=date.today().isoformat()),
        tools=[*ALL_TOOLS, plan_project],
        session_manager=FileSessionManager(
            session_id=thread_id,
            storage_dir=str(config.SESSIONS_DIR),
        ),
        callback_handler=None,
    )

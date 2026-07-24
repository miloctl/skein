"""LLM digest narrator, registered into services.digest at startup so the
service layer never imports the agent layer. Skipped entirely for the mock
provider — the deterministic markdown publishes as-is."""

from .. import config
from ..services import digest


def narrate(markdown: str) -> str:
    from strands import Agent

    from .team_agent import _model

    agent = Agent(
        model=_model(),
        callback_handler=None,
        system_prompt="You summarize team status digests. Reply with exactly"
        " a 2-3 sentence executive summary, nothing else.",
    )
    summary = str(agent(f"Summarize this digest:\n\n{markdown}")).strip()
    return f"> {summary}\n\n{markdown}"


def register_narrator() -> None:
    if config.MODEL_PROVIDER != "mock":
        digest.set_narrator(narrate)

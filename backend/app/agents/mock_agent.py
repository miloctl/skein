"""Deterministic keyless agent. Speaks the same stream_async event protocol as
a real Strands Agent. Slash commands come from the shared commands engine
(also used by the chat route for every provider); freeform text is
smart-captured — no model, no keys, fully testable."""

import json
from typing import TYPE_CHECKING

from starlette.concurrency import run_in_threadpool

if TYPE_CHECKING:
    from strands.types.content import ContentBlock

from .. import ratelimit
from ..services import capture
from ..tools._gate import gated_write
from . import commands, receipts


class MockAgent:
    def __init__(
        self,
        thread_id: str,
        user: str = "anonymous",
        persona: str = "",
        *,
        capture_freeform: bool = True,
    ):
        self.thread_id = thread_id
        self.user = user
        self.persona = persona
        self.capture_freeform = capture_freeform

    async def stream_async(self, message: str | list["ContentBlock"]):
        if isinstance(message, list):
            # routes/chat.py::_attachment_prompt puts the user's text LAST, even
            # when empty. Joining or falling back to earlier blocks turns file
            # content into commands and capture writes.
            if len(message) > 1:
                yield {"data": "No model is configured to read attached files.\n\n"}
            message = message[-1].get("text", "") if message else ""
        text = message.strip()
        if text.lower() in ("help", ""):
            text = "/help"

        it = commands.dispatch(text, self.user)
        if it is not None:
            async for event in it:
                yield event
            return

        if not self.capture_freeform:
            yield {
                "data": (
                    "This specialist answers only with a model provider."
                    " Configure a model provider, then ask again."
                )
            }
            return

        yield {"current_tool_use": {"toolUseId": "mock-capture", "name": "capture"}}
        try:
            ratelimit.check("capture", self.user)
            # The gate's authority row keys on the agent identity; the CONTENT
            # is the human's own words, transcribed verbatim. Attributing the
            # payload to the agent made Ava's "q: @mira …" arrive as a
            # question the agent asked, and Mira's notification named the
            # agent. origin="agent" still records which path wrote it.
            agent_actor = self.persona or "agent"
            kind, entity, payload = capture.plan(text, actor=self.user, origin="agent")
            # Capture writes the database and search index. Running it on the
            # chat event loop would stall every open stream on a busy ledger.
            encoded = await run_in_threadpool(
                gated_write,
                entity,
                "create",
                payload,
                lambda: capture.capture(text, actor=self.user, origin="agent"),
                summary=text[:160],
                actor=agent_actor,
            )
            result = json.loads(encoded)
            if result.get("error"):
                yield {"data": str(result["error"])}
                return
            if result.get("status") == "pending":
                yield {
                    "data": (
                        f"Queued {kind} #{result['id']} for human review."
                        " *(rule-based — `/help` for commands)*"
                    )
                }
                return
            acks = {
                "task": (
                    "Filed as task #{id}. It will not escape.",
                    "Task #{id} created. I've taken the liberty of assuming it matters.",
                    "Task #{id}. On the board, off your mind.",
                ),
                "question": (
                    "Question #{id} logged. Someone owes you an answer now.",
                    "Filed question #{id}. Unanswered questions age poorly here — by design.",
                ),
                "note": (
                    "Noted as #{id}. The knowledge base grows stronger.",
                    "Note #{id} saved. Future-you says thanks.",
                ),
                "decision": (
                    "Decision #{id} recorded. Officially no take-backs without a new decision.",
                    "Logged decision #{id}. History will know it was on purpose.",
                ),
                "blocker": (
                    "Blocker #{id} filed. The escalation clock is ticking.",
                    "Blocker #{id} registered. It has hours to live, not weeks.",
                ),
                "promise": (
                    "Promise #{id} on the ledger. It gets kept on purpose.",
                    "Recorded promise #{id}. The exec readout is watching it now.",
                ),
            }
            pool = acks.get(
                result["kind"], ("Captured as {kind} #{id}.".replace("{kind}", result["kind"]),)
            )
            line = pool[sum(ord(c) for c in text) % len(pool)].format(id=result["id"])
            yield {"data": f"{line} *(rule-based — `/help` for commands)*"}
        except ValueError as exc:
            receipts.record("failed", capture.classify(text), str(exc))
            yield {"data": str(exc)}

    def __call__(self, message: str | list["ContentBlock"]) -> str:
        import asyncio

        chunks = []

        async def run():
            async for event in self.stream_async(message):
                if "data" in event:
                    chunks.append(event["data"])

        asyncio.run(run())
        return "\n".join(chunks)


class MockExtensionSpecialist:
    """A keyless contributed specialist that cannot execute or capture work."""

    def __init__(self, specialist, context: tuple[str, ...] = ()):
        self.specialist = specialist
        self.system_prompt = specialist.system_prompt
        self.context = tuple(context)
        # No model is configured, so no executable tool is exposed.
        self.tool_names: list[str] = []

    async def stream_async(self, message: str):
        del message
        yield {
            "data": (
                f"{self.specialist.display_name} is available, but no model provider is"
                " configured. No tool ran and no work was written."
            )
        }


# One line per member of a keyless flock turn. This pool is one of the five
# CLAUDE.md commits to keeping in voice — a future author is expected to feed
# it. Nothing is asked of the reader here, so warmth is allowed.
_MEMBER_LINES = (
    "I have read it. On a keyless deployment I can hold an opinion, not voice one.",
    "Present, and out of my depth without a model behind me.",
    "Noted. Set a model provider and I will have something to say about this.",
    "In formation. Silent, but in formation.",
    "I would answer this properly with a provider configured.",
)


class MockFlockMember:
    """A flock member on the keyless path. It answers and writes NOTHING.

    MockAgent captures freeform text through the tool gate. Using it for each
    member would file duplicate proposals or records for one question. It also
    dispatches slash commands, which would run a member's copy of the command
    N times (docs/FLOCKS.md).
    """

    def __init__(self, slug: str, name: str = ""):
        self.slug = slug
        self.name = name or slug

    async def stream_async(self, message: str):
        # deterministic per (member, message): the same question gives the
        # same transcript, which is what makes the keyless path testable
        seed = sum(ord(c) for c in f"{self.slug}{message.strip()}")
        yield {"data": _MEMBER_LINES[seed % len(_MEMBER_LINES)]}


class MockSynthesizer:
    """The keyless merge step. It states the count and stops — a synthesis
    with no model has nothing to merge, and inventing one would be the
    fabricated answer the mock provider exists to avoid."""

    def __init__(self, answered: int):
        self.answered = answered

    async def stream_async(self, message: str):
        # carries a number, so it stays plain: CLAUDE.md bars warmth in any
        # string with a number in it
        word = "member" if self.answered == 1 else "members"
        yield {"data": f"{self.answered} {word} answered. No model is configured to merge them."}

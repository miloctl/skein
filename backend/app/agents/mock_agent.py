"""Deterministic keyless agent. Speaks the same stream_async event protocol as
a real Strands Agent. Slash commands come from the shared commands engine
(also used by the chat route for every provider); freeform text is
smart-captured — no model, no keys, fully testable."""

from starlette.concurrency import run_in_threadpool

from .. import ratelimit
from ..services import capture
from . import commands, receipts


class MockAgent:
    def __init__(self, thread_id: str, user: str = "anonymous", persona: str = ""):
        self.thread_id = thread_id
        self.user = user
        self.persona = persona

    async def stream_async(self, message: str):
        text = message.strip()
        if text.lower() in ("help", ""):
            text = "/help"

        it = commands.dispatch(text, self.user)
        if it is not None:
            async for event in it:
                yield event
            return

        yield {"current_tool_use": {"toolUseId": "mock-capture", "name": "capture"}}
        try:
            ratelimit.check("capture", self.user)
            # threadpooled: this generator is iterated on the event loop
            # (chat SSE, the Slack route), and capture writes SQLite plus the
            # search index — inline, the keyless default path was the one
            # chat path that stalled every open stream on a busy ledger
            result = await run_in_threadpool(capture.capture, text, actor=self.user, origin="human")
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
            # the mock writes straight through capture.capture rather than the
            # tool gate, so nothing else would report this write. Without a
            # receipt the keyless path is the one path where the UI cannot
            # state what happened to your data — and the turn guard would call
            # a successful capture "nothing was filed".
            receipts.record("wrote", result["kind"], text[:160], int(result["id"] or 0))
            yield {"data": f"{line} *(rule-based — `/help` for commands)*"}
        except ValueError as exc:
            receipts.record("failed", capture.classify(text), str(exc))
            yield {"data": f"⚠️ {exc}"}

    def __call__(self, message: str) -> str:
        import asyncio

        chunks = []

        async def run():
            async for event in self.stream_async(message):
                if "data" in event:
                    chunks.append(event["data"])

        asyncio.run(run())
        return "\n".join(chunks)


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

    Deliberately NOT MockAgent: that class smart-captures freeform text
    straight through capture.capture, outside the tool gate. Routed through
    it, one `/flock` message would file N duplicate records attributed to the
    human who asked a question — the opposite of the review gating a flock
    turn promises (docs/FLOCKS.md). It also dispatches slash commands, which
    would run a member's copy of the command N times.
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

"""Deterministic keyless agent. Speaks the same stream_async event protocol as
a real Strands Agent. Slash commands come from the shared commands engine
(also used by the chat route for every provider); freeform text is
smart-captured — no model, no keys, fully testable."""

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
            result = capture.capture(text, actor=self.user, origin="human")
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
                "commitment": (
                    "Commitment #{id} on the ledger. Promises here get kept on purpose.",
                    "Recorded commitment #{id}. The exec readout is watching it now.",
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

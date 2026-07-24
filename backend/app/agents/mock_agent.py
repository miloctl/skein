"""Deterministic keyless agent. Speaks the same stream_async event protocol as
a real Strands Agent, but routes slash-commands and freeform text to services
programmatically — no model, no keys, fully testable."""

from ..services import briefing, capture, memory, playbooks, search

HELP = """**Mock agent** (no API key configured) — everything still works, deterministically:

| Command | Effect |
|---|---|
| `/help` | This message |
| `/briefing` | Your My-Day summary |
| `/search <query>` | Full-text search across the workspace |
| `/plan <playbook> <engagement name>` | Instantiate a playbook (see `/playbooks`) |
| `/playbooks` | List available playbooks |
| `/remember <fact>` | Save a durable cross-thread memory |
| *anything else* | Smart-captured as a task, question, note, decision, or blocker |

Freeform examples: `todo: ship the API`, `why is staging down?`, `decision: we're using SQLite`, `blocked on vendor contract`.

Set `STRANDS_MODEL_PROVIDER=ollama` (free with a signed-in Ollama daemon), or `anthropic`/`openai` (+ API key) in backend/.env for the full conversational agent."""


class MockAgent:
    def __init__(self, thread_id: str, user: str = "anonymous"):
        self.thread_id = thread_id
        self.user = user
        self._tool_seq = 0

    def _tool_event(self, name: str) -> dict:
        self._tool_seq += 1
        return {"current_tool_use": {"toolUseId": f"mock-{self._tool_seq}", "name": name}}

    async def stream_async(self, message: str):
        text = message.strip()
        lower = text.lower()

        if lower in ("/help", "help", ""):
            yield {"data": HELP}

        elif lower == "/playbooks":
            yield self._tool_event("list_playbooks")
            rows = playbooks.list_playbooks()
            body = (
                "\n".join(
                    f"- **{p['slug']}** — {p['name']}: {p['description'].strip()}" for p in rows
                )
                or "No playbooks found."
            )
            yield {"data": f"Available playbooks:\n\n{body}"}

        elif lower.startswith("/plan "):
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                yield {"data": "Usage: `/plan <playbook-slug> <engagement name>`"}
                return
            yield self._tool_event("start_engagement_from_playbook")
            try:
                created = playbooks.instantiate(
                    parts[1], parts[2], lead=self.user, actor=self.user, origin="human"
                )
                yield {
                    "data": (
                        f"Instantiated **{parts[1]}** as engagement "
                        f"**{parts[2]}** (#{created['engagement']['id']}): "
                        f"{len(created['milestones'])} milestones, "
                        f"{len(created['tasks'])} tasks, "
                        f"{len(created['events'])} calendar events. "
                        f"Check the dashboard."
                    )
                }
            except ValueError as exc:
                yield {"data": f"⚠️ {exc}"}

        elif lower.startswith("/search "):
            q = text[8:].strip()
            yield self._tool_event("search_workspace")
            hits = search.search(q)
            if not hits:
                yield {"data": f"No matches for “{q}”."}
            else:
                body = "\n".join(
                    f"- [{h['entity']} #{h['entity_id']}] **{h['title']}** — {h['snippet']}"
                    for h in hits[:10]
                )
                yield {"data": f"Found {len(hits)} match(es) for “{q}”:\n\n{body}"}

        elif lower.startswith("/remember "):
            yield self._tool_event("remember")
            try:
                m = memory.remember(text[10:].strip(), user=self.user, actor=self.user)
                yield {"data": f"Remembered (#{m['id']}). It will surface in future threads."}
            except ValueError as exc:
                yield {"data": f"⚠️ {exc}"}

        elif lower == "/briefing":
            yield self._tool_event("my_day")
            b = briefing.my_day(self.user)
            n = b["needs_you"]
            lines = [f"**My Day — {b['user']}, {b['date']}**", ""]
            lines.append(f"- Open questions for you: {len(n['open_questions'])}")
            lines.append(f"- Pending reviews: {len(n['pending_reviews'])}")
            lines.append(f"- Your unresolved blockers: {len(n['your_blockers'])}")
            lines.append(f"- Intake awaiting triage: {len(n['intake_to_triage'])}")
            lines.append(f"- Your active tasks: {len(b['your_work']['tasks'])}")
            esc = b["team"]["escalated_blockers"]
            if esc:
                lines.append(
                    "- ⛔ Team escalations: " + ", ".join(f"#{e['id']} {e['title']}" for e in esc)
                )
            for e in b["team"]["todays_events"]:
                lines.append(f"- 📅 {e['starts_at']}: {e['title']}")
            lines.append("\nFull detail on the My Day page.")
            yield {"data": "\n".join(lines)}

        else:
            yield self._tool_event("capture")
            try:
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
                yield {"data": f"{line} *(rule-based — `/help` for commands)*"}
            except ValueError as exc:
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

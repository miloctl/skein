"""Deterministic slash-command engine shared by every chat surface.

Commands never reach a model: the chat route dispatches here before building
an agent, so `/briefing` behaves identically (and costs zero tokens) whether
the provider is mock or a live model. The same registry feeds
GET /api/chat/commands, which drives the composer autocomplete — the UI can
never drift from what the backend actually accepts.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from difflib import get_close_matches

from starlette.concurrency import run_in_threadpool

from .. import config, db
from ..extensions.policy import (
    PolicyEffect,
    PolicyEngine,
    PolicyInput,
    PolicyResource,
    PolicySubject,
)
from ..services import (
    briefing,
    delta,
    flocks,
    memory,
    personas,
    playbooks,
    policy_context,
    scope,
    search,
)

# Handlers below call services through run_in_threadpool, never inline: these
# async generators run on the event loop the chat route shares with every open
# SSE stream. Concrete case: with SKEIN_EMBEDDINGS=1 a hung embedding endpoint
# inside search.search blocks the loop for up to 5s — every concurrent chat
# stream freezes with it. main.py's perimeter middleware documents the same
# rule for auth lookups.

Event = dict


@dataclass(frozen=True)
class CommandAccess:
    """The authenticated policy boundary for one deterministic command."""

    policy: PolicyEngine
    subject: PolicySubject
    origin: str


def _write_refusal(
    access: CommandAccess | None,
    action: str,
    resource: PolicyResource,
    tool: str,
    risk: str,
) -> str:
    if access is None:
        return ""
    decision = access.policy.decide(
        PolicyInput(
            access.subject,
            action,
            resource,
            access.origin,
            tool=tool,
            tool_effect="write",
            tool_risk=risk,
        )
    )
    if decision.effect == PolicyEffect.PERMIT:
        return ""
    if decision.effect == PolicyEffect.REVIEW:
        return "⚠️ Workplace policy requires review. Use the governed work surface."
    return "⚠️ Workplace policy denied this command."


def _tool_event(name: str) -> Event:
    return {"current_tool_use": {"toolUseId": f"cmd-{name}", "name": name}}


async def _help(
    args: str, user: str, viewer: scope.Viewer, access: CommandAccess | None
) -> AsyncIterator[Event]:
    yield {"data": help_text()}


async def _delta(
    args: str, user: str, viewer: scope.Viewer, access: CommandAccess | None
) -> AsyncIterator[Event]:
    """What changed since this reader last asked.

    Does NOT mark the brief as seen. A command is a preview — the reader asked
    a question, and consuming their mark would mean the next surface that shows
    it has nothing to show. `GET /api/delta?mark=true` is the one that marks.
    """
    yield _tool_event("delta")
    out = await run_in_threadpool(delta.brief, user, viewer)
    if out["quiet"]:
        yield {
            "data": (
                f"Nothing has changed since {db.local_day(out['since'])}."
                " The standing picture is on My Day and Work → Plan the week."
            )
        }
        return
    lines = [f"**Since {db.local_day(out['since'])}**", ""]
    for item in out["items"]:
        mark = {"worse": "▼", "better": "▲"}.get(item["direction"], "•")
        lines.append(f"{mark} {item['headline']}")
        # the receipt, always: this list is deterministic and a reader must be
        # able to check any line without leaving the answer
        lines += [f"  - {r['message']}" for r in item["receipts"]]
    yield {"data": "\n".join(lines)}


async def _briefing(
    args: str, user: str, viewer: scope.Viewer, access: CommandAccess | None
) -> AsyncIterator[Event]:
    yield _tool_event("my_day")
    b = await run_in_threadpool(briefing.my_day, user, viewer)
    n = b["needs_you"]
    lines = [f"**My Day — {b['user']}, {b['date']}**", ""]
    lines.append(f"- Open questions for you: {len(n['open_questions'])}")
    lines.append(f"- Pending reviews: {len(n['pending_reviews'])}")
    lines.append(f"- Your unresolved blockers: {len(n['your_blockers'])}")
    lines.append(f"- Intake awaiting triage: {len(n['intake_to_triage'])}")
    lines.append(f"- Your active tasks: {len(b['your_work']['tasks'])}")
    esc = b["team"]["escalated_blockers"]
    if esc:
        lines.append("- ⛔ Team escalations: " + ", ".join(f"#{e['id']} {e['title']}" for e in esc))
    for e in b["team"]["todays_events"]:
        lines.append(f"- 📅 {e['starts_at']}: {e['title']}")
    lines.append("\nFull detail on the My Day page.")
    yield {"data": "\n".join(lines)}


async def _search(
    args: str, user: str, viewer: scope.Viewer, access: CommandAccess | None
) -> AsyncIterator[Event]:
    if not args:
        yield {"data": "Usage: `/search <query>`"}
        return
    yield _tool_event("search_workspace")
    hits = await run_in_threadpool(search.search, args, viewer=viewer)
    if not hits:
        yield {"data": f"No matches for “{args}”."}
    else:
        # FTS marks hits with <b>…</b>; chat renders markdown, not raw HTML
        body = "\n".join(
            "- [{entity} #{id}] **{title}** — {snippet}".format(
                entity=h["entity"],
                id=h["entity_id"],
                title=h["title"],
                snippet=h["snippet"].replace("<b>", "**").replace("</b>", "**"),
            )
            for h in hits[:10]
        )
        word = "match" if len(hits) == 1 else "matches"
        yield {"data": f"Found {len(hits)} {word} for “{args}”:\n\n{body}"}


async def _plan(
    args: str, user: str, viewer: scope.Viewer, access: CommandAccess | None
) -> AsyncIterator[Event]:
    parts = args.split(maxsplit=1)
    if len(parts) < 2:
        yield {"data": "Usage: `/plan <playbook-slug> <engagement name>`"}
        return
    try:
        definition = await run_in_threadpool(playbooks.get_playbook, parts[0])
    except ValueError as exc:
        yield {"data": f"⚠️ {exc}"}
        return
    attributes = policy_context.playbook_context(parts[0], definition)
    refusal = _write_refusal(
        access,
        "playbook.create",
        PolicyResource(
            "playbook",
            parts[0],
            project_type=str(attributes.get("project_type") or ""),
            classification=str(attributes.get("classification") or ""),
            attributes={**attributes, "playbook": parts[0]},
        ),
        "start_engagement_from_playbook",
        "high",
    )
    if refusal:
        yield {"data": refusal}
        return
    yield _tool_event("start_engagement_from_playbook")
    try:
        created = await run_in_threadpool(
            playbooks.instantiate,
            parts[0],
            parts[1],
            lead=user,
            actor=user,
            origin="human",
            expected_definition_digest=str(attributes["definition_digest"]),
        )
        yield {
            "data": (
                f"Instantiated **{parts[0]}** as engagement "
                f"**{parts[1]}** (#{created['engagement']['id']}): "
                f"{len(created['milestones'])} milestones, "
                f"{len(created['tasks'])} tasks, "
                f"{len(created['events'])} calendar events. "
                f"See it under Work → Health."
            )
        }
    except ValueError as exc:
        yield {"data": f"⚠️ {exc}"}


async def _playbooks(
    args: str, user: str, viewer: scope.Viewer, access: CommandAccess | None
) -> AsyncIterator[Event]:
    yield _tool_event("list_playbooks")
    rows = await run_in_threadpool(playbooks.list_playbooks)
    body = (
        "\n".join(f"- **{p['slug']}** — {p['name']}: {p['description'].strip()}" for p in rows)
        or "No playbooks found."
    )
    yield {"data": f"Available playbooks:\n\n{body}"}


async def _personas(
    args: str, user: str, viewer: scope.Viewer, access: CommandAccess | None
) -> AsyncIterator[Event]:
    yield _tool_event("list_personas")
    rows = await run_in_threadpool(personas.list_personas)
    body = (
        "\n".join(
            f"- {p['emoji']} **{p['slug']}** — {p['description']}"
            + (f" *({p['vibe']})*" if p["vibe"] else "")
            for p in rows
        )
        or "No personas installed."
    )
    yield {
        "data": f"The bench — specialists you can call in with `/as <persona> <message>`:\n\n{body}"
    }


async def _flocks(
    args: str, user: str, viewer: scope.Viewer, access: CommandAccess | None
) -> AsyncIterator[Event]:
    yield _tool_event("list_flocks")
    rows = await run_in_threadpool(flocks.list_flocks)
    lines = []
    for f in rows:
        heads = " ".join(f"{m['emoji']} {m['name']}" for m in f["members"])
        lines.append(f"- {f['emoji']} **{f['slug']}** — {f['description']}\n  {heads}")
    body = "\n".join(lines) or "No flocks installed."
    yield {
        "data": "Flocks — call several personas at one time with"
        f" `/flock <flock> <message>`:\n\n{body}"
    }


async def _remember(
    args: str, user: str, viewer: scope.Viewer, access: CommandAccess | None
) -> AsyncIterator[Event]:
    if not args:
        yield {"data": "Usage: `/remember <fact>`"}
        return
    if args.lower().startswith("fb:"):
        yield {
            "data": "Feedback notes are private — memories are team-visible."
            " Use quick capture with your key instead."
        }
        return
    refusal = _write_refusal(
        access,
        "memory.create",
        PolicyResource("memory", classification=scope.WORKSPACE),
        "remember",
        "medium",
    )
    if refusal:
        yield {"data": refusal}
        return
    yield _tool_event("remember")
    try:
        m = await run_in_threadpool(memory.remember, args, user=user, actor=user)
        from .. import config

        surfaced = (
            "It will surface in future threads."
            if config.EFFECTIVE_PROVIDER != "mock"
            else "Visible via /api/memories. It surfaces in chat once a model"
            " provider is configured (mock has no system prompt)."
        )
        yield {"data": f"Remembered (#{m['id']}). {surfaced}"}
    except ValueError as exc:
        yield {"data": f"⚠️ {exc}"}


COMMANDS: list[dict] = [
    {
        "name": "help",
        "args": "",
        "description": "List every command",
        "handler": _help,
    },
    {
        "name": "briefing",
        "args": "",
        "description": "Your My Day summary",
        "handler": _briefing,
    },
    {
        "name": "delta",
        "args": "",
        "description": "What changed since you last looked",
        "handler": _delta,
    },
    {
        "name": "search",
        "args": "<query>",
        "description": "Full-text search across the workspace",
        "handler": _search,
    },
    {
        "name": "plan",
        "args": "<playbook> <engagement name>",
        "description": "Instantiate a playbook as a new engagement",
        "handler": _plan,
    },
    {
        "name": "playbooks",
        "args": "",
        "description": "List available playbooks",
        "handler": _playbooks,
    },
    {
        "name": "remember",
        "args": "<fact>",
        "description": "Save a durable cross-thread memory",
        "handler": _remember,
    },
    {
        "name": "personas",
        "args": "",
        "description": "List the bench of invokable specialist personas",
        "handler": _personas,
    },
    {
        "name": "flocks",
        "args": "",
        "description": "List the flocks — groups of personas you can call at one time",
        "handler": _flocks,
    },
    # handler None: resolved by the chat route (needs the agent layer);
    # listed here so autocomplete and /help stay a single source of truth
    {
        "name": "as",
        "args": "<persona> <message>",
        "description": "Ask a bench persona instead of the Chief of Staff",
        "handler": None,
    },
    # registered even though the route runs it: dispatch() answers any
    # UNKNOWN /word with a did-you-mean line, and get_close_matches would
    # match this one to /flocks — the fan-out would never run
    {
        "name": "flock",
        "args": "<flock> <message>",
        "description": "Ask a flock of personas at one time",
        "handler": None,
    },
]


def catalog() -> list[dict]:
    return [{k: c[k] for k in ("name", "args", "description")} for c in COMMANDS]


def help_text() -> str:
    rows = [
        f"| `/{c['name']}{' ' + c['args'] if c['args'] else ''}` | {c['description']} |"
        for c in COMMANDS
    ]
    if config.EFFECTIVE_PROVIDER == "mock":
        # mock is reached two ways, and only one of them is about a key: an
        # unconfigured deployment, or a configured provider that degraded
        # (bad name, missing key, bad SKEIN_MAX_TOKENS). Claiming "no API
        # key" for the second sends the operator to fix the wrong thing.
        why = (
            "the configured model provider is unavailable — /health names the fault"
            if config.MODEL_PROVIDER_ERROR
            else "no model provider configured"
        )
        head = f"**Mock agent** ({why}) — everything still works, deterministically. Chat capture only creates — to fix or delete a record, use its edit control in the UI:"
        rows.append(
            "| *anything else* | Smart-captured as a task, question, note, decision, or blocker |"
        )
        tail = (
            "Freeform examples: `todo: ship the API`, `why is staging down?`, "
            "`decision: we're using SQLite`, `blocked on vendor contract`.\n\n"
            "Set `SKEIN_MODEL_PROVIDER` in backend/.env for the full conversational "
            f"agent — one of: {', '.join(sorted(p for p in config.PROVIDERS if p != 'mock'))}. "
            "`ollama` needs no key (free with a signed-in daemon). `openai_compatible` "
            "points at any OpenAI-shaped endpoint via `SKEIN_MODEL_BASE_URL`."
        )
    else:
        head = "**Commands** run instantly — no model call, same answer every time:"
        rows.append("| *anything else* | Goes to the Chief of Staff agent |")
        tail = "Commands work in chat, Slack, and the CLI alike."
    return f"{head}\n\n| Command | Effect |\n|---|---|\n" + "\n".join(rows) + f"\n\n{tail}"


async def _unknown(name: str) -> AsyncIterator[Event]:
    close = get_close_matches(name, [c["name"] for c in COMMANDS], n=1, cutoff=0.5)
    hint = f" Did you mean `/{close[0]}`?" if close else ""
    yield {"data": f"`/{name}` is not a command.{hint} Type `/help` to see them all."}


def dispatch(
    text: str,
    user: str,
    viewer: "scope.Viewer | None" = None,
    access: CommandAccess | None = None,
) -> AsyncIterator[Event] | None:
    """Event stream for slash-command text; None means 'not a command —
    give it to the agent'. Command-shaped tokens that match nothing get a
    did-you-mean reply instead of a silent (and costly) trip to the model.

    Every handler takes the viewer, including the six that ignore it. A
    handler that reads scoped work has to be handed one, and giving only the
    two that need it today a different signature makes the next /command an
    unfiltered read by default.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    token = stripped.split(maxsplit=1)[0]
    name = token[1:].lower()
    if not name.isalpha():
        return None
    rest = stripped[len(token) :].strip()
    for c in COMMANDS:
        if c["name"] == name:
            if c["handler"] is None:
                return None  # route-level command (e.g. /as needs the agent)
            return c["handler"](rest, user, viewer or scope.NOBODY, access)
    return _unknown(name)

"""What each agent write is CALLED where a person reads it.

The registry keys (`note_delete`, `task_completion`) are schema words. They
reached the reader in six places: the authority card, its aria-labels and
picker, the approvals header and its checkbox label, the chat receipt, the
team notification, and the gate's refusal. This module is the one place that
names them, so a new entity cannot ship with a raw identifier in front of a
person — tests/test_lexicon.py fails the build instead.

The atom is the CAPABILITY, not the entity: one imperative phrase per
(entity, action) pair. A noun cannot carry a residual. `blocker` registers
create AND update, and its update is `resolve_blocker` — glossed as "a
blocker" the row reads like permission to flag problems, when it also grants
declaring them solved. Every phrase below leads with the verb the handler
actually runs; check services/review.py::_registry before adding one.

Display only. db.log_activity writes the raw action and entity into the
hash-chained ledger and those rows can never be rewritten (CLAUDE.md), so
nothing here may be fed back into a stored string.
"""

# Review records for extension executions use these fixed pseudo-entities.
# They are not part of the legacy authority registry, but they still reach
# the same human approval interface and therefore need stable labels.
REVIEW_ONLY = frozenset(
    {
        ("extension_tool", "create"),
        ("extension_mcp_tool", "create"),
        ("extension_core_tool", "create"),
        ("extension_workflow", "create"),
        ("extension_public_command", "create"),
    }
)


# (entity, action) -> imperative phrase. Verb first, always.
CAPABILITY: dict[tuple[str, str], str] = {
    ("absence", "create"): "add time away for a teammate",
    ("authority", "create"): "change what an agent can do alone",
    ("blocker", "create"): "raise a blocker",
    ("blocker", "update"): "resolve a blocker",
    ("blocker_edit", "update"): "change a blocker or its owner",
    ("decision", "create"): "record a decision",
    ("decision", "update"): "supersede a decision",
    ("delegation", "create"): "hand a task to an agent and name its sponsor",
    ("engagement", "create"): "open an engagement",
    ("engagement", "update"): "change an engagement",
    ("event", "create"): "put an event on the calendar",
    ("event_cancel", "update"): "delete an event from the calendar",
    ("extension_tool", "create"): "run a governed extension tool",
    ("extension_mcp_tool", "create"): "run a governed remote tool",
    ("extension_core_tool", "create"): "run a governed stock tool",
    ("extension_workflow", "create"): "continue a workplace workflow",
    ("extension_public_command", "create"): "run a held integration write",
    ("intake", "create"): "file an intake request",
    ("intake_edit", "update"): "change an intake request",
    ("lesson", "create"): "record a lesson",
    ("memory", "create"): "add a memory",
    ("memory_forget", "update"): "forget a memory for good",
    ("milestone", "create"): "add a milestone",
    ("milestone", "update"): "change a milestone",
    ("note", "create"): "write a note",
    ("note_delete", "update"): "delete a note",
    ("note_edit", "update"): "change a note",
    ("playbook", "create"): "start an engagement from a playbook",
    ("promise", "create"): "make a promise",
    # promise/update and promise_settle/update are the SAME function
    # (promises.update_promise). The phrases match on purpose: two names for
    # one power let a reader forbid one and believe both were closed.
    # test_lexicon pins the duplication so it cannot be half-renamed.
    ("promise", "update"): "settle a promise",
    ("promise_edit", "update"): "change a promise, its due date, or who it is for",
    ("promise_settle", "update"): "settle a promise",
    ("question", "create"): "ask a question",
    ("question", "update"): "answer a question",
    ("question_assign", "update"): "assign a question to someone",
    ("standup", "create"): "post a standup",
    ("task", "create"): "add a task",
    ("task", "update"): "change a task",
    ("task_completion", "update"): "mark a delegated task done",
    ("weekly_plan", "create"): "commit tasks to a week",
}

# Collective noun for the entities that register more than one action. The
# authority matrix is keyed on the entity alone, so its row must enumerate
# the capabilities rather than pick one and hide the rest.
PLURAL: dict[str, str] = {
    "blocker": "blockers",
    "decision": "decisions",
    "engagement": "engagements",
    "milestone": "milestones",
    "promise": "promises",
    "question": "questions",
    "task": "tasks",
}


def phrase(entity: str, action: str) -> str:
    """What one write is called. Falls back to the raw pair rather than
    inventing a name — an unglossed entity must look wrong, not plausible."""
    return CAPABILITY.get((entity, action), f"{action} {entity}")


def entity_label(entity: str) -> str:
    """What an AUTHORITY row is called: the grant covers every action the
    entity registers, so a single-action entity reads as its phrase and a
    multi-action one enumerates its verbs. "blockers (raise, resolve)" is
    the whole point — "a blocker" hid the resolve."""
    actions = sorted(a for (e, a) in CAPABILITY if e == entity)
    if not actions:
        return entity
    if len(actions) == 1:
        return phrase(entity, actions[0])
    verbs = [phrase(entity, a).split()[0] for a in actions]
    return f"{PLURAL.get(entity, entity)} ({', '.join(sorted(verbs))})"

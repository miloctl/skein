# Comment threads on tasks, decisions and blockers

Confirmed 2026-09-28 with the repository owner.

- **Outcome:** People and agents talk about a task, a decision or a blocker on that record. A
  sponsor steers a delegated agent from the task panel, and the agent answers in the thread.
- **User:** The strike team, and agents that hold delegations. Sponsors most.
- **Why now:** A sponsor who sees a wrong assumption in the worklog has three bad routes. The
  worklog is evidence only the delegate and the sponsor write. Chat `/as scout` puts the
  correction in a private solo transcript. A new delegation re-arms the wake but carries no
  message. The worklog, the wake queue and @mentions exist, with no shared place to talk.
- **Success:** Ana writes "@scout target is 17, see decision #41" on task #212. One wake is
  queued for scout. Scout's reply lands in the same thread with no review step, because the
  task is delegated to scout. Raj asks "@dana confirm the freeze window?", Dana answers there,
  and Raj is told. Raj asks on decision #41 why it still holds, and its decider is told. A
  person who cannot read the parent never sees a comment, a count, or a notice about one. On
  `SKEIN_MODEL_PROVIDER=mock`, everything except the model's reply works.
- **Constraint:** One flat thread per parent row. No new UI library, no new setting, no
  read-state table. Comment text never goes into `activity.detail`.
- **Out of scope:** Reactions, nested replies, edit history, search indexing, explicit watch
  or mute, the CLI, MCP tools (open question 1), forge comment sync, extension events. Each
  becomes a row in the cut table of `docs/ROADMAP.md` with the trigger that brings it back.

The 2026-08-21 posture note freezes new portfolio surfaces. The owner approved this feature
with four others on 2026-09-28 (`docs/ROADMAP.md`). Threads serve the agent loop the posture
funds: the sponsor steers a delegation where the work is, and the delegate answers there.

## What exists before this work (2026-09-28)

| Part | Where | Note |
|---|---|---|
| Worklog | `delegation.report_progress`, `list_worklog` | Direct write by the delegate or sponsor. It inherits the task's tier (`scope.inherit`) and bumps `tasks.updated_at` (trust-loop hardening). `list_worklog(actor=)` is the delegation door onto crew tasks. |
| Ungated agent writers | `tests/test_gate_coverage.py::UNGATED_WRITERS` | Four: the delegation trio and `generate_handoff`. Each calls `refuse_when_consultative`. The trio honors `forbidden` on `task` (`delegation._check_not_forbidden`). `tests/test_flock_turns.py` derives its cases from the list. |
| @mentions | `services/mentions.py` | `scan` dedupes on `(entity, entity_id, person)` in `mention_log`. `_reaches` reads the tier through `search._tier_of`. `names_in` is the same parser with no notices. |
| Wake queue | `services/agent_wakeups.py` | `enqueue` coalesces to one row per agent and sets `rerun_requested` on a running one. Callers: a human or verified delegation (`delegation.py:173`) and send-back (`review.py:1626`). `WAKE_TOOLS`, `_WAKE` (`agent_runner.py`) and the wake outcome codes shipped. |
| Agent inbox | `delegation.agent_inbox` | `viewer=None` is the agent's own door (tool and MCP). The REST door strips `message` and gets no `last_progress`. |
| Task panel | `components/task-peek.tsx` | `?task=N`, `openTaskPeek(taskId)`, `PeekLink`. My Day renders notices as `<Link href={a.link}>` (`app/page.tsx:681`, `:987`), which cannot open the panel. |
| Row anchors | `lib/entity-ref.ts::HREF` | A decision lands on `/charter#charter-entry-N`, a blocker on `/dashboard#blocker-N`. Both rows carry `tabIndex={-1}`. |
| References | `refs.readable_refs(..., quoted=False)` (988249be) | Checks tier and policy before it names a row. `ReceiptLine` renders the result, and a task ref opens the panel. |
| Text guards | `services/wording.py` | `INVISIBLE` and `fence()` (hardening). |
| Tombstone | `chat_threads.delete_shared_message` (migration 014) | Author delete, text cleared, row kept. |

## Decisions

### D1. One `comments` table with three parent columns (owner)

```sql
-- NNN_comments.sql, the next free number at merge (044 is the calendar's). No semicolons here.
CREATE TABLE comments (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id bigint REFERENCES tasks(id) ON DELETE CASCADE,
    decision_id bigint REFERENCES decisions(id) ON DELETE CASCADE,
    blocker_id bigint REFERENCES blockers(id) ON DELETE CASCADE,
    created_by text NOT NULL,
    origin text NOT NULL CHECK (origin IN ('human', 'agent', 'agent_verified')),
    body text NOT NULL,
    created_at text NOT NULL,
    edited_at text,
    deleted_at text,
    deleted_by text NOT NULL DEFAULT '',
    visibility text NOT NULL DEFAULT 'workspace'
        CHECK (visibility IN ('private', 'crew', 'workspace')),
    crew_id bigint REFERENCES crews(id),
    -- exactly one parent, so a row can never sit in two threads
    CHECK (num_nonnulls(task_id, decision_id, blocker_id) = 1),
    -- a tombstone holds no text, whatever path wrote it
    CHECK (deleted_at IS NULL OR body = '')
);
CREATE INDEX idx_comments_task ON comments (task_id, id) WHERE task_id IS NOT NULL;
CREATE INDEX idx_comments_decision ON comments (decision_id, id) WHERE decision_id IS NOT NULL;
CREATE INDEX idx_comments_blocker ON comments (blocker_id, id) WHERE blocker_id IS NOT NULL;
```

- `comments`, not `task_comments`: it holds three kinds of thread. The gated entity is
  `comment` (D5). **Not `task_worklog`:** sponsor evidence, which insights read as delegation
  signal (`insights.py:488`, `:1246`). **Not `notes` plus a parent id:** every comment would
  show on the Notes page and in note search. **Not a polymorphic `(entity, entity_id)`:**
  calendar D1's reason, a two-ended scope check on every read and no FK cascade.
- **FK with CASCADE.** Only Your data (private) and the offboarding erase delete a parent, and
  the cascade covers both with no service code. The partial indexes serve the thread read and
  keep that delete from scanning the table. **No setting:** `BODY_LEN = 4000`
  (`work.DESCRIPTION_LEN`) fails tier question 4, with no standing reason to change it.

### D2. A comment takes its parent's tier at write and keeps it

`scope.inherit(parent)` sets `visibility` and `crew_id`. `sharing.py` only widens, and a
child keeps its own tier: a crew-era comment stays crew after the task is shared, because its
author chose that audience. So `comment tier <= parent tier` for the life of the row, and a
readable comment has a readable parent. The thread never shows "N hidden comments": a count
that includes an unreadable row is the leak. The `scope.inherit` docstring names two callers
"and by nothing else", so slice 1 adds `comments.add_comment` to it.

### D3. A comment does not move its parent (owner)

No `updated_at` bump. A worklog note is work, and it bumps. A comment is talk. If "is this
still alive?" made a task look fresh, `stale_wip` and `aging_wip` would stop firing on
stalled work. That sentence goes at the INSERT.

### D4. The delegate posts directly on its own task. Everywhere else, the gate (owner)

- **Direct:** an agent's comment on an open task (`status NOT IN ('done', 'void')`) delegated
  to that agent is a direct write, like `report_progress`. It joins `UNGATED_WRITERS` as
  `"post_comment": "wrote"`. `add_comment(..., as_delegate=True)` calls
  `refuse_when_consultative("comment on a delegated task")`, and refuses an agent that is
  `forbidden` on `task` (the loop's kill switch, `delegation._check_not_forbidden`) or on
  `comment`. After the task lock (D6) it re-checks `delegated_agent == actor` and the status,
  so a reassignment between the tool's plain read and the insert is refused. `as_delegate`
  adds refusals and removes none.
- **Gated:** any other agent comment goes through `tools/_gate.py` at the default level
  (`review` while `SKEIN_AGENT_REVIEW=1`), and an approval applies `add_comment(**payload,
  actor=proposed_by, origin="agent_verified")` (`review.py:979-988`). With no requester the
  agent comments only on its own open delegated task (owner), so this path runs only in chat.
- **Reach, in a chat turn:** the requester must read the parent (`_delegation_reach`, or
  `policy_context.existing_scoped` for a decision or a blocker). A private parent is refused
  before the gate, "An agent cannot comment on a private record. Write the comment yourself.",
  because `assert_editable` refuses the agent on a private row and the approval would fail.
- **Rate:** the direct path runs the gate's own line, `ratelimit.check("write",
  requester_identity() or actor)`, the agent's bucket when unattended. One comment costs one
  token, and its @mentions and thread notices ride on it (owner). It never wakes anyone (D6).
- **SECURITY.md**, "Agent writes": "Four writers skip the gate" becomes five, and the
  delegation loop gains "comment on the task". **Shared chats:** neither tool joins
  `SHARED_CHAT_TOOLS`, because a room agent posting into a task thread speaks to two audiences.

### D5. The gated entity `comment`, and the registries it still needs

The gated path needs a proposal shape, so most of the seven gated-entity registries stay. One
entity serves three parents:

| Registry | Entry | Guard |
|---|---|---|
| `_gate._FAMILY` | **None.** `comment` is not a `<root>_<verb>` name, so its own authority row governs the gated path. The gated path ALSO refuses an agent that is `forbidden` on the comment's parent entity (`task`, `decision` or `blocker`), checked in `add_comment` before the gate files anything, so `forbidden` on a parent stops every comment path, not only the direct one (owner review, 2026-09-28). | `test_authority.py::test_every_registry_mutator_has_a_family_entry`; a new test pins the parent refusal |
| `review._registry()` | `"comment": {"create": comments.add_comment}` | `test_lexicon.py`, `test_review.py` |
| `review._TARGET_TABLE` | `"comment": "comments"`. After approval `result_id` is a comment id, so `"tasks"` would read task #<comment id>. | `test_review.py::test_every_registry_entity_maps_to_a_target_table_or_is_named_untargeted` |
| `review._CREATE_PARENT` | Values widen to a tuple of `(table, key)` pairs, and the first key present wins: `"delegation": (("tasks", "task_id"),)`, `"comment": (("tasks", "task_id"), ("decisions", "decision_id"), ("blockers", "blocker_id"))`. Two readers change, `_target_tier` and `_governing_tiers`. A pending reply takes its parent's tier, so a non-member neither reads nor approves it. | `test_review.py::test_a_create_proposal_is_judged_by_the_tier_of_the_row_it_names` |
| `notifications._SOURCE_ALIASES` | **None.** The gate's `notify-team` source is `comment` itself, typed through `_TABLES`. | none |
| `policy_context._TABLES` | `"comment": ("comments", "")`. `for_change` gets one branch: a `comment` create returns `comments.parent_context(...)` under `Viewer.for_actor(actor)`, the `delegation` branch's shape, so a project rule sees the task's project. | no enumeration test, slice 4 pins it |
| `lexicon.CAPABILITY` | `("comment", "create"): "comment on a task, decision or blocker"` | `test_lexicon.py` |
| `activity.VERBS` | `post_comment`, `edit_comment` (normal), `delete_comment` (loud). Slice 1, for every path. | `test_activity_feed.py` |

Also: `_gate._creates_in_a_crew` gains `("decision_id", "decision")` and `("blocker_id",
"blocker")`. It already refuses a gated create under a crew task for any named requester, not
only in a shared chat, and without the two keys a gated comment would land on a crew decision
but not on a crew task. The rule: an agent writes into a crew only as a task's delegate.
`test_gate_coverage.py` gets `ARGS["post_comment"] = {"task_id": 2, "body": "coverage probe
comment"}` (the trio's task, so the direct path), `expected_writers` and the `UNGATED_WRITERS`
row. That harness runs the direct path only, so slice 4 tests the gated path on its own.

### D6. A human @mention of the delegate wakes it, on task threads only (owner)

After the insert, `add_comment` queues one wake when all of these hold:

- The parent is a task (decisions and blockers have no delegate), and `origin in ("human",
  "agent_verified")`, the rule at `delegation.py:173`: an agent-origin write never starts an
  unattended chain.
- The task has a `delegated_agent` and is not `done` or `void`. `agent_inbox` drops those, so
  a wake would only run the agent's other tasks.
- `task.delegated_agent in mentions.names_in(body, actor)[1]`: the parser `scan` uses, not
  `scan`'s result. `_reaches` never reaches an agent on a crew task (agents hold no crews), and
  the wake must not inherit that gap.

Then `agent_wakeups.enqueue(agent, task_id, requested_by=actor)` runs in the same transaction.
The parent's `SELECT ... FOR UPDATE` is the first lock `add_comment` takes: the tier, the
delegate and the status decide the insert and the wake, and without the lock a concurrent
reassignment wakes an agent that no longer holds the task (CLAUDE.md, "A read takes no
lock"). Any human who can read the task may wake it (owner). Five people who write @scout cost
at most one follow-up turn, and the daily wake cap, the token ceilings, the pause switch and
the gate still apply. An edit never wakes (D8). An @agent that is not the delegate gets the
current @mentions notice and no wake, because the runner works delegated tasks only
(`agent_runner._due`). The POST answers `woke: ""` and `notified: ["scout"]`, so the status
line stays honest. The panel's wake line needs no change: `agent_wakeups.status(agent,
task_id=)` projects the row the comment wrote.

### D7. Thread notices come from rows every reader already sees (owner)

No subscription table. The recipients are the parent's named parties plus everyone who wrote
in the thread:

    task: {assignee, sponsor}   decision: {created_by, decided_by}   blocker: {owner, created_by}
      ∪ {created_by of earlier comments, tombstones included}
      − {actor} − {names scan just notified} − agents − inactive users
      − anyone scope.can_read(tier, crew_id, Viewer.for_actor(p)) refuses

Each gets `notify(p, lambda t: f"{actor} commented on task #{t['id']}.", tier="digest",
link=..., source_entity=<parent>, source_id=<parent id>)`. The link is `?task={tid}#comment-
{cid}`, or the row anchor from `refHref`'s map for a decision or a blocker. A recipient who
holds an unread personal notice with this thread's link (`?task={tid}#comment-%`, or the exact
row anchor) is skipped: that notice already points into the thread. The parent lock
serializes the check. `ponytail:` a dismissal that races it can hide one notice, and the
upgrade is a per-(person, parent) row, the deferred watch row. An edit sends none. This is
Fizzy's `CommentEventNotifier` and Mattermost's follow-on-first-post, without a watcher list.

### D8. Authors edit their own comment, marked, with no history (owner)

`edit_comment` takes the comment row `FOR UPDATE` first. An unreadable or absent row is
`scope.missing`. Anyone but the author gets `PermissionError`, "Only the author can edit this
comment." A tombstone is a 400: "This comment is deleted. Post a new comment." The body passes
the post checks. It sets `body` and `edited_at`, logs `edit_comment` with ids, and re-runs
`mentions.scan`, so a newly named person is told once and nobody twice. No wake, no thread
notice. The UI shows "edited" beside the time. No agent edits a comment: it posts a correction.

The risk the owner accepted: a steering comment can change after the agent acted on it.
`read_comments` returns `edited_at`, and `unanswered_for` compares `COALESCE(edited_at,
created_at)`, so the agent's next turn lists the edited comment again. The how-to says to post
a new comment with @agent when the change must act now. A session that read the old text
keeps it.

### D9. Delete leaves a tombstone that names the deleter (owner)

`delete_comment` takes the comment row `FOR UPDATE` first. The author may delete. A human who
can read the comment may delete an agent's comment (`origin` not `human`): the agent is the
mechanism, not a speaker with a stake, and with direct replies this is how a person removes a
wrong one. Anyone else gets `PermissionError`, "Only the author can delete this comment." The
UPDATE sets `body = ''`, `deleted_at` and `deleted_by`, and a repeat writes nothing. The
ledger gets `delete_comment` with ids. `mention_log` rows (no text) and queued wakes stay, and
sent notices quote no body. A hard delete would leave replies that answer nothing.

### D10. Two mount points outside the task panel (owner)

A decision thread mounts on its Team → Charter row, and a blocker thread on its Work → Browse
→ Blockers row. `refHref` sends a `decision #41` or `blocker #4` reference to exactly those
rows, and the notice links land there, so every path to a thread arrives where it is. Each row
gets a "Comments" button (`aria-expanded`) that mounts the thread, closed at first: rows that
each fetch a thread on load are N reads nobody asked for. The task panel's "Blocked by" list,
My Day and Browse → Decisions get no mount: each leads to one of the two rows already.

### D11. Text stays out of the ledger and out of prompt strings

`db.log_activity(actor, "post_comment", f"{parent} #{pid} comment #{cid}")` carries ids at
every tier. Not `scope.detail`: it adds the body at the workspace tier, the ledger can never
be rewritten, and an author delete must remove the text. `lib/task-ref.ts` does not learn the
comment verbs, because the detail can name a decision, and an action-keyed parser must never
open the task panel on a decision id (`activity-task-refs.test.tsx`). Bodies reach a model as
tool-result JSON only. A change that splices them into `_WAKE` must use `wording.fence()`.

### D12. References in a body link through `readable_refs`

`GET` answers each comment with `refs`: one `refs.readable_refs(joined bodies, viewer,
resource_filter=policy.permits, quoted=False)` per page, then each comment keeps the refs its
own body names (`refs.refs(body, quoted=False)`), in order. A call per comment is 200 policy
walks. `quoted=False`, because in prose an apostrophe is not a quote ("Mira's blocker #4").
The panel renders the body through `ReceiptLine`, never as markup.

## The surfaces

**Service, `backend/app/services/comments.py`.** The parent keywords match the gate payload,
so an approval splats it with no mapping:

```python
BODY_LEN = 4000
def add_comment(body: str, *, task_id: int = 0, decision_id: int = 0, blocker_id: int = 0,
                actor: str, origin: str = "human", as_delegate: bool = False) -> dict
    # {"id", "parent", "parent_id", "notified": [names], "woke": agent_or_""}
def list_comments(viewer: scope.Viewer = scope.NOBODY, *, task_id: int = 0,
                  decision_id: int = 0, blocker_id: int = 0, actor: str = "",
                  limit: int = 200) -> list[dict]
def get_comment(comment_id: int, viewer: scope.Viewer) -> dict
def edit_comment(comment_id: int, body: str, *, actor: str) -> dict
def delete_comment(comment_id: int, *, actor: str) -> dict
def parent_context(parent: str, parent_id: int, viewer: scope.Viewer) -> dict[str, str]
def unanswered_for(agent: str, task_ids: list[int], limit: int = 20) -> list[dict]
```

`add_comment`, in one transaction: exactly one parent id. The body stripped, and refused when
empty, over `BODY_LEN`, or matching `wording.INVISIBLE` (agents read comments as context,
`review._refuse_invisible`). The parent `FOR UPDATE`, then `scope.assert_editable` as the read
check (it lets a machine actor reach a crew row, which the approval needs). The `as_delegate`
re-check, `scope.inherit`, the INSERT, the ledger (D11), `mentions.scan("comment", cid, body,
..., parent=(kind, pid))`, the wake (D6), thread notices (D7).

`list_comments` reads the parent through its own `visible_filter` first (an unreadable parent
is a 404), then `visible_filter(viewer, "comments")`, oldest first, the limit clamped to
1..200. A row is `{id, created_by, origin, body, created_at, edited_at, deleted_at,
deleted_by, can_edit, can_delete}`. `actor=` is the `list_worklog` delegation door: for a
task's `delegated_agent` the tier filter is skipped on that task, because an agent holds no
crews. A private task cannot carry a delegate.

`parent_context` is the one resolver for REST and the gate: a task through `work.get_task` and
`work.task_read_policy_context` (the `post_delegate` shape), a blocker through
`blockers.existing_policy_context`, a decision through `policy_context.existing_scoped`.

`unanswered_for` returns others' comments on the agent's open delegated tasks that are newer,
by `COALESCE(edited_at, created_at)`, than the agent's own last comment or worklog note on
that task. No tombstones, oldest first, each body cut to 1000 characters. The watermark is the
agent's own writes, not a read record.

**Mentions.** `mentions.scan` gets one keyword, `parent: tuple[str, int] | None`. The dedupe
key stays `("comment", cid)`: a key on the parent would suppress Dana's second mention in the
thread. The notice's source is the parent, typed, so its project and tier drive the policy
filter: "{actor} mentioned you in a comment on task #212." `_tier_of` must find the comment's
own tier, so `search._ENTITY_TABLE` gets `"comment": "comments"`. **Without that entry
`_tier_of` returns None, `_reaches` returns True for everybody, and a crew comment notifies a
non-member.** Slice 1's first test pins it.

**REST, `backend/app/routes/api.py`.** All eight are CurrentUser plus ViewerDep and go into
`extensions/fastapi.py::_HANDLER_POLICY`. The policy action is the derived
`skein.rest.<method>.<path literals>`.

| Route | Cap |
|---|---|
| `GET /api/{tasks,decisions,blockers}/{id}/comments` | a read |
| `POST /api/{tasks,decisions,blockers}/{id}/comments` `{body}` | `ratelimit.check("write", user)` |
| `PATCH /api/comments/{comment_id}` `{body}` | `ratelimit.check("write", user)` |
| `DELETE /api/comments/{comment_id}` | `ratelimit.check("delete", user)` |

A write handler holds the parent first (`policy_context.hold_resource`), then runs
`comments.parent_context`, `enforce_decision(decide(...))` and the service in one transaction.
PATCH and DELETE find the parent through `get_comment`. `CommentIn` (`extra="forbid"`, `body`
capped at `BODY_LEN`) serves POST and PATCH, so `test_patch_cap_parity.py` holds. Bad text and
an edit of a tombstone are 400, unreadable is 404 with an absent id's text, a non-author edit
or delete is 403, and a rate cap is 429 with Retry-After.

**Agent tools, `backend/app/tools/portfolio.py`,** beside the trio:

- `read_comments(task_id=0, decision_id=0, blocker_id=0, limit=20)`: a read, so no receipt.
  The requester's read first (`_delegation_reach`, or `existing_scoped` for a decision or a
  blocker), then `list_comments(..., actor=agent_identity())`.
- `post_comment(body, task_id=0, decision_id=0, blocker_id=0)`: the D4 reach rules, then the
  direct path with a `wrote` receipt, or `gated_write("comment", "create", payload, lambda:
  comments.add_comment(**payload, actor=agent_identity(), origin="agent"))`.

Both join `ALL_TOOLS` and `WAKE_TOOLS`, and `post_comment` joins `CORE_WRITE_TOOLS`. `_WAKE`
gets one model-facing paragraph: "If my_agent_inbox lists new_comments, call read_comments on
that task before other work. A comment from your sponsor can change the work. Follow it, then
answer once with post_comment. Do not answer a comment that asks you nothing." `agent_inbox`
adds `new_comments` from `unanswered_for`, over the tasks that passed `task_filter`, only when
`viewer is None` (the `last_progress` rule). A comment receipt is `wrote`, so
`agent_runner._outcome` does not call a comment-only turn `nothing_filed`.

**UI, `frontend/components/comment-thread.tsx`,** props `{parent, id, delegatedAgent,
status}`:

- `<ol>`, oldest first. Each item is `<li id="comment-{id}" tabIndex={-1}>` with the author,
  the time, an "agent" tag when `origin` is not `human`, and "edited" when `edited_at` is set.
  The body is `ReceiptLine` over `{message: body, refs}` with `whitespace-pre-wrap`.
- In the task panel, after Worklog, the thread calls `useHashTarget(comments)`, so
  `#comment-9` from a cold load, a notice or a pasted link lands focus on that item. No Browse
  register claims a `comment-` anchor (`registerForAnchor`), so the page behind stays put.
- Composer: a native `<textarea maxLength={4000}>` labelled "Add a comment", and "Post
  comment". On an open delegated task a hint reads "Write @{agent} to ask the agent. Skein
  starts one agent turn." After a post the field clears and keeps focus, and
  `lib/status.ts::reportStatus(..., "confirmation")` says "Comment posted.", "Comment posted.
  {agent} gets one agent turn." or "Comment posted. Notified: dana, raj."
- Edit, on the author's own rows: the same textarea with "Save" and "Cancel", then focus on the
  item and "Comment saved." Delete has two inline steps, "Delete this comment? Skein removes
  the text." [Delete comment] [Keep], with no warmth, then focus on the tombstone
  ("{deleted_by} deleted this comment.") and "Comment deleted."
- Empty state (voice allowed): "No comments yet. Nobody has pulled on this line." Errors use
  the shared backend-unreachable string, or "Skein could not post the comment. {detail}"
- `openTaskPeek(taskId, anchor = "")` sets the hash, pushes the state, dispatches
  `skein-peek`, then `skein-hash` `{anchor}`. The two notice lists in `app/page.tsx` render a
  `?task=N` link through `PeekLink`: the panel listens only for `popstate` and `skein-peek`.

**Visibility, erasure, Your data, export.**

- `scope.CLASSIFIED["comments"] = "created_by"`, `NOUN["comments"] = "comment"`
  (`test_scope.py`). `users._ATTRIBUTION["comments"] = ("created_by", "deleted_by")`.
- `erasure._ORDER` gets `comments` first (`test_erasure.py`). A private comment exists only on
  its author's own private parent, so it goes with the person. Crew and workspace comments
  stay with the name.
- `my_data.LABELS["comments"] = "body"` (`test_my_data.py`), and no `_POLICY_ENTITY` row, as
  for a worklog entry. A shared comment is deleted in the thread, not there.
- `admin.TABLES` gets `comments` after `blockers` (`test_admin_export.py`), with no parent
  redaction: an exported comment has an exported parent (D2).
- `retention.KEPT` gets `comments` in the user-deleted group beside `task_worklog`. Not
  `CASCADED`: that map names one parent per child, and this table has three.
- `test_visibility_authz.py`: `_mutations` gets `add_comment`, `edit_comment` and
  `delete_comment`. `_UNFILTERED_READS` gets `unanswered_for` (the `last_progress` reason) and
  the thread-notice author read in `add_comment` (each author passes `scope.can_read` first).
- **Anti-surveillance:** no per-person comment counts, no "most active" row, no insights rule
  over comments, no read state.

## Slices

One commit per slice, each passing `./scripts/lint.sh` and both full suites, with a CHANGELOG
`## Unreleased` entry. Every new test fails against the code before its slice (CLAUDE.md).

### Slice 1 - The thread for people (backend)

The migration, the service (no wake, no thread notices), the eight routes,
`_HANDLER_POLICY`, `mentions.scan(parent=)`, `search._ENTITY_TABLE`, the three VERBS, the
`scope.inherit` docstring, and the registries under "Visibility, erasure, Your data, export".
Failing first, in `backend/tests/test_comments.py`:

- A crew comment that names non-member @bo notifies nobody (without the `_ENTITY_TABLE` row,
  bo is notified). Dana's second mention in the same thread notifies her again.
- A non-reader gets byte-identical 404 text on GET, POST, PATCH and DELETE, for a crew parent
  and for an absent id, for each parent kind.
- A workspace comment's activity detail holds no word of the body.
- A non-author human edit and delete are 403. An author delete leaves `body = ''`. A human
  deletes an agent-origin comment, and the tombstone names them. A raw UPDATE that sets
  `deleted_at` while text remains, and a raw INSERT with two parents, violate a CHECK.
- An edit sets `edited_at`, notifies a newly named person once, and re-notifies nobody.
  Invisible format characters are refused on POST and on PATCH.
- `comment tier <= parent tier` holds after `share_with_team` on the parent. Deleting a private
  task through Your data removes its comments.

### Slice 2 - The task panel, the deep link, the knot

`comment-thread.tsx`, the mount in `task-peek.tsx`, `openTaskPeek(taskId, anchor)`, and
`PeekLink` for `?task=` notice rows. The first user-facing slice, so the card ships here:

```yaml
  - id: threads
    feature: Comment threads
    knot: Becket Hitch
    set: hitches
    pitch: Talk about the work where the work lives, and steer an agent without leaving it.
    how: "Open a task and write under Comments. Write @name to notify a person. On a delegated task, write @ and the agent name to start one agent turn. Select Edit to change your own comment. Delete removes the text of your comment."
    link: /dashboard
    since: <ship date>
```

Predicate: `"threads": lambda u: _has("SELECT 1 FROM comments WHERE created_by = ?", (u,))`.
The card count in `tests/test_fieldguide.py` goes from 69 to 70, and FEATURES.md to "70
cards" and "69 tieable" (`test_inventory_claims.py`). If slice 3 slips, cut the wake sentence
from `how:`. Failing first, in `frontend/__tests__/comment-thread.test.tsx`:

`#comment-9` in the URL lands focus on that item after the fetch settles. A tombstone renders
the deleter and no text, and an edited row says "edited". `decision #41` in a body is a link
only when `refs` carries it. A `?task=12#comment-9` notice row renders `PeekLink`, not
next/link.

### Slice 3 - The sponsor steers

The wake rule (D6), `woke` in the POST answer, the composer hint, the status line. Failing
first: a human comment with @delegate on an open task queues exactly one `agent_wakeups` row
with `trigger_task_id` = the task, and two comments leave one pending row. A done task, an
agent-origin @delegate, and an edit that adds @delegate queue nothing. An @agent that is not
the delegate gets a notice and no wake. A crew task's delegate is woken, though `scan` cannot
reach it.

### Slice 4 - The agent answers

The two tools, `as_delegate`, the D5 registries, `_creates_in_a_crew`, the gate-coverage and
flock-turn entries, `WAKE_TOOLS`, `_WAKE`, `agent_inbox.new_comments`, SECURITY.md "Agent
writes", and the tool counts (63 to 65 in README.md and FEATURES.md). Failing first:

- The delegate's `post_comment` on its open task writes a row with `origin=agent`, files no
  proposal, and records `wrote`. `forbidden` on `task` or on `comment`, a consultative turn
  (`test_flock_turns.py` gains the case), and a second comment with the `write` limit at 1
  each refuse it.
- In a chat turn, a comment on another workspace task files a `comment` proposal, and approval
  posts it with `origin=agent_verified`. A project rule that denies the task's project refuses
  it at the gate. A crew task, a crew decision and a private parent are refused before a
  proposal exists. With no requester, a task the agent is not delegated is refused.
- `new_comments` lists Ana's comment until the agent comments or writes a worklog note, and
  lists it again after Ana edits it. The REST inbox carries no body. Every tool that `_WAKE`
  names is in `WAKE_TOOLS`.

### Slice 5 - Thread notices (owner)

D7. Failing first: Dana answers Raj without @raj, and Raj gets one notice. A second comment
while it is unread adds none. A mentioned person gets one notice, not two. A crew non-member
and agents get none. A comment on a decision tells its decider.

### Slice 6 - Decision and blocker threads in the UI (owner)

The D10 mounts in `app/charter/page.tsx` and the Blockers section of `app/dashboard/page.tsx`.
The `threads` card's `how:` gains "On Team → Charter, or on a row in Work → Browse → Blockers,
select Comments." No new card. Failing first, in vitest: the charter row's Comments button
mounts a thread that reads `/api/decisions/41/comments`, the blocker row's reads
`/api/blockers/4/comments`, and neither fetches before the button is selected.

## Docs in the ship commits

- `docs/FEATURES.md`: a new **Comment threads** row (slice 1, extended through slice 6).
  Edits to **Task side peek**, **@mentions**, **Agent inbox**, **Durable delegation wake
  queue**, **Delegation work loop**, **Offboarding erase**, **Your data**, and the counts in
  **Agent write path** and **Field guide**. The MCP count does not change.
- `docs/AGENT-WAKEUPS.md`: "Scope" gains the comment trigger, and "Enqueue contract" the
  rule (parent lock first, no wake on an edit or an agent-origin comment). The non-goal "Put
  the model reply in a human Chat thread" stays true.
- `docs/VISIBILITY.md`, "Parent rows copy text into child rows": `comments.add_comment` into
  `comments`, which keeps its tier when the parent widens. `SECURITY.md`, `README.md`: slice 4.
- `docs/ROADMAP.md`, in slice 6: delete the "Discussion threads on tasks" bullet (it still
  says agent replies go through review) and drop `task-threads` from the "Draft designs"
  sentence. In "Watch subscriptions on tasks", "Task threads (below) cover" becomes "Comment
  threads cover". Cut rows (item: trigger): nested replies: one thread carries two
  interleaved conversations. Edit history: somebody needs a comment's text before an edit.
  Comment search: somebody searches for a thing said in a thread. Mute: somebody asks to stop
  thread notices. Extension events: a workplace module asks. `skein comment` CLI: a terminal
  user without MCP asks. Pagination past 200: a thread reaches 200. Reactions: refused, no
  workflow asks for them.

## Interactions

The calendar took migration 044 and two knots, and its `readable_refs(quoted=False)` is reused
(D12). Each routine-spawned task has its own thread. A board card can later show a comment
count through `visible_filter`, never a raw count. Thread text never syncs to GitLab, and
forge comment import stays deferred (`origin` has no external-author value).

## Residual risks accepted

- **An unreviewed agent reply.** Any reader of a task can address its delegate, whose answer
  posts with no review (D4), so a prompt-injected delegate can post a wrong comment and
  @mention people. Bounds: only the delegate on its open task, `forbidden`, the `write`
  bucket, `WAKE_TOOLS`, the invisible-text refusal, and delete by any human reader (D9).
- **Deleted or edited text persists** in the woken agent's session (`sessions`, owner-scoped
  model state) and in a gated reply that quotes it, as a note deleted after an agent read it
  does. The shared-chat delete clears room sessions, and a task has none.
- **A steering comment can change after the agent acted on it** (D8). **A dismissal that races
  the unread check can hide one thread notice** (D7's `ponytail:`).
- **A missed registry leaks a mention.** Slice 1's first test pins it. About twenty lists
  change, and all but `policy_context._TABLES` have an enumeration test.
- **Merge order.** The migration number, the knot count and the tool count move with the other
  branches. Rebase the counts last.

## Open questions for the owner

1. **MCP parity.** Deferred by the owner. Trigger: an MCP-connected agent holds a delegation,
   and its sponsor asks for the reply in the thread. Until then the MCP `my_inbox` passes
   `viewer=None`, so it lists `new_comments` from slice 4 on, and that agent answers with the
   MCP `report_progress`, whose note clears the watermark. The build is about 20 lines:
   `read_comments` (READ), `post_comment` (WRITE, the same rules), and the FEATURES MCP count.

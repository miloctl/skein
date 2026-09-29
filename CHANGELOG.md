# Changelog

Step 1 of the upgrade procedure in `docs/EXTENSIONS.md` is to read the release
notes and deprecations. This file is those notes.

Each release records three kinds of change. **Contracts** covers anything a
private extension package imports or declares: `app.extensions`, `app.public`,
`@miloctl/skein-extension-api`, the content schemas, and the compatibility fields. A
package author reads this section and nothing else to plan an upgrade.
**Behavior** covers what the running system does differently. **Operations**
covers what whoever runs the server must do differently.

A contract entry names the version a package must declare to use it. Additive
contracts keep extension API 1.0: a package that does not use the new contract
keeps its existing `minimum_core` and needs no change.

## Unreleased

### Contracts

- Routines: `GET /api/routines` answers `{zone, routines}`, each row with `next_local`, `last_local`, `open_task_id`, `can_edit` and `can_delete`. `POST /api/routines` (a proved identity, 201) takes a title, `weekdays` ("1,4", Monday is 1), `at_time` ("07:00"), `every_weeks` (1 to 4), `starts_on`, `due_days` (0 to 27), an assignee or an agent, `acceptance_criteria`, and a tier that is fixed from then on. The workplace action `skein.rest.post.routines` decides a create. `PATCH /api/routines/{id}`, `POST /api/routines/{id}/resume` and `DELETE /api/routines/{id}` take a proved identity and the owner, and a delete also takes any reader once the owner is deactivated. `POST /api/routines/{id}/pause` takes any reader. A task made by a routine carries `routine_id`. New activity actions `create_routine`, `update_routine`, `pause_routine`, `resume_routine`, `delete_routine`, and the quiet `fire_routine` and `skip_routine` by `scheduler`.
- Trust scores count a routine's acceptances apart: `proposed`, `approved`, `rejected` and `approval_rate` are hand verdicts, and `routine_approved` and `routine_rejected` are new. Routine acceptances build no streak, so `recent_streak`, `rejection_streak` and `last_verified_verdict` read hand verdicts only.
- `GET /api/tasks/board` answers the board: `{scope, limit, done_days, today, open, done}`. `engagement_id` or `milestone_id` narrows it to one parent, and `mine=true` to the caller's tasks. A card carries the Browse fields plus `committed_week`, `delegated_agent`, the waiting-on link, `quiet_days` and its readable open `blockers` as `{id, title}`. Done holds the last 7 days. A hidden or denied parent answers 404 as an absent one does. Rows and blockers pass the workplace action `skein.rest.get.tasks`.
- `PATCH /api/tasks/{id}` takes `expected_status`. If the task's status differs, the write is refused with 409 and the message names the current status. `POST /api/blockers/{id}/resolve` answers `task_unblocked`, true when the resolve returned its task to In progress.
- New agent tools `read_comments` and `post_comment`, for 65 tools. A delegate's comment on its own open task posts at once, the fifth writer that skips the gate. In a chat turn, any other agent comment goes through the review gate as entity `comment`. With no person asking, it is refused. The agent inbox adds `new_comments` on the agent's own doors, and the REST inbox carries no comment text.
- Comment threads: `GET` and `POST /api/tasks/{id}/comments`, `/api/decisions/{id}/comments` and `/api/blockers/{id}/comments` (`{body}`, at most 4000 characters), `PATCH /api/comments/{id}` (`{body}`, the author only) and `DELETE /api/comments/{id}` (a tombstone). A POST answers `{id, parent, parent_id, notified, woke}`. A thread row carries `can_edit`, `can_delete`, `edited_at`, `deleted_at`, `deleted_by` and the readable `refs` in its body. New activity actions `post_comment`, `edit_comment` and `delete_comment`, which carry ids only.
- `POST /api/webhooks/gitlab` takes GitLab project webhooks. The `X-Gitlab-Token` header must equal `SKEIN_GITLAB_WEBHOOK_TOKEN`, checked before the body is read. A push to a `task/42-…` branch starts task 42, and an opened or merged merge request moves it as a Gitea pull request does. A system hook is a 400, and any event other than push, merge request and pipeline is acknowledged and ignored. The delivery ID is `Idempotency-Key`, else `X-Gitlab-Event-UUID`, and receipts keep a separate namespace per forge, so a GitLab resend writes nothing twice.
- `POST /api/documents` creates a document (`title`, `content`, `visibility`, `crew_id`) and answers `{id, revision: 1, title}`. With no visibility it is private for a signed-in caller and workspace for a trusted-header name. The Your data export adds `revisions` to each private document.
- `GET /api/review/{id}/diff` for a `document_edit` answers `unified`, `base_revision` and `head_revision`.
- The `edit_document` agent tool stamps `base_revision` on its payload, and `read_artifact` returns `revision` for a document. A `document` or `document_edit` payload cannot carry `change_id`: approval records it.
- `PUT /api/documents/{id}` saves a document's body as the next revision (`content`, `base_revision`). It answers `{id, revision, unchanged}`, 409 when the base is stale, and 400 for an artifact that is not a document. `GET /api/documents/{id}/revisions` lists the newest 100 revisions without bodies. `GET /api/documents/{id}/revisions/{n}` answers one revision with its `markdown` and a unified `diff` against revision n−1. `POST /api/documents/{id}/revisions/{n}/restore` copies revision n as the new head. `GET /api/artifacts/{id}` adds `revision` for a document. New activity action `restore_document`.

### Behavior

- Work → Board shows the open work that you can read in four columns: To do, In progress, Blocked, and what finished in the last 7 days. A card shows its assignee, priority, due date, week, open blockers, what it waits on, its delegate, and "Not moved for N days" when In progress work passes the stale-work cutoff that Health uses. Select a task to open the task panel. Only my tasks narrows the board to your own. The engagement brief links to the board for the engagement and for each milestone. The board reloads when the task panel closes, after a capture or a verdict, and when you return to the tab. New field-guide card: Board (Ossel Hitch).
- A task moves on the board by drag with a mouse, or with Move and then a column, which works with a keyboard and on a touch screen. Blocked asks what blocks it and raises a blocker. A blocked task lists its blockers with Resolve, and it moves to In progress when the last one is resolved. If the task's status changed after your board loaded, the move is refused, the message names the current status, and the board reloads. A task delegated to an agent has no Move.
- Two people who resolve the last two blockers of a task at the same time now release the task to In progress. Before, each resolve saw the other blocker still open, and the task stayed Blocked with no open blocker.
- Decisions on Team → Charter and blockers on Work → Browse → Blockers have a Comments button that opens their thread.
- A reply in a thread tells the parent's named parties and everyone who wrote there before, once while their notice is unread. A task tells its assignee and sponsor, a decision its author and decider, and a blocker its owner and author. Agents, people who cannot read the comment, and the author are not told.
- A delegated agent answers its sponsor in the task's thread. Its next turn lists comments it has not answered, and the wake prompt tells it to read the thread and answer once. Any person who can read the comment can delete an agent's comment.
- On a delegated task, a comment that names the agent with @ queues one agent turn. The composer says so, and the status line names the agent. A finished task, a comment an agent wrote and an edit start nothing.
- The task panel has Comments: read the thread, post with @name to notify a person, and edit or delete your own comment. A notice about a comment opens the panel on that comment. My Day's notices that link to a task now open the task panel. Before, they changed the address and opened nothing. New field-guide card: Comment threads (Becket Hitch).
- `skein context --engagement 7 --write AGENTS.md` records `engagement=7` in the start marker, and a later `--write` with no flag refreshes that engagement's pack. `--engagement 0` writes the team pack. Every `--write` asks for the workspace tier, the team pack included. The engagement pack flattens the outcome and kill criteria to one line, so a newline cannot write a heading. New field-guide card: Context pack in a repository (Ashley Bend).
- `skein context --write AGENTS.md` writes the pack between two marker lines and keeps every other byte of the file, CRLF line endings included. It overwrote the whole file. A file with text and no block is refused unless `--force`, which adds the block at the end. Broken markers are refused, `--force` or not, and markers inside a fenced code example are the person's text. The write goes through a temporary file, and a symlinked name stays a symlink. Upgrade: a file written by an earlier `skein context --write` has no markers and is refused. Delete the old pack text, then run the command again.
- Settings → Connections shows the GitLab webhook URL beside the Gitea one, with the events to select and the token to set, and the context-pack hint names `skein context --engagement <id> --write AGENTS.md`. `docs/SETUP.md` has a Connect GitLab section. The forge field-guide card names both tokens.
- A failed GitLab pipeline on the project's default branch, or on `main` or `master`, files one high-impact team blocker ("CI red on team/app@main") as `forge`, and the next passed one resolves it. A pipeline stopped at a blocking manual job counts as passed. Running, cancelled, skipped, tag, merge request and child pipelines do nothing, and so does a repository path or branch name with whitespace, control or format characters. The workplace rule on `skein.integration.ci` decides both the GitLab path and `POST /api/webhooks/ci`. On the GitLab path a refusal answers 200 ignored with no receipt, because GitLab disables a hook after repeated failed deliveries, and a resend after the rule changes applies.
- Two red CI runs for one repository and branch that arrive together file one blocker. Each run filed its own. A green run that races a person resolving the blocker no longer answers 400.
- A `Closes-Task: #42` line in a pull request or merge request description closes task 42 when it merges, on Gitea and GitLab. `skein pr-body` writes that line, and the forge read nothing from it, so a merge on a branch without the task number moved nothing. The line wins over a closing phrase elsewhere in the title or description. `Refs-Task` still never closes a task.
- Work → Reports has New document: write a document and choose who can see it, starting at only you for a signed-in person. A private document is never indexed or read by an agent, its revisions travel in your Your data download, and the offboarding erase takes them with it. The Your data card now lists these under Private documents and reports. Opening your own document no longer ties the ask-an-agent card.
- Search finds a document by its current text, and a document hit opens it on Reports. Words that only an earlier revision had stop matching.
- Approvals shows an agent's document edit as the lines it changes against the revision it was filed on, where the card showed a dash beside the new text. It warns when the document changed after the proposal.
- An agent's document edit is pinned to the revision it was filed against. If a person saved in the meantime, approval refuses the edit and settles it as rejected without counting against the agent. A quote that is not in the document, an edit that changes nothing and an edit that leaves the document empty are refused when the agent files the edit, where before the proposal failed at approval and returned to the queue.
- Work → Reports offers Edit and History on a document. Edit saves the Markdown as the next revision, with a preview. If somebody saved first, the save is refused and your text stays. History names who made each revision, shows what it changed, and restores an earlier one as a new revision. The report kind reads Document instead of Agent document. New field-guide card: Write, edit and restore documents (Racking Bend).
- Every write to a document is a numbered revision, so an agent's edit keeps the text it replaced. A document written before this version seeds revision 1 from its file on its next write. A direct agent write to a document is recorded with origin `agent`.
- Approvals takes a proposal's card out of the queue when an approval settles it as rejected (a stale document edit, a task that is already done). The card stayed until a reload, and a second approval answered with an error.
- An administrator can no longer merge an account that wrote records for a crew, unless its person requests the merge. The merge moved authorship, and the author of a crew record keeps reading it after leaving the crew, so the target account read the crew's records.
- A blocker raised against a task no longer reopens a task that was finished at the same moment. The finished task keeps its Done status and its completion time.
- The task panel raises a blocker against the task: select blocked…, write what blocks it, pick the impact, and name who can clear it. The blocker sets the task to Blocked and starts its escalation clock. The status list no longer offers a bare Blocked, which left a task blocked with no reason on record. New field-guide card: Block a task with its reason (Water Knot).

### Operations

- Migration 047 adds `routines` and `tasks.routine_id`. The routines table is in both database dumps and the portable export, the offboarding erase takes a private one, Your data lists it, and a rename moves its owner, assignee, agent and paused-by names. A new job `routines` runs every 5 minutes and at startup: a restart fires a missed time once, late. Deactivating a person pauses their routines, and deactivating an agent pauses the routines that delegate to it.
- Migration 046 adds `comments`: one row per comment, with exactly one parent (task, decision or blocker) and that parent's tier. It is in both database dumps and the portable export, the offboarding erase and Your data take a private one, and a rename moves `created_by` and `deleted_by`.
- New optional Secret key `SKEIN_GITLAB_WEBHOOK_TOKEN`. Empty keeps the GitLab webhook closed (503). CLAUDE.md and `docs/intent/work-durability.md` record GitLab inbound webhooks as approved, and outbound GitLab calls as an open decision.
- Migration 045 adds `document_revisions`: one row per revision of a document, with its body. It is in both database dumps and left out of the portable export.

## 0.6.11 — 2026-09-28

### Contracts

- `GET /api/context-pack?engagement=<id>` takes `tier=workspace`, which builds the engagement pack at the workspace tier whatever the caller can read. Any other value is a 422.
- `POST /api/review/{id}/reject` takes `send_back` (default `false`). On a `task_completion` it queues one more agent turn for the delegate in the verdict's transaction and returns `sent_back: true`. It needs a note, and it is refused unless the task is still delegated to the agent that submitted it. `skein review reject ID --send-back` sends the same request.

### Behavior

- An unattended agent turn records what it did. The task panel names a turn stopped at its step or token limit, a write the gate refused, a write that failed, and a turn that filed nothing, instead of one "did not record progress" sentence. A turn that failed before it used any tool is `failed`, not "can have written records". The wake prompt states the per-run limits.
- Approvals offers "Send the task back to <agent> for another turn" when you reject a submitted task. The agent wakes with your note in its inbox, and the wake prompt tells it to act on that note before it submits again. A rejection without it still wakes nobody.
- Text a person or a remote source supplies cannot close the labelled wrapper the model reads it in. An attached file, an image description, a shared-chat message, a flock answer, persona instructions and extension context all go through one helper that escapes attribute values and neutralizes the closing tag inside the text.
- `skein context --engagement <id> --write <file>` writes the workspace tier only. It wrote the caller's own view, so a person's private tasks and their crews' rows reached a repository file. It now also says that the file holds records every teammate can read.
- Invisible Unicode format characters are stripped from remote MCP tool descriptions, input-schema text and results before the model reads them. A hidden character still changes the tool's first-use version.
- `GET /api/absences` and the `list_absences` tool return at most 200 current and upcoming windows, soonest first. The list had no cap.
- A worklog note moves its task. Needs a call, the flow metrics, the Monday stale-work nudge, the digest and insights no longer name a delegated task stalled while its agent reports progress.

### Operations

- `docs/VISIBILITY.md` describes time away filed for somebody else as the code handles it: with no tier named, the window is private to the person away with its dates shared. The document said it was refused and fell back to the workspace tier.
- `SECURITY.md` states where to report a vulnerability (GitHub private vulnerability reporting), which versions get fixes, the properties Skein promises, and what is out of scope.

## 0.6.10 — 2026-09-28

### Contracts

- `GET /api/calendar?start=&end=` (at most 42 days) answers events, open task, milestone and promise due dates, and time away, each at the caller's tier and through the workplace policy per kind. It carries `today` (the team day) and `truncated` (each kind that reached its 500-row cap). `mine=true` narrows tasks to the caller's own. Events are drawn by overlap, and an all-day end is exclusive, as in the ICS feed.
- `GET /api/events/{id}` answers one event with `starts_local`, `ends_local` and `agenda_refs`, the `question #12` style references in its agenda that the caller may open. `event` is now a reference target in receipts and agendas.
- `PATCH /api/events/{id}` edits or reschedules an event: an empty field is unchanged, and `-` clears the end, description, attendees or agenda. `engagement_id` 0 is unchanged and a negative one unlinks. It never takes a tier, and an unknown field is a 422. The agent tool `update_event` is gated as `event_edit`, its own authority row in the `event` family, for 63 tools.
- Migration 044 adds a nullable `event_id` (`ON DELETE SET NULL`) to notes, tasks, decisions, questions, blockers, promises and intake requests. The seven create services and agent create tools take `event_id`, refused when the writer cannot read the meeting or the row would be wider than it. The gate checks the meeting before an agent's create is queued, including the workplace policy on the meeting.
- `GET /api/events/{id}/items`, `POST /api/events/{id}/links` and `DELETE /api/events/{id}/links/{kind}/{item_id}` read, add and remove the records linked to a meeting. The link routes are judged on the linked record's workplace policy as well as the meeting's.
- `POST /api/ingest` takes `event_id` and refuses an unknown field with 422. Every proposal from that paste carries the meeting's id and tier.
- An event links only to an engagement at least as wide as itself. `POST /api/events` and `PATCH /api/events/{id}` refuse a narrower one with 400.
- `POST /api/field-guide/calendar` ties the new `calendar` card (rate-capped, fixed knot id).
- Extension API `1.0.0` is unchanged. No typed view carries `event_id`, and a link emits no domain event.

### Behavior

- Work → Calendar (`/calendar`) shows the month as a grid, or on a phone as a list of the days that hold something. It shows meetings, open task, milestone and promise due dates, and time away, on the team clock. "Only my tasks" is on by default. Another person's time away shows only from today on, and a window whose owner shared only its dates reads "away".
- A meeting opens in a panel at `/calendar?event=<id>`, where a search hit on a meeting now lands. The panel shows the time, attendees, the agenda with its references linked, the pre-meeting brief, Edit, delete, and From this meeting: the records linked to it, with controls to link and unlink one.
- Paste notes for this meeting (`/ingest?event=<id>`, from the panel and from My Day's meeting ask) links each approved record back to the meeting, at the meeting's visibility. A trusted-header name with no key can paste only for a meeting the whole team can see. A question whose assignee cannot read the meeting stays under Not captured.
- My Day's meeting ask says how many records that the reader can read came out of the meeting. The reader still records the outcome.
- Work → Browse no longer has a Calendar register. Add events on Work → Calendar.
- Two new field-guide cards: Calendar (Clove Hitch) and What came out of a meeting (Lark's Head).

### Operations

- Migration 044 runs at startup. It adds seven nullable columns with no default, which rewrites no table, and seven partial indexes on those columns. Each index build holds a short write lock on its table. No configuration changes.

## 0.6.9 — 2026-09-28

### Contracts

- `GET /api/notes` takes `before` (a note id: the notes older than it) and `limit` (1 to 100, default 25), newest first on an id keyset. `POST /api/field-guide/notes` ties the new `notes` card (rate-capped, fixed knot id).
- The Skein MCP server adds `search_notes(keyword, limit, before)`, for 25 tools. Over a personal key it reads with the key owner's Viewer: their private notes and their crews' notes. Over stdio it reads the workspace tier. Policy action `skein.mcp.notes.read`, with a per-row projection.
- The agent tool `search_notes` reads with the requester's Viewer through a per-row workplace policy projection (`skein.tool.search_notes`). A shared chat and an unattended run read the workspace tier.
- The agent write gate refuses an agent's update or delete of a private record the requester can read: "An agent cannot change a private record. Change it yourself." Such a proposal auto-rejected on approval as "target no longer exists". After a chat turn's `search_notes` returns a private or crew note, every later write in that turn goes to review, and a write the team would review is refused.
- Workplace policy: for an agent, the policy context of an existing row carries the row's own classification, at the gate and at approval. A crew row reached every rule unclassified, so a rule keyed on `classification == "crew"` never ran for an agent's change to one.
- Extension API `1.0.0` is unchanged.

### Behavior

- Work → Notes (`/notes`) lists every note you can read, newest first, 25 at a time. Search, open a note to its markdown, edit it (only the changed fields are sent), delete it, or share it with the team. A capture adds the new note on top without dropping older pages or an open edit. A search hit on a note opens `/notes?note=<id>`.
- A bare C opens quick capture when focus is not in a form field and quick capture or a task panel is not open. Settings → You → Keyboard turns it off in this browser.
- In your own chat, the agent can search your private notes. In a shared chat or an unattended run it reads the team's notes only.
- `delete_note`, `cancel_event` and `forget_memory` on a private row give the refusal's reason instead of "no note #N". A crew member's agent can propose deleting a crew note, as it could already edit one.
- The note delete confirmation on Notes and Browse says the activity record can keep the note's topic. It claimed 300 characters, and a delete logs the id only.
- A new field-guide card, Your notes (Miller's Knot), ties when you open Notes.
- A `%` or `_` in a note search matches that character. It was a wildcard, and it matched every note.

### Operations

- Dependencies: PyJWT 2.15.0 (from 2.13.0), Strands SDK 1.57.1 (from 1.56.0) and pydantic 2.13.5 (from 2.13.4) in `backend/requirements.lock` and both workplace template locks. Declared minimums are unchanged. PyJWT 2.14.0 carries six security advisories, several for `PyJWKClient`, which OIDC sign-in uses. `scripts/check-session-upgrade.py` passed for sessions written on 1.56.0, restored, appended and restarted on 1.57.1, and rolled back to 1.56.0. Rebuild the image from the new lock.
- In 0.6.8 the daily `retention-prune` job failed with KeyError on any day an export file older than 14 days existed under `data/exports`, so nothing else was pruned that day either. It now completes and names the files in its activity row. No action is needed.

## 0.6.8 — 2026-09-26

### Contracts

- Workplace project policy: every route whose first path literal names no entity is judged on the rows it reads or writes, with the same per-row decision the list routes make. Affected: the activity feed and ledger, insights, findings run, disposition and convert, rituals, usage, stakeholders, the digest, the review diff, the week plan, share, the 1:1 brief, the chat engagement link and room detail, the artifact reader and the administrator export. Under a rule that denies the reader a project class, an aggregate that cannot drop that class answers 403, and a document with no engagement answers 403 unless the rule leaves every class open. A body `engagement_id` never stands in for the stored one: the write is judged on the row's current project first.
- `POST /api/review/{id}/approve`, `/reject` and `/approve-batch` judge the reviewer's reach on the target row's project. A proposal the queue and the diff withhold cannot be approved or rejected.
- A resumed workflow step re-decides the requester's `playbook.create` right before it runs. A rule that now denies it answers 403 on approve and settles the proposal, the same as the governed playbook path; it answered 400 as an apply failure and left the proposal pending. A rejection of such a proposal counts against no agent.
- `POST /api/review` refuses a proposal whose payload or summary carries a Unicode format character (category Cf, except U+200D), with the field named. `GET /api/review`, every markdown packet and the CLI render such a character in an older row as `<U+XXXX>`.
- `POST /api/users/{name}/rename` and `POST /api/users/{name}/active` require a named administrator in every mode. A merge by another person is refused while the account is in a crew, is an active member of a private shared chat, or holds a pending invitation. A human cannot be renamed to a name that ends in `-mcp`.
- `GET /api/crews/mine` answers the strong viewer's crews, and `[]` for a trusted-header name with no key.
- Delegation: an agent identity is minted only for a strong caller, from every door (REST, the agent tool and a review verdict). A person's `<name>-mcp` identity cannot be delegated to. The remote MCP door refuses a person name over 64 characters and acts only through the row it minted.
- The Skein MCP server's `capture` takes `share_with_team` (default false) and files the person's own record at their private tier.
- The body cap is chosen by route: `POST /api/files` takes the upload limit plus a framing allowance, every other `/api` route takes 1 MB whatever its Content-Type. `/api/health` reports membership in `pg_execute_server_program`, `pg_read_server_files` and `pg_write_server_files` as a database warning.
- `GET /api/provenance/{kind}/{id}` no longer returns the proposal's `requested_by` or `review_note`, and returns the proposal of a private or crew review to its review owner only.
- The forge webhook answers a private task exactly like a nonexistent id. The engagement close notice counts workspace tasks only and is sent only for a workspace engagement; the owner of a hidden open task gets a notice of their own.
- The CLI refuses a redirect that leaves the configured scheme, host and port, so the personal key never follows a 3xx to another origin.
- Extension API `1.0.0` is unchanged.

### Behavior

- Records filed about a person by someone else take that person's narrowest tier: time away filed for a teammate is theirs with the dates shared, and they are told at once; a standup an agent files in a teammate's name is theirs to judge; a note carries the requester's own name; a strong paster's meeting-note proposals are theirs alone to approve, with no team notice; a mock-provider line and an MCP capture from a signed-in person land at "only you".
- A shared task keeps its waiting-on link at the team tier, and the slip forecast lists only team-visible targets. A dates-only window no longer says whether it is PTO. The daily digest counts team reviews only.
- The command bridge writes no session for a chat deleted under it, and a fresh claim of a thread id starts empty instead of replaying a session nobody owned.
- Personal MCP servers: a server deleted and re-added under the same name asks for its first-use reviews again, and one server's colliding tool names cost only those tools, never the owner's whole agent build. Deactivation keeps the registrations through the 30-day grace, drops the live connections and any open sign-in, and erasure removes them with the account.
- The export carries the exporting administrator's own crews and the team notice names them.
- The review page shows every invisible format character as its code point. `skein review` prints every payload field, and `skein review approve` prints the proposal and its diff before the verdict lands.
- The room-leave confirmation says the messages stay for the members. The weak-identity notice appears on growth interests, the agent inbox and the flock traces. The MCP servers card says which servers get the first-use review. The Settings roster tells a key-holder administrator which edits need a listed name.
- A mermaid init directive can no longer turn HTML labels back on, and the page CSP refuses media loads.

### Operations

- Every superuser bootstrap heredoc (`deploy/postgres-init/10-app-role.sh`, the ConfigMap copy in `deploy/k8s/base/postgres.yaml`, and the two workplace-template scripts) sets `search_path` to `pg_catalog, pg_temp` before its first statement and qualifies its calls. Re-run them once on an existing database. `scripts/reference-deployment-contract.sh` checks it.
- The restore fence in `deploy/k8s/README.md` step 7 cancels pending merge requests and deletes personal MCP server rows. Owners register their servers again after a restore.
- `SKEIN_ADMINS` must name whoever renames, merges or deactivates accounts, in every mode. `backend/.env.example` and `deploy/k8s/OPERATOR.md` say so.
- The legacy export file under `data/exports` is deleted after 14 days. The manual dump the runbook makes matches no retention pattern: delete it once its recovery point is verified.
- `ops/ollama-bridge.py` listens on the Docker gateway address (`BRIDGE_LISTEN`, default `172.17.0.1`); every interface takes `BRIDGE_LISTEN_ALL=1`.
- No migration.

## 0.6.7 — 2026-09-25

### Contracts

- `GET /api/review/stranded` lists every pending proposal that no active person can approve or reject, as `id`, `entity`, `action`, `created_at` and `reason`, never its summary or payload. It lists a proposal that no active person can read, a team proposal whose target row was deleted, and an authority change when no active administrator can exist. It answers a named administrator only (a name in `SKEIN_ADMINS`, or the `SKEIN_OIDC_ADMIN_GROUP` group) and requires core `0.6.7` or later.
- `execution_status` on an approve response, a batch row and a settled review row can be `failed`: an extension or remote MCP tool call that did not complete. `completion_unknown` still means a write that can have run.
- Approve and batch approve classify their failures. A busy database (a lock timeout, a deadlock, a serialization failure), a directory resolver that did not answer, and an MCP server that is not ready answer 503 with `Retry-After`. The MCP codes are `MCP_SERVER_CONNECTING`, `MCP_SERVER_UNAVAILABLE`, `MCP_CONNECT_BUSY` and `MCP_CONNECTION_CLOSED`. `MCP_SIGN_IN_REQUIRED` and `MCP_SERVER_DELETED` answer 409. A fault in the server's own state answers 500 without its text, where it answered 400 with it. Batch approve gives every id one result row and never stops at a failed item.
- `POST /api/mcp/servers/{server_id}/sign-in` answers 503 `MCP_SERVER_UNAVAILABLE` with `Retry-After` when the connect got no answer, 503 `MCP_CONNECT_BUSY` when no connect slot is free, and 503 `MCP_SIGN_IN_SLOW` when the server took too long to start the sign-in. A server that answered but is not an OAuth MCP server still answers 400.
- `GET /api/provenance/{kind}/{id}` answers 404 for an absent row of every kind. It answered 500 for `question_assign`, `memory`, `intake` and `note_edit`.
- These reads require a named administrator, and the trusted-header fallback administrator gets 403: `GET /api/admin/export/download`, `GET /api/admin/keys`, `GET /api/eval/capture`, `GET /api/feedback` for everyone's feedback, crew member lists, and another person's `<name>-mcp` record in `/api/agents`, `/api/agents/trust`, `/api/agents/{agent}/inbox`, `/api/review/stats` and `/api/review/season`.
- Rejecting an authority change requires a strong administrator, the same as approving one.
- `POST /api/keys/request` accepts a signed-in person without a key. A name in a mint command must start with a letter, a digit or `_`, and can contain `@` and `+`.
- The Skein MCP server adds `forget_memory`. It is annotated destructive and always files a proposal that a person approves.
- Extension directory resolvers: an OIDC requester is refreshed through the directory resolver whenever the composition has an identity mapper, a policy rule, a tool handler or a workflow action, because each of them reads the requester. Without a resolver, such a composition refuses the verdict. A composition with none of them (the stock core app) skips the refresh and drops the saved groups. One refresh has 2 seconds in total, and a resolver that raises or runs out of time is a `DirectoryOutage` (503 with `Retry-After`). A resolver that returns `None` for a person still refuses.
- Extension API `1.0.0` is unchanged.

### Behavior

- A stock OIDC deployment can approve an agent's proposals again. Every approval of an agent proposal from a signed-in person failed with "The requester directory identity could not be refreshed."
- Dead-end proposals: a proposal whose policy can no longer be recomputed (its requester or agent deactivated, its task moved, its module removed) can be rejected. A strong requester can always withdraw their own request while they can read the record it changes. Approving a proposal whose target was deleted rejects it on the record. A team memory or an engagement memory that no other active person can approve is refused when it is filed, with the fix. The authority job files nothing when no active administrator can approve it: none is named, every named person is deactivated or renamed, or the auth mode admits nobody.
- Settings → Operations lists the stranded proposals to a named administrator, with a fix for each. If the list does not load, the card says so and does not show its all-clear line.
- Trust streaks count only verdicts on the agent's work. A rejection that nobody chose does not count: a rejection of a deleted target, of a changed contract, of an action the current policy now denies, or of a proposal whose policy cannot be recomputed. A requester's withdrawal and an owner's drop of their own personal MCP call do not count either. Review-only extension calls keep their counts but have no streak, so they file no promotion that can never apply.
- Separated duties (`SKEIN_REVIEW_SEPARATION=1`): the owner of a proposal at the private tier approves it, because nobody else can read it. A shared-chat proposal follows the same rule as a solo one, so separation or policy approvers send it to the team review.
- Personal MCP servers: a trusted-header name with no key cannot use another person's personal servers. A call is judged by its owner or by a reviewer that the current policy names, and a reviewer who cannot judge it starts no connection. Approving a call on a server this process has not connected starts the connect and answers when to try again, with the real state: connecting, in a retry wait, waiting for a free connect slot, signed out, or deleted. One owner holds at most 2 of the 8 connect slots of a process, and a connect that never finishes gives its slot back. A closed session, a deleted or edited server, and a server re-added under the same name are noticed on every call and on every replica.
- Remote MCP results: a remote failure that the SDK returns as a result is a failed read, or a write whose completion is unknown. It was recorded as a completed write in the ledger. The model reads "The write can have run, so its completion is unknown. Do not retry it." for such a write, because providers drop the completion status. A turn stopped during a write records the write as completion unknown. The review page says "the call did not complete" for a failed call and names every id of a mixed batch.
- Memories: a plain `/remember` saves your own memory, also in a chat linked to an engagement, and recall brings it back with that engagement's work. `/remember team:` shares: in a linked chat it files the engagement proposal, elsewhere the team proposal, and both need a key or a sign-in. In a linked chat, workplace policy decides `/remember` with the engagement's tier and project type.
- A shared-chat agent can post standups, record time away and assign questions.
- An agent create in a crew is refused with the true reason: only crew members create records in a crew, and an agent is not a crew member.
- Key requests: a signed-in person without a key can request one on Settings. The request goes to the active named administrators, or to the team when there is none. Settings prints a mint command only for a name that every shell keeps intact, quoted for bash, sh and PowerShell.
- A deadlock or serialization failure is a busy database that a retry fixes, so it answers 503 instead of 500, or 400 with Postgres text in an approval. Approving a task completion and closing the task directly no longer deadlock, and neither do approving a create and renaming its engagement.
- A document for an engagement that does not exist is refused by name instead of failing with a 500 on every approval.

### Operations

- Migration `043_extension_review_failed.sql` adds `failed` to the allowed statuses of `extension_review_invocations`. It rewrites no rows.
- Write each `SKEIN_ADMINS` name the way that person signs in. Skein matches the case after the first sign-in, but a key request sent before it goes to the exact spelling in the list. A deactivated name, and a name that a rename freed, count as no administrator.
- In trusted-header mode with no `SKEIN_ADMINS`, every key holder is still an administrator for settings and backups, but the export download, the key list, the capture replay, everyone's feedback, crew member lists, crew-steward repair and the stranded list need a name in `SKEIN_ADMINS`. `deploy/k8s/OPERATOR.md` lists them.
- A deployment that composes an identity mapper, a policy rule, a tool handler or a workflow action for OIDC users needs a directory resolver, or agent proposals from signed-in people cannot be approved. The stock app needs none.
- After the upgrade, a named administrator can check Settings → Operations for proposals that no one can settle.

## 0.6.6 — 2026-09-24

### Contracts

- `GET /api/settings/reasoning` returns the team reasoning level: `level`, `override`, `levels` (the names the team model declares), `ignored`, and `applies`. `POST /api/settings/reasoning` with `{"level": "<name>"}` sets it, and an empty `level` clears it. Each row of the `GET /api/settings/model` menu carries `reasoning`, the list of level names that model declares. Level params are never served. The GET requires a named identity, and the POST requires an administrator. These endpoints require core `0.6.6` or later. Extension API `1.0.0` is unchanged.
- The REST create models refuse an unknown field with a 422: tasks, notes, questions, decisions, standups, events, blockers, intake, capture, engagements, lessons, promises, milestones, absences, private notes, `POST /api/notifications/read`, `POST /api/users/{name}/rename`, and `POST /api/week/plan`. An unknown field was dropped, so a misspelled `visibility` filed a row at the workspace tier. A client that sends extra fields to these routes must stop sending them.
- An `/api` request body over 1 MB returns 413 before it is parsed, with or without a Content-Length. Multipart uploads, the forge webhook, and the remote MCP endpoint keep their own limits.
- Event rows from `/api/events`, the digest, and My Day carry `starts_local` and `ends_local`, the times on the team clock. `starts_at` and `ends_at` are UTC.
- `POST /api/context-pack/publish` returns `file`, the archive's name, in place of `path`, its absolute path on the server.
- `GET /api/users?all=1` includes deactivated teammates for an administrator only. Everyone else gets the active roster.
- `GET /api/my-data` returns counts of what only you can read, by kind. `GET /api/my-data/{kind}` lists your private records of one kind, `DELETE /api/my-data/{kind}/{id}` deletes one, and `GET /api/my-data/export` downloads your own data as JSON. All four need a strong identity.
- `DELETE /api/auth/sessions` ends every browser session of the cookie's person, this one included. It requires the cookie-bound CSRF value.
- `DELETE /api/auth/session` and `DELETE /api/auth/sessions` return 200 with `{"logout_url": ...}` instead of 204. The URL is the identity provider's sign-out page for this browser, or an empty string. Settled `/api/review` rows carry `text_cleared_at`.
- Extension API `1.0.0` is unchanged.

### Behavior

- A `SKEIN_MODELS` entry can declare reasoning levels. An administrator sets the team level on **Settings → AI runtime → Reasoning (team)**, and a person sets a level for one solo chat with `/reasoning <level>`. `/reasoning` lists the levels of the model of the chat and names the level in force. `/reasoning default` returns the chat to the team level. A turn uses the level of the chat, then the team level, whichever the model of the turn declares, else the default of the model.
- Only the main solo chat turn, including an `/as` persona turn, sends a level. Titles, planners, consults, flocks, the vision sidecar, the unattended runner, agent turns in shared chats, and long-chat summaries do not.
- A long-chat summary now runs on the model without the reasoning level and keeps no reasoning block. A summary that an older session stored with a reasoning block loses that block when the session is restored. The SDK stores a summary as a user message, and Anthropic and Bedrock refuse a reasoning block there, so every later turn of that chat failed.
- The In force summary on Settings and `skein model` add a Reasoning row. When the team level sets an output cap, the Output cap row says so and names Reasoning (team) as the source, and the Parameters row counts the level params. The field guide adds two cards: **Pick how much the model reasons in one chat** and **Set the team reasoning level**.
- Event times without an offset are the team's clock, stored as UTC. The calendar feed writes UTC times with `Z` and a `DTSTAMP`, keeps 90 days of past events, and skips an event whose end is before its start. All-day events reach the digest and My Day at and west of UTC.
- Findings, blockers, intake, portfolio, and spend: a re-fired finding loses the disposition of the older one. Resolving one blocker no longer unblocks a task that another blocker still holds, and a blocker on a done or void task leaves it closed. An experiment cannot be accepted without an engagement. Void work counts as finished. Monthly spend and budgets use the team's month, and the weekly spend chart starts on Monday.
- Search filters by kind before it cuts the result list, and memory recall reads past the first 20 matches. Promise and engagement edits reach the search index. Adoption findings take the last digest slots.
- A disconnect before the first chat frame releases the turn lock. A deleted shared-chat message leaves the agent sessions that copied it. A solo chat deleted during a turn stays deleted, and a delete during a turn is refused. `/as` accepts an attachment with no text. A slash command or `/flock` with an attachment is refused with a message that says the file was not read. A refusal no longer starts a thread, and a failed command no longer shows the server error text.
- Agents: an unattended run respects the consult cap, and the daily token ceiling counts consults and planners on the run. Governed tools do their database work off the event loop. The image describer, the digest narrator, and long-chat summaries record their token use.
- Personal MCP tools: the tool version is a SHA-256 of the whole tool contract, so every personal MCP tool asks for first-use approval once after this release, and again when its description, schema, or annotations change. An OAuth sign-in completes only in the browser that started it.
- A memory addressed to one person reaches only that person on every search surface: REST search, `/ask`, the short-id lookup, `/search` in chat, and the agent and MCP `search_workspace` tools. Recall, the memory list, and forget already worked this way. `/remember` addresses a memory to the speaker, so a teammate can no longer read it through search. A private memory now reaches its addressee: the tier check compared the database role name, not the memory's `user`.
- Shared devices: signing out forgets the picked name, so the next person on the browser does not act as it (trusted-header), and a chat's title leaves the browser tab when the page closes. `skein config` with a new key, name or server deletes the cached briefing and attention count of the previous identity, and `skein my-day --cached` answers only the identity that fetched the briefing, also when `SKEIN_API_KEY` or `SKEIN_URL` changed it. A key that whoever runs the server mints says "minted at the server" in its owner's activity.
- The daily unattended agent run holds only the wake tools its prompt names, the same contract as a wake, so no system MCP tool is in reach with nobody watching.
- Hidden records leave no trace in derived views: `/api/allocations` no longer returns a 500 for an allocation on an engagement you cannot read (and answers as for an absent one), and withholds that row. Usage folds every hidden engagement into one "other work" row with no id. The pulse counts, the standup chain, and the "Shipped" recap count workspace rows only, and the token-spend finding no longer lists chat ids, which name whose chat spent.
- Person agents: another person's `<name>-mcp` agent no longer appears in your activity feed, the agent list, review stats, the season readout, the settled review lists with their reviewer notes, the approval record on review rows, or the team context pack, and its inbox answers as an absent agent. The authority job no longer files proposals from a person agent's verdict streak. The owner sees it, and an administrator sees it where the trust page already let one.
- Tool reviews: a review-gated stock or remote tool call in a real agent loop now reaches the review queue. The live agent object in the saved call state made every such call fail. The saved state keeps only `request_state`, not the chat transcript or the system prompt. A personal MCP server call is reviewed privately by the server's owner, also under `SKEIN_REVIEW_SEPARATION`, because it runs on the owner's credential and returns their data. When workplace policy names approver groups, the call stays in the team review so they can judge it.
- Agent memories: the agent `remember` tool files a memory for the person who drove the turn, not for the whole team or for a person the model names. The review gate files every memory addressed to a person as a proposal private to that person, whichever tool filed it (the agent tool or the MCP server), and they judge it, also under `SKEIN_REVIEW_SEPARATION`. A private proposal of any kind sends no team notice. An addressed memory's activity-log row carries the id only, and the portable export leaves memories addressed to people out. A shared-room agent's search reads no addressed memory, and `shared-chat` is a reserved name. An approved create no longer shows its text in the approved review list after the row it made is deleted, and a proposal's activity-log row no longer names the person who asked.
- With embeddings on, only workspace records, and no memory addressed to a person, are sent to the embeddings service. Search terms still go there, and the search box now says "Search terms go to the embeddings service." `/api/health` carries `semantic_search`.
- A judged proposal from a person (bulk ingest files them) shows its verdict and the reviewer's note only to the proposer and the reviewer, in the settled review lists, review stats, and the rejection-spike finding.
- An agent's proposal from your chat is private to you when you have a strong identity (an API key or a deployment sign-in): only you see it in Review, no team notice quotes it, and your approval shares the result. This covers the review gate, stock and extension tools, and remote MCP tools. The agent changes only records you can read, and a record you cannot read is refused before any proposal is filed. The team still reviews it when `SKEIN_REVIEW_SEPARATION=1` is on, when policy names approvers, for an unattended run, and for a trusted-header name with no key. Review marks such a proposal with "Only you can see this proposal."
- Time away: your own window now offers three choices. "Only me" has no team effect. "The team sees that I am away" counts the dates in capacity, planning, the weekly draft and staffing what-ifs, and hides the kind and the note. "The team sees the details" shows everything. With a key or a sign-in, the form starts at "Only me" (without one, at "The team sees the details", because a private row is unreadable to that caller), and it remembers your last choice. You can widen a window later with "share the dates" or "share with the team" (`POST /api/absences/{id}/share`). A private window's dates no longer reach capacity or planning unless you share them. This includes windows filed before this release. The agent `add_absence` tool takes `team_sees`, which defaults to the narrowest choice: "nothing" for the strong requester's own window, "details" for a teammate's or a weak requester's. `skein absences add` takes `--team-sees`. An `/api/absences` request with no tier gets "Only me" for your own window, and everyone on the roster for a teammate's.
- With a key or a sign-in, standups and captures now start at "only you" (the REST default of `/api/standups` and `/api/capture` too). A trusted-header name with no key cannot read a private row, so for it they still start at everyone on the roster. The standup card, the capture palette and the time-away form remember your last choice. `skein standup` and `skein capture` take `--team`. Every record's badge now also marks "everyone on the roster". A private standup, note, task, question, decision or blocker has "share with the team" beside its "only you" mark (`POST /api/share/{kind}/{id}`, strong identity). The share is one way. It is refused for a record linked to work that fewer people can see, and a void task stays out of search. A capture that asks a teammate a question goes to the roster when the request names no tier, because it cannot be private. The agent `post_standup` tool takes `share_with_team`. Its standup is private only when the author is the strong requester, and a private one forks no blocker. A crew picker whose crew is gone now falls back to "only you", not to the roster. Review shows who reads the row a create proposal makes.
- 1:1 briefs: a teammate's brief opens only under a 1:1 pairing they accepted. On the People page, ask a teammate for a pairing, or let a teammate prepare 1:1s with you. Either side can end it, and the subject sees each lead and when they last opened the brief (`/api/private/pairs`). Your own brief and your private 1:1 notes need no pairing. A lead whose request the subject declined or ended can ask again after 7 days, and deactivation ends a person's pairings.
- Shared chats: a new room agent reads messages from its join point. The steward can tick "Also share the earlier messages" when adding it, and the room's system message tells every member which. `POST /api/shared-chats/{id}/agents` takes `share_history` as true or false (false by default). Agents added before this release keep the whole history. An agent removed and added again without the history also starts a new model session.
- Trusted-header mode: solo chats, attached files, memories, My Day's notices and your own activity tell a caller with no key "Anyone who can reach this server can pick your name and read this. Sign in with a key for privacy."
- Growth interests: only you see yours until you select "share with the team" in Settings (`POST /api/users/growth-interests/share`). The roster, staffing what-ifs and the portable export show only shared interests. Interests saved before this release start unshared.
- Team memories: a memory reaches the team only when you share it. Select "share with the team" on Agents → Memory (`POST /api/memories/{id}/share`, strong identity), or type `/remember team: <fact>`. Either files a proposal the team can read, and a teammate other than you approves it. An approved share moves the memory from you to the team. The author of an engagement memory can no longer approve it alone.
- Administrator actions: a rename, a merge into your account, and a deactivation or reactivation now send you a notice. An export of the workspace data and the revocation of every API key send the team a notice. A script or the scheduler sends none.
- When a workplace policy names approver groups for memories, a memory addressed to a person is still a proposal private to that person, and that person approves or rejects it. Before, every teammate's "Review needed" notice quoted it, and nobody could give a verdict. Approver groups govern shared records only: the same holds for an agent's "only me" standup or time away, which its person judges alone, also under `SKEIN_REVIEW_SEPARATION=1`. The team notice now goes out only for a proposal about a record at the workspace tier.
- A key request goes to the named administrators (`SKEIN_ADMINS`), or to the team when none are named, instead of to everyone.
- Crews: every teammate still sees each crew's name and member count, but only its members (with a key or a sign-in) and named administrators see who is in it. `/api/crews` rows carry `member_count`.
- Feedback: `GET /api/feedback` returns only your own feedback, or all of it to an administrator, and you can delete your own with `DELETE /api/feedback/{id}`. A feedback row stores the chat input and output it judged. `GET /api/eval/capture` (`skein eval`) requires an administrator.
- The server's access log drops the query string, so search terms, `/ask` questions and 1:1 note lookups no longer reach the cluster's logs. The path, status and timing stay.
- The activity log records the id and the changed field names, never the text, when a note, blocker, promise, intake request, time off, or growth interest is edited or deleted. The row that records a workspace record's creation still carries its title. A deleted note no longer keeps its first 300 characters there. Rows written earlier keep their text, because the log cannot be rewritten.
- Merges: a merge into your own account is refused, and so is a merge by another person while the source account holds data only its owner can read (private-tier rows, solo chats, addressed memories, private review proposals, attached files, MCP servers). The source account's API keys are revoked and its OIDC sign-in bindings and notifications are deleted, so none of them reaches the target. Bind the person's IdP subject again with `python -m app.bind_oidc`. A merge still moves the source's shared-room and crew memberships. A name that a person's rename or merge freed cannot be claimed by a new person or agent, because the activity log still names the earlier owner, but the account whose history it is can rename back to it. A rename now also moves the owner of private review proposals. A merge that carries such data now takes both accounts: signed in as the duplicate, ask to merge on Settings → You (`POST /api/merge-requests`); signed in as the account you keep, confirm. That merge moves everything, including the 1:1 journal, notifications and the OIDC binding. Either account can cancel or decline a pending request. The source account gets a notice when a request is filed from it. A request lapses after 7 days, and a deactivation or a key revocation of either account, or revoke-all, cancels it. A request between two accounts bound to the same identity provider is refused when it is filed. An administrator's merge ends the source's 1:1 pairings instead of moving them.
- Auth and visibility: an API key cannot be minted for a deactivated account. A rename moves the private journal in the same transaction as the roster. A trusted-header caller with no X-User cannot edit crew rows. Accepting a revoked invitation does not tell the caller whether the room is archived.
- Rate caps: supersede, milestone and blocker edits, and the CI webhook take the write cap. A 1:1 brief pull takes its own cap. Refusals no longer quote the rejected value for provenance kinds, renames, personas, playbook start dates, and duplicate crew or engagement names.
- Frontend: status messages stay live under the task panel and the capture dialog, and success messages use the confirmation tone. Review verdicts settle against the current queue. Settings and People reload on identity changes only, so a theme paint or a write in another tab no longer clears an unsaved 1:1 note draft. Shared chat keeps its read cursor behind sends and removes deleted messages from open rooms. A chat reply that ends without a done frame is marked as cut. Focus moves to the next row after a delete. Intake number fields can be emptied while typing.
- Deleted content leaves the model sessions. An attached text file is kept in a chat's history as a pointer, and each later turn reads the file's current text. Deleting the file clears the model sessions of the chats that point at it, because an answer or a summary can quote it; the chats stay. An image description is kept as the file name. Deleting a shared-chat message also deletes the agent answers to it ("Deleted with the message it answered.") and clears every agent session of the room, so each agent re-reads the room from its join point, its own answers included. The delete waits while any agent in the room is answering. A chat with no activity for 90 days loses its model sessions, and the chat stays. Deleting a chat no longer deletes the sessions of another chat whose id starts with its id and `--`.
- Settings → You → Your data shows what only you can read, deletes any one of your private records (standups, tasks, notes, questions, decisions, promises, blockers, requests, lessons, engagements, milestones, events, time away, worklog entries), and downloads your private data as one JSON file. The list and the download pass the workplace policy per row. A delete is refused while other records point at the row. Shared records keep no delete for their author. A new field-guide card, Your data, points to it. A handoff's answer names its file instead of its server path.
- A deactivated person's private data is deleted 30 days after deactivation, on the date shown: private records, solo chats and chat folders, shared chats where they are the only person left, attached files, memories addressed to them, proposals reviewed privately for them, notifications and the 1:1 notes they wrote. The job repeats daily for what reaches the account later. Team and crew records, and crew reviews, stay with the name. Reactivating before then keeps everything. The deactivate confirmation names the date, and the roster shows it. `POST /api/users/{name}/active` returns `erase_on` for a deactivated person.
- With OIDC, signing out of Skein also signs you out of the company login when the identity provider publishes a sign-out endpoint. The browser goes there and comes back to Skein signed out, also after the Skein session expired. It carries the ID token only when the audience setting differs from the client id. Otherwise sign-out stays local, as before.
- Settings → You has "Sign out of every browser". It ends your Skein session in every browser, this one included. Your API keys keep working. A company-login session on another device stays open, and the Settings text says so.
- Copies have horizons: daily digests, old context-pack versions and the text of settled proposals leave after 180 days, an unattended agent run's session after 30 days, and the requester's name on a cost row after 365 days. A proposal keeps who proposed, who judged and the verdict, and the review list says "The text was deleted after 180 days." An approved first use of a personal MCP tool stays approved.
- Document tools answer for an unshared artifact as for an absent one, and a chat claimed by somebody else answers "No chat was found." without repeating the id.
- CLI: server text reaches the terminal without control characters, a failed request prints one line instead of a traceback, `skein config` repairs a corrupt file and keeps it at mode 0600, and `skein attention` never waits more than its timeout.

### Operations

- A `SKEIN_MODELS` entry accepts `reasoning`: a map from level names (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`) to the request params each level sends. Level params pass the same refusals as `params` and merge after them. Inside `extra_body`, `extra_query`, `additional_args`, `additional_request_fields`, and `options`, a level merges one level deep, so the other fields there stay. A `null` in a level removes the key it names, because thinking models refuse the temperature a persona or `SKEIN_MODEL_PARAMS` can send. README "Reasoning levels" has one recipe per provider. Roll the image before or with the ConfigMap: an older server reads `reasoning` as an unknown field and voids the menu.
- A level whose `budget_tokens` is at or above the output cap of its request voids the menu at startup, and `/api/health` names the level. The check reads the merged request and the cap that the provider sends. On `openai` and `openai_compatible` it runs only when params set `max_completion_tokens` or `max_tokens`.
- Migration `032_chat_thread_reasoning.sql` adds `chat_threads.reasoning`. It adds a column with a default and rewrites no rows.
- Migration `033_chat_message_reference_indexes.sql` indexes the foreign keys to `chat_messages`. Migration `034_mcp_oauth_browser_binding.sql` adds a column to `mcp_oauth_flows`. Migration `035_released_names.sql` adds `released_names`, which records the names that renames and merges free. Migration `036_absence_dates_shared.sql` adds `absences.dates_shared`, false for every existing row, so an existing private window stops counting in capacity and planning until its person shares the dates. Migration `037_growth_interests_shared.sql` adds `users.growth_shared`, false for every existing row. Migration `038_one_on_one_pairs.sql` adds `one_on_one_pairs`. No pairing exists after the upgrade, so every lead asks again. Migration `039_merge_requests.sql` adds `merge_requests`. Migration `040_chat_member_history_from.sql` adds `chat_members.history_from`, 0 for every existing member. None rewrites rows. A name freed before this release is not recorded.
- Events filed before this release in a zone other than UTC were stored as typed, and now read as UTC. Each shows shifted by the zone offset. Delete those events and add them again.
- `rss` left the `SKEIN_EXTRA_TOOLS` allowlist: the model chose its URL and request headers with no egress filter, and feedparser opens a local path. A deployment that lists it logs a refusal and loads the other tools.
- `backend/requirements.lock` carries hashes, and the backend image installs it with `--require-hashes`. `scripts/audit-deps.sh` audits that lock. The build backend is pinned to `setuptools==84.0.0`.
- The workplace template installs `skein-agents` through `skein-agents.lock` with `--require-hashes`. The template digest is zeros and fails the build until the consumer pins the digest of the published wheel.
- The example production egress policy allows DNS on port 5353, the CoreDNS pod port that OpenShift matches after the Service rewrite. The example dev Routes carry an IP allowlist annotation with a placeholder range that admits nobody until an operator sets it. The images keep code read-only to the runtime user, and the workplace Deployments carry resource requests and limits.
- The Gitea CI checkout token no longer lands in `.git/config`, and the Gitea e2e job has the postgres service the GitHub job has.
- Migration `042_user_erasure.sql` adds `users.deactivated_at` and `users.erased_at`. An account that is already inactive starts its 30 days at the upgrade. A new daily job, `erase-departed` (04:20), erases accounts past that date.
- OIDC: register `https://<frontend-host>/` as a post-logout redirect URI at the identity provider, and publish `end_session_endpoint` in discovery, so sign-out ends the provider session too (deploy/k8s/README.md). Sessions signed in before the upgrade hold no ID token and sign out locally.
- The retention prune runs daily at 04:00 instead of monthly. Migration `041_pending_change_text_cleared.sql` adds `pending_changes.text_cleared_at`.
- The backup mirror keeps 14 dumps, the same count as local, instead of 30. The first backup after the upgrade deletes the older mirror dumps. Copy any you must keep before you upgrade.
- Same-day backup retries mirror the partial backup instead of dumping again. The nightly chain check streams the ledger in batches. Retention prunes interval-job receipts.

## 0.6.5 — 2026-09-22

### Contracts

- `GET /api/chat/specialists` returns the built-in bench and workplace specialists that the caller can invoke. Each entry contains only `slug`, `name`, `description`, and `emoji`. The response is private and not cached. This endpoint requires core `0.6.5` or later. Extension API `1.0.0` is unchanged, and `/api/personas` still supplies sticky persona modes and shared-room agents.

### Behavior

- An expired browser session no longer causes repeated session and theme requests. After recovery confirms an anonymous session, repeated invalid responses do not restart recovery. Theme adoption skips locked sessions. Cookie-free trusted-header visitors still receive the team theme.
- Enter, Tab, and picker clicks complete the mention at the caret in solo and shared chat without deleting the rest of the draft. Completion replaces the whole token, including text after the caret, and returns the caret after the name. In shared chat, Enter sends immediately only when the whole token is already the selected agent name.
- The solo chat picker includes eligible workplace specialists. The list is fetched for the current identity, and restricted specialists are not carried across sign-in changes.
- The findings list, the Insights summary, and the Planning queue put feature-adoption findings after team findings. The adoption rule files one finding for each unused field-guide card. When 50 cards passed their grace period, these rows used up the 50-row list and the Planning queue's 30 finding slots, and team findings such as an aging question disappeared.
- Recent changes folds new feature-adoption findings into one row, such as "39 field-guide features have no team-wide first use 30 days after they entered the field guide.", with one evidence line for each finding that names the feature. The row does not count toward the 50-finding limit. A new install files one adoption finding for each unused card at once, and as separate rows they made the first week's summary incomplete, so it could not be marked reviewed.
- Browser session requests stop after 60 seconds: the sign-in configuration read, the session read, the sign-in exchange, and the sign-out. A request that never answered kept the page at "Checking your browser session" until reload. Because session reads, sign-in, and sign-out share one lock across tabs, it also stopped sign-in and sign-out in every tab. The page now shows the backend-unreachable message with **Try again**.
- Chat uses the tested assistant-ui `0.15` dependency set. This removes the snapshot subscription defect that caused repeated updates in installed workplace frontends. Local chat history, streaming, Stop, attachments, and inert Markdown images retain their existing behavior.

### Operations

- The backend and workplace locks select Strands Agents SDK `1.56.0`. A cross-version PostgreSQL check passed SDK `1.55.1` writes, `1.56.0` restore and append, restart with offloaded-result retrieval, and `1.55.1` rollback and append. This release adds no database migration.
- Upgrade the backend and frontend host together. Workplace frontend roots must carry the host's `@assistant-ui/tap` override at `0.9.18` and regenerate their npm lock. The installed host builder checks this override and every resolved copy. Do not apply this override to an older assistant-ui `0.14` host.
- The backend and workplace locks select `strands-agents-tools` `0.8.9`, and the package requires `0.8.7` or later. Before `0.8.7`, the optional `calculator` tool ran `symbols("...", cls=N)` with full Python builtins, so a deployment that listed `calculator` in `SKEIN_EXTRA_TOOLS` let the model run code on the server. If a deployment enables `calculator`, upgrade.
- `think` is no longer an allowed extra tool: the model chooses its model provider and settings, which runs a model call outside Skein's model factory. A `SKEIN_EXTRA_TOOLS` list that names `think` loads the other tools and logs that `think` was refused. Remove it from the list.
- Upstream deprecated every allowed extra tool. Each call logs a deprecation warning, and `strands-agents-tools` `0.9.0` makes it an error log. Extra tools stay off by default.
- GitHub and Gitea workflows use the same `setup-uv` `v10.1.0` action commit. The release-contract gate continues to refuse mismatched mirror pins.

## 0.6.4 — 2026-09-20

### Contracts

- `GET /api/delta` reports seven team calendar dates and carries `window_start`, `window_end`, `snapshot_id`, `review_revision`, `reviewed`, and `truncated`. Finding items carry `rule_id` and `severity`, and a new finding reports `direction` as `new` rather than `worse`. The reader's last-seen mark no longer decides the window, and a `mark=true` query writes nothing.
- `POST /api/delta/ack` records that one reader reviewed one summary. Its body takes `snapshot_id` and `review_revision` and refuses every other field. A summary that changed since the preview, or a revision another tab already spent, answers 409. An incomplete summary answers 400, because a refresh returns the same incomplete summary. The route stores a fingerprint and a revision, never a source timestamp, so a review cannot consume a change the reader never saw.
- A contributed `navigation` item renders in the workspace sidebar and in its narrow-screen drawer, with the declared query and fragment unchanged. Extension API 1.0 does not change. An item that declares no icon takes a neutral one.

### Behavior

- My Day's "Since you last looked" becomes Recent changes: one summary over seven team calendar dates, five headlines at the front, and low-severity feature-adoption findings grouped last. A finding reads as new rather than as a health call that worsened. **Mark this summary reviewed** records the summary on screen, not a point in time, so a summary that changes afterwards returns, and rows already reviewed can return with it. Opening the summary, expanding it, or reading its evidence records nothing.
- One workspace sidebar replaces the top navigation row and the per-page section tabs. It holds the five destinations and the open group's pages, at 240px from 1024px wide, at 56px when collapsed, and in a labelled drawer below that width. Search, page help, and Capture stay in the utility bar. Settings, the field guide, and the identity menu sit in the sidebar footer.
- My Day leads with Needs you and Your work. The standup composer sits in a closed disclosure below the task list, and the shared queues stay behind Show team context for every reader.
- Browse shows one register at a time, chosen from a grouped selector at every width. Hidden registers stay mounted, so a half-typed form survives a switch. The selector rewrites the address fragment in place: it adds no history entry and does not move focus, because a closed selector reports a change on every arrow key.
- Planning owns the weekly draft and the commitment. Health leads with engagement condition and risk and links to Planning for the plan. The specialist bench, the chat welcome, Approvals, and the task panel keep their main action and their evidence in view and fold reference material into disclosures.
- The page help control reads **Help** at every width.

### Operations

- The `delta_seen:<user>` rows in `app_settings` are no longer read. Nothing removes them. They are safe to leave in place or to delete.
- A browser still running the previous bundle asks for `GET /api/delta?mark=true`, which now writes nothing. That reader sees the same summary until the new bundle loads.

## 0.6.3 — 2026-09-18

### Contracts

- `WorkItems.get_blocker` and `update_blocker` read the blocker through the viewer the composition boundary granted, as the task and promise reads already did. A blocker outside that viewer's scope answers `BLOCKER_NOT_FOUND` with the same sentence the other reads use, no longer the row.

### Behavior

- A rename that merges into an existing account is refused when the account that moves is the caller's own. The merge carried the caller's API keys onto the target row, so any keyholder could become a colleague and read their private journal. A teammate can still run the merge.
- Memory recall with no person named returns the team-wide memories only. An unattended turn and a tool call with no requester read every person's targeted memories before.
- Deactivating an agent settles its queued wake as `refused` with reason `agent_unavailable`, the unattended runner refuses a deactivated identity, and a delegation to a deactivated agent is refused with the instruction to reactivate it first.
- A capture whose text carries a command-wrapped feedback line (`/x fb:`) is refused by the service, the same shape the capture route already skips policy for. A multi-line capture with such a line skipped both the policy check and the feedback branch and was captured as a task.

### Operations

- The restore fence in `deploy/k8s/README.md` step 7 now removes the sealed bearer token and OAuth sign-in from every restored personal MCP server row. A token revoked or a server deleted after the backup came back usable before. Owners enter the token or sign in again.

## 0.6.2 — 2026-09-17

### Contracts

- Solo chat threads carry `model_id` (migration 031). The rows of `GET /api/chats` return it; empty means the team model. A second message on a thread whose turn is still running answers 503 with `Retry-After: 5` and the sentence "The model session is in use. Wait for the current turn to finish.", no longer the generic database-busy sentence.

### Behavior

- `/model` in a solo chat lists the model menu and names the model the chat runs on. `/model <id>` picks one for that chat, `/model default` returns it to the team model. The composer completes the id.
- The chat composer shows **Stop** while a turn runs. Stop aborts the stream and frees the thread for the next message. After 30 seconds without a reply the working indicator says so and points to Stop. A turn that ended without a reply shows the reason the server gave.

### Operations

- The OpenAI-compatible and Anthropic clients retry a failed request once, not twice. Each retry re-waits the full read timeout on a provider that accepts the request and never answers.

## 0.6.1 — 2026-09-17

### Contracts

- No change to extension API 1.0, `app.extensions`, `app.public`, or the compatibility declarations. A package that loads on core 0.6.0 loads on this release unchanged.

### Behavior

- In a private shared chat, an `@` followed by the first letters of an invited agent narrows the agent chips to the matches. Enter, Tab, or a click completes the picked one. A slug typed in full sends on the first Enter. A sent message shows at once as "Sending…" and the box empties before the server answers. If the send fails, the draft comes back with the error.
- While a shared-chat agent answers, the room shows the reply streamed so far under the agent's status line, refreshed by the 2-second poll. The stored message stays the only durable reply.
- In solo chat, a specialist's nameplate joins the first word of the reply, so the working indicator stays visible through the model wait.
- A stale browser session cookie reaches the sign-in gate once. The nav no longer polls attention while the session is locked, which removed a sign-out loop.

### Operations

- `SKEIN_OIDC_GROUPS_SOURCE=userinfo` reads the groups claim from the issuer's userinfo endpoint once per access token, with the `sub` checked against the token. `SKEIN_OIDC_USERINFO_URL` overrides the discovery document. The default `token` reads the access token as before.
- Migration 030 adds `chat_agent_runs.partial_text`. It applies at startup.
- The solo-chat stream answers with `Cache-Control: no-cache` and `X-Accel-Buffering: no`, so a buffering edge delivers frames as they are sent.

## 0.6.0 — 2026-09-08

### Contracts

- Core 0.6.0 reaches the previous compatibility ceiling. To load on this core, a package with `maximum_core_exclusive = "0.6.0"` or `maximumCoreExclusive: "0.6.0"` must advance that ceiling to `"0.7.0"`, and a pip bound of `<0.6.0` must widen to `<0.7.0`. Run the extension contracts before changing these declarations. Minimum-core floors and extension API 1.0 remain unchanged.
- Solo-chat history adds `GET /api/chats/{thread_id}/messages/page` with `before=<message id>` and `limit` from 1 to 200, default 50. It returns `{messages, next_before}`. The existing messages endpoint retains its newest-1000 bare array.
- `GET /api/tasks/browse` returns a compact task projection after scope and workplace-policy checks. Its fields are `id`, `title`, `status`, `priority`, `assignee`, `due_date`, `completed_at`, `forge_url`, `visibility`, and `crew_id`. Read `/api/tasks/{id}` for full task details. Extension API 1.0 does not change.

- Browser sign-in now establishes an opaque server session. The browser token endpoint returns identity and CSRF metadata, not provider credentials, and refuses browser refresh tokens. Existing browser credentials are cleared on upgrade.
- Cookie-authenticated browser requests cannot mint permanent API keys. Explicit key entry exchanges the key once; direct bearer clients retain key creation.
- OIDC browser sign-in requires `SKEIN_CREDENTIAL_KEY` in the deployment Secret, HTTPS, and same-site frontend/API origins with explicit CORS. Browser-session rows are excluded from recovery archives.

- CI webhook writes require a personal API key or deployment sign-in. A shared token with a self-asserted name is refused.
- The roster omits other people's saved themes and internal identity ownership fields. Growth interests remain shared staffing context.

### Behavior

- My Day preserves keyboard focus after action refreshes, including when background refreshes overlap, without moving focus from another control.
- Keyless chat accepts attached-file turns without a server error. It states that files remain unread and processes only the person's message through the existing command and review gates. File content cannot become a command or capture request.
- Task Peek restores focus only after a panel has opened. Initial page load keeps the normal keyboard order and skip link. A history-backed close waits for URL synchronization before restoring focus, so native fragments stay in history without taking focus from the opener. Search remains the fallback when the opener is gone.
- Slack commands and outbound delivery are removed, along with the Tavily and Exa research tools. In-app notifications remain available at once. RSS and local helper tools remain supported.
- Signed Gitea replay checks use the repository namespace, native event type, and exact raw payload SHA-256, even under a new delivery UUID. A matching stored fingerprint prevents replay from overwriting a later human task edit. Suppressed new UUIDs receive permanent alias receipts with `task_id = NULL` and remain bound against conflicting bytes or event types, including unsupported events. Matching compatibility headers remain accepted. This conservative rule also suppresses genuinely new byte-identical occurrences. Changed bytes remain eligible for existing policy and task-state checks. Initial headerless requests and pre-028 receipts lack fingerprint history for new-UUID replay protection. This is scoped signed-byte replay protection, not an unconditional exactly-once guarantee.
- Readiness includes bounded database and application-pool checks. Liveness remains available during database loss or request-worker saturation. Transient database disconnects return safe JSON 503 responses with Retry-After at authentication and route boundaries. Callers must check a write's outcome before retrying it. Filesystem permission failures return safe server errors without exposing internal paths; policy refusals remain 403.

- Agent wakes, shared-chat turns, and scheduled firings use a unique token per execution claim. The heartbeat renews only registered live acquisitions before expiry. Execution-bound transactions fence local tool and session writes as well as completion. A stale worker cannot release a successor's claim. Unknown external outcomes still require reconciliation.
- Scheduled work holds job-wide exclusion separately from firing receipts. Only explicitly retry-safe jobs automatically release failed firings for retry. Other jobs preserve evidence of an uncertain outcome. The heartbeat starts before catch-up work. Cron keys progress across repeated daylight-saving hours, and acquisition failures do not abort startup. Failed advisory-lock cleanup discards the connection.
- MCP OAuth sign-in claims the numeric server before discovery. Codes are sealed in transient flow rows, and all callbacks use the same one-shot expiry checks. Concurrent grants cannot mix client and token state. Flow rows expire independently of another sign-in and are excluded from recovery dumps. Per-agent turns and chat in-flight markers use token-owned claims. A running agent turn polls the pause row.
- The five deployment-wide caps count in a `rate_hits` table as fixed windows across processes. Async ingress offloads these database checks and bounds lock waits. Per-person caps stay process-local.
- The anchor-log append holds a database lock across processes. Forge delivery receipts are exempt from generic job retention. New transient tables have explicit retention decisions.

- Saved solo chats load recent messages first. Load older messages adds earlier pages to the current transcript without replacing existing message nodes. Thread and identity changes discard loaded pages and obsolete responses. A field-guide card ties only after a successful, nonempty older-page read.
- Browse task editing changes title, assignee, and due date only. Task Peek separately loads and displays the full description without adding description editing.
- MCP task pages scan past the former 500-row window. Offsets and limits count readable tasks after policy checks, and linked records retain their access checks.
- Task proposals reuse service validation for fixed fields before storage. Invalid legacy task proposals become rejected at approval instead of returning to the queue. Relationships and permissions remain apply-time checks.

- Browser sessions have an eight-hour absolute lifetime, server-side refresh, explicit logout, and identity-bound request handling across tabs. Private mounted state and stale responses do not cross account changes. A Browser sign-in card joins the field guide.
- Reviewed remote MCP execution reads the SDK result envelope correctly. Successful Context7 documentation calls now report completion instead of failure.

- Forgetting a person-addressed memory requires that person's authority, including agent proposals applied through review. Deletion records do not copy memory content into shared activity.
- Concurrent engagement creation and renaming cannot bypass the duplicate-name check. Promise edits and settlement share a service-level row lock, and concurrent key requests produce one pending notification.
- NUL characters in database-bound text produce an input error rather than a server fault.
- Keyboard actions restore focus after rows or editors close. Chat completion and successful actions use status announcements. Forms keep visible labels, and the sticky header leaves room for focused controls.

- A theme change crossfades through the View Transitions API where the browser has it. The hue sliders and the Settings page apply at once, and reduced motion turns the fade off.
- The ⌘K box offers theme commands while you type: a mode, a theme pack by name, or `Colorway: next`. Enter still searches. A Themes card joins the field guide.

### Operations

- The backend requires MCP `>=2.1.1,<2.2`, Strands Agents SDK `>=1.55.1`, and HTTPX2 `>=2.9` at runtime. HTTPX `>=0.28.1,<1` remains the TLS context builder to preserve existing certificate trust. MCP 1.x is no longer supported. Rebuild the backend image and refresh workplace Python locks against the matching core wheel.
- The frontend pins Next.js and its ESLint configuration to 16.3.4 and Sharp to 0.35.4 to address image-processing and Windows server security advisories. Workplace roots must use the same Next.js pin and Sharp override.
- Database pool creation and shutdown are serialized, so concurrent first requests cannot leave an orphan pool. Browser-authentication tests reuse the existing application lifespan and stop maintenance before database teardown.
- Migration 028 adds generic namespaced forge delivery receipts. Migration 029 adds a nonunique repository/event/payload index that permits existing duplicate fingerprints and new alias receipts. Receipt creation and the corresponding policy-checked task mutation commit together. Receipt retention stays permanent. Existing execution fencing, scheduling, and backup/restore behavior remain independent of optional integrations.
- Local Docker fault and PostgreSQL recovery contract scripts use isolated resources and generated test credentials. The guarded OpenShift validation procedure requires an explicit disposable namespace. No production replica default changes. See deploy/k8s/README.md for required cluster and recovery checks.

- Migrations 022 to 025 add lease columns to `agent_wakeups`, `chat_agent_runs`, and `job_runs`, the `mcp_oauth_flows` and `rate_hits` tables, and `mcp_servers.oauth_signin_required`. After a restart, a turn the old process held stays `running` for up to two minutes before the sweep marks it `lease_expired`.
- Migrations 026 and 027 add per-acquisition execution tokens and replace legacy OAuth transit rows with server-bound, sealed claims. Existing transient sign-ins must restart after upgrade.
- The supported deployment stays one replica with Recreate. If more than one replica is ever intended, create the data PVC as `ReadWriteMany` from the start. `backend/tests/test_two_processes.py` runs the fences against a second real process.

- Coordinated manual dumps exclude `public.browser_sessions` and `public.mcp_oauth_flows` data. Before any application process starts after recovery, the runbook SQL clears restored sessions and invalidates API keys. It marks pending or running shared-chat agent requests and delegation wakes as `completion_unknown`, clears execution flags and follow-up wake requests, and preserves history. Run the guarded SQL for external full copies too. Keep ingress closed until reconciliation finishes. `SKEIN_SCHEDULER=0` alone does not stop shared-chat recovery.
- Upgrade instructions require each image tag and its matching reviewed digest. Render the private overlay and check both backend and environment-specific frontend references before sync. A tag-only edit retains the old image bytes.

- Atomic REST policy checks and transaction lifecycle work run off the event loop. Contended database locks have a bounded wait and return a retryable response instead of blocking the API.
- Failed scheduled backups can retry on the same day. Weekly-plan and stale-work claims commit with their database effects.
- Personal MCP discovery starts in a bounded background pool. Registration and chat do not wait for remote startup, and deleted or renamed connections cannot return through a late discovery result.
- Web pages refuse framing and MIME-type guessing. Cross-origin requests omit the referrer.

- The npm packages publish to public npmjs.com through OIDC Trusted Publishing, with provenance, instead of GitHub Packages. `@miloctl/skein-extension-api` and `@miloctl/skein-frontend-host` install with no token, and the `.npmrc` scope routing and `read:packages` PAT are gone. The first version of each package is published by hand once, then the workflow publishes (RELEASING.md).

## 0.5.0 — 2026-09-02

### Contracts

- This release reaches the compatibility ceiling of the previous line. A package that declared `maximum_core_exclusive = "0.5.0"` must declare `"0.6.0"` to load on this core, and a pip bound of `<0.5.0` must widen to `<0.6.0`. A package that does not move stops loading with `supports core versions from X up to but not including Y`. Nothing else in a package needs an edit for the version itself.
- The in-API MCP endpoint composes the same modules as the REST API. A private package's policy, identity, and tool contributions apply to MCP calls over HTTP without `SKEIN_MCP_MODULES`.
- MCP policy actions add `skein.mcp.week.read` and `skein.mcp.memories.read`. A workplace policy that enumerates MCP actions must include them.
- A governed remote MCP server can omit its `tools` block. Skein then derives effect and risk from each tool's own MCP annotations, and every write from that server needs a human review.
- A human identity cannot take a name that ends in `-mcp`. That suffix names the agent identity a person's remote MCP calls act through. Existing rows keep resolving.

### Behavior

- Skein serves its own MCP tools over HTTP at `/api/mcp-server`. The endpoint sits under the perimeter and resolves each caller from a personal API key or deployment sign-in. A self-asserted `X-User` and an agent-owned key are refused.
- A remote MCP caller acts as the agent `<name>-mcp`, reserved on first use. Writes record origin `agent`, actor `<name>-mcp`, and `requested_by` the person. Each person's MCP agent earns authority on its own matrix row.
- The MCP server adds `update_task`, `ask_question`, `answer_question`, `resolve_blocker`, `recall_memories`, and `week`. It offers 23 tools. Every tool declares its MCP annotations.
- `list_tasks` and `search_workspace` take a limit. An MCP result above 256 KiB and a request body above 1 MiB are refused. An unexpected tool error answers a fixed sentence, and the server log records the exception class.
- A person registers their own remote MCP servers in Settings → Connections. Those tools join only the chat turns that person drives. A flock member, a shared chat, the unattended runner, and the MCP actor never receive them.
- A personal MCP server signs in with OAuth 2.1 or a bearer token. Skein seals the tokens and the registered client under `SKEIN_CREDENTIAL_KEY`. A database backup carries ciphertext only.
- Every write from a personal MCP server opens a review. A read opens one review the first time that server, tool, and version runs, then it runs under policy.
- A registered MCP server URL cannot name loopback, link-local, multicast, or reserved addresses. Skein checks the URL again at every connect and does not follow redirects. A person registers up to 8 servers, and one server contributes up to 32 tools.
- `GET /api/agents/trust` shows a person's `-mcp` agent to that person and to administrators only. Its rejection streak is person-level data.
- A rename carries the person's MCP agent, its authority, and its trust history. Deactivation deactivates that agent and deletes the person's registered servers.

### Operations

- `SKEIN_CREDENTIAL_KEY` seals personal MCP credentials and belongs in the deployment Secret. Generate it with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. If the key is absent, a personal server accepts no token.
- Core migration 019 adds the `mcp_servers` table. Core migration 020 adds its OAuth columns.
- An OAuth sign-in for a personal MCP server lives in the process that started it. Run one backend replica, or a sign-in that returns to a different replica fails.
- Claude Code reaches `/api/mcp-server` through a `.mcp.json` entry that reads `SKEIN_API_KEY`, the variable the CLI already uses. The key stays out of the shell history.
- `deploy/k8s/overlays/example-prod/backend-egress.yaml` models the backend egress allowlist. Add one row for each service the deployment reaches before you apply it.
- MCP tool bodies share the backend's sync thread pool with the REST handlers. Every MCP call, reads included, draws on the `mcp` rate bucket.
- Release finalization signs the immutable tag with a fine-grained `RELEASE_TAG_TOKEN` from the protected environment. The token needs Contents write and Workflows write. A preflight names the missing secret before any checkout.
- Release publication and finalization flatten downloaded artifacts. A single-ID download no longer lands under a nested directory the publishers cannot see.
- The installed frontend contract proves the root-owned override refusals. It removes each override and corrupts each lock entry, and it requires the refusal both times.

## 0.4.0 — 2026-08-30

### Contracts

- A retryable `PublicError` with status 503 includes `Retry-After: 60`. Scheduled extension jobs preserve the declared machine code and retryable value. They do not log chained adapter details.
- The workplace template owns an unpublished local contract. It accepts current source or exact prebuilt Skein artifacts without a source checkout at runtime.
- Artifact-only consumer contracts require one shared `SHA256SUMS` file for the exact Skein wheel and npm tarballs.
- The reference directory resolver now fails closed. A private package must supply its authoritative server-side directory adapter before production approval revalidation.
- The backend requires Strands Agents 1.50 or later for per-turn limits and context offload.
- Persona frontmatter accepts optional `flock: false`. Flock validation reads the resolved overlay definition, and omitted means flock-eligible.
- Task dependencies accept `waiting_on=question:<id>`. An answered question satisfies the edge through the existing shared work service.

### Behavior

- A temporary OIDC refresh failure keeps the OIDC session. The browser does not send a stored key or shared token. The request stays bound to the signed-in identity.
- OIDC issuer and endpoint URLs require HTTPS. Skein permits literal loopback HTTP only for local tests. Server-side redirects cannot change the origin.
- OIDC discovery, token, error, and signing-key responses have a 256 KiB body limit and a 5-second total deadline. Four fixed provider slots bound timed-out reads.
- OIDC requires `sub` and binds each human to the verified `(iss, sub)` pair. Username changes cannot transfer private data to a different subject. Overlength and control-character usernames are refused.
- Provider failures write only the exception class to platform logs. Provider response text and tracebacks stay out of logs that bypass Skein content access rules.
- The reference workplace adapter bounds response size and item count. It rejects malformed data, redirects, control characters, duplicate IDs, and unsupported status values.
- The reference status outbox uses leases, per-item order, retry delays, dead-letter rows, and count and wall-clock drain limits. Task events use the same queue.
- The reference dashboard shows an unavailable state when metrics fail. The **Try again** control loads the metrics without a page reload.
- A browser navigation can cancel an in-flight request body. Skein classifies that disconnect as a 400 instead of logging an unhandled server error.
- Frontend extension policy reloads when the stored OIDC session, API key, or trusted-header identity changes. A sign-in cannot leave permitted extension UI hidden.
- Zero-adoption findings start after the field-guide grace period. The final grace day no longer creates a finding.
- The stock bench grows to 23 personas and 5 flocks. Deterministic routing evaluations and pressure traces keep adjacent specialist descriptions distinct.
- Unattended turns have per-invocation turn and token limits. An administrator can pause all unattended runs, queued work stays pending, and cancelled turns retain a durable reason.
- Oversized tool results move to session-scoped offload storage before chat persistence. A storage backstop truncates uncovered tool results above 128 KiB.
- The hourly embedding reconcile job repairs missing vectors after provider recovery. It does not block startup, and partial or complete failures make job health red.
- The findings engine adds `task_abandoned` for open work with multiple contributions followed by silence. Its receipt carries counts, never contributor names.
- Chat distinguishes transient provider load from configuration faults by safe status and exception class. The orchestrator marks external and model-produced text as content, not instructions.
- Governed MCP tools validate the nested result against `output_schema`. MCP and embedding failures log exception classes without remote response text or tracebacks.

### Operations

- The provider for OIDC browser tests handles concurrent connections from the browser and the backend. Browser preconnects no longer block the token exchange.
- Kubernetes readiness uses `/ready` and returns 503 when authentication configuration is invalid. Startup and liveness remain on `/health`.
- Atlas migration 5 adds ordered status leases and preserves pending migration 4 rows. The migration applies during normal extension composition.
- Core, PostgreSQL, and workplace base images use immutable digests. A registry tag cannot change executed image bytes without a source diff.
- OIDC browser tests disable embeddings and run the managed servers as direct child processes. Local runs can prestart the stack with `PW_REUSE=1`.
- The consumer-owned contract uses a run-specific non-superuser database role. Build tools and tested application code never receive the administrator credential.
- Existing roster users need an explicit `python -m app.bind_oidc '<subject>=<user>'` binding before an OIDC cutover. New users bind on first sign-in.
- Application workloads disable default service-account tokens. PostgreSQL bootstrap also removes `REPLICATION` from a pre-existing app role.
- Package installers, frontend builds, identity providers, and browser tests do not receive the database administrator credential. Contract setup and test backends receive it only when they need database access. CI PostgreSQL services and helper images use immutable digests.
- Image publication requires a clean commit at the trusted remote's annotated release tag. It validates every target tag before any build or push.
- The installed frontend contract uses digest-pinned Node 22 and dynamic loopback ports. Ambient browser tokens cannot enter the build or managed runtime.
- Core migration 017 extends task dependency types with questions. Core migration 018 adds session-scoped context-offload storage.
- New reserved content identities are `security-engineer`, `test-engineer`, `requirements-interviewer`, `workflow-architect`, `release-captain`, `research-synthesist`, `plan-reviewer`, `codebase-archaeologist`, `migration-steward`, `org-psychologist`, `technical-writer`, `experiment-tracker`, `judgment`, `people`, and `shiproom`. Before upgrade, run `python -m app.identity_audit`. If a name conflicts, rename the incoming stock file and its references. Do not rename an established human or private machine identity.
- `SKEIN_AGENT_RUN_TURNS` and `SKEIN_AGENT_RUN_TOKENS` bound unattended SDK loops. `SKEIN_OFFLOAD_RESULT_TOKENS` and `SKEIN_OFFLOAD_PREVIEW_TOKENS` control chat result offload.
- The durable `agent_automation` setting pauses and resumes unattended runs without changing authority or review policy.
- The lint gate runs the Simplified Technical English self-test, then checks field-guide `how:` instructions. Service wording remains a warning-only ring.

## 0.3.2 — 2026-08-28

This patch aligns the published frontend host with the tested workplace package boundary. Existing `0.3.x` extensions need no compatibility change.

### Contracts

- No `app.extensions`, `app.public`, or `@miloctl/skein-extension-api` signature changed. Extension API stays 1.0.
- Atlas stays at `2.0.0` with `skein-agents>=0.3.0,<0.4.0`. Version `0.3.2` remains inside that range.

### Behavior

- Browse create controls now have stable form names. Browser autofill no longer reports empty form metadata for these controls.

### Operations

- Workplace tests use a hash-locked dependency closure constrained by the production lock. Current-package tests start in a fresh installed environment.
- The package-built browser contract uses signed OIDC identities for denied, integration, and manager paths. It also completes one core write.
- Contract cleanup is bounded and uses explicit disposable database names. Ambient Atlas credentials cannot enter the browser contract.
- Partial publication retries validate the original run and reuse its tested artifact. PyPI skips an identical wheel and refuses different bytes.
- Release preparation now uses one version input to update synchronized packages, exact artifacts, locks, documentation, and the release marker.
- A protected finalization workflow verifies registry bytes from the original artifact before it creates an annotated release tag.

## 0.3.0 — 2026-08-27

Two deployment defaults changed, and both are visible to a private package.
This is a MINOR release for exactly that reason: a package declaring
`skein>=0.2.0,<0.3.0` will not resolve 0.3.0, which is the cap doing its job.
Widen to `<0.4.0` and re-read the two Behavior entries below before you do.

### Contracts

- No `app.extensions`, `app.public` or `@miloctl/skein-extension-api` signature
  changed, and extension API stays 1.0. The only contract action is the
  compatibility range: a package that declared `maximum_core_exclusive =
  "0.3.0"` must declare `"0.4.0"` to load on this core. Nothing else in a
  package needs an edit for the version itself.
- The Python distribution is now `skein-agents` and publishes to public PyPI through GitHub OIDC. The public imports remain
  `app.extensions`, `app.public`, and `app.main.create_app`. Public PyPI owns
  the unrelated `skein` name, so current packages must not depend on it.
  The import boundary now scans Python stubs and permits only `create_app`
  from `app.main`. Bare, wildcard, private, and aliased private imports fail.
- The complete frontend host now publishes as the private GitHub package `@miloctl/skein-frontend-host`. A workplace project installs it with the private `@miloctl/skein-extension-api` package and runs
  `skein-frontend-build` after it compiles its private extension.
- Atlas 2.0 declares `skein-agents>=0.3.0,<0.4.0`. Atlas 1.x remains the
  pinned fixture for the explicit 0.2.3 package transition.

### Behavior

- **The review gate is ON by default.** `SKEIN_AGENT_REVIEW` defaults to 1;
  it defaulted to 0 through 0.2.x. A mutating agent write — including a
  governed extension tool at the `review` authority level — becomes a
  proposal a human approves, so `execute_tool` returns `review_required`
  carrying a `review_id` where it previously returned `completed` with the
  write already done. An extension whose job or test asserts `completed`
  must either assert both outcomes or grant that (agent, entity) pair
  `autonomous` in the authority matrix. `SKEIN_AGENT_REVIEW=0` restores the
  0.2.x behavior deployment-wide. This is the one trust boundary that
  shipped open while every other one failed closed, and it is what lets
  trust accrue at all: with the gate off no verdict is ever recorded, so no
  agent can earn autonomy.
- **The authentication mode fails closed.** `SKEIN_AUTH_MODE` defaults to
  `api-key`; it defaulted to `trusted-header` through 0.2.x. A deployment
  that set nothing authenticated every caller by a self-asserted `X-User`
  header and now refuses them. Set `SKEIN_AUTH_MODE=trusted-header`
  explicitly to keep the old posture, or supply real credentials.

- `fold_identity` now normalizes before it strips and composes after. The
  old order was not idempotent: a compatibility character that decomposes
  into whitespace ("¯" becomes space plus combining macron) kept the
  space, and a stripped zero-width joiner left a base and its mark
  uncomposed. Two spellings that render the same could fold to two
  identities. Property tests (`tests/test_identity_fold.py`) hold the fold
  to idempotence, case, compatibility forms, and invisible characters. A
  roster with names that only now fold equal surfaces them through the
  existing conflict quarantine, `python -m app.identity_audit`.

### Operations

- CI gates backend line coverage at 90% and prints frontend coverage.
  Local `pytest` is unchanged. `RELEASING.md` now records the release
  procedure. `./scripts/mutation-test.sh <module>` runs on-demand mutation
  testing over `app/services/`.
- The contract rehearsals pin their own `SKEIN_*` environment
  (`scripts/lib/hermetic-env.sh`) and take only `SKEIN_DATABASE_URL` from
  the caller, so a rehearsal answers the same way on a developer machine
  and in CI. Their steps are files under `scripts/contract/` rather than
  shell heredocs, so ruff checks them and a failure names a real file and
  line.
- `reference-images-contract.sh` builds and starts the core and Atlas images.
  It starts a PostgreSQL container on an isolated network and points both
  backend images at it. Skein requires PostgreSQL in 0.2.3 and later. The old
  gate supplied no server, so the image could not start. The contract does
  not use the caller's `SKEIN_DATABASE_URL`. That URL can point to a developer
  database, where image startup can run core migrations. Each readiness loop
  prints the container log when the service does not start.
- The Atlas reference repository owns one npm lock and one combined Python
  production lock. Its final images install exact package artifacts and do
  not inherit Skein application images.
- The old frontend-host archive and host-image contracts are removed. The
  clean frontend contract installs npm tarballs and starts the completed
  standalone server.
- A 0.2.x deployment must uninstall the old `skein` and Atlas 1.x
  distributions before it installs `skein-agents` and Atlas 2.0.

## 0.2.2 — 2026-08-13

The second tagged release, and the first that proves an upgrade: the Atlas
reference extension passes every contract unchanged across the 0.2.1 to 0.2.2
hop, and a database built by 0.2.1 reaches the same schema as a fresh 0.2.2
build with its activity chain intact.

### Contracts

- `WorkItems` gains blocker commands: `create_blocker`, `update_blocker`, and
  `get_blocker`, with `CreateBlockerCommand`, `UpdateBlockerCommand`, and
  `BlockerView`. An impediment from an external system is a blocker in
  Skein's vocabulary, and the facade previously offered only tasks, so an
  integration filed it as the wrong entity. An update resolves a blocker or
  corrects its wording; escalation stays with the scheduled sweep that owns
  the clock. Needs `minimum_core = "0.2.2"`.
- `WorkItems` gains promise commands: `create_promise`, `update_promise`, and
  `get_promise`, with `CreatePromiseCommand`, `UpdatePromiseCommand`, and
  `PromiseView`. A promise carries a direction, an audience, and a settlement
  status that no other entity has. It settles once. Needs
  `minimum_core = "0.2.2"`.
- The event catalog gains `skein.promise.created` and `skein.promise.updated`.
  Both need `minimum_core = "0.2.2"` in a subscribing package.
- The event catalog gains `skein.blocker.created` and `skein.blocker.updated`,
  emitted from the shared blocker write path so every caller produces them.
  Composition still refuses a subscription outside the catalog.
- A public work command that policy holds for review is now durable. A rule
  that returns `review` on any public work command stores the command, and `PublicError.review_id` names the proposal a human approves;
  approval runs the exact saved command under a new grant with the integration
  still recorded as its author. Before this a route answered `409` and a job
  answered `POLICY_REVIEW_UNSUPPORTED` and neither left anything to approve,
  so an unattended integration could be stopped but never asked. A rule on the
  operation action itself still returns `POLICY_REVIEW_UNSUPPORTED`: there is
  no request to resume. Needs `minimum_core = "0.2.2"` to read `review_id`.
- `ExtensionStore(path, include_in_backup=True)` puts an extension-owned
  database into the daily core backup. Previously nothing in core copied it,
  so every private package's data survived on deployment-side discipline
  alone. Skein does not mirror an extension store off the box, and retention
  stays extension-owned. Needs `minimum_core = "0.2.2"` only to set the flag;
  a store from an older package is backed up by the default.
- `app.extensions.assert_import_boundary` raises when a private package
  imports a Skein module outside `app.extensions`, `app.public`, and
  `app.main`. It reads source, so a dynamic import evades it: this is a drift
  check, not a security boundary. Use it for the import-boundary test that
  `docs/EXTENSIONS.md` requires. Needs `minimum_core = "0.2.2"`.

### Behavior

- A public read applies the caller's own visibility filter on every entity.
  `get_promise` read any row, so an extension route could return a teammate's
  private promise. Blocker and task reads were already filtered.
- An idempotency key names one kind of record. The receipt stored the kind and
  nothing compared it, so a key reused across two commands replayed one entity
  as another and returned a row the caller never wrote. A reused key now
  answers `IDEMPOTENCY_KEY_REUSED`.
- A linked write carries the project class of the row it attaches to. A
  blocker and a promise read that class from the caller instead, so a rule
  keyed on it governed task writes into a regulated engagement and skipped
  the other two.
- An approval releases the one action it answered. A command that meets two
  independent review rules is held again for the second, rather than riding
  the first approval through a gate whose approvers were never computed.
- The review queue carries a bounded preview of the command it is holding, so
  a status change bundled into a create is visible on the approve screen.
- A held command whose target vanished before the verdict settles as rejected
  instead of returning to the queue on every attempt.
- A domain write carries the declared effect and risk of the contribution
  performing it. A route, job, tool, event subscriber, or workflow action
  declares `effect` and `risk`, and the `work.task.*` decision previously
  reached the policy engine as `effect="none"`, `risk="low"` whatever the
  contribution said, so a workplace rule keyed on risk never fired on the
  write it meant to gate. Rules keyed on project type are unaffected.
- An extension route's work grant ends with its response. A route declares no
  deadline, so a thread the handler started kept writing core rows under the
  route's provenance after the response and after shutdown. A later call now
  returns `EXECUTION_CONTEXT_CLOSED`. Use a `JobContribution` for background
  work.
- `ExtensionStore` connections refuse `ATTACH`. The configured-path check
  sees only the file the store opened, so one `ATTACH` statement reached a
  core database from a connection that had already passed it. Both checks
  prevent accidents and neither is an isolation boundary.
- Composition logs a warning when no installed `skein` distribution names the
  core version and the source fallback is used instead. Every module
  compatibility range is checked against that number, so a guessed one can
  refuse a valid private package.

### Operations

- Core migration 021 widens the reviewed-invocation kinds so a held public
  command can be stored. It rebuilds `extension_review_invocations` and copies
  every existing row.
- `SKEIN_REVIEW_SEPARATION=1` refuses an approver who is the person a
  proposal came from, so an approval costs a second pair of eyes without a
  policy rule. Off by default. A policy rule that names `approver_groups`
  composes with it and both checks must pass. Rejection is unchanged: a rule
  that traps a proposal in the queue is worse than one person declining it.

## 0.2.1 — 2026-08-13

First release after the workplace extension boundary. Extension API 1.0 for
both the backend and the frontend.

### Contracts

- A tool contribution can declare `error_codes`, and Skein preserves a
  declared code from `PublicError` instead of returning the generic
  `tool_error`. Needs `minimum_core = "0.2.1"`.
- A reviewed tool that writes through the supplied `WorkItems` service runs
  its command in the reviewer's transaction. Needs `minimum_core = "0.2.1"`.
  A tool that performs no reviewed local write keeps a `0.2.0` floor.
- A workflow action that uses `WorkItems` after review has the same
  requirement. An external-only action keeps a `0.2.0` floor.
- `ExtensionStore.transaction` supplies an explicit SQLite transaction. Needs
  `minimum_core = "0.2.1"`. A package with a `0.2.0` floor uses `connect`.

### Behavior

- Core REST mutations, agent tools, classified MCP tools, workflow steps,
  contributed routes, scheduled jobs, and frontend capability checks all
  evaluate one composed policy engine. A workplace permit cannot remove a
  core denial.
- Core migrations 018 through 020 record durable identity ownership,
  notification sources, and creation-time policy context.

### Operations

- `SKEIN_MCP_MODULES` gives the standalone MCP process the same module
  composition the API process uses. Without it that process composes core
  only, which leaves two policy boundaries in one deployment.
- `python -m app.identity_audit` reports and repairs roster identity
  conflicts. Run the documented claim commands before the first restart on
  this release.

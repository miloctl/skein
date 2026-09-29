# Document editing and revisions

Confirmed 2026-09-28 with the repository owner.

- **Outcome:** People change a document in place, and no saved text is lost. Every save,
  agent edit and restore is a numbered revision. Anyone who can read a document can open
  a revision, see what it changed and who made it, and restore it. A person can also
  write a new document and choose who can see it.
- **User:** The strike team's people, who write, edit and restore, and its agents, who
  propose edits through the review gate.
- **Why now:** Agents write design docs and runbooks (`tools/files.py` →
  `services/documents.py`). A person who finds a wrong section must ask an agent to fix
  it, because every artifact route is a read (`GET /api/artifacts*`). Each agent edit also
  deletes the text it replaced (`artifact_files.publish(..., old=path)` calls
  `delete_after_commit`), so a bad edit has no undo short of a volume restore.
- **Success:** Raj saves a fix with no agent involved. Ana restores revision 4 with one
  select, and the bad edit stays in history. Each revision names who made it, to readers
  of that document only. Of two people saving at once, the second gets a 409 and keeps the
  unsaved text. An agent proposal filed against revision 5 after revision 6 exists is
  refused at approval, says why, and does not count against the agent. Mira writes a
  runbook with **New document** at "only you": nobody else can open it or its history, her
  Your data download carries every revision, and the offboarding erase deletes them all.
- **Constraint:** No editor library, no CRDT, no live co-editing, no lock held across
  requests. Only `kind = 'document'` is editable. `SKEIN_MODEL_PROVIDER=mock` works end
  to end. No new gated entity and no new agent tool. Revision bodies never enter
  `activity.detail`.
- **Out of scope:** Editing generated reports and uploads. Presence, merge help, per-line
  blame, rename, delete, notifications on edit, MCP and CLI document commands, sharing a
  document wider, and a person's document filed under an engagement. Each is a row in the
  cut table of `docs/ROADMAP.md` with its trigger (S3, S7).

The 2026-08-21 posture note in `docs/ROADMAP.md` freezes new portfolio surfaces, and its
2026-09-28 entry approves this feature among five. It is team content, not portfolio. S4
and S5 also serve the agent loop the posture funds: an agent edit names its base revision,
a stale one settles without counting against the agent, and the reviewer sees a real diff.

## What exists before this work (2026-09-28)

| Part | Where | Note |
|---|---|---|
| `artifacts` table | `001_baseline.sql`, `003`, `010` | `kind`, `title`, `path`, `created_by`, `visibility`, `crew_id`, `derived_from`, `size`, `content_sha256`. No history. The last migration is `044_event_links.sql` (the calendar's). |
| Document service | `services/documents.py` | `create_document` hard-codes `workspace` and keeps the `_check_source` laundering guard and the 512 KB cap. `edit_document` is one exact `str_replace` under `_document_row` (`FOR UPDATE`, workspace only, `kind = 'document'` only). The new file replaces the old one, which is deleted after commit. |
| Agent tools | `tools/files.py` | `read_artifact` (reads at `scope.NOBODY`), and `create_document` and `edit_document` through `tools/_gate.py`. Neither write passes `origin`, so the service default `"human"` applies. Every other tool passes `origin="agent"` (`tools/collab.py`). |
| Gate registries | `review._registry`, `_DIFF_TABLES`, `_TARGET_TABLE`, `_gate._FAMILY`, `policy_context._TABLES`, `lexicon`, `notifications`, `activity.VERBS` | `document` and `document_edit` are in each. |
| REST | `routes/api.py` | `GET /api/artifacts`, `/api/artifacts/page`, `/api/artifacts/{artifact_id}`. No write route. |
| UI | `app/artifacts/page.tsx` (Work → Reports) | List, body through `ArtifactMarkdown`, "Filed by", Copy and Download Markdown. `KIND_LABEL.document` is "Agent document". |
| Reference parser | `services/refs.py` | The calendar added `refs(text, quoted=False)` for text a person wrote (the agenda uses it). `handoff.read_artifact` still parses a document with the default, where an apostrophe blanks a span. |
| Review diff | `review.change_diff` | For `document_edit`, `current` is `{"old": None, "new": None}`: `artifacts` has no such columns. |
| Search | `services/search.py` | Documents are not indexed. `_ENTITY_TABLE` has no artifact entry. |
| Your data, erasure | `my_data.py`, `erasure.py` | Private artifacts other than uploads are counted (`erasure.holdings`), listed, deleted and erased with their file. The export carries the row without its path and without its body. |
| Field guide | `fieldguide/knots.yaml` | `agent_document` (Zeppelin Bend) ties when a person opens a document. 69 cards, 68 tieable. |

## Decisions

Each decision names the alternative it replaced, so the next reader does not make it a
second time.

### D1. Only `kind = 'document'` is editable

`digest`, `readout`, `handoff`, `ritual` and `plan-snapshot` are generated, and each
generator rewrites its own row on its next run, so a hand edit is lost without notice. An
`upload` stays its person's own file (`_document_row` refuses it, and the FEATURES "Agent
documents" row records why).

### D2. Revisions are database rows, not files, and every one is kept (owner)

Rows are in the daily dump and the `SKEIN_BACKUP_MIRROR` `public` dump, so history
survives a lost artifact volume. `ON DELETE CASCADE` from `artifacts` covers every delete
path, and the transaction does the rollback. Files need an unlink per revision on every
delete path and a `publish` plus `on_rollback` per file. No cap and no pruning: revisit
when a document passes 200 revisions or the table passes 5% of the dump (cut-table row).

### D3. The file stays as the published head. The head row decides every write (owner)

`handoff.read_artifact` with its `content_sha256` check, the agent `read_artifact` tool and
the Reports page keep reading the file. The head row and the file are written in one
transaction with one SHA-256. A write reads the head body from the row, never the file, so
the next write republishes a changed file, and `read_artifact` refuses the mismatch until
then. Retire the file when a recovery drill finds the volume and the dump out of step.

### D4. One writer, a compare-and-swap on the base revision, the document row first

- `_publish_revision` is the only insert into `document_revisions`. It publishes the file
  (`artifact_files.publish` deletes the previous one after commit), updates
  `artifacts.path/size/content_sha256`, inserts `head + 1`, and logs.
- Every writer takes `SELECT ... FOR UPDATE` on the `artifacts` row before it reads the
  head. Approval takes the same row first: `_approve_change_locked` calls
  `policy_context.hold_resource("document_edit", id)`, which locks the `artifacts` row
  before the proposal is claimed. No path locks the document after another row. The
  primary key is the backstop: a writer without the lock fails on the duplicate
  `(artifact_id, revision)` (a 500) instead of overwriting a revision.
- **No-op rule.** If the new body's SHA-256 equals the head's, the call returns the head
  and writes nothing. It runs before the base check, so a double-selected Save, a retry
  after a lost response and a restore of the current text all succeed with no empty
  revision.
- **Base check.** If `base_revision != head`, raise `StaleRevision`: "document #12 changed
  after revision 5, and revision 6 is newer. Read revision 6, then make the change again."
  `base_revision = 0` means no base was stated (a proposal queued before S4) and applies
  against the head.
- **Restore (owner: no confirm dialog).** A restore copies revision n as `head + 1` with
  `restored_from = n`, and the bad revision stays. Restoring the previous head undoes it.
- Prior art: buzz canvases (`buzz`, b6a26556) check the expected revision before
  any side effect and replay an identical head as success, as these rules do.

### D5. A stale agent proposal auto-rejects and does not count (owner)

`class StaleRevision(db.Conflict, db.TerminalReject)`. `db.Conflict` makes the REST save a
409 (`main.py::conflict_error_handler`). `db.TerminalReject` makes `_approve_change_locked`
settle the proposal as rejected with `reviewed_strong = 0`, so `delegation.trust_scores`
does not count it. Without it, the generic handler resets the proposal to pending and it
returns after every approval. The agent inbox (`delegation.agent_inbox`) lists the
rejection and its note, and the agent proposes again. Send-back (the hardening) is for
`task_completion` only (`review._send_back_target`). A routine that edits a document on a
schedule relies on this refusal to keep a person's edit made in between.

### D6. Every reader edits. Agents edit workspace documents only (owner)

`_editable_row` calls `scope.assert_editable("artifacts", row, actor)`, whose docstring
rules that any reader of a row may change it: a private document is its author's, a crew
document its members', a workspace document everyone's. The agent path keeps
`_document_row`, which answers any tier but workspace as absent. S4's filing check gives
the same answer, so an agent proposal on a crew document never files. Without it,
approval settles it as "target vanished" while the document is on the reviewer's screen.

### D7. History names every editor, to readers of that document only (owner)

The alternative was to withhold other people's names, as `provenance._history` does for
tasks. The owner chose names, within these limits (the one gap left is the first residual
risk):

- Authorship of shared text is content. Reports already shows "Filed by `created_by`" on
  every artifact, and a reader who finds a wrong section needs to know whom to ask.
- A name appears only in `GET /api/documents/{id}/revisions` and `.../revisions/{n}`. Both
  run `_require_resource_policy` with the caller's viewer, so a document the caller cannot
  read answers exactly as an absent id does.
- There is no per-person axis. No route, parameter, sort or search takes an author, and no
  query lists revisions across documents. The search index holds title and body only, and
  the portable export leaves the table out. The feed and the raw ledger endpoint keep
  `visible_actor_filter`, so a person's saves never show in another person's feed.

### D8. No new gated entity, no agent restore, no new setting

`document` and `document_edit` keep every gate registry entry they have, and
`test_gate_coverage.py` and the tool counts do not change. An agent that wants old text
back proposes an edit that quotes it. `approve_change` already stamps three entities'
payloads (`requester`, `visibility`, `mint_authorized`) and stamps `change_id` for both
document entities the same way. `MAX_DOCUMENT_BYTES` and `HISTORY_LIMIT` stay constants:
under question 4 of the tier rule, no admin has a standing reason to change them.

### D9. Revisions carry no tier. They inherit the document's by join

`document_revisions` goes in `scope.UNSCOPED`: every read joins its document through
`scope.visible_filter(viewer, "artifacts")`, where a copied tier column needs a cascade on
every tier change. No path changes a document's tier or engagement (`artifacts` is not in
`sharing.SHAREABLE`), so a route's policy decision before the row lock cannot go stale. If
documents become shareable, history widens with them, and the mutating routes must take
`policy_context.hold_resource("artifact", id)` first. `admin.EXCLUDED` gains the table: a
row with no tier lets the portable export carry private bodies. The database backups keep
them.

### D10. People create documents with a tier (owner)

S7 adds **New document** with the visibility picker. The default is "only you" for a
strong identity and the roster for a weak one (`routes/api.py::_personal_default`,
`lib/audience.ts`), the narrowest audience whose author can still open it. Agent documents
stay workspace: the tool sends no tier. A private document stays out of every shared sink:
no search row (`search._is_private` once S6 maps documents), no embedding, no agent read
(`read_artifact` reads the workspace tier), no portable export row, no title in the ledger.

- **Your data export** carries the body of every revision of each private document. Each
  is text only its author can read, and the export carries none of them today.
- **The offboarding erase and the Your data delete** need no new code. `erasure._ORDER`
  and `my_data.delete_private` already delete a private artifact row and its head file,
  and the cascade takes the revisions. S7 pins both with tests.

## Data model

```sql
-- core_migrations/0NN_document_revisions.sql: NN is the next free number when the
-- branch merges (044 is the calendar's).
CREATE TABLE document_revisions (
    artifact_id bigint NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    revision integer NOT NULL CHECK (revision >= 1),
    body text NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    author text NOT NULL,
    origin text NOT NULL CHECK (origin IN ('human', 'agent', 'agent_verified')),
    change_id bigint REFERENCES pending_changes(id) ON DELETE SET NULL,
    restored_from integer,
    created_at text NOT NULL,
    PRIMARY KEY (artifact_id, revision)
);
-- the erase deletes private proposals, and each deleted row otherwise scans this table
CREATE INDEX document_revisions_change_idx ON document_revisions (change_id)
    WHERE change_id IS NOT NULL;
```

The primary key serves "history, newest first" and "head" (`ORDER BY revision DESC
LIMIT 1`). `artifacts` gets no new column.

**Documents from before the migration.** A migration cannot read files, and released
versions have documents with no rows. The first write to one seeds revision 1 under the
same row lock (`_head`) from its verified file, with `artifacts.created_by`, origin `agent`
(agents were the only writers) and `artifacts.created_at`. It holds the body as it stands:
earlier edits were never kept. A file that fails its digest is not seeded
(`handoff.ArtifactUnreadable`). Until then, history answers `{"head": 1, "revisions": []}`.

## Services (`services/documents.py`, the only write path)

```python
class StaleRevision(db.Conflict, db.TerminalReject): ...
HISTORY_LIMIT = 100

def head_revision(artifact_id: int) -> int              # MAX(revision), or 1 with no rows
def create_document(title, content, *, actor="system", origin="human", source_id=0,
                    engagement_id=0, change_id=0, visibility="workspace",
                    crew_id=0) -> dict                   # revision 1. change_id S4, tier S7
def edit_document(artifact_id, old, new, base_revision=0, change_id=0, *,
                  actor="system", origin="human") -> dict  # agent str_replace, n + 1
def save_document(artifact_id: int, content: str, base_revision: int, *, actor: str) -> dict
def restore_revision(artifact_id: int, revision: int, base_revision: int, *,
                     actor: str) -> dict                # revision n copied as a new head
def history(artifact_id: int, viewer: scope.Viewer) -> dict
def read_revision(artifact_id: int, revision: int, viewer: scope.Viewer) -> dict
def unified(before: str, after: str, before_label: str, after_label: str) -> str

def _publish_revision(row, body, *, actor, origin, change_id=0, restored_from=None) -> int
def _head(row) -> dict                                  # seeds revision 1 (Data model)
def _editable_row(artifact_id, actor) -> dict           # FOR UPDATE, assert_editable, kind
def _readable_document(artifact_id, viewer) -> dict     # visible_filter, then kind
```

- `edit_document` keeps its positional order: approval calls `fn(entity_id, **payload,
  actor=..., origin="agent_verified")`. `_check_content` is unchanged.
- The visibility check runs before the kind check, so a hidden artifact of any kind reads
  as absent. A readable non-document is a `ValueError`: "artifact #12 is not a document.
  Only a document can be changed." (or "... has revisions." on a read). An absent revision
  of a readable document is `db.NotFound("no revision 9 of document #12")`.
- `unified` wraps `difflib.unified_diff` over `splitlines(keepends=True)`, `n=3`, per
  request and never stored, with `# ponytail: difflib is quadratic in the worst case and
  the 512 KB cap bounds it. Cap the compared lines if a slow diff shows in traces`.
- `history` returns the newest `HISTORY_LIMIT` rows without bodies (revision, author,
  origin, change_id, restored_from, created_at, size). An older revision still opens by
  number (`# ponytail: 100-row page, add a before= cursor past 100 revisions`).
- Every ledger line is `scope.detail(tier, f"artifact #{id} revision {n}", title)`, never a
  body or a diff. A human save reuses `edit_document`, and a restore logs
  `restore_document`.

## REST routes (`routes/api.py`, next to `get_artifact`)

| Route | Body | Answers |
|---|---|---|
| `PUT /api/documents/{artifact_id}` | `DocumentSave{content, base_revision ≥ 1}` | `{id, revision, unchanged}`. 400, 404, 409, 413, 429 |
| `GET /api/documents/{artifact_id}/revisions` | | `{head, revisions: [...]}` with names (D7) |
| `GET /api/documents/{artifact_id}/revisions/{revision}` | | meta, `markdown`, `diff` against n−1 (`""` for n = 1) |
| `POST /api/documents/{artifact_id}/revisions/{revision}/restore` | `DocumentRestore{base_revision ≥ 1}` | `{id, revision, restored_from, unchanged}`. 404, 409, 429 |
| `POST /api/documents` (S7) | `DocumentIn{title, content, visibility, crew_id}` | `{id, revision: 1, title}`. 400, 413, 429 |
| `GET /api/artifacts/{artifact_id}` (existing) | | adds `revision` (the head) for a document, which the editor sends back as its base |

- Each route takes `CurrentUser`, `ViewerDep` and `PolicySubjectDep`, as `get_artifact`
  does. Each id route first runs `_require_resource_policy(..., "artifact", id)` with the
  caller's viewer, plus `_require_opaque_project_policy` for an engagement-less document,
  so a weak trusted-header name cannot save a private document its read path hides. That
  satisfies `test_policy_axis.py::test_every_bare_route_literal_is_accounted_for`.
- Action names are what `extensions/fastapi.py::_route_policy_action` derives from the path
  literals, so one rule names one action: `skein.rest.put.documents`,
  `skein.rest.get.documents.revisions`, `skein.rest.post.documents.revisions.restore`,
  `skein.rest.post.documents`.
- The mutating routes call `ratelimit.check("write", user)` in the handler, so
  `tests/test_bounded_routes.py` needs no `EXEMPT` row. `content` is
  `Field(max_length=documents.MAX_DOCUMENT_BYTES)`, and the service's byte check is the
  real bound. No body model ends in `Patch` or `EditIn`, so `test_patch_cap_parity.py`
  pairs none, and every `content` field shares that one cap.

## Slices

All seven slices go on one branch, in order. Each passes `./scripts/lint.sh` and both
suites, and fixes the docs it makes false in the same commit. Each new test fails against
the code before the change, with fixtures from a running mock-provider instance.

### S1 — Revision rows behind the existing writes

- The migration. `_publish_revision`, `_head`, `head_revision`. `create_document` writes
  revision 1. `edit_document` reads the head row and writes n + 1. `tools/files.py`
  passes `origin="agent"` to both writes.
- Registries: `scope.UNSCOPED` (`tests/test_scope.py` inventory), `admin.EXCLUDED`
  (`tests/test_admin_export.py`), `users._ATTRIBUTION["document_revisions"] = ("author",)`
  (`tests/test_users.py::test_no_person_column_is_left_out_of_the_rename_map`).
- `tests/test_visibility_authz.py::_EXEMPT_FUNCTIONS`: both UPDATEs move into
  `_publish_revision`, so the `create_document` and `edit_document` entries and their
  comment go. One entry names `documents.py::_publish_revision`: it writes a row its caller
  holds (`_editable_row` ran `assert_editable`, `_document_row` refused all but a workspace
  document, or `create_document` just inserted it).
- `tests/test_documents.py::test_a_changed_document_cannot_be_edited_and_covered_again`
  moves onto a document with no revision rows: the seed refuses a mismatched digest (D3).
- Docs: this file goes to `docs/intent/document-revisions.md`, and `docs/ROADMAP.md` drops
  `document-revisions` from its list of designs with open owner decisions. FEATURES "Agent
  documents" (an edit keeps the replaced text, the tool passes `origin="agent"`), "Backups
  & export" (revisions are rows in both dumps). `docs/VISIBILITY.md` sinks table.
- Fails first, in `tests/test_documents.py`: `test_an_edit_keeps_the_text_it_replaced`
  (today the old file is deleted and no table exists),
  `test_an_agent_edit_records_the_agent_origin` (the tool passes no origin),
  `test_a_document_from_before_revisions_seeds_revision_one_on_its_next_write` (fixture: a
  row and file with no revision rows, the shape a released version leaves).

### S2 — Save, history and restore over REST

- `save_document`, `restore_revision`, `history`, `read_revision`, `unified`,
  `StaleRevision`, `_editable_row`, `_readable_document`, the four id routes, and
  `revision` on `GET /api/artifacts/{id}` (added in the route: `documents` imports
  `handoff`). `restore_document` ("restored an earlier revision of a document", normal) in
  `activity.VERBS`.
- `handoff.read_artifact` parses with `quoted=row["kind"] != "document"`: a document has no
  generated frame, so an apostrophe is a word. The `documents.py` module docstring ("every
  write here goes through tools/_gate.py") changes: people write through REST too.
- Docs: FEATURES "Artifacts & digest — Work → Reports" (the routes) and a new row
  "**Document revisions**" (the no-op rule, the 409, the stale auto-reject, names for
  readers only).
- Fails first, in a new `tests/test_document_revisions.py`:
  - `test_a_save_from_an_old_revision_is_409_and_keeps_the_newer_text`
  - `test_restore_writes_a_new_revision_and_keeps_the_bad_one`
  - `test_a_save_identical_to_the_head_writes_nothing`
  - `test_history_of_an_unreadable_artifact_reads_as_absent` (a private handoff artifact,
    byte-identical to an absent id, as `test_visibility_authz.py` compares)
  - `test_a_generated_report_cannot_be_edited` (a digest, 400)
  - `test_the_ledger_carries_ids_and_never_the_body`
  - `test_history_names_editors_to_readers_only`: Raj saves, an agent edit is approved, and
    Ana's history names both. S7 extends it with a crew document (D7).
  - `test_a_document_links_a_reference_after_an_apostrophe` ("Raj's task #4, don't" gives
    a thread for task #4. Today `_QUOTED` blanks it.)

### S3 — Editor and history on Reports

- **Actions.** For `shown.kind === "document"`, "Report actions" gains **Edit** and
  **History**. `KIND_LABEL.document` becomes "Document": a person now writes part of it.
- **`components/document-editor.tsx`** (new, about 120 lines). A labelled `<textarea>`
  ("Document text, Markdown") and a Write/Preview toggle (`aria-pressed`). In Preview the
  textarea stays mounted with `hidden`, so its undo history survives. Save (off while in
  flight) and Cancel. It opens from an `api(..., { cache: "no-store" })` read, so its base
  is the live head, not a 15-second cached body (`lib/api.ts`). On 409 the text stays and
  the server's sentence goes to `reportStatus(..., "failure")` (`lib/status.ts`, shown by
  `components/status-region.tsx`). On success: "Saved revision 6.", close, refetch, focus
  back to Edit. A `beforeunload` guard is on while the text differs from the base.
- **`components/document-history.tsx`** (new). One row per revision: number, the author's
  name, origin ("Person", "Agent", "Agent, approved in proposal #31" through
  `refHref({entity: "proposal", id})`), "Restored from revision 4", `<time>`. A selected
  row renders with `ArtifactMarkdown` beside its diff against the revision before it.
  Every revision but the head has **Restore revision N**, which posts the head the list
  showed, reports "Restored revision 4 as revision 6." and focuses the History heading.
- **`components/unified-diff.tsx`** (new, about 30 lines). A `<pre>` with one span per
  line, styled by first character (`+` `text-ok`, `-` `text-danger`, `@@` `text-ink-3`).
  The `+` and `-` stay visible, so color is not the only signal. No UI library and no
  shared Button or Modal (docs/VISIBILITY.md, "Frontend").
- `backend/seed.py` writes one document through `documents.create_document` as
  `research-agent`, so the e2e run has a real document.
- **Knot** `document_revisions`: `feature: Edit a document and restore a revision`,
  `knot: Racking Bend` (no card and no other plan uses it), `set: bends`,
  `ties: predicate`, `role: any`, `link: /artifacts`, `since:` the ship date. pitch: "A
  document the team keeps is one the team can fix, and every earlier version stays in
  reach." how: "Open a document on Work → Reports and select Edit. Change the Markdown,
  select Preview to check it, then select Save. Each save is a new revision. To undo a
  change, open History, select an earlier revision, then select Restore." Predicate:
  `_act(u, "edit_document") or _act(u, "restore_document")`. An agent edit logs the agent
  as actor, so only a person's own save ties it. `tests/test_fieldguide.py` counts move
  (69 → 70 cards, 68 → 69 tieable), and so does the FEATURES "Field guide" row.
- Docs: `docs/ROADMAP.md` deletes the "Document editing and revisions" row and adds the
  cut rows below. FEATURES Reports row (Edit, History).
- Fails first: `frontend/__tests__/document-editor.test.tsx` ("keeps the text and shows
  the reason when the save answers 409", "Preview keeps the textarea mounted"),
  `frontend/__tests__/document-history.test.tsx` ("restore sends the head the list
  showed", "names the author of each revision"), `frontend/e2e/document-revisions.spec.ts`
  (edit, save, restore and axe against the seeded mock backend, at 360px too).

| Cut (S3) | Trigger to revisit |
|---|---|
| Live co-editing, presence, locks | A 409 lands on the same document more than once a week. |
| Merge help after a 409 (base → head beside the unsaved text) | People report retyping after a 409. |
| An agent restore tool | A reviewer asks an agent to "undo that edit". |
| MCP and CLI document commands | Someone edits a design doc from their editor. |
| An @mention scan on save | A person asks to be pinged from a document. |
| `document` as a `refs.TARGETS` word | An agenda or a thread names a document and it renders as plain text. |
| Rename and delete a shared document | The first request. |
| A revision cap or pruning | A document passes 200 revisions, or `document_revisions` passes 5% of the dump. |

### S4 — Agent edits pinned to their base

- `tools/files.py::edit_document` stamps `base_revision = documents.head_revision(id)` into
  the payload when it files, so the model does not carry the number. The tool
  `read_artifact` returns `revision`, so the model can name what it read.
- `edit_document` applies the base check (D4). `create_document` and `edit_document` take
  `change_id`, and `_approve_change_locked` sets `payload["change_id"] = change["id"]` for
  both entities (D8).
- `review.unappliable` gains a `document_edit` branch, run when the proposal is filed. The
  target must be a workspace document, or the refusal is `scope.missing_text("artifacts",
  id)`, the `_document_row` sentence (D6). `old` must occur exactly once in the base
  revision: a revision row never changes, so the read takes no lock and its answer holds
  until approval (a document with no rows yet leaves the check to the apply). `change_id`
  becomes a reserved key for both entities, as `actor` and `origin` are. Today a quote
  that never matched files, fails at approval and comes back every time. After this, the
  only apply failure left is a moved base, which settles once (D5).
- Docs: FEATURES "Agent documents" (edits pin a base, a bad quote is refused when filed).
  `docs/ROADMAP.md` "Base values on update proposals" gains one line: document edits
  already carry a base revision.
- Fails first, in `tests/test_document_revisions.py`:
  `test_a_proposal_filed_before_a_human_save_is_refused_and_not_counted` (today it applies
  on top of the person's text),
  `test_a_quote_missing_from_the_document_is_refused_when_filed` (today it files, then
  comes back after every approval),
  `test_an_approved_edit_names_its_proposal_on_the_revision`.

### S5 — The proposal shows as a diff

- For `document_edit`, `review.change_diff` returns `{current: {}, proposed: {}, unified,
  base_revision, head_revision}`. `unified` compares the base body with the base body
  after one `old` → `new` replacement. `change_diff` already filters on the target row, so
  only a reader of the document sees its lines.
- `app/review/page.tsx` renders `UnifiedDiff` in place of the field table, above
  "Technical details", because a document proposal has no other readable summary. When
  `head_revision > base_revision` it adds "The document changed after this proposal.
  Approve refuses it."
- Fails first: `tests/test_review.py::test_a_document_edit_diff_is_unified_against_its_base`
  (today the page renders "—" beside the new text),
  `frontend/__tests__/review-document-diff.test.tsx`.

### S6 — Search finds documents by their current text

- `_publish_revision` calls `index_record("document", id, title, body)`. The index upserts
  on `(entity, entity_id)`, so no old revision is searchable.
  `search._ENTITY_TABLE["document"] = "artifacts"` lets `visible_hits`, `_is_private` and
  `_embeddable` read the tier (workspace text reaches `EMBED_BASE_URL`, crew text stays
  local). `document` is already in `policy_context._TABLES`, so `policy.filter_resources`
  judges its hits. A document written before S6 enters the index on its next write.
- `components/nav-search.tsx` links a `document` hit to `/artifacts?id=${id}`, a special
  case beside `note` and `event`. Without it the hit renders as plain text.
- Docs: FEATURES "Nav search + ask" (a document hit opens on Reports).
- Fails first: `tests/test_search.py::test_a_document_is_found_by_its_current_text_only`,
  and `frontend/__tests__/nav-search.test.tsx` ("a document hit opens it on Reports").

### S7 — People create documents with a tier

- Service: `create_document` gains `visibility` and `crew_id`, resolved by
  `scope.resolve_write` inside its transaction (the crew row lock must last until the
  insert). The ledger line goes through `scope.detail`, and `_check_source` stays.
- Route: `POST /api/documents` with `DocumentIn` (`extra="forbid"`, `title` ≤ 120,
  `content`, `visibility`, `crew_id`, no `engagement_id`), which
  `tests/test_visibility_writes.py::test_a_create_body_exposes_the_tier_its_service_accepts`
  requires. `visibility=body.visibility or _personal_default(request)`, as `post_standup`
  does. It calls `ratelimit.check("write", user)` and decides `skein.rest.post.documents`
  on the new row's tier in the handler, as `post_blocker` does.
- `get_artifact` marks `agent_document` only when `users.is_agent(created_by)`: opening
  your own document is not asking an agent for one.
- A person's document makes two texts false. `_document_row`'s "was not written by an
  agent" becomes "artifact #12 is not a document, so it cannot be changed." (the upload
  sentence stays), and the `edit_document` tool docstring drops "an agent wrote".
- `my_data.export` adds `revisions: [{revision, body, restored_from, created_at}]` to each
  exported private document, all its author's (nobody else can read it, and agents are
  refused). The card label "Other private files" becomes "Private documents and reports".
- UI: **New document** in the Reports page header opens `DocumentEditor` in create mode,
  with a title input and `VisibilityPicker` (`label="document"`, `allowPrivate={strong}`)
  defaulted by `useRememberedAudience("document", strong ? ONLY_YOU : ROSTER, ...)`, as
  `components/event-form.tsx` does. On success the page selects the new document
  (`?id=N`) and reports "Created document #N." The document pane shows `VisibilityBadge`
  for a non-workspace document.
- Knot: the `document_revisions` card becomes `feature: Write, edit and restore
  documents`, its `how:` starts "To write your own, select New document and choose who can
  see it.", and its predicate adds `_act(u, "create_document")`. No new card.
- `tests/test_visibility_authz.py`: `_KINDS`, the crew fixture and `_mutations` gain a crew
  document with `documents.save_document` and `documents.restore_revision`, so
  `test_a_non_reader_cannot_change_a_crew_row` and its author twin cover both.
- Docs: FEATURES "Your data" and "Offboarding erase" (private documents with every
  revision), "Agent documents" (agent documents stay workspace). `docs/VISIBILITY.md`
  "What still lands at workspace, always" (a person now sets an artifact's tier) and the
  `data/artifacts/` sinks row (a scoped document's head file is there, and the row decides
  every read).
- Fails first:
  - `tests/test_document_revisions.py::test_a_person_creates_a_private_document_only_they_can_read`
    (another person's read, history and save all answer as for an absent id)
  - `test_a_private_document_title_never_enters_the_ledger`
  - `test_an_agent_proposal_on_a_crew_document_is_refused_when_filed`
  - `test_history_names_editors_to_readers_only`, extended: a crew document names its
    editors to a member, and a non-member gets the absent-id bytes with no name in them
  - `tests/test_my_data.py::test_the_export_carries_every_revision_of_a_private_document`
  - `tests/test_erasure.py::test_the_erase_takes_a_private_document_and_every_revision`
    (also fails if the table loses `ON DELETE CASCADE`: the delete then aborts the erase)
  - `tests/test_search.py::test_a_private_document_is_never_indexed` (also fails if
    `_ENTITY_TABLE` loses `document`)
  - `frontend/__tests__/document-editor.test.tsx`: "New document starts at only you for a
    signed-in person and at the roster for a weak one"
  - `frontend/e2e/document-revisions.spec.ts`: create a private document and see its badge

| Cut (S7) | Trigger to revisit |
|---|---|
| Share a document to a wider tier (`sharing.SHAREABLE`) | Someone copies a private draft into a new workspace document to share it. The mutating routes then take `hold_resource` first (D9). |
| A person's document filed under an engagement | A person asks to see their document on an engagement page. It needs `scope.assert_relationship_contains`. |
| An agent reads a person's private document in that person's own turn | A request for it. `read_artifact` reads the workspace tier. |

## Residual risks accepted

- **One person's edits can be assembled across documents** by a reader who opens every
  document they can read (D7). It costs one request per document and reaches only
  documents that reader can open. The owner accepted it. `provenance._history` refuses the
  same for tasks, whose dense ids make the walk cheap.
- **`beforeunload` does not cover a client-side route change** in Next.js, so unsaved text
  is lost if a person leaves through the navigation. The 409 path keeps it.
- **Stale agent proposals wait in the queue** after a human save until a verdict. S5 shows
  the stale line, and approval settles them without counting.
- **The base is stamped when the proposal is filed,** not when the model read the
  document. A human save inside the same agent turn becomes the base, and the filing check
  still refuses a quote that the save removed.
- **Two size ceilings.** difflib's worst case on a full rewrite of a 512 KB document (the
  `ponytail:` marker names the fix), and a Your data export that carries every revision of
  each private document. The export is the person's own data, and the write cap bounds its
  growth. A per-document limit is the fix if an export fails in the browser.
- **Invisible characters in a person's text block an agent quote of that span** at review
  authority: `review._refuse_invisible` refuses the proposal and tells the agent why.
- **A person's text reaches an agent** through `read_artifact` as a JSON string in tool
  output, as a note does through search. It is not wrapped with `wording.fence`.
- **A weak trusted-header caller can send `visibility=private`** and file a document its
  own read path hides, as a standup can. The UI does not offer "only you" to it.

## Open questions for the owner

None. The owner settled every open question on 2026-09-28 (D2, D3, the restore in D4, D5
to D7, D10). Work left out is in the cut tables above, each with its trigger.

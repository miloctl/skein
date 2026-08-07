# Skein lexicon — decision table

One concept, one word (CLAUDE.md, "User-visible wording"). This file is the
decision record: it names every concept the UI expresses in more than one
word, the evidence, and the term that wins. `frontend/__tests__/one-wording.test.ts`
enforces the decided rows, so a losing synonym in user-visible text fails CI.

Scope note: **user-visible strings only.** Database columns, API paths, and
function names keep their current identifiers — renaming a label is a copy
change, renaming a column is a migration. The counts below separate the two,
because the cost of a decision is the first number, not the second.

Evidence: user-visible strings extracted from 8 surfaces (web, CLI, API
refusals, chat replies, notifications, app copy, knot `pitch:`/`how:`) on
2026-08-04.

Method note, recorded because it changed the numbers: the first extraction
read only JSX text nodes and prop values, and so missed strings held in
ternaries, object literals, and lowercase values — about 40% of the web
copy, including every `LEVEL_LABEL` value ("acts alone", "not allowed") and
multi-line JSX prose. Counts below are from the corrected pass. No ruling
changed, but any future count must come from rendered screens (Phase 1),
not a regex: a static sweep cannot see what a component assembles.

Status: rows 1, 1b and the rule questions R1-R4 are **DECIDED and applied**
(2026-08-04). Rows 2-7 remain open.

---

## Decisions needed

### 1. A thing a person owes someone else

| | |
|---|---|
| Words in use | **commitment** (13 visible / 6 files) · **promise** (10 / 5) |
| Where they split | Pages say "Commitments"; capture chip, prefix `promised:`, DB field, and CLI say "promise" |
| Code identifiers | standardized too (2026-08-04, pre-production): `promises` table, `/api/promises`, kind `promise`, verbs `add_promise`/`update_promise`/`edit_promise`, insight rules `promise_due`/`promise_slip`/`promise_missed`, ICS UID `promise-{id}@skein` |
| **DECIDED** | **promise** (2026-08-04) — applied end to end, wire included, and enforced by
`one-wording.test.ts`: "commitment" in user-visible text fails CI |

Why: it is the word the user types (`promised: …`), the word the capture chip
offers, and the word the data model already uses for the content. CLAUDE.md's
own agreement example is "1 promise carries". "Commitment" appears mainly in
card titles that we control.

The wire followed the reader (2026-08-04): keeping `commitment` as the
stored kind required a display-mapping layer on every surface, and the CLI
missed it once. The rename used the pre-production migration override
(edits to the pre-squash migrations 008/016/017, since folded into
001_baseline.sql; existing databases must be recreated) —
after the first production deploy this is permanent: the ICS UID would
duplicate calendar events and the activity chain cannot rewrite old verbs.
What stays `commitment` in source: the typed-input aliases (`commitment:`
prefix, "we committed to") and the weekly commitment line (1b).

---

### 1b. CARVE-OUT (DECIDED, applied): "the weekly commitment line" stays

`skein week draft` (CLI), `weekly.py`, and Insights all use **commitment**
for the set of tasks a team commits to an ISO week — not a debt owed to a
named person. Verified: `cli/skein_cli.py:19`, `:504`,
`backend/app/services/weekly.py:1`, `:131`, `services/insights.py`.

If row 1 resolves to "promise", this must be recorded here as deliberate,
or the next sweep reads it as an un-fixed straggler and destroys it. The
weekly line is a **commitment**; a debt to a person is a **promise**.

---

### 2. Naming a conversation

| | |
|---|---|
| Words in use | **chat** (29 / 9) · **conversation** (4 / 3) · **thread** (1 visible) |
| Where they split | Everything says "chat"; the rename field says "Conversation name"; `thread` is the code word |
| Recommendation | **chat** |

Why: 29 to 4 is not a contest, it is an oversight. "Thread" stays as the code
identifier (`thread_id`) and inside the weaving metaphor ("All threads even"
is brand voice, exempt).

Cost: 4 string edits, `thread-title.tsx` and `agents/page.tsx`.

---

### 3. The record type an authority rule covers

| | |
|---|---|
| Words in use | **entity** (7 / 5) — unglossed on the surface that needs it most |
| Problem | Not a synonym clash: the word is jargon with no definition where a user must act on it (the Authority dropdown label is bare "Entity") |
| Recommendation | keep **entity** as the identifier, but never show it unglossed |

Why: the concept is real and has no plain-English one-word equivalent
("record type" is two words and vaguer). The fix is a gloss at the point of
choice — the dropdown label becomes "Record type (task, decision, note…)" —
not a rename. `agents/page.tsx` already models this well for levels
(`autonomous → "acts alone"`).

Cost: 2 label edits plus one helper sentence.

---

### 4. Engagement status colour

| | |
|---|---|
| Words in use | **health** (7 / 3) · **rating** (2 rendered / 1 file) |
| Cause | The single "rating" was introduced 2026-08-04 replacing "verdict" — it fixed a real problem (verdict belongs to reviews) but picked a third word |
| **DECIDED** | **health** — applied. The card is titled "Engagement health — each call shows why", and the page line reads "evidence behind every health call" |

Why: the card is titled "Engagement health", the API is
`/api/portfolio/health`, and the knot cards say health. The stray lines now
match them. ("Verdict" correctly means a review decision; "score" correctly
means intake scoring. Both are separate concepts and stay.)

Cost: 2 strings — one more than the static count above. A rendered walk
found the card title as well as the page line, which is the method note at
the top of this file proving itself: a component assembles a title from a
prop, and a regex over source counts one of the two.

---

### 5. Charter record vs its content

| | |
|---|---|
| Words in use | **charter entry** (title label, submit button) · **agreement** (body label, placeholder, replacement field) |
| Recommendation | keep both, but assign them: the record is a **charter entry**, its text is the **agreement** |

Why: these may be two real things rather than drift — the entry is the row,
the agreement is what it says. Today the split is accidental, not stated.
Deciding it makes the placeholders read deliberately.

Cost: 0–2 strings, depending on the call.

---

### 6. Field-guide card

| | |
|---|---|
| Words in use | **card** (the UI noun) · **knot** (`knots.yaml`, API errors — AND rendered on every card face) |
| Recommendation | keep **card** as the noun and the tied/untied vocabulary; FIX the per-card knot name |

CORRECTION (2026-08-04): this row previously called "knot" internal. It is
not. `frontend/app/guide/page.tsx:132` renders `{c.knot}` as a label on
every card, and the guide's whole state vocabulary is knot-derived and
user-visible ("Tied means you used it", "Newly tied since your last visit").
That vocabulary is an identity asset and it glosses itself on first contact
— keep it.

The per-card knot NAME is the defect, and it is worse than decorative: it
contradicts the taxonomy printed above it. The guide groups cards into
Loops · Hitches · Bends · Stoppers, then labels cards in the **Loops** set
with `Clove Hitch`, `Cow Hitch`, `Lark's Head`, `Cleat Hitch`, `Buntline`,
`Cat's Paw`, `Highwayman's Hitch`, `Marlinspike`, `Anchor Bend`,
`Monkey's Fist`, `Thief Knot`, `Figure Eight` — and one Bend set card with
`Timber Hitch`. **13 of 32 cards carry a knot from the wrong class**
(measured). The reader best equipped to enjoy the metaphor is the one
guaranteed to see it break.

Two fixes: reassign `knot:` in knots.yaml so each name matches its set's
real class (turns ornament into information — the label would then tell you
which set you are in), or delete `{c.knot}` from the card face and keep the
names as author-facing flavor.

---

### 7. Person on the team

| | |
|---|---|
| Words in use | **teammate** (UI, 4 web uses) · **person**/**user** (API field names) · **human** (only in contrast to agents) |
| Recommendation | no change |

Why: this reads as drift in a raw count but is not. "Teammate" is the UI word
throughout; "person"/"user" appear only where they name a field
("person is not an active teammate"), which the lowercase-fragment
convention requires; "human" is a genuinely different concept (human vs
agent). Listed to close it.

---

### 8. Flock

| | |
|---|---|
| Words in use | **flock** (the product noun: a named group of bench personas, `/flock`, `backend/flocks/*.yaml`) · **flock** (the brand metaphor: a flock of geese in flight is a skein) |
| Recommendation | no change — the collision is the point |

Why: the metaphor is where the feature name came from, and both readings
land on the same idea (several strands called into one formation). Nothing
is ambiguous in use: the brand sense appears in `docs/brand/`, in
`docs/ROADMAP.md` prose, and in the /agents flock empty state ("No flock
has flown yet", "who is on the wing") — never inside a functional string,
which is exactly the warmth carve-out CLAUDE.md draws. The product sense is
always adjacent to a slug or a command. Listed to close it, so the next
wording pass does not file it as drift.

The member word is **member**, not "persona", inside flock copy — a persona
is what the thing IS on the bench, a member is the role it plays in a
flock. `docs/FLOCKS.md` and `services/flocks.py` hold that line.

---

## States that LIE — found by the state-mapping pass, not wording bugs

A wording review needs every state a user can reach, so the states were
mapped first. Four of them make a false claim. These are defects, not word
choices, and they outrank every row above: a wrong claim beats a clumsy one.

**T1. A failed card loads forever.** FIXED 2026-08-04. `app/portfolio/page.tsx:90-103` — six
cards fetch independently and each `.catch` only calls `reportStatus`. State
is never set, so `health === null` stays true and the card renders
`Loading…` permanently. With the backend down you get six cards saying
"Loading…" and one toast naming one of them.

This is a regression I introduced on 2026-08-04. The cards used to
initialize to `[]`, which rendered "Nobody is over 100%" on failure — a
false verdict. I changed them to `null` to stop that, and traded a loud lie
for a quiet one: the card now claims work is in progress after the work
stopped. The fix is a third state (error) per card, not a different
initializer. `app/agents/page.tsx` Mission control has the same shape.

**T2. No loading state at all.** FIXED 2026-08-04. `/review`, `/intake`,
`/charter` started from `[]`, so a slow or failed load rendered the EMPTY
state — `/review` flashed its whimsy empty line on every navigation, and
`/intake` had no empty state at all, making "loading", "none", and "failed"
one blank list. All three now hold null until the fetch settles, and
`__tests__/loading-states.test.tsx` pins loading-vs-empty-vs-failed for
each (verified to fail against the two-state shape).

**T3. Silent catches.** FIXED 2026-08-04. Six `/agents` fetches (trust,
entities, personas, status, memories, the agents list) swallowed failures.
Three then rendered a CLAIM — "No reviewed proposals yet", "Nothing
remembered yet" — while the bench and the status strip vanished, and the
entity dropdown silently fell back to a one-item list that reads as "these
are the only record types". Each section now states its own failure, in
the same wording portfolio uses for a failed card. Pinned by
`__tests__/agents-silent-catches.test.tsx`.

**T8. Three retry buttons exist in the whole app** (`/dashboard` ×2,
`/auth/callback`). Everywhere else recovery is a manual reload, which no
string mentions.

Also flagged, not wording: batch selections over 100 rows were silently
dropped (FIXED 2026-08-04 — `BatchApproveIn` accepted 200 ids while the
route looped over 100, so 150 selections returned 100 answers and lost 50
with nothing said; the loop now honors the validated input, pinned by
`test_batch_approve_returns_one_result_per_id`). `/review` having no
manage gate was investigated and is NOT a defect: approvals take
`CurrentUser` by design — "Humans hold every switch" means any identified
human may verify agent work — and only `authority` changes require a
strong identity (`services/review.py:204`). Manager controls is a
per-browser display toggle that "does not grant permissions", so gating
approvals behind it would hide a permitted action without restricting
anyone. Intake is gated because triage is a manager function; approving
is not. `/ingest` truncates at 20 unclassified lines with no "and
N more", authority `not allowed` fires on `onChange` with no confirmation,
and the Agents empty state advertises "delegate a task" for which no UI
exists (already ROADMAP item 3).

---

## Rule-level decisions (CLAUDE.md changes, not word choices)

These came out of the brand-voice audit. Each is a change to the STANDARD,
so each needs a call before any further wording work.

### R1. The brand-voice exemption list cannot stay an enumeration

CLAUDE.md exempts five named things (`whimsy.ts` pools, digest openers,
mock-agent replies, theme pack names, knot `pitch:` lines). The audit found
roughly 200 unexempt strings carrying deliberate voice — so the list
protects what someone remembered to write down and silently condemns the
rest. Unprotected today, all verified:

- the goose: `frontend/app/page.tsx:357` (`🪿` on the progress bar) and
  `services/engagements.py:270` (`🚢🪿 Shipped:`). `globals.css:520` carries
  the comment "the goose must survive a white card in light mode", so
  someone fought for it. It is the README's V-formation metaphor made
  literal, and it is the most distinctive thing in the UI.
- the colorway names: `lib/theme.ts:37-44` — "Madder & woad",
  "Verdigris & copper". Madder and woad are the historical dye plants for
  red and blue textiles. Deepest brand research in the repo.
- the ritual all-clears: `services/rituals.py:134` ("Nothing dangling.
  Close the laptop — the week is settled.") and `:257`.
- the blocker funeral: `services/blockers.py` — survives only because a
  test asserts on it.

**Proposed rule** (inverts the design from "list what is safe" to "list
what is dangerous", which is shorter and fails in the right direction):

> Copy may be warm when the system is idle, empty, or finished and the user
> has nothing they must do. Everywhere else the standard applies. Never
> warm, no exceptions: destructive confirmations, permission refusals,
> data-loss warnings, anything during an incident, and any string carrying
> a number. The enumerated list stays only for the five places where voice
> must be MAINTAINED rather than merely permitted.

**Resolved (2026-08-04):** CLAUDE.md now carries this rule verbatim. The
enumerated list survives only as the five MAINTAIN places.

### R2. The rhetorical-question ban is scoped to errors — it was applied wider

CLAUDE.md's bullet reads "**Errors** state what happened... No rhetorical
questions". On 2026-08-04 it was applied to four strings that are not
errors: "Just looking?" (My Day helper copy), "Lost it?" (Settings helper
copy), "Who are you?" (nav tooltip), and "Still real?" (a digest
notification).

Three of those rewrites stand on other grounds (an imperative beats a
question, condition-first). One lost meaning and should be revisited:
`services/portfolio.py` "Still real? Split it, unblock it, or put it back
in the pool" → "Split, unblock, or put back in the pool." The question was
carrying a fourth option — *the task may no longer matter* — which the
replacement drops.

Decide: widen the rule to all functional text (and accept the four
rewrites), or hold it to errors (and restore what the notification lost).

**Resolved (2026-08-04):** held to errors and refusals — CLAUDE.md scopes
the bullet and judges a question elsewhere on whether it carries
information. The dropped fourth option is back as a statement:
`services/portfolio.py` now ends "…or close it if it no longer matters."

### R3. Emoji have no rule at all

At audit time CLAUDE.md did not mention them, and they are load-bearing:
digest section headings, chat receipt chips, CLI output, the whole sidebar
icon set. A compliance pass with no guidance strips all or none.

One case needed a ruling rather than a preference: `services/schedule.py`
put `🎯` and `🤝` into **ICS calendar SUMMARY fields**. Those render in
Outlook and Google Calendar, in someone else's typography, next to real
meetings. It is the one emoji use whose cost is not ours to absorb.

**Resolved (2026-08-04):** CLAUDE.md now rules: emoji follow the warmth
rule, and never in text that leaves Skein's own surfaces. The ICS emoji
are removed.

### R4. This file granted an exemption it had no authority to grant

An earlier revision of row 2 declared "All threads even" brand voice and
exempt. It is a hardcoded string in `app/page.tsx:601`, not in
`whimsy.ts` — so it is NOT on CLAUDE.md's closed list. Two governing
documents disagreed about what the closed list contains.

Recorded as a precedent question: either the list is not closed (see R1),
or the lexicon may not grant exemptions. Until R1 is decided, this file
grants none.

**Resolved (2026-08-04):** by R1. The closed list is gone; the warmth rule
in CLAUDE.md is the authority, and it already covers "All threads even"
(an all-clear line). The lexicon records decisions — it grants nothing.

---

## Already settled (2026-08-06)

### crew

A durable group of **people**. Pairs with **flock**, which is a named group of
**personas**. The member word is `member` in both.

`team` was the obvious name and is taken five ways: a `SYSTEM_ACTORS` entry, the
notifications broadcast address whose rows the first reader clears for everyone,
a `promises.audience` value, a `resolve_teammate` passthrough, and
`app_settings['team_theme']`. A table of things people can name would collide
with all five. `teammate` stays the word for one person.

A crew is not an **engagement**. An engagement joins people to work, reaches
`closed`, allocates a percent, and carries a date window. Crew membership is
durable and binary, because it is what the visibility tier reads
(`docs/VISIBILITY.md`).

### the three tiers

The wire values are `private` / `crew` / `workspace`. **Two of the three are
never shown.** The picker and the badge say **only you**, **{crew} only**, and
**everyone on the roster** (`components/visibility-picker.tsx`).

The badge has a fourth string, **one crew only**. It says "this row is
crew-scoped and I cannot name the crew", which happens three ways: on first
paint before `/api/crews` answers, when that request fails, and permanently
for a row scoped to a **deactivated** crew (the endpoint returns active crews
only, while `crews_of` still returns deactivated ones, so the row stays
readable to its members). The picker shows it too, in the one case where a
crew is already chosen and the list did not load — without it the select
would fall back to "everyone on the roster" while still submitting the crew.

It is deliberately not the same shape as the picker's `{crew} only`: a reader
must be able to tell that the name is missing rather than read it as a
different setting.

One place shows the wire values: the API refusal for a malformed
`visibility` field (`services/scope.py::resolve_write`). That names what the
endpoint accepts, and no UI can produce it — the picker only ever sends one of
the three.

`workspace` is never shown **as a tier**. The word does appear elsewhere in a
different sense — "search across the workspace" means the corpus, not an
audience — which is exactly why it must not also name who can read a row.
`private` is never shown because it is already taken: the People page's
author-private journal lives in a separate `private.db` that no code path
opens. A tier column earns less than a separate file (`docs/VISIBILITY.md`
argues exactly this), so it must not reuse the word on the surface. "only you"
says the same thing and claims nothing about the file layer.

"everyone on the roster", not "everyone": the tier was NAMED `workspace` so no
reader takes it for public, and a bare "everyone" hands that reading straight
back.

The concept's own name on a surface is **visible to** — the picker's label.
Not "scope" (a code word), not "audience" (`promises.audience` is a different
enum on the same screens), and not "sharing" (nothing is shared; a tier is read
access that either already exists or does not. Nothing changes hands).

### Switching something off without deleting it

| | |
|---|---|
| Words in use | **deactivate** (roster, crews) · **retire** (2, one of them a different concept) |
| Where they split | `app/settings/page.tsx` used BOTH in one sentence. `app/insights/page.tsx` uses "retire" for retiring a RULE, which is a different act |
| **DECIDED** | **deactivate** — it is the verb on every button, the aria-label, and the confirmation. `retire` stays only where the object is a rule |

## Already settled (2026-08-04, enforced or applied)

| Concept | Winner | Note |
|---|---|---|
| The AI you talk to | **Chief of Staff** | was also "the agent", "the chat agent" |
| Capturing quickly | **quick capture** | was also "smart capture" |
| The daily page | **My Day** | was "My-Day" in two mirrored command lists |
| An authority row | **rule** | was "override" in the empty state |
| Mission control | lowercase **control** | was title-cased in one pointer |
| Context pack | **context pack** | bare "packs" collided with theme packs |
| Backend refusal shape | lowercase fragment | 187 of 192; the frontend joiner adapts, not the messages |
| Key-request reply | one string, both surfaces | pinned in `one-wording.test.ts` |
| Unreachable backend | one string, every surface | pinned in `error-wording.test.ts` |

## Deliberately two words

| Concept A | Concept B | Why they must not merge |
|---|---|---|
| **verdict** (a review decision) | **score** (intake priority) | different acts, different surfaces |
| **health** (engagement RAG) | **rating** — retired 2026-08-06, see #4 | not a second concept: it was one word for the same thing |
| **check** (user action) | **verify** (provenance) / **reconfirm** (charter, decisions) | CLAUDE.md reserves the last two |
| **delete** (destruction) | **forget** (memories only) | CLAUDE.md |
| **card** (guide UI) | **knot** (guide source) | see #6 |
| **private** (the People journal — a separate `private.db` file no code path opens) | **only you** (the visibility tier, a column) | one is structural, one is a filter. `docs/VISIBILITY.md` refuses to let the column claim the word |
| **crew** (a durable group of people) | **flock** (a named group of personas) | see the crew entry; the member word is `member` in both |

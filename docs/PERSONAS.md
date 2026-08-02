# The Bench — persona spec

Curated specialist personas the team can invoke in chat. A persona is the
same Chief-of-Staff agent wearing a different head: same review gate, same
provenance — a different system prompt, and crucially **its own agent
identity**, so the authority matrix and trust scores track each persona
separately. By default a persona shares the full tool registry; frontmatter
behavior fields can narrow that (see "Behavior fields" below).

Source material: definitions adapted from `~/external/agency-agents`
(867 agents; we vendor a curated subset as repo files — no runtime
dependency on the external checkout).

## Why these ten

Eight picked for direct relevance to a dev strike team, two for career
growth (pairs with Settings "growth interests" and the 1:1 loop):

| Slug | From | Lens |
|---|---|---|
| code-reviewer | engineering-code-reviewer | correctness/security review of pasted diffs |
| backend-architect | engineering-backend-architect | design consultations, tradeoff analysis |
| minimal-change-engineer | engineering-minimal-change-engineer | scope discipline: the smallest fix that works |
| onboarding-guide | engineering-codebase-onboarding-engineer | new-teammate questions, no hazing |
| incident-commander | engineering-incident-response-commander | structured incident coordination (pairs with blockers/escalation clocks) |
| sprint-prioritizer | product-sprint-prioritizer | intake triage + weekly planning support (RICE) |
| meeting-notes | project-management-meeting-notes-specialist | structured extraction (pairs with /ingest) |
| project-shepherd | project-management-project-shepherd | cross-engagement follow-through |
| growth-mentor | specialized/personal-growth-mentor | goal clarity, habit design, accountability — career growth |
| training-designer | specialized/corporate-training-designer | skill-building plans for the team — career growth |

## Deployment overlay

`SKEIN_PERSONAS_DIR` names a directory of extra persona files loaded
alongside `backend/personas/`. An overlay file with a stock slug wins, and an
overlay `pack.json` replaces the stock one wholesale. This keeps a
deployment's own bench in its own repo instead of a fork. The strict
validator covers overlay files and labels them `(overlay)`.

## Architecture

- **`backend/personas/*.md`** — one file per persona, edited like code
  (the playbooks precedent). Frontmatter: `name`, `description`, `emoji`,
  `vibe`, `disclosure`, plus the optional behavior fields (`model`,
  `temperature`, `tools` — see below); body = the persona system prompt,
  written Skein-aware (knows the capture grammar, the review gate, and its
  own lens). Slug = filename.
- **`services/personas.py`** — deterministic loader/parser (no YAML dep);
  `list_personas()` / `get_persona(slug)`. Slugs are `[a-z0-9][a-z0-9-]{1,40}` because
  they double as agent identities (and are path-safe by the same rule).
- **REST** — `GET /api/personas`, `GET /api/personas/{slug}`.
- **Agent identity contextvar** (`agents/identity.py`) — the prerequisite
  fix: chat tools previously hardcoded `actor="agent"`, collapsing every
  chat agent into one identity. `gated_write` and all direct-write tools
  now resolve the actor from a `ContextVar` (default `"agent"`), set per
  chat request. Personas therefore accrue their **own** authority rows,
  trust scores, review verdicts, and Mission Control presence.
- **Invocation** — `/as <persona> <message>` in chat (autocompletes like
  every command; `/personas` lists the bench). The route resolves the
  persona BEFORE the model: unknown slug is a deterministic error listing
  the bench. Each persona gets its own session thread
  (`{thread}--{slug}`), so switching personas doesn't cross-contaminate
  conversation memory. Invocation registers the persona (idempotently) as a
  `kind=agent` user (deliberate, from the curated registry — not the
  typo-minting path that was removed).
- **Mock provider** — `/as` works keyless: the route emits the persona's
  masthead (emoji, name, vibe — plus a privacy disclosure for the growth
  personas) and the mock engine handles the message deterministically.
  Every persona surface (list, bench UI, invocation) is testable with
  zero keys; identity attribution is covered by the contextvar tests —
  mock-mode captures stay human-attributed, since the mock engine is the
  user's own smart capture, not the tool gate.
- **Safety** — unchanged by construction: personas start at `review`
  authority like any agent; their writes become proposals; `forbidden`
  works per persona per entity. A persona can't do anything the Chief of
  Staff couldn't — it just thinks differently and signs its own name.

## UI

- **Sticky sessions**: invoking a persona (bench card, `/as` message, or
  picking one from the autocomplete) enters that persona's mode for the
  thread — a chip above the composer shows the emoji + name with an ×
  that returns to the Chief of Staff, and the placeholder reads "Message
  <name>…". Freeform messages are invisibly prefixed with `/as <slug> `
  by the runtime adapter (the backend contract is unchanged); slash
  commands are never prefixed, so they stay deterministic. An explicit
  `/as <other> <message>` switches modes.
- **Agents page**: "The bench" card — emoji, name, `/as` slug,
  description, vibe; click → `/chat?as=<slug>` which enters that
  persona's mode directly. Personas appear in Mission Control after
  first use.
- **Chat**: `/as` + `/personas` in the composer autocomplete; typing
  `/as ` continues into slug completion from the live bench.

## Non-goals (this iteration)

- ~~No per-persona tool restriction~~ — superseded: frontmatter `tools`
  declares an allowlist, enforced at Agent construction for BOTH the persona's
  agent and its planner sub-agent (the planner runs under the persona's
  identity, so its writes are the persona's writes). The layering with the
  authority matrix is: the allowlist decides what the model sees at
  construction, the matrix gates each write per entity at call time — the
  stricter of the two wins in both directions.
- No persona-to-persona conversation (see ideation A4 for handoffs).
- No auto-selection of personas (the human picks; the CoS remains the
  default head).


## Design notes (from the 5-agent review)

- **Provenance:** proposals record `requested_by` — the human whose `/as`
  message drove the persona — so reviewers see "by code-reviewer · asked
  by dana". Authority changes require strong identity (`StrongUser`).
- **Slug reservation:** bench slugs are reserved names; `ensure_user`
  refuses to create a human with a persona's slug (and vice versa), so
  identities can't be shadowed or absorbed.
- **Planner conflation:** `plan_project` runs under the invoking head's
  identity — a persona answers for what its planner creates. Deliberate:
  one accountable identity per conversation.
- **Memory bleed:** durable memories are team-scoped, not persona-scoped.
  The growth personas therefore disclose (masthead) that chat is stored
  server-side and their filings are team-visible, and they ask before
  filing. Genuinely private career prep belongs on the People page.
- **Sticky sessions (shipped):** the mode chip + invisible prefixing
  resolved the per-message `/as` interaction cost flagged in review.

## Behavior fields (since 2026-08-02)

Frontmatter can add three optional fields; pack-wide defaults live in
`personas/pack.json` (`{"defaults": {...}}`), persona wins field-by-field.

- `model:` — model ID override. Never the provider or endpoint: a persona
  file cannot redirect traffic.
- `temperature:` — 0.0 to 2.0. Beats `SKEIN_MODEL_PARAMS` (the persona is the
  more specific operator intent). A bad value drops at runtime and fails the
  validator.
- `tools:` — comma list, deny-by-omission once declared: the persona's agent
  (and its planner) is built with exactly those tools, so the model never
  sees the rest. Extra/MCP tools cannot be allowlisted by name. A pack-wide
  `tools` default restricts every persona and no persona can override it
  back to unrestricted — keep pack defaults minimal.

Runtime parsing is lenient (a malformed persona drops off the bench, chat
stays up). `python -m app.services.personas` is the strict validator that
lint.sh and CI run — the same file fails the build instead of vanishing.
Behavior fields apply on real providers only; the mock path never builds a
Strands agent.

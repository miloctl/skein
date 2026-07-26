# The Bench — persona spec

Curated specialist personas the team can invoke in chat. A persona is the
same Chief-of-Staff agent wearing a different head: same tools, same review
gate, same provenance — a different system prompt, and crucially **its own
agent identity**, so the authority matrix and trust scores track each
persona separately.

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

## Architecture

- **`backend/personas/*.md`** — one file per persona, edited like code
  (the playbooks precedent). Frontmatter: `name`, `description`, `emoji`,
  `vibe`; body = the persona system prompt, written Skein-aware (knows the
  capture grammar, the review gate, and its own lens). Slug = filename.
- **`services/personas.py`** — deterministic loader/parser (no YAML dep);
  `list_personas()` / `get_persona(slug)`. Slugs are `[a-z0-9-]+` because
  they double as agent identities.
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
  conversation memory. First invocation registers the persona as a
  `kind=agent` user (deliberate, from the curated registry — not the
  typo-minting path that was removed).
- **Mock provider** — `/as` works keyless: the mock agent answers with the
  persona's masthead (emoji, name, vibe) and routes the message through
  the same deterministic command/capture engine. Every persona surface
  (list, bench UI, invocation, identity attribution) is testable with
  zero keys.
- **Safety** — unchanged by construction: personas start at `review`
  authority like any agent; their writes become proposals; `forbidden`
  works per persona per entity. A persona can't do anything the Chief of
  Staff couldn't — it just thinks differently and signs its own name.

## UI

- **Agents page**: "The bench" card — emoji, name, one-line description,
  vibe; click → `/chat?as=<slug>` which prefills the composer with
  `/as <slug> `.
- **Chat**: `/as` + `/personas` appear in the composer autocomplete via
  the existing data-driven command catalog.

## Non-goals (this iteration)

- No per-persona tool restriction (they share ALL_TOOLS; the authority
  matrix is the restriction mechanism).
- No persona-to-persona conversation (see ideation A4 for handoffs).
- No auto-selection of personas (the human picks; the CoS remains the
  default head).

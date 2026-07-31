# Field guide ("knots")

First-use feature discovery. Every card in the guide is a shipped Skein
feature; a card is **tied** once you've used the feature and **untied** until
then. Untied cards carry the how-to and a deep link — the guide is
progressive documentation with read-state, not an achievement system.

Shaped by a three-agent concept review (PM, game design, behavioral
psychology, 2026-07-30). The reviews converged on: frame as a field guide,
never as trophies; individual state is self-visible only; exclude private
and judgment-laden surfaces; silent retroactive seeding; quiet cadence.

## Design constraints (load-bearing — do not relax casually)

- **Self-scoped, forever.** A person's unlock state is visible only to that
  person. No per-person grid, no completion feed, no "4 of 6 have tied this"
  with names. This is the anti-surveillance rule (FEATURES.md) applied here:
  person-keyed data never becomes a judgment surface. The builders' signal is
  the `feature_unadopted` findings rule — feature-keyed, nameless, and only
  for zero-adoption rows where no individual can be singled out.
- **First-use only.** No counters, no streaks, no tiers, no points — ever.
  Counters are where Goodhart lives. Where the entity has a lifecycle, the
  knot keys to the **terminal verb** (settle, resolve, close, verdict), which
  presupposes real work; verdict-*direction* is never rewarded (a settle
  counts whether kept or missed; a review counts whether approved or
  rejected).
- **Structurally excluded** — no knots, no coverage, nothing: private 1:1
  notes and the `fb:` journal (an unlock's existence is metadata private.db
  exists to hide), key requests, every `POST /api/feedback` surface (the
  eval corpus must never be incentivized), and agent identities (agents have
  trust scores; two reward systems would blur both).
- **Quiet.** Retroactive seeding is silent — a veteran's history renders as
  already-tied, with zero ceremony. "Newly tied" appears once, in-page, on
  the next guide visit. One rotating weekly suggestion on My Day; dismissing
  a suggestion suppresses that knot permanently. No toasts, no confetti
  (reserved for the team Ship It moment), no percentages in the nav.
- **No activity rows for unlocks.** The activity feed is team-visible;
  logging "tied X" there would leak individual guide state. Unlocks follow
  the `tool_usage` precedent (self-scoped telemetry), not the provenance
  convention for work data.
- **Weak identity accepted, on purpose.** `GET /api/field-guide` rides
  `CurrentUser` (spoofable `X-User`, trusted-LAN model), same as the
  briefing and every personal-but-not-private surface. Unlock state is
  discovery telemetry about tool usage, not private content — the truly
  private surfaces (1:1 notes, `fb:` journal) are excluded from the guide
  entirely and keep their `StrongUser` wall. Reviewed and accepted
  2026-07-31; revisit only if unlock state ever gains sensitive content.

## Mechanics

- Registry: `backend/fieldguide/knots.yaml` — id, feature label, knot name
  (flavor subtitle, never the primary label), set, pitch, how-to, deep link,
  optional `role: manager` tag, `since` date. Predicates live in
  `app/services/fieldguide.py` keyed by id (SQL stays in the service layer);
  the loader fails loudly on a card without a predicate or vice versa.
- State: `feature_unlocks` (migration 033), append-only rows
  (person, knot, kind tied|dismissed, seen, first_at). Predicates *detect*;
  the table *holds* — activity gets pruned by retention, unlocks survive.
- Detection: lazy on guide-page load and My Day hint, plus a sweep of all
  active humans inside the daily findings run (so `feature_unadopted` never
  fires on stale data). Human, active, named users only.
- Read-only features (search, `/ask`) write nothing to detect against, so
  their routes call `fieldguide.mark()` directly — fire-and-forget.
- Sets are real knot families used as grouping: **Loops** (solo basics),
  **Hitches** (attaching to another rope — the agent loop), **Bends**
  (joining two ropes — cross-system seams, the highest-teaching-value
  cards), **Stoppers** (finishing), plus a manager-tagged group so IC
  completionists aren't chasing unreachable cards.

## Shipping a feature? Add its card.

Convention, not CI gate: a feature PR that adds a user-facing surface adds
its knot in the same commit. A new locked card *is* the release note — the
guide is the "what's new in Skein" surface. A feature without a card is a
feature you've decided is undiscoverable.

## Deferred / rejected

- Git-trailer bend (Carrick Bend): not cleanly detectable — the hook's task
  update is indistinguishable from a web edit. Needs a source tag on the
  update first.
- Sets meta-knots, capstone, hidden joke knots: v2, only if the team
  actually engages.
- Toast on unlock: replaced by the in-page "newly tied" strip — detection
  is lazy, so a real-time toast would require polling.
- Week-open brief line: rejected — the per-person suggestion would sit in a
  team-visible artifact (leaks untied state), and two push channels in one
  week violates the one-nudge-per-week cap. My Day is the only push.
- Kill criterion (60 days): if guide-page visits are zero for two
  consecutive weeks after week 2, retire the page; keep the registry and
  the findings rule.

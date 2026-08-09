# Review transcripts

Design rationale, kept for the *why not*. A diff records what changed. These
records say which alternatives were examined and what made them lose.

Every file here is closed. No open work lives in this directory. All un-shipped
work is in `docs/ROADMAP.md` under "Open backlog".

| File | Why it is kept |
|---|---|
| `2026-07-23-implementation-spec.md` | The original phase plan (phases 0–4, all built). Kept for the data model, the keyless-dev rationale, and the accepted tradeoffs it records. Formerly `docs/SPEC.md`; moved here 2026-08-09. |
| `2026-07-23-ideation-rounds.md` | Rounds 1–3 panel transcripts and syntheses. Records the round-2 not-built list (editor touchpoints, weekly changelog, and six more), which lives nowhere else. |
| `2026-07-24-backlog-burndown.md` | The architecture-review burn-down: which design each of the eight fixes chose, and where the tests moved on 2026-08-02. |
| `2026-07-24-agent-sol.md` | The only spec for about eight deferred designs: delegation contracts, Playbooks 2.0, the evidence pack, the outbox, the capability broker. The implementation plan cites its closing line as "sol's bar". |
| `2026-07-24-agent-fable.md` | The origin of two lines that `docs/FEATURES.md` now states as conclusions, and the player-coach reasoning behind the implementation plan's first standing guardrail. |
| `2026-07-24-panel.md` | The 15 private-record leak paths, and the "privacy by structure, not by filter" argument that produced `private.db`. The implementation plan records the countermeasures. Only this file records the threats. |
| `2026-07-24-synthesis.md` | The decision record over the three files above. Its cut table moved to ROADMAP. |
| `2026-07-24-implementation-plan.md` | The three-wave plan the synthesis produced, executed, with the recorded deviations between spec and ship (key-minting hardening, the exhaustive canary, the golden-trace reshape). Formerly `docs/PLAN.md`; moved here 2026-08-09. |
| `2026-07-25-ideation-run.md` | Mechanism sketches and per-agent reasoning for the proposals that moved to ROADMAP. |
| `2026-07-27-theme-review.md` | The definition site for TD1, TD2, TD6, TP3, TP5 and TP6. `globals.css`, `theme.ts` and `whimsy.ts` cite those IDs in source comments. |
| `2026-08-08-product-gaps.md` | The definition site for G1–G10 and the rankings behind the 2026-08-08 promotions. Records the two-quarters-ahead diagnosis, the trust-flywheel dead-end under the live configuration, and the readout WIP egress fact. |

## Deleted 2026-08-02

The 2026-08-02 consolidation removed four transcripts because every conclusion
in them was recorded elsewhere. The 3-week dogfood log went into
`2026-07-24-implementation-plan.md`. The whole-app UX review and the whole-app audit went into
`docs/FEATURES.md` and `docs/CORRECTIONS.md`. Fresh-eyes audit 2 left its two
skip decisions as comments at the code they describe.

The same consolidation removed two `pack-contrast-check` scripts. They were
checks that nothing ran. `scripts/check_theme_contrast.py` replaces them and
runs in `lint.sh` and CI.

## Adding a review

A review file is written to be drained, not to accumulate. When the work lands:

1. Move every surviving proposal to `docs/ROADMAP.md`.
2. Write a status line at the top of the review that says what shipped.
3. If nothing in the file is cited from code or another doc, delete it.

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
| `2026-08-09-product-review.md` | The definition site for R1–R7. A three-lens read (developer, manager, and the product as a human-and-AI operating system) of everything shipped. Records the surfaced-versus-unsurfaced table and the diagnosis behind it: value is computed and never reaches a reader, and several loops stop at 80%. |
| `2026-08-10-workplace-extensibility-assessment.md` | The pre-change assessment of `d3b0f2e`, and the definition site for scenarios A–H. It is the baseline the extension work was scored against: modularity 5/10, workplace extensibility 3/10, upgradeability 4/10. Only this file records why each score was that low. |
| `2026-08-10-workplace-extensibility-plan.md` | The execution plan for the extension boundary: baseline traces, the decision log behind each contract shape, the drift record, and the settled risk table. Records why several proposed contracts were removed rather than shipped. Formerly `docs/exec-plans/workplace-extensibility.md`; moved here 2026-08-13 when the work closed. |
| `2026-08-12-extension-boundary-audit.md` | The adversarial audit of the extension boundary before the final remediation. Records which claims did not hold against the code, and which of its findings were fixed against which were deferred to `docs/ROADMAP.md`. |
| `2026-08-13-workplace-extensibility-results.md` | The final report: scenario A–H results, the contract inventory, the reviewer scores, and the remediation record. Section 20 records the three post-remediation reviews and the defects they found. Formerly `WORKPLACE-EXTENSIBILITY-RESULTS.md` at the repository root; moved here 2026-08-13. Living authoring guidance is in `docs/EXTENSIONS.md`, not here. |
| `2026-08-15-product-study-method.md` | **The repeatable part.** How a live product study is run: persona set, the month and long-horizon simulations, the selection rules an item must pass before it is built, and the four-agent review panel. Its fourth-pass section is the pre-merge review method — the lens split, the verify-before-reporting rule, and the stash-the-fix test proof. The only file here that is a procedure rather than a record. Formerly `PLAN.md` at the repository root. |
| `2026-08-15-product-study-findings.md` | Observations from four passes of live use, and the definition site for the D-numbered problems the proposals cite. Records what was seen and deliberately NOT built, which lives nowhere else. Findings describe the product at the time seen, not current behavior. Formerly `findings.md` at the repository root. |
| `2026-08-15-product-study-proposals.md` | The P-numbered proposals with the evidence standard each had to meet, and the reason behind every deferral and refusal. Kept for the *why not*. Formerly `proposals.md` at the repository root. |
| `2026-08-15-product-study-results.md` | What the study shipped, written at delivery, with the per-improvement reasoning `docs/FEATURES.md` compresses away. The feature table wins on any disagreement. Formerly `new_features.md` at the repository root. |

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

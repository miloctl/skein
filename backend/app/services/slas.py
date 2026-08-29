"""Staleness SLA constants. The gradation is deliberate: a task goes quiet
(digest nudge) before it goes stale (Monday WIP nudge) before it counts as
aging WIP (findings rule) — three escalating signals, never one cliff."""

DIGEST_STALLED_DAYS = 3  # digest: "stalled tasks" section
STALE_WIP_DAYS = 7  # portfolio: health receipt + Monday nudge
SILENCE_DAYS = 7  # portfolio: engagement with open tasks but no activity
AGING_WIP_DAYS = 14  # insights: aging_wip findings rule
ABANDONED_DAYS = 14  # insights: task_abandoned findings rule (spike then silence)

# The n floor docs/INSIGHTS.md holds every verdict to: below it a claim is
# noise wearing a percentage sign, so the number is shown and the verdict is
# withheld. It lives HERE because portfolio and insights both need it and
# must never import each other (services/readout.py records that rule), so
# any other home means two spellings and a rule that can pick the friendlier
# one.
VERDICT_FLOOR_N = 8

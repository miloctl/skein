"""Staleness SLA constants. The gradation is deliberate: a task goes quiet
(digest nudge) before it goes stale (Monday WIP nudge) before it counts as
aging WIP (findings rule) — three escalating signals, never one cliff."""

DIGEST_STALLED_DAYS = 3  # digest: "stalled tasks" section
STALE_WIP_DAYS = 7  # portfolio: health receipt + Monday nudge
SILENCE_DAYS = 7  # portfolio: engagement with open tasks but no activity
AGING_WIP_DAYS = 14  # insights: aging_wip findings rule

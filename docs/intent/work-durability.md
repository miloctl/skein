# Work durability, at one replica or several

Confirmed 2026-09-06 with the repository owner.

- **Outcome:** No agent run, scheduled job, or inbound webhook is ever lost
  or run twice, and the same code runs correctly at one replica or several.
- **User:** The strike team's agents and integrations. People retry on
  their own.
- **Why now:** First production deploy is ahead, the cluster has RWX
  storage, and the reliability shape must be settled before habits form.
- **Success:** A two-process harness passes concurrent boot, worker loss
  mid-turn, duplicate delivery, and recovery. Kill a pod mid-turn and every
  piece of work completes once or is visibly marked unknown. Nothing is
  silently dropped.
- **Constraint:** One replica with Recreate is the shipped default. Flipping
  to two with RollingUpdate is a manifest change gated on the harness
  passing. No Redis, no object storage. PostgreSQL and RWX only. The
  application never reads its own replica count.
- **Out of scope:** Browser-side write queues, scheduler leader election,
  and multi-replica as the day-one deployment.

## Why not zero downtime first

The team accepts brief deployment interruptions. Correct recovery takes
priority over uninterrupted access. Execution ownership, durable receipts,
and explicit handling of uncertain outcomes are required at any replica
count. Additional replicas also require shared storage and tests of actual
concurrent execution, process loss, and rolling upgrades. The presence of
lease columns alone does not satisfy this requirement.

The outcome above is the design goal, not an unconditional exactly-once
guarantee. A database transaction can fence local writes. An external
system can accept an action before the worker loses its connection, leaving
its result unknown. Do not automatically repeat such an action without an
idempotency contract or reconciliation. Keep uncertain work visible.

## Why catch-up at boot for webhooks

GitHub is the selected forge. Catch-up is separate work: a GitHub event
adapter, repository and webhook allowlists, API credentials held in the
deployment Secret, and durable recovery progress are still required.

GitHub does not automatically redeliver failed webhooks. Its delivery
history supports bounded recovery, not an unlimited record of events.
Run catch-up after startup and periodically. If the history no longer covers
the gap, report it and reconcile current repository state instead of
claiming that every event was recovered. Repository webhook redelivery
requires Webhooks write permission, separate from the incoming signature
secret.

Slack slash commands are interactive requests, not a recoverable event log.
A person retries a command that the server did not receive. Do not promise
slash-command recovery from channel history.

See [GitHub failed-delivery handling](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries)
and [redelivery limits](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks).

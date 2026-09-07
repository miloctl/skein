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
  multi-replica as the day-one deployment, and new Git hosting or Teams
  integrations. The first deployment must work through Skein's own UI.

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

## Runtime independence

The planned deployment cannot reach GitHub. An internal GitLab service is
reachable, but integration with it is not a first-deployment requirement.
Teams is also an option to assess later, not another feature to build now.
Core work durability must remain independent of those integrations.

GitHub can remain the source and release system outside the isolated runtime.
Approved images and packages enter through the organization's deployment
process. Running Skein needs no GitHub token, webhook, or recovery polling.

Existing Gitea and authenticated CI behavior remain available without adding
a new integration. Generic delivery receipts prevent repeated application of
an accepted delivery. They cannot recover a request that never reached Skein.
Any future source-specific catch-up needs a demonstrated workflow need and an
approved network path. Do not promise recovery where neither exists.

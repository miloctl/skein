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

Nobody is hurt by the seconds a Recreate deploy costs. Lost work comes from
unfenced startup recovery and unclaimed scheduled jobs, and both are fixed
by ownership leases and single-flight claims with one replica. A second
replica adds nothing to that target and adds a class of bugs that appear
only with two processes. It stays available as a manifest change once the
harness proves the code.

## Why catch-up at boot for webhooks

A delivery that lands during a deploy gap is recovered by asking the forge
and Slack what was missed since the last recorded delivery. That closes the
loss window without a second replica, and it also covers the failure a
second replica cannot: the sender never delivering at all.

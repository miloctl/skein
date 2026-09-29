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
  multi-replica as the day-one deployment, and Teams integrations. GitLab
  inbound webhooks are the one approved Git hosting integration
  (`docs/intent/gitlab-forge.md`). The first deployment must work through
  Skein's own UI.

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
reachable. Its inbound webhooks are approved (`docs/intent/gitlab-forge.md`):
Skein receives push, merge request and pipeline events and makes no call
back. Outbound GitLab calls are an open decision for the owner. Teams is an
option to assess later, not another feature to build now. Core work
durability must remain independent of those integrations.

GitHub can remain the source and release system outside the isolated runtime.
Approved images and packages enter through the organization's deployment
process. Running Skein needs no GitHub token, webhook, or recovery polling.

Gitea, GitLab and authenticated CI behavior remain available. None of them
can recover a request that never reached Skein. For GitLab, the recovery is
the project webhook's Resend request, which receipts make safe.
Any future source-specific catch-up needs a demonstrated workflow need and an
approved network path. Do not promise recovery where neither exists.

## Forge replay boundary

The GitLab token authenticates the request, not the bytes. Receipts and
fingerprints apply unchanged, in a separate `gitlab` namespace for each
repository URL: a delivery ID binds to one event and body digest, and the
fingerprint over the raw bytes suppresses a resend under any ID. The rest of
this section describes the signed Gitea path, and holds for GitLab with
"authenticated" in place of "signed".

A fingerprint combines the repository namespace, native event type, and exact
raw payload SHA-256. A stored fingerprint suppresses replay regardless of
the delivery UUID, including after a human task edit. This also covers mapped
events that produced an ignored outcome. A new UUID receives an alias receipt
with `task_id = NULL`, because it performed no task transition. Original and
alias UUIDs remain bound to their event type and bytes within the repository
namespace. Reuse with different bytes or an event type, including an unsupported
event, is refused.

When signed bytes are identical, Skein cannot distinguish a replay from a
genuinely new occurrence. Skein conservatively suppresses both within the same
repository namespace and native event type. Changed bytes remain eligible for
existing policy and task-state checks, even when the branch, commit pair, pull
request, or target state is unchanged. JSON formatting and metadata are part
of those bytes. This is scoped signed-byte replay protection, not a semantic
event identity or an unconditional exactly-once guarantee.

Receipts and task changes commit together. A failed transaction creates no new
receipt and leaves the request eligible for retry. Receipts remain permanent,
including alias receipts, because pruning them permits old events to overwrite
later human edits. Unsupported or unmapped new events create no receipt.

A request without a delivery UUID creates no receipt. An existing native
fingerprint still suppresses an exact request sent without that header. An
initial headerless request leaves no fingerprint history. Pre-028 legacy
receipts preserve suppression for their original delivery IDs but contain no
reconstructible fingerprint. Neither case guarantees suppression of a later
replay under a new UUID.

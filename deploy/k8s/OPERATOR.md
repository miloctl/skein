# Operator card

This page is for the person who runs Skein and did not build it. It
covers the OpenShift deployment (`deploy/k8s/`). The build-time decisions
and their reasons are in `README.md` in this directory — you do not need
them for routine operation.

An **admin** here means a person named in `SKEIN_ADMINS`, or in the IdP
group that `SKEIN_OIDC_ADMIN_GROUP` names. The admin API calls below need
that person's credential. An API key carries no IdP groups, so a `curl`
step that sends an admin API key works only for a name in `SKEIN_ADMINS`.
One exception is for development only: in `trusted-header` mode with
neither variable set (the `example-dev` overlay), every key holder is an
admin. An admin found that way can change settings and take backups.
Reading other people's data still needs a name in `SKEIN_ADMINS`: the
portable export download, the list of every person's keys, another
person's agent record, the stranded-proposal list, the capture replay,
everyone's feedback, crew member lists, and crew-steward repair.

Write each `SKEIN_ADMINS` name the way that person signs in. Skein matches
case after the first sign-in, but a key request sent before it goes to the
exact spelling in the list. A deactivated or renamed name counts as no
administrator.

Every `oc` command below needs your namespace: add `-n <namespace>`.

## What this system needs from you

Nothing routine. Backups, ledger verification, and cleanup run on an
internal schedule. CI re-checks the code weekly. A bad model provider or
a bad config value degrades the feature and reports itself — it does not
take the service down. Check `/api/health` monthly. Upgrade when the
maintainer publishes a release, on your own schedule.

One key is yours to keep. `SKEIN_CREDENTIAL_KEY` in `skein-secrets` seals
the tokens people store for their personal MCP servers. Generate it once:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If the key changes, every stored token reads as empty. Those servers then
connect without a token until their owners add them again. If the key is
not set, people can add a server without a token only, and the settings
card says so.

## The monthly check

Open `https://<backend-route-host>/api/health` with a credential — a
personal API key, or a signed-in browser session. The page always returns
200 when the process and the database are up: the content is the
diagnosis, not the status code. The open `/health` endpoint answers startup
and liveness probes. The open `/ready` endpoint answers readiness probes and
returns 503 when authentication configuration is invalid or the bounded database check fails.
Liveness does not depend on database availability. Both probes carry only `ok`, `auth_mode`, and
`auth_error`. Read `/api/health` after database access returns for further diagnosis.

| Field | Healthy value | If not |
|---|---|---|
| `auth_error` | empty string | Sign-in is broken for everyone. Read the message. Fix the named `SKEIN_*` value in the overlay and sync. |
| `provider_error`, `models_error`, `embeddings_error` | `null` | Agent chat degraded to mock. Deterministic features still work. Fix the named model setting when convenient. |
| `overlay_errors` | `[]` | A persona/playbook/flock ConfigMap did not mount. Check the volume mounts against the `SKEIN_*_DIR` values. |
| `timezone` / `timezone_error` | your team zone, error `null` | Rituals fire at UTC hours instead of local. Set `SKEIN_TZ` to an IANA `Region/City` name. |
| `jobs[]` | every entry has `"stale": false`; `last_success` is recent, or `null` on a fresh install (that is not a fault) | The flag sets at twice the job's period — for a daily job, `true` means roughly two days already missed. Read the pod log: `oc logs deployment/skein-backend`. If the log does not explain it, contact the maintainer. |
| `activity_chain.unverified` | `0` or a small positive number (rows since the last nightly verify) | A NEGATIVE value means the ledger is shorter than what was already verified. That is truncation. Do not restart anything. Contact the maintainer. |
| any other `*_error` field | `null` | Read the message — each one names its own fix. If the overlay values do not explain it, contact the maintainer. |

## The agent spend bounds

Unattended agent turns cost real tokens. Every bound below has a safe
default, but nobody has chosen the values for YOUR deployment until an
overlay sets them. Each one is an environment variable in the overlay
ConfigMap (`deploy/k8s/overlays/*/kustomization.yaml`);
`backend/.env.example` documents every value in full.

| Variable | Default | What it bounds |
|---|---|---|
| `SKEIN_AGENT_RUNNER` | empty (off) | Which agents get one scheduled turn per day. Empty means the daily runner wakes nobody. |
| `SKEIN_AGENT_WAKES_PER_DAY` | 24 | Workspace-wide cap on delegation-triggered turns per day. |
| `SKEIN_AGENT_DAILY_TOKENS` | 0 (no ceiling) | Per-agent daily token ceiling. It refuses the NEXT run after the spend. Set this before you set a provider key. |
| `SKEIN_AGENT_RUN_SECONDS` | 300 | Wall clock on one unattended turn. |
| `SKEIN_AGENT_RUN_TURNS` | 30 | Turn cap on one unattended run, stopped cleanly with a named reason. An admin can change it later on Settings → AI runtime → Deployment limits. |
| `SKEIN_AGENT_RUN_TOKENS` | 200000 | Token cap on one unattended run. Admin-adjustable like the turn cap. |
| `SKEIN_OFFLOAD_RESULT_TOKENS` | 2500 | A chat tool result above this size is stored per session and replaced in context by a preview, so it stops being re-billed on every later turn. 0 turns the offload off. |
| `SKEIN_OFFLOAD_PREVIEW_TOKENS` | 1000 | The preview size. Keep it under the result threshold. |

**The stop switch.** Settings → AI runtime → "Unattended agent runs"
pauses the daily runner and the wake queue without a redeploy. A run in
progress stops at its next step. Queued work stays pending and drains when
an admin resumes. The switch stops automation only — it does not change
what any agent is allowed to do.

## Upgrade

1. Pause ArgoCD auto-sync. Check the StorageClass for `skein-data` and
   `skein-backup-mirror`. Its `allowVolumeExpansion` value must be `true`.
2. Apply a storage-only change first. Request 360Gi for `skein-data` and 320Gi
   for `skein-backup-mirror`. Wait until both PVC status capacities match.
   If expansion is unavailable, keep the backend stopped while the storage
   administrator copies each volume to a larger replacement PVC.
3. The maintainer publishes images tagged `X.Y.Z` (backend) and
   `X.Y.Z-<env>` (frontend).
4. In the private deploy repo, update each `newTag` and its matching reviewed digest
   in the overlay's `images:` block. Use the frontend digest for that environment.
   A tag-only change keeps the old image bytes because the digest controls the pull.
   Before a commit or sync, render the private overlay:

   ```sh
   kubectl kustomize <private-overlay-directory> > rendered.yaml
   grep 'image:' rendered.yaml
   ```

   Check both application image references against the reviewed registry digests.
   Stop if either reference contains an old or zero digest. Then commit and sync.
5. The backend pod stops, restarts on the new version, and applies database
   migrations. The service is down for one pod restart.
6. Check `/api/health`, then resume ArgoCD auto-sync.

Do not roll back through ArgoCD after a sync has completed. Migrations
only move forward. If a release is faulty, the maintainer ships the next
version, or you restore the pre-upgrade backup (`README.md`, restore
section) and lose everything written since.

## Restart a pod

```
oc rollout restart deployment/skein-backend -n <namespace>
```

This takes the service down for the length of one restart. The deployment
uses Recreate until the exact deployment passes cross-node storage, failure, and mixed-image compatibility checks.
Database-backed coordination does not replace those checks. PostgreSQL supports concurrent writers.
With scheduled jobs enabled, startup checks today's recovery files and retries
an incomplete backup on the same day. Existing job claims do not block the retry.
Check `/api/health` after the restart. If the backup still reports a failure,
read the pod log. Fix the cause, then run a manual backup.

## Replica and recovery rehearsals

Keep one replica with Recreate in the supported deployment.
Do not activate multiple replicas because a local Docker drill passes.
Use the `Replica validation gate` in `README.md` for a platform-approved disposable namespace.
The procedure requires explicit context and namespace values, a disposable label, and an opt-in before faults.
Its preflight checks RWX data and mirror claims, separate nodes, probes, runtime UIDs, and the disruption budget.
The optional storage probe writes unique markers and checks their bytes through both pods.
It does not certify Route draining, storage independence, or old/new migration compatibility.
Those gates need the recorded target-cluster fault procedure and a separate reviewed activation change.

For a local database and file rehearsal, run
`scripts/recovery-contract.sh <local-backend-image>` from a source checkout.
The script creates a disposable PostgreSQL 17 server with generated credentials.
It runs the restricted-role and pre-boot restore contracts with native PostgreSQL clients.
It never uses local development database shims, and it cleans only resources it created.
Save the image digests, database versions, test counts, and `exit=` result.
Do not treat a skipped or blocked cluster gate as a pass.

## Backups

Daily at 03:00 team time (the `timezone` field on `/api/health`). The last
14 full database dumps stay local. If the mirror is configured and available,
the last 30 core public-schema dumps stay there. These dumps contain all core
`public` tables and their rows except browser-session rows. They exclude
`private`, extension schemas, and artifact bytes. Protect them like the database. Artifact bytes need a
separate storage backup. Before a risky change, take one by hand: sign in as
an admin and use Settings → "Backups (team)" → "Back up now", or call
the API:

```
curl -X POST -H "Authorization: Bearer <admin-api-key>" \
    https://<backend-route-host>/api/admin/backup
```

The database dump and artifact storage snapshot are not atomic by default. For
a coordinated recovery point, stop every process with Skein database
credentials and scale the backend to zero. Use a one-shot pod labeled
`app=skein-maintenance` with PostgreSQL credentials and the `skein-data` mount.
Use the full-database `pg_dump` command in `README.md` with
`--exclude-table-data=public.browser_sessions` and no schema filter.
Keep all writers stopped until the storage snapshot completes.

Read the restore procedure in `README.md` before recovery.
Before application startup, run its SQL to invalidate restored keys and browser sessions
and mark restored agent requests as `completion_unknown`.
Apply it to external full copies too. The SQL skips tables absent from older backups.
A restored pending request can have executed after the backup point.
`SKEIN_SCHEDULER=0` does not stop shared-chat startup recovery or new explicit chat requests.
Keep ingress closed until credential, request, job-claim, and anchor reconciliation finishes.

## The exit

If this tool is retired or abandoned, the data is not trapped:

- PostgreSQL stores the database rows on the `skein-db` volume. Each daily
  `database-<date>-<backup-id>.dump` uses the standard `pg_dump` custom format. It contains
  public, private, and opted-in extension schemas in one snapshot.
- Artifact bodies are files on the Skein data volume. Database dumps contain
  their metadata rows, not those files. Copy the artifact volume for recovery.
- Settings → Backups → Download export returns portable work JSON from one
  database snapshot. A direct client can use
  `curl -fOJ -H "Authorization: Bearer sk-skein-..." <url>/api/admin/export/download`.
  The export excludes chats, private rows, review proposals, notifications,
  feedback, generated insights, the activity ledger, usage telemetry, context
  packs, deployment settings, scheduler state, extension schemas, and artifact
  bytes.
- With no model provider configured the app runs keyless indefinitely —
  abandonment degrades nothing except the agent features.

To decommission: copy the latest database dumps and the artifact volume off
the cluster. Take a JSON export if needed. Then delete the ArgoCD Application
and the namespace.

## Contact the maintainer

- `activity_chain.unverified` is negative, or the Insights page
  (`/insights`, where the daily 06:50 findings surface) reports a ledger
  fault you cannot explain.
- A job shows `"stale": true` and the pod log does not explain it.
- An upgrade leaves `/api/health` with an error that the overlay values do
  not explain.

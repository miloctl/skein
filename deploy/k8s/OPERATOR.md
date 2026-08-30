# Operator card

This page is for the person who runs Skein and did not build it. It
covers the OpenShift deployment (`deploy/k8s/`). The build-time decisions
and their reasons are in `README.md` in this directory — you do not need
them for routine operation.

An **admin** here means a person named in `SKEIN_ADMINS`, or in the IdP
group that `SKEIN_OIDC_ADMIN_GROUP` names. The admin API calls below need
that person's credential. Every `oc` command below needs your namespace:
add `-n <namespace>`.

## What this system needs from you

Nothing routine. Backups, ledger verification, and cleanup run on an
internal schedule. CI re-checks the code weekly. A bad model provider or
a bad config value degrades the feature and reports itself — it does not
take the service down. Check `/api/health` monthly. Upgrade when the
maintainer publishes a release, on your own schedule.

## The monthly check

Open `https://<backend-route-host>/api/health` with a credential — a
personal API key, or a signed-in browser session. The page always returns
200 when the process and the database are up: the content is the
diagnosis, not the status code. The open `/health` endpoint answers startup
and liveness probes. The open `/ready` endpoint answers readiness probes and
returns 503 when authentication configuration is invalid. Both carry only `ok`, `auth_mode`, and
`auth_error`, so the reason remains readable without a credential.

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
4. In the private deploy repo, set the new tags in the overlay's `images:`
   block. Commit and sync.
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
uses Recreate so two backend pods never overlap on in-process scheduler,
rate-limit, chat-turn, or file state. PostgreSQL supports concurrent writers.
Interrupted scheduled work re-runs on its next schedule, with
one exception: a restart during the nightly job band (03:00–07:00 team
time) can interrupt that day's backup after the day is already claimed.
The next `/api/health` response reports the failed latest attempt. Run a
manual backup if the Operations card shows that failure.

## Backups

Daily at 03:00 team time (the `timezone` field on `/api/health`). The last
14 full database dumps stay local. If the mirror is configured and available,
the last 30 core public-schema dumps stay there. These dumps contain all tables and rows in the core `public`
schema. They exclude `private`, extension schemas, and artifact bytes. Protect
them like the database. Artifact bytes need a
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
Run a full-database `pg_dump` with no schema filter, and keep all writers
stopped until the storage snapshot completes.

The restore procedure is in `README.md` — read it before you need it,
and note the anchor-log step at the end.

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

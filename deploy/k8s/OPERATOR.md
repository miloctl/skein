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
take the service down. Check `/health` monthly. Upgrade when the
maintainer publishes a release, on your own schedule.

## The monthly check

Open `https://<backend-route-host>/health`. No credential is needed. The
page always returns 200 when the process and the database are up — the
content is the diagnosis, not the status code.

| Field | Healthy value | If not |
|---|---|---|
| `auth_error` | `null` | Sign-in is broken for everyone. Read the message. Fix the named `SKEIN_*` value in the overlay and sync. |
| `provider_error`, `models_error`, `embeddings_error` | `null` | Agent chat degraded to mock. Deterministic features still work. Fix the named model setting when convenient. |
| `overlay_errors` | `[]` | A persona/playbook/flock ConfigMap did not mount. Check the volume mounts against the `SKEIN_*_DIR` values. |
| `timezone` / `timezone_error` | your team zone, error `null` | Rituals fire at UTC hours instead of local. Set `SKEIN_TZ` to an IANA `Region/City` name. |
| `jobs[]` | every entry has `"stale": false`; `last_success` is recent, or `null` on a fresh install (that is not a fault) | The flag sets at twice the job's period — for a daily job, `true` means roughly two days already missed. Read the pod log: `oc logs deployment/skein-backend`. If the log does not explain it, contact the maintainer. |
| `activity_chain.unverified` | `0` or a small positive number (rows since the last nightly verify) | A NEGATIVE value means the ledger is shorter than what was already verified. That is truncation. Do not restart anything. Contact the maintainer. |
| any other `*_error` field | `null` | Read the message — each one names its own fix. If the overlay values do not explain it, contact the maintainer. |

## Upgrade

1. The maintainer publishes images tagged `X.Y.Z` (backend) and
   `X.Y.Z-<env>` (frontend).
2. In the private deploy repo, set the new tags in the overlay's
   `images:` block. Commit.
3. ArgoCD syncs. The backend pod stops, restarts on the new version, and
   applies database migrations at startup. The service is down for the
   length of one pod restart. This is expected.
4. Check `/health` after the sync.

Do not roll back through ArgoCD after a sync has completed. Migrations
only move forward. If a release is faulty, the maintainer ships the next
version, or you restore the pre-upgrade backup (`README.md`, restore
section) and lose everything written since.

## Restart a pod

```
oc rollout restart deployment/skein-backend -n <namespace>
```

This takes the service down for the length of one restart (the
deployment strategy is Recreate, on purpose — the database has one
writer). Interrupted scheduled work re-runs on its next schedule, with
one exception: a restart during the nightly job band (03:00–07:00 team
time) can interrupt that day's backup after the day is already claimed.
The next `/health` check still shows the job green until the stale flag
catches up, so after a restart in that window, look for a `backup claim`
error line in the pod log — if it is there, run a manual backup (below).

## Backups

Daily at 03:00 team time (the `timezone` field on `/health`). The last
14 stay on the data volume, the last 30 on the mirror volume
(`/backup-mirror`). Before a risky change, take one by hand: sign in as
an admin and use Settings → "Backups (team)" → "Back up now", or call
the API:

```
curl -X POST -H "Authorization: Bearer <admin-api-key>" \
    https://<backend-route-host>/api/admin/backup
```

The restore procedure is in `README.md` — read it before you need it,
and note the anchor-log step at the end.

## The exit

If this tool is retired or abandoned, the data is not trapped:

- The data is one PostgreSQL database on the `skein-db` volume. The daily
  backups are the complete record: `platform-<date>.dump` and
  `private-<date>.dump`, both standard `pg_dump` custom-format files that
  `pg_restore` loads into any PostgreSQL server.
- The export (Settings → "Backups (team)" → "Download export", or
  `GET /api/admin/export` with an admin credential) returns the work
  data as JSON — tasks, promises, decisions, and the rest of the shared
  tables. The export deliberately excludes chat transcripts,
  private-visibility rows, and the private schema. For a
  complete copy, take the `.dump` backups, not the export.
- With no model provider configured the app runs keyless indefinitely —
  abandonment degrades nothing except the agent features.

To decommission: copy the latest backups off the cluster, take an export
if JSON is wanted, then delete the ArgoCD Application and the namespace.

## Contact the maintainer

- `activity_chain.unverified` is negative, or the Insights page
  (`/insights`, where the daily 06:50 findings surface) reports a ledger
  fault you cannot explain.
- A job shows `"stale": true` and the pod log does not explain it.
- An upgrade leaves `/health` with an error that the overlay values do
  not explain.

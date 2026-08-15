# Skein on OpenShift

For day-to-day operation by someone who did not build the system, read
`OPERATOR.md` in this directory instead — this file holds the build-time
decisions and their reasons.

This directory holds the Kustomize base for a single-team OpenShift
deployment, plus two example overlays. Copy an overlay into your private
deploy repo and replace every `example.com` value. The split is the same
one `deploy/README.md` describes for compose: this repo holds nothing
specific to your site.

`scripts/reference-deployment-contract.sh` renders both overlays in CI and
pins the decisions below. If you change the base, keep that script true.

## Topology: one replica, Recreate, block storage

The backend runs as exactly one process. The DATABASE no longer requires
that — PostgreSQL takes concurrent writers, and the check-then-write paths
hold real locks. Three things still do: the scheduler runs in-process,
rate caps and the chat turn registry live in process memory, and artifacts
and exports sit on a `ReadWriteOnce` volume that only one pod can mount.
The manifest pins `replicas: 1` and `strategy: Recreate` and names these
mechanisms in a comment. Do not raise the replica count until all three
move.

The data PVC holds artifacts, exports and the local backup copies. It must
bind to block storage (`ReadWriteOnce`), which is also what forces
`Recreate`. The database has its own volume, claimed by the `skein-db`
StatefulSet.

## The database

`base/postgres.yaml` runs one PostgreSQL StatefulSet with its own PVC. The
image tag pins a MAJOR version, and it must match the `pg_dump` the backend
image installs (`backend/Dockerfile`, `PG_MAJOR`): `pg_dump` refuses a
server newer than itself, and the failure lands in the nightly backup job
rather than at boot, so the daily copy just stops being written.

`skein-db-secret` carries FOUR keys, and the split is the point:

| key | who uses it |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | the bootstrap superuser, used once by initdb |
| `POSTGRES_DB` | the database name |
| `SKEIN_APP_USER` / `SKEIN_APP_PASSWORD` | the role **the backend connects as** |

The backend composes the app credentials with the host from the ConfigMap
into `SKEIN_DATABASE_URL`. There is no default: it refuses to start without
one rather than quietly serving an empty database.

**The application role must not be a superuser.** A superuser can
`COPY ... FROM PROGRAM`, which runs shell commands on the database pod, and
`pg_read_file`, which reads its filesystem — so any SQL bug, and any
extension (they supply raw SQL), escalates to command execution on that
container. `base/postgres.yaml` creates the role with `NOSUPERUSER` on first
boot. `/api/health` reports `database_warnings` if the backend connects
as a superuser anyway, which is the only signal a deployment that skipped
it gets. A managed PostgreSQL needs the same role created by hand.

Both passwords initialise the cluster on FIRST boot only. Changing the
Secret later changes nothing in the database — use `ALTER ROLE` and update
the Secret together.

**If your organization offers a managed PostgreSQL**, delete
`postgres.yaml` from the base, point `SKEIN_DB_HOST` at that server, and
put its credentials in the same Secret. Nothing else changes.

## ArgoCD

Point one ArgoCD `Application` at each overlay directory in your private
deploy repo. An upgrade is a commit that bumps the image tags in the
overlay. The sync recreates the backend pod, and the new pod applies
migrations at startup as the sole writer.

- **Upgrades take the service down** for the length of one pod restart.
  That is the cost of Recreate, and it is correct here. Do not move
  migrations to a pre-sync Job: the Job would fight the old pod for the
  RWO data volume, and old code would then serve a newer schema. (Two pods
  applying migrations at once is safe on its own — `init_db` takes an
  advisory lock — but that is not the reason Recreate is here.)
- **Roll forward only.** Migrations are append-only with no downgrades.
  After a sync has applied migrations, an ArgoCD rollback runs old code
  against a newer schema, and nothing tests that combination. Recovery
  from a bad release is the next version, or a restore from the
  pre-upgrade backup. The 03:00 daily backup is the rollback point. Before
  a risky sync, take a manual backup: Settings → "Backups (team)", or
  `POST /api/admin/backup`.

## Secrets

The Secret `skein-secrets` never goes in git. It holds the model provider
keys and the optional `SKEIN_FORGE_WEBHOOK_SECRET` and `SKEIN_ICS_TOKEN`.
Create it out of band, or manage it with the cluster's secret operator
(External Secrets, Sealed Secrets — whichever the platform team already
runs). A keyless mock deployment needs no Secret: the reference is
`optional: true`.

## Images

`scripts/publish-images.sh` pushes one backend image per version and one
frontend image per version per environment. The frontend bakes its API
URL at build time, so a prod image cannot serve dev. The overlay's
`images:` block picks the registry, the version, and the environment
suffix. Never use a moving tag: ArgoCD syncs what the tag names, and a
moved tag deploys untracked changes.

## PingFederate (oidc mode)

The backend validates the access token in-process as a JWT. Hand the IdP
team this list. Item 1 is the one that blocks everything.

1. A JSON Web Token Access Token Manager (ATM), RS256, mapped as this
   client's default. Opaque reference tokens do not work.
2. The ATM attribute contract must carry `iss`, `aud`, `exp`, the
   username claim (`preferred_username` preferred), and a multi-valued
   `groups` claim. `SKEIN_OIDC_USERNAME_CLAIM` and
   `SKEIN_OIDC_GROUPS_CLAIM` adapt to different names.
3. The exact issuer string, and whether discovery is served at it. If
   not, also the authorize URL, the token URL, and the ATM's JWKS URL —
   `SKEIN_OIDC_AUTHORIZE_URL`, `SKEIN_OIDC_TOKEN_URL`, and
   `SKEIN_OIDC_JWKS_URL` override discovery. Ask which JWKS carries the
   access-token signing keys: an ATM can publish its own, separate from
   the discovery document's.
4. The exact `aud` value the ATM stamps. Set it in
   `SKEIN_OIDC_AUDIENCE` verbatim.
5. A public OAuth client: no secret, Authorization Code grant, PKCE
   (S256). The token request arrives from the backend pod's egress IP,
   not from the browser.
6. The Refresh Token grant, enabled for this public client, rolling
   allowed. Without it, each user signs in again every access-token
   lifetime.
7. One registered redirect URI per environment:
   `https://<frontend-route-host>/auth/callback`.
8. The exact admin group string as the claim emits it. The match is
   case-sensitive.
9. An access-token lifetime of 2 minutes or more. The frontend refreshes
   60 seconds before expiry.

First sign-in creates a roster row for any user the IdP authenticates.
The PingFederate client assignment is therefore the access-control
boundary: scope the client to the intended population.

## Corporate CA and proxy

All IdP traffic uses the Python standard library. Set `SSL_CERT_FILE` to
the corporate CA bundle (the example-prod overlay mounts a `skein-ca`
ConfigMap), or mount the CA into the system trust store.
`REQUESTS_CA_BUNDLE` does nothing here. If the cluster injects a proxy,
put the IdP host in `NO_PROXY`.

## Backups, the mirror, and restore

Daily backups land on the data PVC. The base mounts a second PVC at
`/backup-mirror` and sets `SKEIN_BACKUP_MIRROR`, so a lost data volume
does not take the backups with it.

The mirror also anchors the activity ledger's tamper-evidence: the
nightly job appends the verified chain tip to an anchor log on both
volumes, and the daily findings rule replays every anchored line. That
comparison only means something when the mirror sits on an independent
storage backend. Decide one of these, in writing, in your deploy repo:

- Bind `skein-backup-mirror` to a storage class on separate hardware.
- Accept the reduced guarantee: same array, protection against volume
  loss only, not against an attacker who controls the storage.

**Restore** (drilled in `tests/test_admin_backup.py`):

1. Scale the backend to zero, so nothing writes during the load.
2. Load both dumps of the SAME date — they reference each other's people.
   Run from a pod that has the client tools and the backup volume: `oc debug`
   on the backend deployment has both.

   ```
   # PG* variables, never --dbname with the URL: argv is world-readable in
   # `ps` for every process on the node, and the URL carries the password.
   export PGHOST=skein-db PGPORT=5432 PGUSER=… PGDATABASE=…
   export PGPASSWORD=…            # from the skein-db-secret

   # --no-owner: the dump records the role that owned each object, and a
   # restore into a different role (a managed server, a renamed user) fails
   # object by object without it.
   # --dbname takes the NAME, not the URL: pg_restore must connect to restore
   # at all, and the name carries no password into argv.
   pg_restore --dbname "$PGDATABASE" --clean --if-exists --no-owner \
       platform-<date>.dump
   pg_restore --dbname "$PGDATABASE" --clean --if-exists --no-owner \
       private-<date>.dump
   ```

   Restore one `extension-<name>-<date>.dump` per extension store the
   deployment has — they are separate FILES, listed in the backup response.

   If the database itself is gone rather than damaged, create it first
   (`createdb "$PGDATABASE"`). `pg_restore` loads into an existing database
   and does not make one.
3. Scale back to one replica. Boot applies migrations newer than the
   backup.
4. Expect the anchor-log finding. Every line anchored after the backup
   date now points at rows the restore removed, so the nightly
   verification reports tampering daily — that is the check working,
   because a restore is a loss of history. After you confirm the restore
   explains the finding, trim both anchor logs
   (`backups/activity-anchors.log`, local and mirror) back to the backup
   date, and record the restore in a note. Trimming the logs is also
   what an attacker would do: never trim them on anyone else's word.

## What differs per environment

The overlays carry the full set: image tags, Route hosts,
`SKEIN_AUTH_MODE`, the `SKEIN_OIDC_*` block, `SKEIN_CORS_ORIGINS` (the
exact frontend origin — scheme and host, no trailing slash),
`SKEIN_ADMINS`, `SKEIN_TZ`, the model provider, and the CA mount. Dev
stays keyless: mock provider, trusted-header auth, no Secret.

Persona, playbook, and flock overlays translate from the compose pattern
to one `configMapGenerator` per directory, mounted at
`/overlay/<kind>` with the matching `SKEIN_*_DIR` variable. The
directories are small (under 100 KB total), far inside the ConfigMap
limit. `/api/health` reports `overlay_errors` when a variable points at a
directory that is not mounted.

## Observability

`/health` is the probe target: open, and only `ok`, `auth_mode` and
`auth_error` — the one fault readable without a credential, because a
broken auth config refuses every authenticated request. `/api/health` is
the diagnosis surface, behind identity: provider, timezone and overlay
errors, per-job last-success with stale flags, database warnings, and
the activity-chain state. Both return 200 whenever the process and the
database are up, even when degraded to mock — alert on the error fields,
not on the status code. Logs go to stdout as plain lines. There is no
Prometheus endpoint: if the platform team requires metrics or JSON logs,
that is new work — ask for their standard first.

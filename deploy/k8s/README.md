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
and exports use one shared file tree. The manifest pins `replicas: 1` and
`strategy: Recreate` so two backend pods never overlap. `ReadWriteOnce`
prevents cross-node attachment on common block storage. It does not prevent
two pods on one node from mounting the volume. Do not raise the replica count
until all three single-process mechanisms move.

The data PVC holds artifacts, exports and the local backup copies. It uses
block storage with `ReadWriteOnce`. The database has its own volume, claimed
by the `skein-db` StatefulSet.

## The database

`base/postgres.yaml` runs one PostgreSQL StatefulSet with its own PVC. The
digest pins the executed bytes. The tag documents the PostgreSQL major.

When the PostgreSQL major changes, update the tag and digest together. Also
update `backend/Dockerfile` (`PG_MAJOR`), Compose, CI services, and contract
scripts. `pg_dump` refuses a server newer than itself. This failure stops the
nightly backup instead of stopping application boot.

`skein-db-secret` carries FIVE keys, and the split is the point:

| key | who uses it |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` | the bootstrap superuser, used once by initdb |
| `POSTGRES_DB` | the database name |
| `SKEIN_APP_USER` / `SKEIN_APP_PASSWORD` | the role **the backend connects as** |

The backend composes the app credentials with the host from the ConfigMap
into a quoted conninfo in code (`config._database_url`), never into a URL in
the manifest — a password holding `@ : / % ? #` breaks URL parsing. There is
no default: it refuses to start without the components rather than quietly
serving an empty database.

**The application role must not be a superuser.** A superuser can
`COPY ... FROM PROGRAM`, which runs shell commands on the database pod, and
`pg_read_file`, which reads its filesystem — so any SQL bug, and any
extension (they supply raw SQL), escalates to command execution on that
container. `base/postgres.yaml` creates the role with `NOSUPERUSER` on first
boot. `/api/health` reports `database_warnings` if the backend connects
as a superuser anyway. `tests/test_database_role.py` runs the actual bootstrap
script twice, then applies migrations, private notes, an extension migration,
and a backup as the restricted role. A managed PostgreSQL needs the same role
created by hand.

Both passwords initialise the cluster on FIRST boot only. Changing the
Secret later changes nothing in the database — use `ALTER ROLE` and update
the Secret together.

**If your organization offers a managed PostgreSQL**, delete
`postgres.yaml` from the base, point `SKEIN_DB_HOST` at that server, and
put its credentials in the same Secret. As the database administrator,
pre-create the `private` schema and make the Skein application role its owner.
Do the same for each declared `ext_*` schema. Do not grant database-wide
`CREATE` to the application role.

For an existing database, run the same ownership step before this release:

```
CREATE SCHEMA IF NOT EXISTS private AUTHORIZATION <skein-app-role>;
ALTER SCHEMA private OWNER TO <skein-app-role>;
```

## ArgoCD

Point one ArgoCD `Application` at each overlay directory in your private
deploy repo. An upgrade is a commit that bumps the image tags and immutable
digests in the overlay. Replace every zero digest with the digest of the
reviewed registry image. The sync recreates the backend pod, and the new pod applies
migrations at startup as the sole writer.

- **Upgrades take the service down** for the length of one pod restart.
  That is the cost of Recreate, and it is correct here. Do not move
  migrations to a pre-sync Job: the Job can overlap the old pod, and old code
  would then serve a newer schema. (Two processes applying migrations at once
  is safe on its own — `init_db` takes an
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
`images:` block records the registry, version, environment suffix, and
digest. The digest prevents a moved tag from changing the executed bytes.
Keep version tags immutable for registry history and the publication step
that records this digest.

## PingFederate (oidc mode)

CAUTION: Do not deploy the current browser sign-in to production. The browser stores provider tokens and personal API keys in `localStorage`. Complete the roadmap BFF session first.

The backend validates the access token in-process as a JWT. Hand the IdP
team this list. Item 1 is the one that blocks everything.

1. A JSON Web Token Access Token Manager (ATM), RS256, mapped as this
   client's default. Opaque reference tokens do not work.
2. The ATM attribute contract must carry `iss`, `sub`, `aud`, `exp`, the
   username claim (`preferred_username` preferred), and a multi-valued
   `groups` claim. `SKEIN_OIDC_USERNAME_CLAIM` and
   `SKEIN_OIDC_GROUPS_CLAIM` adapt to different names.
3. The exact issuer string, and whether discovery is served at it. If
   not, also the authorize URL, the token URL, and the ATM's JWKS URL —
   `SKEIN_OIDC_AUTHORIZE_URL`, `SKEIN_OIDC_TOKEN_URL`, and
   `SKEIN_OIDC_JWKS_URL` override discovery. Ask which JWKS carries the
   access-token signing keys: an ATM can publish its own, separate from
   the discovery document's. All issuer and endpoint URLs must use HTTPS.
   Skein permits literal loopback HTTP only for local tests. Server-side
   redirects cannot change the origin.
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

First sign-in creates a roster row only when the username is new. Skein then
binds that row to the verified `(iss, sub)` pair. A changed username claim
does not move private data to a different subject.

Before an OIDC cutover, bind each existing roster user explicitly:

```sh
SKEIN_AUTH_MODE=oidc SKEIN_OIDC_ISSUER=https://idp.example.com \
  python -m app.bind_oidc '<subject>=<existing-user>'
```

The command refuses a subject or user that already has a different binding.
The PingFederate client assignment is also an access-control boundary. Scope
the client to the intended population.

## Corporate CA and proxy

All IdP traffic uses the Python standard library. Set `SSL_CERT_FILE` to
the corporate CA bundle (the example-prod overlay mounts a `skein-ca`
ConfigMap), or mount the CA into the system trust store.
`REQUESTS_CA_BUNDLE` does nothing here. If the cluster injects a proxy,
put the IdP host in `NO_PROXY`.

## Backups, the mirror, and restore

The daily local recovery unit is `database-<date>-<backup-id>.dump`. It contains the
`public` and `private` schemas, plus each extension schema that opted into
backup. One `pg_dump` process gives the file one PostgreSQL snapshot.

The same data PVC also holds artifact bytes under `/data/artifacts`. The dump
contains artifact metadata only. Full recovery needs a matching storage backup
of `/data`, or at minimum `/data/artifacts`, restored at the same path.

New and revised artifact rows carry the exact file SHA-256. Skein compares it
after reading the file and before it parses, returns, or otherwise uses the
bytes. Legacy rows with
no digest remain unchecked. This check detects a changed file or a mismatched
volume. It does not replace a coordinated database and storage snapshot. An
actor who can rewrite both PostgreSQL and the artifact volume can replace both
values without detection.

The base mounts a second PVC at `/backup-mirror` and sets
`SKEIN_BACKUP_MIRROR`. The directory must exist. Skein never creates it because
an absent mount must not become a local false mirror. The mirror receives:

- A `public`-schema `platform-<date>-<backup-id>.dump`. It contains all tables and rows in the core `public` schema, not
  only workspace-visible rows. It excludes `private`, extension schemas, and
  artifact bytes. Protect it like the full database dump.
- Its own append of `activity-anchors.log`.

The mirror does not contain private notes, extension schemas, or artifact
bytes. It is a partial recovery source. The application cannot determine if it
is off-box or on independent hardware. Record that storage decision in the
deploy repository.

The base sizes `skein-data` at 360Gi and the mirror at 320Gi against the
10Gi database request. The data PVC covers 14 full dumps, 14 public dumps,
one portable export, and artifact headroom. The mirror covers 30 public dumps.
These values do not assume compression. If an overlay changes the database
request or retention, patch both recovery volumes with the same calculation.

The daily database dump and an external storage snapshot are not one atomic
recovery point. For a coordinated manual point, stop every process with Skein
database credentials, including standalone MCP and extension workers. Scale
the backend to zero. From a one-shot pod labeled `app=skein-maintenance`, with
PostgreSQL credentials and the `skein-data` mount, run a full-database `pg_dump`
with no schema filter. Keep all writers stopped until the storage snapshot
completes. This full manual dump needs the same access controls as the database.

**Restore.** `tests/test_admin_backup.py` drills atomic archive load,
schema data, and artifact recovery. The test database uses its bootstrap
superuser. The deployment render contract pins the restricted application-role
shape. Rehearse that role handoff against the target PostgreSQL service.

1. Pause ArgoCD auto-sync. Remove or disable the backend Route, and check that
   its host is inaccessible. Stop standalone MCP and extension workers, then
   scale the backend to zero. Query `pg_stat_activity` and stop if a
   non-maintenance Skein session remains.
2. Restore the matching `skein-data` storage copy at `/data`. If only artifact
   files were copied, restore them at `/data/artifacts`.
3. As the platform database administrator, create a clean database that stays
   owned by the platform administrator. Grant the Skein role `CONNECT`, plus
   `USAGE` and `CREATE` on `public`. Pre-create `private` and each declared
   `ext_*` schema with the Skein role as owner. Do not grant database-wide
   `CREATE` to the Skein role.
4. Before the load, compare the dump against its recorded digest. Each backup
   appends `backup=<file> sha256=<hex>` to `activity-anchors.log` beside the
   dumps and on the mirror. If the values differ, or the local and mirror
   logs disagree, stop: the dump changed where it rested. Without a mirror
   the local log shares the backup volume, so this check detects an
   accident, not an attacker who can write both files.

   ```
   grep "backup=database-<date>-<backup-id>.dump" activity-anchors.log
   sha256sum database-<date>-<backup-id>.dump
   ```

5. Switch `PGUSER` and `PGPASSWORD` to the Skein application role. Remove schema
   creation entries from the archive list because the administrator already
   created the permitted schemas. Then restore the recovery unit:

   ```
   set -euo pipefail
   export PGHOST=skein-db PGPORT=5432 PGUSER=<skein-app-role> PGDATABASE=…
   export PGPASSWORD=…
   pg_restore --list database-<date>-<backup-id>.dump > restore.full.list
   test -s restore.full.list
   grep -q ' SCHEMA - private ' restore.full.list
   grep -q ' TABLE public tasks ' restore.full.list
   grep -v ' SCHEMA - ' restore.full.list > restore.list
   pg_restore --dbname "$PGDATABASE" --clean --if-exists --no-owner \
       --no-privileges --no-comments --single-transaction --exit-on-error \
       -L restore.list database-<date>-<backup-id>.dump
   ```

   If this command fails, stop. Its transaction leaves the pre-created empty
   schemas intact. Do not start the backend with a partial restore.

6. Read the restored verified anchor before you start the backend:

   ```
   psql -Atc "SELECT s.value || ' ' || h.value
       FROM app_settings s JOIN app_settings h ON h.key = 'activity_chain_hash'
       WHERE s.key = 'activity_chain_seq'"
   ```

7. Invalidate restored personal keys before traffic can reach the backend:

   ```
   psql -c "UPDATE api_keys SET active = 0"
   ```

   Reconcile `users.active` with the identity provider before you reopen ingress.
8. If the restored anchor is nonempty, require its exact `seq` and `hash` in at
   least one retained anchor log. If neither log contains it, stop. Do not write
   a new baseline over lost history. Then remove lines with a greater sequence.
   Keep the matching line and all earlier lines. Never trim these logs for
   another reason.
9. Set `SKEIN_SCHEDULER=0`, keep the Route absent, and scale the backend to one.
   Boot applies newer migrations without running catch-up jobs. Check health and
   record the restore in a note.
10. Reconcile `job_runs` one job at a time. `job_outcomes` does not store the
   claim `run_key`, so no generic join proves that a claim has its effect. Check
   each catch-up job's activity and domain receipt. Remove a claim only when its
   effect is absent and replay is safe. Keep the scheduler off until this is
   complete.
11. Reconcile the roster and mint replacement API keys. Then restore the scheduler
    setting, recreate the Route, restart stopped workers, and resume ArgoCD sync.

### Mirror-only partial recovery

Use `platform-<date>-<backup-id>.dump` only when the local recovery unit is
lost. This archive contains the complete core `public` schema, including chats,
key hashes, and private-visibility rows. It does not contain the `private`
schema, extension schemas, or artifact bytes.

Restore it with the same Route, scheduler, key, claim, and anchor controls. Then
initialize empty `private` and current extension schemas. Clear `artifacts`
metadata before ingress opens because no matching files survived. Record the
irreversible private-note, extension-data, and artifact losses. Protect this
archive like the database. `tests/test_admin_backup.py` drills this degraded
path separately.

## What differs per environment

The overlays carry the full set: image tags and digests, Route hosts,
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

Structured settings can use mounted YAML instead of one-line JSON. If a
document is credential-free, create it with `configMapGenerator`, mount
it, and point the matching `<NAME>_FILE` variable at it.
`SKEIN_MODEL_PRICES_FILE` is credential-free by schema.

If `SKEIN_MODELS_FILE` contains a credential in `params.extra_headers`,
mount it from a Secret. Apply the same rule to `SKEIN_MODEL_PARAMS_FILE`
when `extra_headers` contains a credential. If `SKEIN_MCP_SERVERS_FILE`
contains a literal `auth_token`, mount it from a Secret. A file that uses
`auth_token_env` can stay in a ConfigMap, but `skein-secrets` must supply
the named environment variable.

The example-prod overlay carries `backend-egress.yaml`, an egress allowlist
for the backend pod. It is the control that makes a personal MCP server
safe by construction: the URL check refuses this host and the metadata
service, the policy decides everything else. Add a row for each service
the deployment reaches before you apply it.

Skein's own MCP server is `<backend URL>/api/mcp-server`, behind the
perimeter like every other `/api` path. A person connects Claude Code with
their personal API key as the bearer; the backend venv is not needed on
their machine.

An OAuth sign-in for a personal MCP server registers
`<backend URL>/api/mcp/oauth/callback` as its redirect URI, built from the
request's own base URL. Behind the router, set `SKEIN_TRUST_PROXY_HOPS` so
the forwarded scheme is used, and keep one backend replica: a pending
sign-in lives in the process that started it.

`SKEIN_CREDENTIAL_KEY` also belongs in `skein-secrets`. It seals the tokens
people store for personal MCP servers through Settings. Without it, a
personal server can be added without a token only.

Set one form of a structured setting, never both. `/api/health` reports a
both-set fault for the three model settings.

## Observability

`/health` is the open startup and liveness target. `/ready` is the open
readiness target and returns 503 when authentication configuration is invalid. Both carry
only `ok`, `auth_mode`, and `auth_error`. `/api/health` is the diagnosis
surface behind identity. It carries provider, timezone and overlay errors,
per-job last-success with stale flags, database warnings, and activity-chain
state. `/health` and `/api/health` return 200 when the process and database are
up, including mock degradation. Alert on their error fields. Logs go to stdout
as plain lines. There is no
Prometheus endpoint: if the platform team requires metrics or JSON logs,
that is new work — ask for their standard first.

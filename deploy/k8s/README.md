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

The supported deployment keeps `replicas: 1` and `strategy: Recreate`.
Database-backed execution claims, shared deployment-wide rate counters, and
cross-process file locks are necessary controls, not proof of replica safety.
Do not raise the replica count until delayed-worker, process-loss, and
rolling-upgrade drills pass and every pod can read the same artifact and
recovery files. Per-person rate caps remain process-local.

`ReadWriteOnce` prevents cross-node attachment on common block storage. It
does not prevent pods on the same node from mounting the volume. Recreate
remains the upgrade boundary that prevents overlapping backend pods.

The data PVC holds artifacts, exports and the local backup copies. It uses
block storage with `ReadWriteOnce`. The database has its own volume, claimed
by the `skein-db` StatefulSet.

If the deployment can ever run more than one backend replica, create the
data PVC as `ReadWriteMany` on shared storage from the start. An access mode
cannot change on an existing claim, so a later move means a new claim and a
copy. The anchor-log append already serializes across processes through a
database lock, so a shared volume keeps one anchor history.

## Replica validation gate

Keep the base and normal overlays at one replica with Recreate.
A local Docker result does not prove cross-node storage or OpenShift Route behavior.
There is no activated multi-replica overlay in this repository.
Run the following procedure only in a disposable namespace approved by the platform team.
Do not use production credentials, production PVCs, a shared database, or real upstream side effects.
Do not drain a node or change cluster-wide storage configuration.

### Prepare the disposable candidate

Have the platform team create the namespace and label it
`skein.dev/durability-drill=disposable`. This label identifies the test target, not a production opt-in.
Use fresh database credentials and separate PVCs for all test state.
Keep ArgoCD and other reconcilers away from the candidate during faults.
Record the namespace UID, cluster identity, storage owner, image digests, and test start time.

The candidate configuration must meet these conditions before faults start:

| Area | Required candidate value |
|---|---|
| Backend | At least two replicas, `RollingUpdate`, `maxUnavailable: 0`, `maxSurge: 1` |
| Placement | Backend pods on separate nodes. Use required pod anti-affinity on `kubernetes.io/hostname`. Supply one eligible node per replica plus one for the surge pod |
| Data and mirror | Two distinct Bound `ReadWriteMany` claims, mounted at `/data` and `/backup-mirror` |
| Storage | The storage owner must identify independent failure domains for the mirror. Different PVC names alone prove nothing |
| Identity | The real restricted OpenShift security context. No fixed UID, root workaround, or privileged init container |
| Probes | `/ready` for readiness, `/health` for startup and liveness, at least 210 seconds for shutdown |
| Disruption budget | `policy/v1` PodDisruptionBudget, selector `app: skein-backend`, `minAvailable: 1` |
| Images | Reviewed immutable backend digests. Use mock and a disposable upstream fixture. Keep test hooks out of serving images |
| Database | PostgreSQL 17 and the restricted application role from `deploy/postgres-init/10-app-role.sh` |

Do not copy this candidate configuration into a normal overlay before every gate below passes.
Provisioning the test candidate is separate from activating a supported deployment.
A StorageClass must support real cross-node RWX, coherent file reads, and file locks.
Record storage snapshot and restore procedures for both recovery volumes.

Select the target explicitly. These commands never switch the current context:

```sh
set -euo pipefail
context='<approved-disposable-context>'
namespace='<approved-disposable-namespace>'
k() { kubectl --context "$context" --namespace "$namespace" --request-timeout=30s "$@"; }
guard() {
    python3 scripts/openshift-durability-check.py --context "$context" --namespace "$namespace" "$@"
}
k get namespace "$namespace" -o jsonpath='{.metadata.uid}{"\\n"}'
guard
```

The preflight reads configuration and checks probes through `exec`. It does not inject faults.
It refuses missing targets, unlabeled namespaces, RWO claims, one replica, unready pods, or same-node placement.
It prints actual pod image IDs and runtime UIDs without reading Secrets.
For an explicit storage write probe, run:

```sh
guard --probe-storage --confirm-namespace "$namespace"
```

This probe creates unique files with exclusive creation on both volumes.
Both pods must append, read the resulting bytes, and call `fsync` on separate nodes.
The probe deletes only markers it created successfully.
If access fails after creation, keep the printed run log and remove only that run's marker after access returns.
This tests assigned UID permissions, not independent storage hardware or all filesystem failure modes.

### Execute faults and keep the receipts

Before each fault, run `guard --allow-faults --confirm-namespace "$namespace"`.
This is an explicit opt-in check, not a command that injects faults.
Stop if it fails. Keep every later `kubectl` command bound to the same context and namespace.
Capture command exit codes, pod UIDs, node names, timestamps, logs, HTTP responses, and persisted receipts.
Do not count an HTTP response alone as proof of a committed effect.
`scripts/durability-contract.py` supplies the local container cases and their deterministic upstream fixture.
Its `scripts/fixtures/Durability.Dockerfile` derives a test image from the real backend image.
The fixture exercises normal HTTP routes and holds work at a controlled upstream boundary.
Port that fixture to the approved disposable namespace before the delayed-worker cluster cases.
Do not install the fixture in a normal deployment or count the Docker run as cluster proof.

1. Start bounded deterministic work through both direct pod endpoints and the backend Route.
   Record request IDs, execution lease tokens, artifact digests, and job claims before the fault.
   Keep one streaming chat open through the Route and send new requests through a separate client.
   Record the Route's timeout configuration and router version.
2. Watch `k get endpointslices -l kubernetes.io/service-name=skein-backend -w` from a separate terminal.
   Evict one explicitly selected backend pod through the Eviction API.
   Do not use pod deletion as proof of the disruption budget. Deletion bypasses it.

   ```sh
   pod='<recorded-candidate-backend-pod>'
   guard --allow-faults --confirm-namespace "$namespace"
   printf '{"apiVersion":"policy/v1","kind":"Eviction","metadata":{"name":"%s","namespace":"%s"}}' \
       "$pod" "$namespace" | k create --raw "/api/v1/namespaces/$namespace/pods/$pod/eviction" -f -
   ```

   Before replacement readiness, a second eviction that violates `minAvailable` must return HTTP 429.
   Do not force it. Check that new Route requests stop reaching the terminating pod.
   Check the existing stream completes within the grace period or reports its interrupted outcome honestly.
   No late worker can create a second reply, proposal, worklog entry, or external effect.
   Save EndpointSlice transitions and terminal request rows, then wait for replacement readiness.
3. Repeat the container failure cases with pods on separate nodes.
   Include a delayed worker past real lease expiry, stale resume, process loss, and commit-before-ack retry.
   Check one persisted effect for each receipt and explicit unknown outcomes for uncertain external calls.
   Test file reads through the other pod after every artifact write.
   Check matching artifact digests and both anchor logs after concurrent backup and ledger verification.
4. With the storage owner, deny the candidate's storage access without affecting any other namespace.
   Agree on the restoration command before this fault.
   A missing mirror must report a partial backup, not create a local replacement directory.
   Wrong artifact bytes must fail their digest check. Restore access and check both volumes again.
5. Rehearse database recovery against only the candidate StatefulSet or its dedicated managed database.
   For the supplied StatefulSet, an explicitly guarded restart is:

   ```sh
   guard --allow-faults --confirm-namespace "$namespace"
   k delete pod skein-db-0 --wait=false
   ```

   During database loss, `/ready` must return 503 within its bound and `/health` must remain live.
   After database recovery, readiness must return without duplicate job or execution effects.
   Also test loss for one backend while its peer can still reach PostgreSQL.
   Use only a platform-approved, namespace-scoped fault mechanism.
   Restore its access before the next gate. Do not modify node networking.
6. Run the complete restore procedure below against fresh candidate DB and file copies.
   Keep ingress closed until the pre-boot fence and anchor checks pass.
   Prove revoked browser authority and unfinished OAuth codes cannot return.
   Prove API keys remain disabled and restored work remains `completion_unknown` after boot.
   Check private notes, extension data, artifact bytes, and the independent mirror recovery boundary.

### Prove the exact old and new image pair

Migration serialization is not schema compatibility. A database lock only prevents simultaneous migration writers.
Do not overlap a version that predates the acquisition fences with a version that relies on them.
Before a rolling trial, compare both images' migration files and extension migration contracts.
Classify each change as compatible with old readers and writers, or stop the rolling trial.
Renames, drops, new required fields, changed enum values, and changed lease semantics need explicit compatibility evidence.
If compatibility fails, retain Recreate and use a stopped-writer upgrade.

Start the disposable candidate on the exact old digest and prepare a coordinated recovery point.
Record `schema_version`, extension versions, and migration file digests before the trial.
Run old-image reads and writes against a disposable database migrated by the new image first.
Then keep old-image requests active while the candidate rolls to the new digest:

```sh
new_image='<reviewed-registry-image>@sha256:<reviewed-digest>'
guard --allow-faults --confirm-namespace "$namespace"
k set image deployment/skein-backend "backend=$new_image"
k rollout status deployment/skein-backend --timeout=15m
```

Record actual overlapping old and new pod image IDs, not only the rollout exit code.
Repeat receipt, lease, session, artifact, and Route checks during overlap and after the last old pod exits.
If a check fails, close candidate ingress and use the saved restore procedure.
Do not undo migrations or treat a tag rollback as recovery.

Keep the signed-off target evidence in the private deployment repository.
A successful preflight is not approval to raise production replicas.
Activation needs storage-owner sign-off, all fault receipts, exact image-pair compatibility proof, and a separate reviewed deployment change.
If any target, credential, image, storage, or fault mechanism is absent, record that gate as blocked.

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

For each backend and environment-specific frontend tag, set its matching reviewed digest.
A tag-only change keeps the old digest and therefore the old image bytes.
Before a commit or sync, render the private overlay:

```sh
kubectl kustomize <private-overlay-directory> > rendered.yaml
grep 'image:' rendered.yaml
```

Check both application image references against the reviewed registry digests.
Stop if either reference contains an old or zero digest.

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

Browser sign-in keeps provider tokens on the server behind an opaque
`__Host-` cookie (`docs/BROWSER-SESSIONS.md`). It has three prerequisites
beyond the IdP list below. `SKEIN_CORS_ORIGINS` must name the exact
frontend origin: the backend does not trust forwarded-protocol headers, so
behind the edge-terminated Route the same-origin fallback never matches and
every sign-in answers `BROWSER_ORIGIN_DENIED` until the origin is listed.
The base kustomization sets no origin; each overlay must. `SKEIN_CREDENTIAL_KEY`
must be in `skein-secrets`. Both Routes must serve HTTPS on one schemeful
site.

The backend validates the access token in-process as a JWT. Hand the IdP
team this list. Item 1 is the one that blocks everything.

1. A JSON Web Token Access Token Manager (ATM), RS256, mapped as this
   client's default. Opaque reference tokens do not work.
2. The ATM attribute contract must carry `iss`, `sub`, `aud`, `exp`, the
   username claim (`preferred_username` preferred), and a multi-valued
   `groups` claim. `SKEIN_OIDC_USERNAME_CLAIM` and
   `SKEIN_OIDC_GROUPS_CLAIM` adapt to different names. If the ATM cannot
   carry groups, set `SKEIN_OIDC_GROUPS_SOURCE=userinfo`: Skein then reads
   the same claim from the issuer's userinfo endpoint, once per access
   token, and checks that its `sub` matches the token. The userinfo
   response must then carry the groups claim, and the client's scopes must
   release it. `SKEIN_OIDC_USERINFO_URL` overrides discovery.
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
Browser-session tables remain in the archive, but their rows do not.
Recovery requires a new browser sign-in.

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

- A `public`-schema `platform-<date>-<backup-id>.dump`. It contains all core
  `public` tables and their rows except browser-session rows, not only
  workspace-visible rows. It excludes `private`, extension schemas, and
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
with no schema filter. Set the PostgreSQL connection variables for the intended
source database first. Exclude browser sessions and MCP sign-in flows so a restore
cannot reactivate revoked sessions or unfinished grants:

```sh
set -euo pipefail
umask 077
mkdir -p /data/backups
backup_file="/data/backups/database-$(date -u +%Y%m%dT%H%M%SZ)-manual.dump"
pg_dump --format=custom --exclude-table-data=public.browser_sessions --exclude-table-data=public.mcp_oauth_flows --file "$backup_file"
sha256sum "$backup_file"
```

Record the digest with the storage snapshot identifier in the recovery record.
This manual command does not append a digest to `activity-anchors.log`.
Keep all writers stopped until the storage snapshot completes.
This full manual dump needs the same access controls as the database.

### Disposable local restore contract

Run `scripts/recovery-contract.sh <local-backend-image>` from a source checkout.
Reuse the reviewed backend image from the container drill, or build `backend/Dockerfile` locally first.
The script starts its own PostgreSQL 17 server with generated credentials and unique Docker resource labels.
It publishes no ports and never reads the local PostgreSQL client shims.
It deletes only container and network IDs it created. It retains the supplied image.
Test dependencies install inside its disposable runner, not inside the serving image.
Tests import the current checkout through a read-only mount. The image supplies Python, application dependencies, and PostgreSQL clients.
This contract does not certify that mounted source matches the image's packaged application bytes.

The script enables `SKEIN_ROLE_CONTRACT=1` for `tests/test_database_role.py`.
The real bootstrap utility removes superuser, database-create, role-create, replication, and RLS-bypass privileges.
The restricted role runs migrations, writes private and extension data, takes a backup, and restores it.
The restore list excludes schema creation because the administrator pre-creates allowed schemas.
The role never receives database-wide `CREATE`.

`tests/test_recovery_runbook.py` executes the pre-boot SQL below, not a parallel implementation.
Its combined restricted-role drill checks file loss and recovery, dump digests, both anchor logs, and ledger verification.
It checks disabled keys, cleared browser and OAuth rows, terminal unknown requests, and no replay through application boot.
A separate full-archive drill proves the fence invalidates a session revoked after the archive point.
The local mirror uses a separate tmpfs filesystem. It does not prove independent storage hardware.
`tests/test_admin_backup.py` also checks full and mirror-only recovery boundaries.

Save the script output, image digests, PostgreSQL versions, and final `exit=` value with the test evidence.
The upgrade-render tests run separately on a host with `kubectl`.
Local success does not replace the target-cluster role and storage handoff.

**Restore.** Rehearse this procedure against the target PostgreSQL service and its assigned runtime UID.

1. Pause ArgoCD auto-sync. Close all ingress, including the frontend proxy,
   backend Route, and MCP access. Check that those paths are inaccessible.
   Stop standalone MCP and extension workers, then scale the backend to zero.
   Query `pg_stat_activity` and stop if a non-maintenance Skein session remains.
   Keep every application process stopped until the pre-boot steps below finish.
2. Restore the matching `skein-data` storage copy at `/data`. If only artifact
   files were copied, restore them at `/data/artifacts`.
3. As the platform database administrator, create a clean database that stays
   owned by the platform administrator. Grant the Skein role `CONNECT`, plus
   `USAGE` and `CREATE` on `public`. Pre-create `private` and each declared
   `ext_*` schema with the Skein role as owner. Do not grant database-wide
   `CREATE` to the Skein role.
4. Before the load, compare the dump against its recorded digest.
   For a manual dump, use the digest recorded with the storage snapshot identifier.
   Each Skein-generated backup appends `backup=<file> sha256=<hex>` to
   `activity-anchors.log` beside the dumps and on the mirror.
   If the values differ, or the local and mirror logs disagree, stop.
   The dump changed where it rested. Without a mirror, the local log shares the backup volume.
   This check detects an accident, not an attacker who can write both files.

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

7. Before application startup, invalidate restored credentials and stop restored
   agent requests. Run this against the restored database from the maintenance pod:

   ```sh
   psql -X --set ON_ERROR_STOP=on --single-transaction <<'SQL'
   UPDATE public.api_keys SET active = 0;
   DO $restore$
   DECLARE
       recovered_at text := to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC',
                                    'YYYY-MM-DD"T"HH24:MI:SS"+00:00"');
   BEGIN
       IF to_regclass('public.browser_sessions') IS NOT NULL THEN
           DELETE FROM public.browser_sessions;
       END IF;
       IF to_regclass('public.mcp_oauth_flows') IS NOT NULL THEN
           DELETE FROM public.mcp_oauth_flows;
       END IF;
       IF to_regclass('public.mcp_servers') IS NOT NULL THEN
           UPDATE public.mcp_servers
           SET auth_token_sealed = NULL, oauth_tokens_sealed = NULL,
               oauth_client_sealed = NULL;
       END IF;
       IF to_regclass('public.chat_agent_runs') IS NOT NULL THEN
           UPDATE public.chat_agent_runs
           SET status = 'completion_unknown', finished_at = recovered_at,
               execution_active = FALSE, error_code = 'restore_reconciliation'
           WHERE status IN ('pending', 'running');
           UPDATE public.chat_agent_runs SET execution_active = FALSE
           WHERE execution_active = TRUE;
       END IF;
       IF to_regclass('public.agent_wakeups') IS NOT NULL THEN
           UPDATE public.agent_wakeups
           SET status = 'completion_unknown', finished_at = recovered_at,
               rerun_requested = 0, reason = 'restore_reconciliation'
           WHERE status IN ('pending', 'running');
       END IF;
   END;
   $restore$;
   SQL
   ```

   If this command fails, stop. Do not start any application process.
   It skips tables absent from older backups, but an unexpected schema error stops recovery.
   Run it for every recovery source, including external full copies and mirror-only dumps.
   External copies can contain sessions revoked after the backup and unfinished MCP
   sign-in flows. Older copies can hold unsealed authorization codes. Delete all flow
   rows before startup, including rows that have not expired. These deletions require
   new sign-ins without changing the credential-sealing key. Personal MCP server
   rows are kept, but their bearer tokens and OAuth sign-ins are removed: a
   token revoked or a server deleted after the backup must not come back
   usable. Each owner enters the token or signs in again.

   `completion_unknown` means the outcome needs operator reconciliation.
   Even a restored `pending` request can have executed after the backup point.
   Keep the source archive unchanged. Record this operation in the recovery record.
   The command keeps chat messages, request identifiers, job claims, and activity history.
   Reconcile `users.active` with the identity provider before you reopen ingress.
8. If the restored anchor is nonempty, require its exact `seq` and `hash` in at
   least one retained anchor log. If neither log contains it, stop. Do not write
   a new baseline over lost history. Then remove lines with a greater sequence.
   Keep the matching line and all earlier lines. Never trim these logs for
   another reason.
9. Set `SKEIN_SCHEDULER=0`, keep ingress closed, and scale the backend to one.
   Boot applies newer migrations without scheduled jobs or catch-up jobs.
   The scheduler flag does not stop shared-chat startup recovery or explicit chat requests.
   Delegation startup recovery can also return an interrupted follow-up request to `pending`.
   Step 7 prevents both queues from replaying restored work.
   Check health through maintenance access and record the restore in a note.
10. Reconcile `job_runs` one job at a time. `job_outcomes` does not store the
   claim `run_key`, so no generic join proves that a claim has its effect. Check
   each catch-up job's activity and domain receipt. Remove a claim only when its
   effect is absent and replay is safe. Never delete claims as a group.
   Check external systems too. A missing restored receipt does not prove that
   an action never occurred after the backup point.

   Reconcile requests marked `restore_reconciliation` in `chat_agent_runs` and
   `agent_wakeups` against chat replies, proposals, worklog entries, and external effects.
   Keep unknown requests terminal. Do not reset them to `pending` for automatic replay.
   After reconciliation, request any remaining work through a new explicit chat message
   or human delegation. Keep the scheduler off and ingress closed until reconciliation finishes.
   Reconcile extension event deliveries and extension-owned queues before restarting their workers.
   Their scheduled drains stop with `SKEIN_SCHEDULER=0`, but their replay rules belong to each extension.
11. Reconcile the roster and mint replacement API keys. Require a new browser sign-in.
    Then restore the scheduler setting, reopen ingress, restart stopped workers,
    and resume ArgoCD sync.

### Mirror-only partial recovery

Use `platform-<date>-<backup-id>.dump` only when the local recovery unit is
lost. This archive contains the complete core `public` schema, including chats,
key hashes, and private-visibility rows. Browser-session rows are excluded.
It does not contain the `private` schema, extension schemas, or artifact bytes.

Restore it with the same ingress, scheduler, credential, request, claim, and anchor controls.
Then initialize empty `private` and current extension schemas. Clear `artifacts`
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

Trusted-header auth accepts any name in the `X-User` header. On a public
Route, any caller can act as any team member. The example-dev overlay
therefore sets `haproxy.router.openshift.io/ip_whitelist` on both Routes.
Its value `192.0.2.0/24` is a documentation range that admits nobody.
Replace it with your office or VPN ranges before you apply the overlay.

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
their personal API key as the bearer, read from `SKEIN_API_KEY` by a
`.mcp.json` entry so the key never sits in a shell history; the backend
venv is not needed on their machine. Tool bodies share the backend's sync
thread pool with the REST handlers, so size it for both.

An OAuth sign-in for a personal MCP server registers
`<backend URL>/api/mcp/oauth/callback` as its redirect URI, built from the
request's own base URL. Behind the router, set `SKEIN_TRUST_PROXY_HOPS` so
the forwarded scheme is used. Pending sign-in ownership and sealed callback codes live in PostgreSQL.
Keep one backend replica until the deployment validation gates pass.

`SKEIN_CREDENTIAL_KEY` also belongs in `skein-secrets`. It seals the tokens
people store for personal MCP servers through Settings. Without it, a
personal server can be added without a token only.

Set one form of a structured setting, never both. `/api/health` reports a
both-set fault for the three model settings.

## Observability

`/health` is the open startup and liveness target. `/ready` is the open
readiness target. It returns 503 when authentication configuration is invalid or the bounded database check fails.
Liveness does not depend on database availability. Both probes carry
only `ok`, `auth_mode`, and `auth_error`. `/api/health` is the diagnosis
surface behind identity. It carries provider, timezone and overlay errors,
per-job last-success with stale flags, database warnings, and activity-chain
state. `/health` and `/api/health` return 200 when the process and database are
up, including mock degradation. Alert on their error fields. Logs go to stdout
as plain lines. There is no
Prometheus endpoint: if the platform team requires metrics or JSON logs,
that is new work — ask for their standard first.

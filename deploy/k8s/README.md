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

The backend runs as exactly one process. SQLite has one writer, the
scheduler runs in-process, rate caps and the chat turn registry live in
process memory. The manifest pins `replicas: 1` and `strategy: Recreate`
and names these mechanisms in a comment. Do not raise the replica count.

The data PVC must bind to block storage (`ReadWriteOnce`). SQLite runs in
WAL mode, and WAL is unsafe on NFS. Do not bind the claim to an NFS or
RWX storage class.

## ArgoCD

Point one ArgoCD `Application` at each overlay directory in your private
deploy repo. An upgrade is a commit that bumps the image tags in the
overlay. The sync recreates the backend pod, and the new pod applies
migrations at startup as the sole writer.

- **Upgrades take the service down** for the length of one pod restart.
  That is the cost of Recreate, and it is correct here. Do not move
  migrations to a pre-sync Job: the Job would fight the old pod for the
  RWO volume and the SQLite write lock, and old code would then serve a
  newer schema.
- **Roll forward only.** Migrations are append-only with no downgrades.
  After a sync has applied migrations, an ArgoCD rollback runs old code
  against a newer schema, and nothing tests that combination. Recovery
  from a bad release is the next version, or a restore from the
  pre-upgrade backup. The 03:00 daily backup is the rollback point. Before
  a risky sync, take a manual backup: `POST /api/admin/backup`.

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

1. Scale the backend to zero. SQLite must have no writer.
2. Copy `platform-<date>.db` over `/data/platform.db` and
   `private-<date>.db` over `/data/private.db`, both from the same date.
   Use `oc debug` with the PVC mounted, or a one-off pod.
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
limit. `/health` reports `overlay_errors` when a variable points at a
directory that is not mounted.

## Observability

`/health` is the probe target and the diagnosis surface: auth, provider,
timezone and overlay errors, per-job last-success with stale flags, and
the activity-chain state. It returns 200 whenever the process and the
database are up, even when degraded to mock — alert on its error fields,
not on its status code. Logs go to stdout as plain lines. There is no
Prometheus endpoint: if the platform team requires metrics or JSON logs,
that is new work — ask for their standard first.

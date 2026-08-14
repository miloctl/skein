# Deploying Skein

This file covers the docker-compose deployment. For OpenShift/Kubernetes
with ArgoCD, use `deploy/k8s/` — its README carries the topology
constraints, the restore procedure, and the identity-provider checklist.
The private-repo split below applies to both.

Run Skein from this public repo plus one small private repo per deployment.
The private repo holds everything specific to your site. This repo never
does. That split is what lets you upgrade by pulling, and reuse Skein at the
next place without a fork.

## What the private deploy repo holds

```
your-skein-deploy/
├── backend.env                 # becomes backend/.env on the box
├── docker-compose.override.yml # overlay mounts, ports, mirror volume
├── playbooks/                  # your playbooks (*.yaml) — SKEIN_PLAYBOOKS_DIR
├── personas/                   # your bench (*.md, pack.json) — SKEIN_PERSONAS_DIR
├── flocks/                     # your flocks (*.yaml) — SKEIN_FLOCKS_DIR
└── README.md                   # box facts: ports in use, mirror target,
                                # runner name, bridge services
```

An overlay file with the same slug as a stock file replaces it. That is how
you tailor `incident.yaml` or re-head `code-reviewer` without editing this
repo.

## Example override

```yaml
# docker-compose.override.yml
services:
  backend:
    volumes:
      - ./playbooks:/overlay/playbooks:ro
      - ./personas:/overlay/personas:ro
      - ./flocks:/overlay/flocks:ro
      - /mnt/nas-backups/skein:/backup-mirror
    environment:
      SKEIN_PLAYBOOKS_DIR: /overlay/playbooks
      SKEIN_PERSONAS_DIR: /overlay/personas
      SKEIN_FLOCKS_DIR: /overlay/flocks
      SKEIN_BACKUP_MIRROR: /backup-mirror
```

`/health` reports `overlay_errors` when an overlay variable points at a
directory that is not mounted — check it after the first start.

## CI on a self-hosted Gitea

`.gitea/workflows/ci.yml` derives the clone URL from the runtime context. It
needs two repository secrets: `CI_CHECKOUT_USER` (the token owner's
username) and `CI_CHECKOUT_TOKEN` (a read-scoped access token). If your
runner shares a host with a live deployment, keep the workflow push-only —
untrusted `pull_request` code must never execute there.

## Upgrading

```bash
git pull                        # this repo
docker compose up --build -d    # migrations apply at startup
```

Migrations are append-only, so a pull never rewrites applied schema. Read
`docs/FEATURES.md` for what shipped.

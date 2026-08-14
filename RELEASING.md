# Releasing Skein

## Container images go to the deployment's own registry

The Kubernetes deployment (`deploy/k8s/`) pulls versioned images from a
registry that the target cluster can reach — for the first deployment,
the workplace-internal registry. `scripts/publish-images.sh` builds and
pushes them: one backend image per version, one frontend image per
version per environment (the frontend bakes its API URL at build time).
Log in to the registry, then run it from the release tag:

```bash
SKEIN_REGISTRY=<registry>/<path> ./scripts/publish-images.sh X.Y.Z \
    prod=<backend-route-url>,<frontend-route-url>
```

The script refuses a version that does not match `backend/pyproject.toml`,
the same guard the CI publish job carries. No image has shipped through it
yet — the first push proves it.

## Where the wheel and npm package go is not settled yet

Nothing has been published. `v0.2.1` and `v0.2.2` are tags on a local
`main`; neither has reached a remote, the `publish` job in
`.gitea/workflows/ci.yml` has never run, and the package registry it
uploads to is not enabled. Read the publish job as a written intention,
not as a path anyone has walked.

Settle the destination before the next tag, because the answer changes the
job rather than merely configuring it:

- **Gitea** is where the code lives, and the job is already written for it.
  That instance is PRIVATE, so a wheel published there is reachable only by
  people and machines that can already reach the instance. That is the
  right answer for a purely internal tool, and the wrong one the moment a
  teammate's laptop or an OpenShift pull secret needs the artifact.
- **GitHub** is public and is where the mirror lives, so artifacts are
  reachable without handing out access to the private instance. It suits
  this repository's Apache-2.0 license and public README. It also means
  publishing the artifact to the world, which is a decision about the
  product, not about CI.

Whichever it is, the gates in section 2 stay the same. Only the publish
step and its token move.

## The tag

A release is a `v*` tag on `main`. The tag is what starts a publish job,
and the job refuses a tag that does not match the version declared in
`backend/pyproject.toml`.

If a step fails, stop, fix the cause, and start that section again.

## 1. Close the release content

1. Write the release notes in `CHANGELOG.md`, under the three headings the
   file explains: Contracts, Behavior, Operations.
2. Check that `docs/ROADMAP.md` holds no item that this release ships.
   Delete each shipped row in the commit that shipped it.
3. Set `version` in `backend/pyproject.toml` to the release number.
   The publish job refuses a tag that does not match this value.

## 2. Run the gates

Run each gate from the repo root. These are the same gates CI runs on push.

1. `./scripts/lint.sh`
2. `./scripts/audit-deps.sh`
3. `(cd backend && .venv/bin/pytest -q -n auto --cov=app --cov-fail-under=90)`
4. `(cd frontend && npm run test:coverage)`
5. `(cd frontend && npm run build)`
6. `(cd frontend && npx playwright test)`
7. `(cd frontend && npx playwright test --config playwright.oidc.config.ts)`
8. `./scripts/upgrade-path.sh`

Step 7 walks the oidc sign-in, which is the production auth mode. It builds
its own frontend, so it takes several minutes. If a walk fails after you
edited a component, rebuild first: the walk serves `.next-oidc`, and
`npm run build` writes `.next`.

If the release changed `app/services/`, also run
`./scripts/mutation-test.sh <module>` for each changed module. Judge the
survivors: a surviving mutation of a permission check, a cap, or a recorded
field is a missing test. Write that test before the tag.

## 3. Walk the app

The suites run against seeded data and fixed walks. A release also needs
one pass of human eyes on what it changed.

1. Start the stack with `./scripts/skein.sh start`.
2. Open the app and walk each surface the release touched.
3. Check the browser console on each walked page. It must stay empty.
4. Complete one write end to end, and check that the result renders.
5. Stop the stack with `./scripts/skein.sh stop`.

## 4. Tag and publish

1. Commit and push `main`.
2. Wait for the push-triggered CI run to pass.
3. Tag the release commit: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. Watch the tag run. The publish job runs after every other job passes.
5. Check that the registry shows the new wheel and host archive.
6. Push the container images with `scripts/publish-images.sh` (first
   section). A deployment pulls by version tag, so ArgoCD sees the release
   only after this step and the overlay's image tag bump.

The first tag that reaches a remote proves this section, and until one
does, treat steps 4 and 5 as untested. The Gitea publish job needs its
package registry enabled and a `PACKAGE_TOKEN` secret with
`packages:write`; without them it fails, which is deliberate — a release
that publishes nowhere is worse than a release that stops.

## 5. After the tag

A published release does not move. The registry refuses a republished
version, so a moved tag leaves a release that is half one tree and half
another. If the release carries a fault, fix forward: the fix ships as the
next version.

After the first production deployment that carries a migration, that
migration keeps its filename for good. `CLAUDE.md` explains why a rename
re-runs the migration and bricks the boot.

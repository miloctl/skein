# Releasing Skein

## Current release state

No Python package, npm package, or image has completed the release path. The local tags stop at `v0.2.3`.

The current package identities are:

- `skein-agents`
- `@skein/extension-api`
- `@skein/frontend-host`

The Gitea package registry is not enabled. Treat the publish job as an untested release path.

## Select the package registries

Select one authoritative Python index before the first package release. The index must not fall through to public indexes for private names.

Do not use `--extra-index-url` for `skein-agents`. pip selects candidates by version, not by index priority.

A hash-verified wheelhouse with `--no-index` is also permitted.

Publish both npm packages to the same registry. Configure the `@skein` scope for that registry.

The Gitea publish job needs `PACKAGE_TOKEN` with `packages:write`. The tag job must fail if the token is absent.

## Publish container images

The target deployment registry stores the final images. For the first workplace deployment, use the workplace registry.

Run this command from the release tag:

```bash
SKEIN_REGISTRY=<registry>/<path> ./scripts/publish-images.sh X.Y.Z \
  prod=<backend-route-url>,<frontend-route-url>
```

The frontend image contains its API and site URLs. Build one frontend image for each environment.

No image has completed this script. The first push validates the image-release path.

## Prepare the release

A release is a `v*` tag on `main`. The tag must match `backend/pyproject.toml`.

If a step fails, fix the cause before you continue.

1. Update `CHANGELOG.md`.
2. Remove each shipped item from `docs/ROADMAP.md`.
3. Set the release version in `backend/pyproject.toml`.
4. Set the same core version in the frontend host and CLI.
5. Update the Atlas compatibility range only when its supported range changes.
6. Regenerate all changed locks.

## Run the release gates

Run these commands from the repository root:

```sh
./scripts/lint.sh
./scripts/audit-deps.sh
SKEIN_CONTRACT_RUN_ID=release \
SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein \
  ./scripts/reference-extension-contract.sh
./scripts/reference-frontend-contract.sh
./scripts/reference-deployment-contract.sh
./scripts/reference-images-contract.sh
./scripts/upgrade-path.sh
(cd backend && .venv/bin/pytest -q -n auto --cov=app --cov-fail-under=90)
(cd frontend && npm run test:coverage)
(cd frontend && npm run build)
(cd frontend && npx playwright test)
(cd frontend && npx playwright test --config playwright.oidc.config.ts)
```

If the release changes `app/services/`, run `./scripts/mutation-test.sh <module>` for each changed module.

## Walk the application

1. Start the stack with `./scripts/skein.sh start`.
2. Open each changed surface.
3. Examine the browser console.
4. Complete one write.
5. Make sure that the result appears.
6. Stop the stack with `./scripts/skein.sh stop`.

## Tag and publish

1. Commit and push `main`.
2. Wait for the push CI run.
3. Create and push tag `vX.Y.Z`.
4. Watch the tag run.
5. Make sure that the registry contains the wheel and both npm packages.
6. Push the deployment images.
7. Update the private deployment repository with the new image digests.

The package job builds each release artifact once. The dependency contracts use those artifacts, and the tag job publishes the same files.

The tag job publishes the extension API before the frontend host.

The tag job does not create registry configuration. Missing registry access must stop the job.

## After the tag

Do not move a published release. Do not publish the same version again.

If a release has a fault, publish a new version.

After the first production migration, keep its filename. A renamed migration runs again on an existing database.

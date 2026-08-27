# Releasing Skein

## Current release state

Release `0.3.0` completed publication and registry pull-back validation. Tag `v0.3.0` records the published release commit.

The package identities are:

- `skein-agents` on public PyPI.
- `@miloctl/skein-extension-api` on GitHub Packages.
- `@miloctl/skein-frontend-host` on GitHub Packages.

The npm packages are private. The release workflow does not publish images. Workplace repositories build and own their final images.

## Configure the accounts

Keep these account settings for each release pull request.

### GitHub

1. Enable Actions for `miloctl/skein`.
2. Confirm that repository policy permits job-level `packages: write`.
3. Protect `main` with pull requests and the required GitHub CI checks.
4. Restrict direct pushes and force pushes to `main`.
5. Create GitHub environments named `pypi` and `npm`.
6. Restrict both environments to `main`.
7. Add a required reviewer to both environments.

Publication starts only when a reviewed release pull request changes `.github/release-version` on protected `main`. Tags do not trigger publication.

The npm publisher uses `GITHUB_TOKEN`. Do not add a package-publishing PAT to the Skein repository.

After the first npm publication, confirm that both packages are private and linked to `miloctl/skein`.

### PyPI

Create a pending Trusted Publisher with these exact values:

```text
Project: skein-agents
Owner: miloctl
Repository: skein
Workflow: ci.yml
Environment: pypi
```

The pending publisher does not reserve the project name. The first successful publication creates the project.

PyPI is public. The published wheel contains the Skein Python source and package content.

## Configure workplace access

A local machine or non-GitHub CI process needs a classic GitHub PAT with `read:packages`.

Store that token in the workplace secret manager. Configure npm with:

```ini
@miloctl:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NPM_TOKEN}
```

GitHub Packages requires authentication for installation, including installation of public packages.

A GitHub Actions workplace repository can use `GITHUB_TOKEN` after each package grants that repository read access.

## Prepare the release

A release starts when a reviewed pull request changes `.github/release-version` on protected `main`. The marker must match `backend/pyproject.toml`.

The release tag records a successful publication. It does not start one.

If a step fails, fix the cause before you continue.

1. Update `CHANGELOG.md`.
2. Remove each shipped item from `docs/ROADMAP.md`.
3. Set the release version in `backend/pyproject.toml`.
4. Set the same core version in the frontend host and CLI.
5. Update the Atlas compatibility range only when its supported range changes.
6. Regenerate all changed locks with Node 22 and Python 3.12.
7. Set `.github/release-version` to the exact release version.

## Run the release gates

Run these commands from the repository root:

```sh
./scripts/lint.sh
./scripts/audit-deps.sh
SKEIN_CONTRACT_RUN_ID=release \
SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein \
  ./scripts/reference-extension-contract.sh
SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein \
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

The frontend package contract starts the installed workplace backend, signed test identities, and copied standalone runtime. Its browser walk includes one core write.

If the release changes `app/services/`, run `./scripts/mutation-test.sh <module>` for each changed module.

## Walk the application

1. Start the stack with `./scripts/skein.sh start`.
2. Open each changed surface.
3. Examine the browser console.
4. Complete one write.
5. Check that the result appears.
6. Stop the stack with `./scripts/skein.sh stop`.

## Push the release

The local GitHub remote is named `github`.

1. Open a release pull request that includes the version, changelog, locks, and release marker.
2. Wait for every required GitHub check.
3. Merge the reviewed pull request into protected `main`.
4. Wait for all five gates and `release-guard` on the `main` push.
5. Approve the protected `pypi` and `npm` environments.
6. Watch both publication jobs.
7. Create tag `vX.Y.Z` on the published `main` commit.
8. Push the tag to the `github` remote.

Example release preparation:

```sh
printf '0.3.0\n' >.github/release-version
git add .github/release-version CHANGELOG.md
git commit -m "release: v0.3.0"
git push github release/v0.3.0
```

After the release pull request publishes successfully:

```sh
git tag -a v0.3.0 -m "Skein 0.3.0" <published-main-sha>
git push github v0.3.0
```

The protected `main` workflow builds each artifact once. The contracts and publishers consume the same uploaded files.

The workflow publishes the extension API before the frontend host. PyPI publication uses GitHub OIDC and stores no PyPI token.

## Retry a partial publication

Do not change `.github/release-version`. Copy the original release run ID from its Actions URL.

Open the GitHub `ci` workflow and select **Run workflow**. Select branch `main` and enter the original release run ID.

The manual run repeats all five gates. It then checks the original run through the GitHub API.

The retry proceeds only when the original run passed every release gate, attempted both publishers, and still holds one usable `release-packages` artifact. A normal CI run is not a release source.

Both publishers consume that original artifact. An identical published file is a no-op, a missing file publishes, and different bytes fail closed.

Approve both the `pypi` and `npm` environments. If the original artifact expired, publish a new version instead of rebuilding the old version from current source.

## Validate the published packages

Do not treat a successful upload as completed publication. Pull each package into a new empty directory.

```sh
rm -rf /tmp/skein-release
mkdir -p /tmp/skein-release

python -m pip download --no-deps \
  --dest /tmp/skein-release \
  skein-agents==0.3.0 \
  --index-url https://pypi.org/simple

npm pack @miloctl/skein-extension-api@1.0.0 \
  --pack-destination /tmp/skein-release
npm pack @miloctl/skein-frontend-host@0.3.0 \
  --pack-destination /tmp/skein-release
```

Compare the pulled files with the tested GitHub Actions artifact. Use SHA-256 for the wheel and npm SHA-512 integrity for each tarball.

Run each package-consuming contract against the pulled files:

```sh
SKEIN_RELEASE_DIST=/tmp/skein-release \
SKEIN_CONTRACT_RUN_ID=pullback \
SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein \
  ./scripts/reference-extension-contract.sh
SKEIN_RELEASE_DIST=/tmp/skein-release \
SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein \
  ./scripts/reference-frontend-contract.sh
SKEIN_RELEASE_DIST=/tmp/skein-release \
  ./scripts/reference-images-contract.sh
./scripts/reference-deployment-contract.sh
```

Without `SKEIN_RELEASE_DIST`, the contracts rebuild local source artifacts and do not test the registry pull-back.

## Publish workplace images

The package workflow does not publish GHCR images. The workplace repository builds and pushes its own final images.

Record each pushed image digest in the workplace deployment repository. Do not deploy a mutable tag without its digest.

## After the tag

Do not move a published tag. Do not overwrite a published package version.

If a release has a fault, publish a new version.

After the first production migration, keep its filename. A renamed migration runs again on an existing database.

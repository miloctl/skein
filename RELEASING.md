# Releasing Skein

## Current release state

The release marker names the package line for this revision. Registry pull-back and the matching annotated tag are the authority for completed publication.

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
5. Create GitHub environments named `pypi`, `npm`, and `release-finalization`.
6. Restrict all three environments to protected `main`.
7. Add a required reviewer and disable self-approval for each environment.
8. Add a `v*` tag ruleset that permits creation by GitHub Actions and refuses updates or deletion.

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

1. Add the release notes to the three `CHANGELOG.md` Unreleased sections.
2. Remove each shipped item from `docs/ROADMAP.md`.
3. Run `python3.12 scripts/prepare-release.py X.Y.Z`.
4. Review every generated change.

The script updates all synchronized versions and artifact paths. It builds the exact artifacts before it regenerates the locks. It writes `.github/release-version` last.

The finalized prior tag must be reachable through Git remote `github`. If the trusted remote has another name, set `SKEIN_RELEASE_REMOTE` to that name.

### When the release crosses a minor boundary

The extension compatibility windows name the first incompatible core
version (`maximum_core_exclusive`, and the `<X.Y.0` pip bound). A release
that reaches that version must move every window forward FIRST, in its own
reviewed commit, or the script refuses with a count mismatch — a bound that
names the new version is a compatibility claim, not stale release text.
The windows live in `extension.toml`, `module.py`, the frontend manifest,
and their doc examples: search for the old bound before you run the script.
Moving a window forward asserts the extension works on the new core. The
passing contract suite is that evidence — do not move a window the suite
has not earned.

### Recover an abandoned prepared release

If a release was prepared (the marker changed) but never published and
finalized, the next preparation refuses because the finalized prior tag
does not exist. Recovery: complete the abandoned release's publication
(push its marker commit, run the publishers, finalize with the run ID). If
that release predates the finalize workflow itself, create the annotated
tag by hand at the release commit and push it — a one-time bootstrap, done
for `v0.3.2` on 2026-08-30, never the normal path.

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
7. Open the `finalize-release` workflow on branch `main` and enter the original release run ID.
8. Approve the protected `release-finalization` environment.
9. Confirm that the workflow creates annotated tag `vX.Y.Z` on the published `main` commit.

Example release preparation:

```sh
python3.12 scripts/prepare-release.py X.Y.Z
./scripts/lint.sh
git diff --check
```

After publication succeeds, the protected finalization workflow pulls the registry files and compares them with the original tested artifact. It creates the annotated tag only after every byte matches.

The protected `main` workflow builds each artifact once. The contracts and publishers consume the same uploaded files.

The workflow publishes the extension API before the frontend host. PyPI publication uses GitHub OIDC and stores no PyPI token.

## Retry a partial publication

Do not change `.github/release-version`. Copy the original release run ID from its Actions URL.

Open the GitHub `ci` workflow and select **Run workflow**. Select branch `main` and enter the original release run ID.

The manual run repeats all five gates. It then checks the original run through the GitHub API.

The retry proceeds only when the original run passed every release gate, attempted both publishers, and still holds one usable `release-packages` artifact. A normal CI run is not a release source.

Both publishers consume that original artifact. An identical published file is a no-op, a missing file publishes, and different bytes fail closed.

Approve both the `pypi` and `npm` environments. If the original artifact expired, publish a new version instead of rebuilding the old version from current source.

## Finalize the published release

Do not treat a successful upload as completed publication. Open the GitHub `finalize-release` workflow on branch `main` and enter the original release run ID.

The workflow validates the original gated run and downloads its immutable artifact ID. It inspects the three package identities and versions without extracting them.

The workflow pulls the matching PyPI wheel and both GitHub npm tarballs with bounded retries. It compares the wheel with SHA-256 and each npm tarball with SHA-512.

After the registry bytes match, approve the protected `release-finalization` environment. The tag job creates annotated tag `vX.Y.Z` at the original release SHA.

An existing identical annotated tag is a no-op. A missing package, byte mismatch, rebuilt artifact, wrong SHA, lightweight tag, or different tag target fails closed.

## Publish workplace images

The package workflow does not publish GHCR images. The workplace repository builds and pushes its own final images.

Record each pushed image digest in the workplace deployment repository. Do not deploy a mutable tag without its digest.

## After the tag

Do not move a published tag. Do not overwrite a published package version.

If a release has a fault, publish a new version.

After the first production migration, keep its filename. A renamed migration runs again on an existing database.

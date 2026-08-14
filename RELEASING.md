# Releasing Skein

A release is a `v*` tag on `main`. The tag starts the `publish` job in
`.gitea/workflows/ci.yml`. That job builds the wheel and the frontend host
archive, and uploads both to the Gitea package registry. It also publishes
`@skein/extension-api` when that package carries a new version.

This is the procedure the 0.2.x releases followed. If a step fails, stop,
fix the cause, and start that section again.

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
2. `(cd backend && .venv/bin/pytest -q -n auto --cov=app --cov-fail-under=90)`
3. `(cd frontend && npm run test:coverage)`
4. `(cd frontend && npm run build)`
5. `(cd frontend && npx playwright test)`
6. `./scripts/upgrade-path.sh`

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
4. Watch the tag run. The `publish` job runs after every other job passes.
5. Check that the registry shows the new wheel and host archive.

The `publish` job needs the `PACKAGE_TOKEN` secret with `packages:write`.
If the job fails on authentication, set that secret and re-run the job.

## 5. After the tag

A published release does not move. The registry refuses a republished
version, so a moved tag leaves a release that is half one tree and half
another. If the release carries a fault, fix forward: the fix ships as the
next version.

After the first production deployment that carries a migration, that
migration keeps its filename for good. `CLAUDE.md` explains why a rename
re-runs the migration and bricks the boot.

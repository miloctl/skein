# Atlas workplace extension

Atlas is a fictional private extension. It shows how a workplace application can depend on Skein without a Skein source checkout.

This README labels each command by execution context. Keep the copied-consumer commands in an external repository. Remove Skein-checkout commands and do not copy Skein scripts.

The workplace repository owns these items:

- The Atlas Python package.
- The Atlas frontend package.
- One Python production lock.
- One Python test lock.
- One npm lock.
- Content overlays.
- Final runtime images.
- Deployment files.

Skein does not scan installed packages. The Atlas composition root explicitly passes its module to `create_app`.

## Customize before you create locks

Rename the starter as one identity. Check all these surfaces:

- The Python distribution and import package.
- The root npm package and frontend workspace.
- The extension ID, contribution IDs, policy actions, and capabilities.
- Service identities, API routes, and environment variables.
- The extension store and its derived PostgreSQL schema.
- Content filenames and content references.
- Backend and frontend images.
- ConfigMaps, Secrets, tests, dependency lock inputs and generated locks, and documentation.

Search tracked paths and file contents for `atlas` after the rename. Also check compiled frontend extension text and `kubectl kustomize .` output. Those generated surfaces can keep an old identity after the source rename is complete.

Regenerate artifacts and locks in this order:

1. Rename manifests, source, content, tests, and deployment files.
2. Build the private extension wheel.
3. Download the Skein wheel and repack the two Skein npm packages into `dist/`.
4. Use Node 22 to regenerate `package-lock.json`.
5. Regenerate `requirements.lock` when the Python production graph changes.
6. Regenerate `requirements-test.lock` when the Python production or test graph changes.
7. Run `npm ci`, `pip check`, and every package, image, deployment, and browser contract.

Do not hand-edit a lock. Keep the exact first-party artifacts in `dist/` while you create the npm lock and build the final images.

## Package boundaries

The backend depends on `skein-agents>=0.3.0,<0.4.0`. The distribution installs the public `app.*` imports.

The frontend root pins these exact dependencies:

- `@miloctl/skein-frontend-host@0.3.2`
- `@miloctl/skein-extension-api@1.0.0`
- `next@16.2.11`
- `react@19.2.4`
- `react-dom@19.2.4`

The core wheel comes from PyPI. The two private npm packages come from GitHub Packages under the `@miloctl` scope.

The executable contract packs the current source into local tarballs before `npm ci`. This proves the same package boundary without publishing during tests.

The root also overrides `postcss` to `8.5.23` and `sharp` to `0.35.3`. Installed package overrides do not affect the root installation.

Atlas uses Node 22. The Atlas frontend remains a local npm workspace. The workplace root compiles it before it runs `skein-frontend-build`.

Atlas stores its data in PostgreSQL schema `ext_atlas_extension`. It has no database file or private data volume.

## Test group-gated identity

`trusted-header` supplies a weak user name and no enterprise groups. Atlas navigation and routes stay unavailable in this mode.

Use signed OIDC groups for permitted paths. The Skein-checkout browser contract uses these identities:

- `ava` has no Atlas group and receives a policy denial.
- `nina` has `atlas-integrations` and can synchronize.
- `mira` has `atlas-delivery-managers` and can open the Atlas dashboard.

A copied workplace must use its own test identity provider. Do not trust a browser-supplied group header.

## Run the backend package contract from a Skein checkout

Start PostgreSQL. Then run the installed-wheel transition contract:

```sh
SKEIN_CONTRACT_RUN_ID=atlas_local \
SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein \
  scripts/reference-extension-contract.sh
```

The contract does these operations:

1. It installs Skein 0.2.3 and Atlas 1.x from pinned wheels.
2. It creates core and extension data.
3. It removes both old distributions.
4. It installs `skein-agents` 0.3.2 and Atlas 2.0 from new wheels.
5. It starts the application against the same database.
6. It compares fresh and upgraded core and Atlas schemas.

The Atlas test suite imports only these public surfaces:

- `app.extensions`
- `app.public`
- `app.main.create_app`

The import-boundary test refuses other `app.*` imports.

## Run backend tests from a copied consumer root

Install the hash-locked test closure before the two first-party wheels:

```sh
uv venv --python 3.12 .venv-test
uv pip install --python .venv-test/bin/python \
  --require-hashes -r requirements-test.lock
uv pip install --python .venv-test/bin/python --no-deps \
  dist/skein_agents-0.3.2-py3-none-any.whl \
  dist/atlas_skein_extension-2.0.0-py3-none-any.whl
uv pip check --python .venv-test/bin/python
```

The test fixture sets `SKEIN_AUTH_MODE=trusted-header` before the first `app` import. It refuses an unsafe database name.

Create an empty disposable PostgreSQL database for each run:

```sh
dropdb --if-exists --host 127.0.0.1 --username skein skein_atlas_test
createdb --host 127.0.0.1 --username skein skein_atlas_test

SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein_atlas_test \
  .venv-test/bin/python -m pytest -q backend/tests

dropdb --host 127.0.0.1 --username skein skein_atlas_test
```

Do not run extension tests against a populated development database.

## Run the frontend package contract from a Skein checkout

Run this command from the Skein repository root:

```sh
SKEIN_DATABASE_URL=postgresql://skein:skein@127.0.0.1:5432/skein \
  scripts/reference-frontend-contract.sh
```

The contract packs the host and extension API. It copies Atlas to a clean directory and runs one locked npm installation.

The build command is:

```sh
npm run build:frontend
```

This command compiles the Atlas workspace. Then it runs:

```sh
skein-frontend-build @atlas/skein-extension
```

The host command writes `dist/frontend`. The directory contains a standalone `server.js`, traced dependencies, static files, and public assets.

The host command does not install packages. It does not change `package.json`, `package-lock.json`, or `node_modules`.

The reference contract also proves these conditions:

- The Atlas manifest is in the production build.
- The Atlas-only `mt-[7px]` class is in the production CSS.
- Host and API tarballs have integrity entries in the lock.
- A second build removes stale output.
- Inherited build variables cannot select another extension.
- The standalone server starts after the temporary stage is deleted.
- Signed OIDC users exercise the denied, integration, and manager paths.
- The manager creates one core task through the package-built browser runtime.

## Run content validation from a Skein checkout

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.content \
  --playbooks examples/workplace-extension/content/playbooks \
  --personas examples/workplace-extension/content/personas \
  --flocks examples/workplace-extension/content/flocks \
  --workflow-action atlas.workplace.notify-manager
```

## Compose the MCP process from a copied consumer root

The API and MCP processes use the same module tuple:

```sh
SKEIN_MCP_MODULES=atlas_skein.composition SKEIN_MCP_USER=you-mcp \
  python -m app.mcp_server
```

Do not maintain a second composition list.

## Build the runtime images from a Skein checkout

Run the executable image contract:

```sh
scripts/reference-images-contract.sh
```

The contract builds the Skein wheel, Atlas wheel, frontend host, and extension API. It then builds the two final Atlas images.

The backend image installs the combined production lock. It installs the two first-party wheels with `--no-deps`.

The frontend image installs the workplace npm lock. It copies only the completed standalone output into the runtime stage.

The workplace images do not inherit Skein application images.

## Render the reference deployment from a Skein checkout

Create `atlas-skein-secrets` through the deployment secret manager. Do not commit secret values.

Render the OpenShift-compatible example:

```sh
kubectl kustomize examples/workplace-extension
```

Apply it only after you configure the database, image names, Secrets, Routes, and storage class.

## Run clean-consumer acceptance

Run these checks from the consumer repository, outside the Skein source tree:

- Import `app` from the installed wheel under `site-packages`, not from `PYTHONPATH` or an editable Skein checkout.
- Install tests from `requirements-test.lock`, then install both first-party wheels with `--no-deps`.
- Copy `dist/frontend` to a temporary directory outside the repository and start `node server.js` there.
- Use signed OIDC users to test denied, manager, and integration group paths.
- Run `kubectl kustomize .` from the consumer root without a path into a Skein checkout.
- Confirm that the repository contains no Skein source directory or Skein Git history.
- After a rename, confirm that the prior starter identity is absent from tracked paths, source text, compiled frontend extension text, and rendered deployment output.

Do not put real workplace names, URLs, or credentials in this example.

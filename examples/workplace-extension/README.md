# Atlas workplace extension

Atlas is a fictional private extension. It shows how a workplace application can depend on Skein without a Skein source checkout.

The workplace repository owns these items:

- The Atlas Python package.
- The Atlas frontend package.
- One Python production lock.
- One npm lock.
- Content overlays.
- Final runtime images.
- Deployment files.

Skein does not scan installed packages. The Atlas composition root explicitly passes its module to `create_app`.

## Package boundaries

The backend depends on `skein-agents>=0.3.0,<0.4.0`. The distribution installs the public `app.*` imports.

The frontend root pins these exact dependencies:

- `@skein/frontend-host@0.3.0`
- `@skein/extension-api@1.0.0`
- `next@16.2.11`
- `react@19.2.4`
- `react-dom@19.2.4`

The root also overrides `postcss` to `8.5.23` and `sharp` to `0.35.3`. Installed package overrides do not affect the root installation.

Atlas uses Node 22. The Atlas frontend remains a local npm workspace. The workplace root compiles it before it runs `skein-frontend-build`.

Atlas stores its data in PostgreSQL schema `ext_atlas_extension`. It has no database file or private data volume.

## Run the backend package contract

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
4. It installs `skein-agents` 0.3.0 and Atlas 2.0 from new wheels.
5. It starts the application against the same database.
6. It compares fresh and upgraded core and Atlas schemas.

The Atlas test suite imports only these public surfaces:

- `app.extensions`
- `app.public`
- `app.main.create_app`

The import-boundary test refuses other `app.*` imports.

## Run the frontend package contract

Run this command from the Skein repository root:

```sh
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

## Run content validation

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.content \
  --playbooks examples/workplace-extension/content/playbooks \
  --personas examples/workplace-extension/content/personas \
  --flocks examples/workplace-extension/content/flocks \
  --workflow-action atlas.workplace.notify-manager
```

## Compose the MCP process

The API and MCP processes use the same module tuple:

```sh
SKEIN_MCP_MODULES=atlas_skein.composition SKEIN_MCP_USER=you-mcp \
  python -m app.mcp_server
```

Do not maintain a second composition list.

## Build the runtime images

Run the executable image contract:

```sh
scripts/reference-images-contract.sh
```

The contract builds the Skein wheel, Atlas wheel, frontend host, and extension API. It then builds the two final Atlas images.

The backend image installs the combined production lock. It installs the two first-party wheels with `--no-deps`.

The frontend image installs the workplace npm lock. It copies only the completed standalone output into the runtime stage.

The workplace images do not inherit Skein application images.

## Deploy

Create `atlas-skein-secrets` through the deployment secret manager. Do not commit secret values.

Render the OpenShift-compatible example:

```sh
kubectl kustomize examples/workplace-extension
```

Apply it only after you configure the database, image names, Secrets, Routes, and storage class.

Do not put real workplace names, URLs, or credentials in this example.

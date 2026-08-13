# Atlas workplace extension

Atlas is a fictional private extension. It shows how a workplace repository can
depend on Skein without copying the Skein source tree.

The example uses explicit startup and build-time composition. Skein does not
scan installed packages or run unknown code.

## Contents

- `backend/` provides a Python package and a private composition root.
- `frontend/` provides a packed JavaScript extension and TypeScript declarations.
- `content/` provides versioned playbook, persona, and flock overlays.
- `deployment/` provides a derivative image and a Kustomize overlay.
- `extension.toml` declares compatible backend and frontend API versions.

The backend package contributes one router, job, identity mapper, policy rule,
context source, governed tool, specialist, event subscriber, migration stream,
and workflow action. Its structured data stays in `atlas-extension.db`.

## Verify the backend package

Run this command from the Skein repository root:

```sh
scripts/reference-extension-contract.sh
```

The script builds separate Skein and Atlas wheels. It installs them into a
normal virtual environment and starts the installed application. It rejects
the old core. It then moves the unchanged Atlas package from core `0.2.0` to a
compatible `0.2.1` artifact built from a different source tree. The test keeps
the private Atlas data. It runs a real Atlas synchronization on both cores.
It also checks the Atlas source against both public interfaces with strict
mypy. It applies migrations 018 through 020 and the required legacy
identity-owner claims. Run
`scripts/upgrade-path.sh` to verify the historical base-to-current migrations,
fresh-schema equality, and activity-chain integrity.

Atlas migration 4 stores a short synchronization claim before a core create.
This claim prevents route and job retries from creating two tasks for one
Atlas item when the extension mapping write fails.

`backend/tests/` holds the extension test suite. It uses only the public
surfaces (`registry_for`, `execute_tool`, `dispatch_events`, `create_app`,
and REST), and it covers registration, policy, tool gating, event
idempotency, provenance, data ownership, and disabling the extension. Copy
this pattern into a private repository. Run it with:

```sh
PYTHONPATH=backend:examples/workplace-extension/backend/src \
  backend/.venv/bin/pytest examples/workplace-extension/backend/tests
```

## Verify the frontend package

Run the clean package rehearsal:

```sh
scripts/reference-frontend-contract.sh
```

The script compiles `index.tsx` and packs the unchanged private package. It
creates Skein frontend host artifacts for core `0.2.0` and `0.2.1`. It installs
the same Atlas archive into each host and runs two production builds.

`@skein/extension-api` is a peer dependency and is not in
`devDependencies`. A private repository installs the packed archive before
the TypeScript build:

```sh
npm install --save-dev --legacy-peer-deps skein-extension-api-1.0.0.tgz
```

Use `--save-dev`, not `--no-save`. npm skips a bare archive argument under
`--no-save`. The `--legacy-peer-deps` flag stops npm from fetching the
`react` peer. The frontend host build supplies React.

A workplace build can then install the package archive. Do not add Atlas to
the core `package.json`.

```sh
cd examples/workplace-extension/frontend
npm_config_cache=/tmp/atlas-npm-cache npm pack --pack-destination /tmp
cd ../../../frontend
npm install --no-save --package-lock=false --legacy-peer-deps \
  /tmp/atlas-skein-extension-1.0.0.tgz
SKEIN_FRONTEND_EXTENSIONS=@atlas/skein-extension npm run build
npm run --silent compose:extensions
```

The last command restores the empty core manifest. A workplace deployment sets
`SKEIN_FRONTEND_EXTENSIONS` during its image build.

For an independent workplace repository, use
`scripts/package-frontend-host.sh` to create a versioned source archive. You can
also derive from the `host` stage in `frontend/Dockerfile`. The example
`deployment/Frontend.Dockerfile` shows the container workflow.

## Compose the MCP process

The standalone MCP server composes the same modules as the ASGI root:

```sh
SKEIN_MCP_MODULES=atlas_skein.composition SKEIN_MCP_USER=you-mcp \
  python -m app.mcp_server
```

`atlas_skein.composition` exports the one `modules` tuple that
`atlas_skein.app` also uses. Do not maintain two composition lists.

## Run content validation

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.content \
  --playbooks examples/workplace-extension/content/playbooks \
  --personas examples/workplace-extension/content/personas \
  --flocks examples/workplace-extension/content/flocks \
  --workflow-action atlas.workplace.notify-manager
```

## Deploy

The deployment README gives the exact wheel, frontend archive, and image build
commands. Run the executable image rehearsal from the Skein root:

```sh
scripts/reference-images-contract.sh
```

The overlay stores the extension database on its own volume. It uses the
`atlas-skein-secrets` Secret for the token sent by `AtlasHttpClient`. Create
that Secret outside Git before you apply the overlay:

```sh
kubectl kustomize examples/workplace-extension
kubectl apply -k examples/workplace-extension
```

Do not put real workplace names, URLs, or credentials in this example.

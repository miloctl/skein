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
compatible `0.2.1` artifact. That artifact contains an additive migration.
The test keeps the private Atlas data.

## Verify the frontend package

Run the clean package rehearsal:

```sh
scripts/reference-frontend-contract.sh
```

The script compiles `index.tsx` and packs the unchanged private package. It
creates Skein frontend host artifacts for core `0.2.0` and `0.2.1`. It installs
the same Atlas archive into each host and runs two production builds.

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

## Run content validation

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.content \
  --playbooks examples/workplace-extension/content/playbooks \
  --personas examples/workplace-extension/content/personas \
  --flocks examples/workplace-extension/content/flocks
```

## Deploy

Build the Atlas wheel into `dist/`. Then build `deployment/Dockerfile` with a
released Skein image as `SKEIN_IMAGE`. Build
`deployment/Frontend.Dockerfile` with a compatible `SKEIN_FRONTEND_HOST` image.
Store the database on its own volume. Create the `atlas-skein-secrets` Secret
outside Git before you apply the Kustomize overlay.

Do not put real workplace names, URLs, or credentials in this example.

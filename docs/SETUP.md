# Workplace setup

Use this guide to create a private workplace repository that depends on Skein packages. The workplace repository does not need the Skein source tree.

Skein uses three first-party packages:

- `skein-agents` provides the Python backend.
- `@skein/frontend-host` provides the Next.js host and production build command.
- `@skein/extension-api` provides the public frontend types and components.

The workplace repository owns its extension code, dependency locks, content, final images, and deployment files.

## Current release status

The Skein packages are not published at this time. Complete the registry and publication steps in `RELEASING.md` before a workplace installs from a registry.

For a local rehearsal, use the artifact procedure in [Use local artifacts before publication](#use-local-artifacts-before-publication).

## Prerequisites

Install or obtain these tools and services:

- Python 3.12.
- `uv` and `pip`.
- Node.js 22 and npm.
- Docker or another OCI image builder.
- PostgreSQL 17 client tools.
- `kubectl` for Kustomize validation.
- A controlled Python package index.
- A controlled npm mirror and private `@skein` registry.
- A workplace image registry.
- A PostgreSQL 17 database.

Do not use `--extra-index-url` for `skein-agents`. Pip does not give the first index priority over the extra index.

## 1. Create the workplace repository

Create this minimum layout:

```text
workplace-skein/
├── backend/
│   └── src/workplace_skein/
│       ├── app.py
│       ├── composition.py
│       └── module.py
├── frontend/
│   ├── index.tsx
│   ├── package.json
│   └── tsconfig.json
├── content/
│   ├── flocks/
│   ├── personas/
│   └── playbooks/
├── deployment/
│   ├── 10-app-role.sh
│   ├── 20-workplace-schema.sh
│   ├── Dockerfile
│   ├── Frontend.Dockerfile
│   └── skein.yaml
├── dist/
├── extension.toml
├── package.json
├── package-lock.json
├── pyproject.toml
├── requirements.in
└── requirements.lock
```

Ignore these generated paths:

```gitignore
.env*
*.env
*.key
*.pem
.npmrc
build/
dist/
.skein/
node_modules/
frontend/node_modules/
frontend/dist/
```

Keep secret examples free of real values. Store credentials in the workplace secret manager.

## 2. Configure the package registries

Configure pip authentication outside the repository. The Python index must contain only trusted packages or the complete controlled dependency mirror.

Route normal npm packages through the controlled npm mirror. Route the private scope through the Skein registry:

```ini
registry=https://<controlled-npm-mirror>/
@skein:registry=https://<host>/api/packages/<owner>/npm/
//<host>/api/packages/<owner>/npm/:_authToken=${NPM_TOKEN}
```

Do not commit the token. Supply `NPM_TOKEN` through the CI secret manager.

## 3. Stage the exact Skein packages

Download the backend wheel without its dependencies:

```sh
mkdir -p dist
python -m pip download --no-deps --dest dist \
  skein-agents==0.3.0 \
  --index-url https://<host>/api/packages/<owner>/pypi/simple
```

Pack the two npm packages into stable local files:

```sh
npm pack @skein/extension-api@1.0.0 --pack-destination dist
npm pack @skein/frontend-host@0.3.0 --pack-destination dist
```

The expected files are:

```text
dist/skein_agents-0.3.0-py3-none-any.whl
dist/skein-extension-api-1.0.0.tgz
dist/skein-frontend-host-0.3.0.tgz
```

Remove old files from `dist` before you stage a new release. An old artifact with the same package family can enter a wildcard build.

## 4. Create the backend extension

Declare the private Python package in `pyproject.toml`:

```toml
[project]
name = "workplace-skein-extension"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = [
  "skein-agents>=0.3.0,<0.4.0",
]

[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["backend/src"]
```

Create one explicit module in `backend/src/workplace_skein/module.py`. Import backend contracts only from these public surfaces:

- `app.extensions`.
- `app.public`.
- `app.main.create_app`.

Do not import another `app.*` module. The installed-package contract refuses internal imports in Python files and stubs.

Create one shared composition tuple in `backend/src/workplace_skein/composition.py`:

```python
from .module import workplace_module

modules = (workplace_module(),)
```

Create the ASGI application in `backend/src/workplace_skein/app.py`:

```python
from app.main import create_app

from .composition import modules

app = create_app(modules=modules)
```

Use the same composition tuple for the MCP process:

```sh
SKEIN_MCP_MODULES=workplace_skein.composition \
SKEIN_MCP_USER=<service-user> \
  python -m app.mcp_server
```

Skein does not scan installed packages. Every process must name the composition module explicitly.

## 5. Build the Python production lock

Build the private wheel:

```sh
uv build --wheel --out-dir dist .
```

Set `requirements.in` to the two exact first-party wheels:

```text
./dist/skein_agents-0.3.0-py3-none-any.whl
./dist/workplace_skein_extension-1.0.0-py3-none-any.whl
```

Compile only the public dependency closure into `requirements.lock`:

```sh
uv pip compile requirements.in \
  --python-version 3.12 \
  --index-url https://<controlled-python-mirror>/simple \
  --no-emit-package skein-agents \
  --no-emit-package workplace-skein-extension \
  --generate-hashes \
  --output-file requirements.lock
```

Install the lock before the first-party wheels:

```sh
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps \
  dist/skein_agents-0.3.0-py3-none-any.whl \
  dist/workplace_skein_extension-1.0.0-py3-none-any.whl
python -m pip check
```

The backend image must use this order. This order prevents pip from resolving a private package through a public fallback.

## 6. Create the frontend extension

Create the workplace root `package.json`:

```json
{
  "name": "@workplace/skein-workplace",
  "version": "1.0.0",
  "private": true,
  "workspaces": ["frontend"],
  "scripts": {
    "build:extension": "npm run build --workspace @workplace/skein-extension",
    "build:frontend": "npm run build:extension && skein-frontend-build @workplace/skein-extension"
  },
  "dependencies": {
    "@skein/extension-api": "file:dist/skein-extension-api-1.0.0.tgz",
    "@skein/frontend-host": "file:dist/skein-frontend-host-0.3.0.tgz",
    "next": "16.2.11",
    "react": "19.2.4",
    "react-dom": "19.2.4"
  },
  "overrides": {
    "postcss": "8.5.23",
    "sharp": "0.35.3"
  }
}
```

Create `frontend/package.json`:

```json
{
  "name": "@workplace/skein-extension",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "import": "./dist/index.js"
    }
  },
  "peerDependencies": {
    "@skein/extension-api": "1.0.0",
    "react": ">=19.0.0 <20"
  },
  "scripts": {
    "build": "tsc --project tsconfig.json"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "typescript": "^5.0.0"
  }
}
```

Export one `FrontendExtension` from `frontend/index.tsx`. Set its compatibility range to the supported Skein release:

```tsx
import {
  FRONTEND_EXTENSION_API,
  type FrontendExtension,
} from "@skein/extension-api";

const extension: FrontendExtension = {
  id: "workplace.extension",
  version: "1.0.0",
  extensionApi: FRONTEND_EXTENSION_API,
  minimumCore: "0.3.0",
  maximumCoreExclusive: "0.4.0",
  navigation: [],
  dashboardCards: [],
};

export default extension;
```

Run npm once to create the lock:

```sh
npm install --ignore-scripts
npm ls @skein/extension-api @skein/frontend-host next react react-dom --all
```

Commit `package-lock.json`. The build command refuses a missing lock, indirect first-party packages, wrong versions, and missing integrity values.

## 7. Build the workplace frontend

Run the locked installation and production build:

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm run build:frontend
```

The command writes the standalone runtime to `dist/frontend`.

The command does not install packages. It does not change the manifest, lock, or `node_modules`.

The command reads standard `.env` files during the build. It removes these files from the runtime output.

## 8. Build the final images

The workplace owns both final images. Do not inherit a Skein application image.

The backend image must do these operations:

1. Install `requirements.lock` with `--require-hashes`.
2. Install both first-party wheels with `--no-deps`.
3. Run `pip check`.
4. Copy only workplace content.
5. Run the workplace ASGI application as a non-root user.

The frontend image must do these operations:

1. Run `npm ci` from the workplace lock.
2. Run `npm run build:frontend`.
3. Copy only `dist/frontend` into the runtime stage.
4. Run `node server.js` as a non-root user.

Use the Atlas Containerfiles as templates:

- `examples/workplace-extension/deployment/Dockerfile`.
- `examples/workplace-extension/deployment/Frontend.Dockerfile`.

Build the images:

```sh
docker build -f deployment/Dockerfile \
  -t <registry>/workplace-skein:1.0.0 .

docker build -f deployment/Frontend.Dockerfile \
  --build-arg NEXT_PUBLIC_API_URL=https://<api-route> \
  --build-arg NEXT_PUBLIC_SITE_URL=https://<frontend-route> \
  -t <registry>/workplace-skein-frontend:1.0.0 .
```

Build a separate frontend image for each API and site URL pair. These values enter the browser bundle at build time.

## 9. Prepare PostgreSQL

Run the role bootstrap as the database administrator:

```sh
deployment/10-app-role.sh
deployment/20-workplace-schema.sh
```

Give the application role access only to these schemas:

- `public` for Skein core tables.
- `private` for author-private Skein records.
- One fixed `ext_<name>` schema for the workplace extension.

Do not grant database-wide `CREATE` to the application role.

Set the database through component variables in the deployment manifest:

```text
SKEIN_DB_HOST
SKEIN_DB_PORT
SKEIN_DB_USER
SKEIN_DB_PASSWORD
SKEIN_DB_NAME
```

Component variables keep punctuation in generated passwords out of URI parsing.

## 10. Prepare the OpenShift deployment

Use the Atlas manifest as the starting point:

- `examples/workplace-extension/deployment/skein.yaml`.
- `examples/workplace-extension/deployment/workplace-patch.yaml`.
- `examples/workplace-extension/deployment/workplace-frontend-patch.yaml`.

Keep these controls:

- One backend replica.
- A `Recreate` backend strategy.
- No fixed `runAsUser`.
- No fixed `fsGroup`.
- `RuntimeDefault` seccomp.
- Read-only root filesystems.
- An `emptyDir` mount at `/tmp`.
- A persistent `/data` mount for artifacts and backups.
- Startup, readiness, and liveness probes.
- Component database variables from ConfigMaps and Secrets.

Render the deployment before you apply it:

```sh
kubectl kustomize . > /tmp/workplace-skein.yaml
```

Check the rendered image names, Secret references, Routes, storage class, and database host. Then apply the reviewed manifest.

## 11. Add CI gates

Run these gates against the workplace repository:

```sh
python -m pip check
python -m pytest
python -m mypy --strict backend/src/workplace_skein
npm ci --ignore-scripts --no-audit --no-fund
npm run build:frontend
npm audit --omit=dev
pip-audit --requirement requirements.lock --no-deps
kubectl kustomize . >/dev/null
```

Add image checks that use an arbitrary user ID and a read-only root filesystem. Mount only `/data` and `/tmp` as writable paths.

Run a browser smoke test against the final frontend and backend images. Check the console, failed requests, extension navigation, and extension API routes.

## 12. Run the acceptance check

A workplace setup is ready when all these statements are true:

- The repository contains no Skein source directory or Git history.
- Python imports resolve from the installed `skein-agents` wheel.
- The private backend imports only public Skein modules.
- Both dependency locks are committed.
- Both first-party npm packages have lock integrity values.
- `skein-frontend-build` writes a standalone runtime.
- The extension UI appears only when its backend capability permits it.
- Both final images run as arbitrary non-root users.
- PostgreSQL uses a restricted application role.
- Kustomize renders without fixed user or group IDs.
- The workplace registry owns the final image digests.

## Use local artifacts before publication

A release engineer can build the three Skein artifacts from a Skein checkout:

```sh
uv build --wheel --out-dir /path/to/workplace-skein/dist backend
npm pack --pack-destination /path/to/workplace-skein/dist \
  ./frontend/packages/extension-api
npm pack --pack-destination /path/to/workplace-skein/dist ./frontend
```

Copy only the wheel and npm tarballs into the workplace build input. Do not copy the Skein source tree or Git history.

Regenerate the workplace locks after the artifact bytes change. Then run every package, image, deployment, and browser gate.

## Upgrade Skein

For each Skein upgrade:

1. Read `CHANGELOG.md` and the extension deprecations.
2. Update the backend compatibility range.
3. Update the frontend compatibility range.
4. Stage the new wheel and npm tarballs.
5. Regenerate both workplace locks.
6. Run the installed-wheel transition against retained extension data.
7. Compare a fresh schema with the upgraded schema.
8. Build new workplace images.
9. Run the browser and deployment gates.
10. Deploy the new image digests.

Do not rename an applied migration after the first production deployment.

## Reference implementation

Use these files when a step needs more detail:

- `docs/EXTENSIONS.md` contains the complete backend and frontend contracts.
- `examples/workplace-extension/README.md` explains the Atlas package.
- `examples/workplace-extension/deployment/README.md` explains the Atlas deployment.
- `frontend/README.md` documents the frontend host command.
- `RELEASING.md` documents Skein package publication.

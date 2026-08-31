# Workplace setup

Use this guide to create a private workplace repository that depends on Skein packages. The workplace repository does not need the Skein source tree.

Skein uses three first-party packages:

- `skein-agents` provides the Python backend.
- `@miloctl/skein-frontend-host` provides the Next.js host and production build command.
- `@miloctl/skein-extension-api` provides the public frontend types and components.

The workplace repository owns its extension code, dependency locks, content, final images, and deployment files.

## Current release status

Use an exact registry package only after its pull-back validation passes and the matching annotated tag exists. `docs/EXTENSIONS.md` names the package line for this revision.

A workplace needs a classic GitHub PAT with `read:packages` outside GitHub Actions. `RELEASING.md` contains the publication and access procedure.

Before registry publication, use the artifact procedure in [Use local artifacts before publication](#use-local-artifacts-before-publication).

## Prerequisites

Install or obtain these tools and services:

- Python 3.12.
- `uv` and `pip`.
- Node.js 22 and npm.
- Docker or another OCI image builder.
- PostgreSQL 17 client tools.
- `kubectl` for Kustomize validation.
- PyPI access or a controlled Python mirror.
- A controlled npm mirror and GitHub Packages access for `@miloctl`.
- A workplace image registry.
- A PostgreSQL 17 database.

Use one Python index for each installation command. A workplace can route PyPI through its controlled mirror.

## 1. Create the workplace repository

Create this minimum layout:

```text
workplace-skein/
├── .dockerignore
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
├── kustomization.yaml
├── package.json
├── package-lock.json
├── pyproject.toml
├── requirements.in
├── requirements.lock
├── requirements-test.in
└── requirements-test.lock
```

Ignore these generated paths:

```gitignore
.env*
*.env
*.key
*.pem
.npmrc
.venv/
__pycache__/
*.egg-info/
build/
dist/
.skein/
node_modules/
frontend/node_modules/
frontend/dist/
```

Create `.dockerignore`. Keep `dist/` in the image context because it contains the exact first-party packages:

```dockerignore
.git/
.env*
*.env
*.key
*.pem
.npmrc
.venv/
__pycache__/
*.egg-info/
build/
node_modules/
.skein/
frontend/node_modules/
frontend/dist/
backend/tests/
```

Keep secret examples free of real values. Store credentials in the workplace secret manager.

### Customize a copied starter

Complete the rename before you create dependency locks. Rename each identity as one unit:

- The Python distribution and import package.
- The root npm package and frontend workspace.
- The extension ID and every contribution ID derived from it.
- Policy actions, capabilities, service identities, and route prefixes.
- Extension environment variables.
- The extension store and its derived PostgreSQL schema.
- Persona, flock, and playbook filenames and content references.
- Backend and frontend image names.
- ConfigMap and Secret names and keys.
- Tests, dependency lock inputs and generated locks, commands, and current documentation.

Search both tracked paths and file contents for the old starter identity. Check the compiled frontend extension and the rendered deployment too. A source-only search does not find an old name in build output or a Kubernetes resource.

The two example READMEs label commands by execution context. Keep the consumer-root commands after you copy the example. Remove Skein-checkout commands and do not copy Skein scripts.

### Regenerate locks after the rename

Use this order:

1. Rename the manifests, source packages, content, tests, and deployment files.
2. Build the private extension wheel.
3. Download the Skein wheel and repack the two Skein npm packages into `dist/`.
4. Use Node 22 to regenerate `package-lock.json` from the renamed manifests and exact npm tarballs.
5. Regenerate `requirements.lock` when the Python production graph changes.
6. Regenerate `requirements-test.lock` when the Python production or test graph changes.
7. Run `npm ci`, `pip check`, and the package, image, deployment, and browser contracts.

Do not hand-edit a lock. If a first-party artifact changes bytes, stage the new artifact before you regenerate the npm lock.

## 2. Configure the package registries

Route PyPI through the controlled Python mirror when workplace policy requires it.

Route normal npm packages through the controlled npm mirror. Route the private scope through GitHub Packages:

```ini
registry=https://<controlled-npm-mirror>/
replace-registry-host=npmjs
@miloctl:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${NPM_TOKEN}
```

Keep `replace-registry-host=npmjs`. The `always` value rewrites local `file:` tarballs as registry URLs under npm 10.

Use a classic GitHub PAT with `read:packages` for local and non-GitHub installs. Do not commit the token.

A GitHub Actions repository can use `GITHUB_TOKEN` after the package grants that repository read access.

## 3. Stage the exact Skein packages

Download the backend wheel without its dependencies:

```sh
mkdir -p dist
python -m pip download --no-deps --dest dist \
  skein-agents==0.4.0 \
  --index-url https://pypi.org/simple
```

Pack the two npm packages into stable local files:

```sh
npm pack @miloctl/skein-extension-api@1.0.0 --pack-destination dist
npm pack @miloctl/skein-frontend-host@0.4.0 --pack-destination dist
```

The expected files are:

```text
dist/skein_agents-0.4.0-py3-none-any.whl
dist/miloctl-skein-extension-api-1.0.0.tgz
dist/miloctl-skein-frontend-host-0.4.0.tgz
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
  "skein-agents>=0.3.0,<0.5.0",
]

[project.optional-dependencies]
test = [
  "pytest>=8",
  "httpx2>=2.9",
  "mypy>=1.13",
  "pip-audit>=2.7",
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

### Test group-gated identity

`trusted-header` supplies a weak user name and no enterprise groups. A group-gated navigation item or route stays unavailable in this mode.

Use a signed OIDC test provider to test permitted group paths. Keep manager and integration groups separate when the workplace uses separate duties.

Do not add a browser-supplied group header. The validated OIDC token or directory resolver must supply each group.

## 5. Build the Python production lock

Build the private wheel:

```sh
uv build --wheel --out-dir dist .
```

Set `requirements.in` to the two exact first-party wheels:

```text
./dist/skein_agents-0.4.0-py3-none-any.whl
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
  dist/skein_agents-0.4.0-py3-none-any.whl \
  dist/workplace_skein_extension-1.0.0-py3-none-any.whl
python -m pip check
```

The backend image must use this order. This order prevents pip from resolving a private package through a public fallback.

### Build the Python test lock

Set `requirements-test.in` to the same wheels. Enable the private package test extra:

```text
-c requirements.lock
./dist/skein_agents-0.4.0-py3-none-any.whl
./dist/workplace_skein_extension-1.0.0-py3-none-any.whl[test]
```

Compile the public production and test closure:

```sh
uv pip compile requirements-test.in \
  --python-version 3.12 \
  --index-url https://<controlled-python-mirror>/simple \
  --no-emit-package skein-agents \
  --no-emit-package workplace-skein-extension \
  --generate-hashes \
  --output-file requirements-test.lock
```

Install the test environment from the generated lock:

```sh
uv venv --python 3.12 .venv-test
uv pip install --python .venv-test/bin/python \
  --require-hashes -r requirements-test.lock
uv pip install --python .venv-test/bin/python --no-deps \
  dist/skein_agents-0.4.0-py3-none-any.whl \
  dist/workplace_skein_extension-1.0.0-py3-none-any.whl
uv pip check --python .venv-test/bin/python
```

Create an empty PostgreSQL database for each test run. Use a database name that contains `test`, `contract`, or `scratch`:

```sh
SKEIN_DATABASE_URL=postgresql://<user>:<password>@<host>:5432/skein_workplace_test \
  .venv-test/bin/python -m pytest -q backend/tests
```

Do not install test tools into the production image.

## 6. Create the frontend extension

Create the workplace root `package.json`:

```json
{
  "name": "@workplace/skein-workplace",
  "version": "1.0.0",
  "private": true,
  "engines": { "node": "22.x" },
  "workspaces": ["frontend"],
  "scripts": {
    "build:extension": "npm run build --workspace @workplace/skein-extension",
    "build:frontend": "npm run build:extension && skein-frontend-build @workplace/skein-extension"
  },
  "dependencies": {
    "@miloctl/skein-extension-api": "file:dist/miloctl-skein-extension-api-1.0.0.tgz",
    "@miloctl/skein-frontend-host": "file:dist/miloctl-skein-frontend-host-0.4.0.tgz",
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
    "@miloctl/skein-extension-api": "1.0.0",
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

Create `frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "declaration": true,
    "emitDeclarationOnly": false,
    "skipLibCheck": true,
    "outDir": "dist"
  },
  "include": ["index.tsx"]
}
```

Export one `FrontendExtension` from `frontend/index.tsx`. Set its compatibility range to the supported Skein release:

```tsx
import {
  FRONTEND_EXTENSION_API,
  type FrontendExtension,
} from "@miloctl/skein-extension-api";

const extension: FrontendExtension = {
  id: "workplace.extension",
  version: "1.0.0",
  extensionApi: FRONTEND_EXTENSION_API,
  minimumCore: "0.3.0",
  maximumCoreExclusive: "0.5.0",
  navigation: [],
  dashboardCards: [],
};

export default extension;
```

Run npm once to create the lock:

```sh
npm install --ignore-scripts
npm ls @miloctl/skein-extension-api @miloctl/skein-frontend-host next react react-dom --all
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

Create two registry configuration files outside the build context. Let the secret manager write credentials into these files:

```ini
# /run/secrets/pip.conf
[global]
index-url = https://<controlled-python-mirror>/simple
```

```ini
# /run/secrets/npmrc
registry=https://<controlled-npm-mirror>/
replace-registry-host=npmjs
```

The Containerfiles require these BuildKit secrets. The secret files do not enter an image layer.

Build the images:

```sh
docker build \
  --secret id=pip-config,src=/run/secrets/pip.conf \
  -f deployment/Dockerfile \
  -t <registry>/workplace-skein:1.0.0 .

docker build \
  --secret id=npm-config,src=/run/secrets/npmrc \
  -f deployment/Frontend.Dockerfile \
  --build-arg NEXT_PUBLIC_API_URL=https://<api-route> \
  --build-arg NEXT_PUBLIC_SITE_URL=https://<frontend-route> \
  -t <registry>/workplace-skein-frontend:1.0.0 .
```

Build a separate frontend image for each API and site URL pair. These values enter the browser bundle at build time.

## 9. Prepare PostgreSQL

Let the secret manager export these values before you run the scripts:

```text
POSTGRES_ADMIN_CONNINFO
POSTGRES_ADMIN_USER
POSTGRES_DATABASE
SKEIN_APP_USER
SKEIN_APP_PASSWORD
```

Run the role bootstrap as the database administrator:

```sh
env \
  POSTGRES_CONNINFO="$POSTGRES_ADMIN_CONNINFO" \
  POSTGRES_USER="$POSTGRES_ADMIN_USER" \
  POSTGRES_DB="$POSTGRES_DATABASE" \
  SKEIN_APP_USER="$SKEIN_APP_USER" \
  SKEIN_APP_PASSWORD="$SKEIN_APP_PASSWORD" \
  deployment/10-app-role.sh

env \
  POSTGRES_CONNINFO="$POSTGRES_ADMIN_CONNINFO" \
  POSTGRES_USER="$POSTGRES_ADMIN_USER" \
  POSTGRES_DB="$POSTGRES_DATABASE" \
  SKEIN_APP_USER="$SKEIN_APP_USER" \
  deployment/20-workplace-schema.sh
```

Do not put the administrator connection string or application password in the repository.

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
uv pip check --python .venv-test/bin/python
SKEIN_DATABASE_URL=postgresql://<user>:<password>@<host>:5432/skein_workplace_test \
  .venv-test/bin/python -m pytest -q backend/tests
.venv-test/bin/python -m mypy --strict backend/src/workplace_skein
npm ci --ignore-scripts --no-audit --no-fund
npm run build:frontend
npm audit --omit=dev
.venv-test/bin/pip-audit --requirement requirements.lock --no-deps
kubectl kustomize . >/dev/null
```

Add image checks that use an arbitrary user ID and a read-only root filesystem. Mount only `/data` and `/tmp` as writable paths.

Run a browser smoke test against the final frontend and backend images. Use signed OIDC users for denied, manager, and integration group paths. Check the console, failed requests, extension navigation, extension routes, and one core write.

## 12. Run the acceptance check

Run acceptance from a clean checkout of the consumer repository, not from a Skein checkout. Copy the completed standalone runtime to a temporary directory outside the consumer source tree before you start it.

A workplace setup is ready when all these statements are true:

- The repository contains no Skein source directory or Skein Git history.
- Python imports resolve from the installed `skein-agents` wheel under `site-packages`, not from `PYTHONPATH` or an editable Skein checkout.
- The private backend imports only public Skein modules.
- The Python production lock, Python test lock, and npm lock are committed.
- Both first-party npm packages have lock integrity values.
- `skein-frontend-build` writes a standalone runtime that starts after it is copied outside the repository.
- The extension UI appears only when its backend capability permits it.
- Both final images run as arbitrary non-root users.
- PostgreSQL uses a restricted application role.
- `kubectl kustomize .` renders from the consumer repository root without a path into the Skein source tree.
- The old starter identity is absent from tracked paths, source text, compiled frontend extension text, and rendered deployment output.
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

Regenerate the npm lock after npm artifact bytes change. Regenerate each Python lock when its dependency graph changes. Then run every package, image, deployment, and browser gate.

## Upgrade Skein

For each Skein upgrade:

1. Read `CHANGELOG.md` and the extension deprecations.
2. Update the backend compatibility range.
3. Update the frontend compatibility range.
4. Stage the new wheel and npm tarballs.
5. Regenerate the npm lock. Regenerate each Python lock if its dependency graph changed.
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

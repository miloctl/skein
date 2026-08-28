# Atlas deployment

Atlas builds its final images from package dependencies. It does not inherit a Skein backend image or frontend-host image.

This README labels each command by execution context. Remove Skein-checkout commands after you copy the example.

## Stage packages from a Skein checkout

Run these commands from the Skein repository root:

```sh
rm -rf examples/workplace-extension/dist
mkdir -p examples/workplace-extension/dist
uv build --wheel --out-dir examples/workplace-extension/dist backend
uv build --wheel --out-dir examples/workplace-extension/dist \
  examples/workplace-extension
npm pack --pack-destination examples/workplace-extension/dist \
  ./frontend/packages/extension-api
npm pack --pack-destination examples/workplace-extension/dist ./frontend
```

These commands build the source artifacts for the local reference contracts.

## Stage packages from a copied consumer root

Configure PyPI and GitHub Packages before you run these commands. Keep registry credentials outside the repository.

```sh
rm -rf dist
mkdir -p dist
uv build --wheel --out-dir dist .
python -m pip download --no-deps --dest dist \
  skein-agents==0.3.2 \
  --index-url https://pypi.org/simple
npm pack @miloctl/skein-extension-api@1.0.0 --pack-destination dist
npm pack @miloctl/skein-frontend-host@0.3.2 --pack-destination dist
```

A local or non-GitHub consumer needs a classic GitHub PAT with `read:packages`.

The Dockerfiles require the exact `0.3.2`, `1.0.0`, and `2.0.0` artifact names. A clean `dist` directory prevents an old artifact from entering the build.

Regenerate `package-lock.json` with Node 22 after an npm artifact changes bytes. Regenerate each Python lock after its dependency graph changes.

## Configure the image builds

Create a pip configuration for the controlled Python mirror. Create an npm configuration for the controlled npm mirror.

Keep both files outside the build context. Inject credentials through the secret manager.

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

The `npmjs` value redirects npmjs lock entries and preserves local `file:` tarballs. Do not use `always` with npm 10.

## Build images from a Skein checkout

Run these commands from the Skein repository root:

```sh
docker build \
  --secret id=pip-config,src=/run/secrets/pip.conf \
  -f examples/workplace-extension/deployment/Dockerfile \
  -t atlas-skein:2.0.0 \
  examples/workplace-extension

docker build \
  --secret id=npm-config,src=/run/secrets/npmrc \
  --build-arg NEXT_PUBLIC_API_URL=https://skein-api.example.invalid \
  --build-arg NEXT_PUBLIC_SITE_URL=https://skein.example.invalid \
  -f examples/workplace-extension/deployment/Frontend.Dockerfile \
  -t atlas-skein-frontend:2.0.0 \
  examples/workplace-extension
```

## Build images from a copied consumer root

Run these commands from the copied repository root:

```sh
docker build \
  --secret id=pip-config,src=/run/secrets/pip.conf \
  -f deployment/Dockerfile \
  -t atlas-skein:2.0.0 \
  .

docker build \
  --secret id=npm-config,src=/run/secrets/npmrc \
  --build-arg NEXT_PUBLIC_API_URL=https://skein-api.example.invalid \
  --build-arg NEXT_PUBLIC_SITE_URL=https://skein.example.invalid \
  -f deployment/Frontend.Dockerfile \
  -t atlas-skein-frontend:2.0.0 \
  .
```

The backend image installs `requirements.lock`. It then installs the Skein and Atlas wheels with `--no-deps`.

The frontend image runs `npm ci` from the workplace lock. It compiles Atlas and runs `skein-frontend-build`.

## Prepare PostgreSQL from a copied consumer root

Run `deployment/10-app-role.sh` as the database administrator. Then run `deployment/20-atlas-schema.sh`.

The scripts create these schemas for the restricted application role:

- `public`
- `private`
- `ext_atlas_extension`

The application role has no database-wide `CREATE` privilege. Atlas uses the fixed `ext_atlas_extension` schema.

The `skein-db-secret` Secret supplies these values:

- `SKEIN_APP_USER`
- `SKEIN_APP_PASSWORD`
- `POSTGRES_DB`

The `skein-config` ConfigMap supplies `SKEIN_DB_HOST` and `SKEIN_DB_PORT`.

## Configure Atlas

Create `atlas-skein-secrets` through the deployment secret manager. Use `secrets.env.example` only as a list of names.

`ATLAS_API_TOKEN` is sent only when `ATLAS_API_URL` is configured. Do not commit token values.

## Render from a Skein checkout

Run this command from the Skein repository root:

```sh
kubectl kustomize examples/workplace-extension
```

## Render from a copied consumer root

Run this command from the copied repository root:

```sh
kubectl kustomize .
```

The manifest uses OpenShift `restricted-v2` controls. It has no fixed `runAsUser` or `fsGroup`.

The backend uses `Recreate` and one replica. It mounts `/data` and a temporary `/tmp` directory.

The frontend is stateless. It mounts only a temporary `/tmp` directory.

## Run contracts from a Skein checkout

Run these commands from the Skein repository root:

```sh
scripts/reference-deployment-contract.sh
scripts/reference-images-contract.sh
```

The image contract uses an arbitrary user ID and a read-only root filesystem. Only `/data` and `/tmp` are writable.

## Run gates from a copied consumer root

Run the consumer-owned package, image, deployment, and browser gates. Use signed OIDC groups for extension policy paths.

Do not copy or invoke Skein `scripts/reference-*` files from the consumer repository.

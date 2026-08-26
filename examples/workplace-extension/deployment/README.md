# Atlas deployment

Atlas builds its final images from package dependencies. It does not inherit a Skein backend image or frontend-host image.

## Stage the packages

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

The Dockerfiles require the exact `0.3.0`, `1.0.0`, and `2.0.0` artifact names. A clean `dist` directory prevents an older artifact from entering a build.

The Atlas npm lock contains the integrity values for the two npm tarballs. If the package bytes change, regenerate the lock.

## Build the final images

```sh
docker build \
  -f examples/workplace-extension/deployment/Dockerfile \
  -t atlas-skein:2.0.0 \
  examples/workplace-extension

docker build \
  --build-arg NEXT_PUBLIC_API_URL=https://skein-api.example.invalid \
  --build-arg NEXT_PUBLIC_SITE_URL=https://skein.example.invalid \
  -f examples/workplace-extension/deployment/Frontend.Dockerfile \
  -t atlas-skein-frontend:2.0.0 \
  examples/workplace-extension
```

The backend image installs `requirements.lock`. It then installs the Skein and Atlas wheels with `--no-deps`.

The frontend image runs `npm ci` from the workplace lock. It compiles Atlas and runs `skein-frontend-build`.

## Prepare PostgreSQL

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

## Render the deployment

Run this command from the repository root:

```sh
kubectl kustomize examples/workplace-extension
```

The manifest uses OpenShift `restricted-v2` controls. It has no fixed `runAsUser` or `fsGroup`.

The image contract runs both images with an arbitrary user ID and a read-only root filesystem. Only `/data` and `/tmp` are writable.

The backend uses `Recreate` and one replica. It mounts `/data` and a temporary `/tmp` directory.

The frontend is stateless. It mounts only a temporary `/tmp` directory.

Run the executable contracts before deployment:

```sh
scripts/reference-deployment-contract.sh
scripts/reference-images-contract.sh
```

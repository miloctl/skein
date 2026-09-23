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

A local Skein wheel does not have the published digest. The reference contracts pin the staged wheel in their own copy of `skein-agents.lock`. If you build the images from a Skein checkout by hand, write the local digest into `skein-agents.lock` first, and do not commit it.

## Stage packages from a copied consumer root

Configure PyPI or your controlled Python mirror before you run these commands. Keep registry credentials outside the repository.

```sh
rm -rf dist
mkdir -p dist
uv build --wheel --out-dir dist .
python -m pip download --no-deps --require-hashes --dest dist \
  -r skein-agents.lock \
  --index-url https://pypi.org/simple
npm pack @miloctl/skein-extension-api@1.0.0 --pack-destination dist
npm pack @miloctl/skein-frontend-host@0.6.6 --pack-destination dist
```

The `@miloctl` npm packages are public on npmjs.com. No registry token is needed.

`skein-agents.lock` pins the Skein wheel by its SHA-256 digest. The download above and the backend image build refuse any other bytes. The template digest is zero, so the first download fails until you pin the release:

1. Open the `skein-agents` release page on pypi.org and select **Download files**.
2. Copy the SHA256 value of the `py3-none-any` wheel. The `finalize-release` workflow compared these PyPI bytes with the tested release artifact before it created the release tag.
3. Write that value into `skein-agents.lock` and commit it with the version change.

Do not copy the digest from the pip error. The error shows the digest of the file that pip received, not the digest that the release published.

The Dockerfiles require the exact `0.6.6`, `1.0.0`, and `2.0.0` artifact names. A clean `dist` directory prevents an old artifact from entering the build. The Dockerfiles pin each base image by digest. Before deployment, replace each zero application-image digest with the digest from the reviewed registry image.

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

The backend image installs `requirements.lock`. It then installs the Skein wheel through `skein-agents.lock` with `--require-hashes`, and the Atlas wheel with `--no-deps`.

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

## Configure identity

Atlas policy needs signed OIDC groups. Add these values to the existing `skein-config` ConfigMap:

- `SKEIN_AUTH_MODE=oidc`
- `SKEIN_OIDC_ISSUER=https://<identity-provider>`
- `SKEIN_OIDC_AUDIENCE=<registered-audience>`
- `SKEIN_OIDC_CLIENT_ID=<public-client-id>`
- `SKEIN_OIDC_ADMIN_GROUP=<admin-group>`
- `SKEIN_CORS_ORIGINS=https://<frontend-route>`
- `SKEIN_TRUST_PROXY_HOPS=1` when one OpenShift router is in front of Skein.

Use HTTPS for the production issuer and its endpoints. The ConfigMap must not contain credentials. Browser sign-in also requires `SKEIN_CREDENTIAL_KEY` in `skein-secrets`. Keep it stable across restarts. Use HTTPS and same-site frontend/API Routes so the Secure session cookie is accepted. Browser sessions are excluded from recovery archives and require a new sign-in after restore.

The example directory resolver in `backend/src/atlas_skein/policy.py` returns no record and fails closed. Before production approval revalidation, replace it with an authoritative server-side directory adapter. The adapter must return no record during an outage.

Build the frontend image with the external API and frontend Routes. Do not use loopback URLs for a deployed browser.

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

The backend uses `Recreate` and one replica. It mounts `/data` and a temporary `/tmp` directory. Startup and liveness use `/health`. Readiness uses `/ready`, so invalid authentication configuration keeps the pod out of service without a restart loop.

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

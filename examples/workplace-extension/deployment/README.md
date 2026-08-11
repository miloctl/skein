# Atlas deployment overlay

This directory is an example. It uses released Skein backend and frontend-host
images as its bases. It installs only the private Atlas wheel, frontend package,
and content.

The Kustomize patch changes the application entry point to
`atlas_skein.app:app`. That module calls `create_app(modules=(atlas_module(...),))`.
It does not modify the default Skein application.

Stage both private release artifacts, then build the derivative images:

```sh
mkdir -p examples/workplace-extension/dist
uv build --wheel --out-dir examples/workplace-extension/dist \
  examples/workplace-extension
npm pack --pack-destination examples/workplace-extension/dist \
  examples/workplace-extension/frontend
docker build -f examples/workplace-extension/deployment/Dockerfile \
  --build-arg SKEIN_IMAGE=skein:0.2.0 \
  -t atlas-skein:1.0.0 examples/workplace-extension
docker build -f examples/workplace-extension/deployment/Frontend.Dockerfile \
  --build-arg SKEIN_FRONTEND_HOST=skein-frontend-host:0.2.0 \
  -t atlas-skein-frontend:1.0.0 examples/workplace-extension
```

`scripts/reference-images-contract.sh` stages the same artifacts in a
temporary directory and builds both images as a release check. The frontend build argument
`SKEIN_FRONTEND_HOST` names the compatible, versioned Skein host image. The
unchanged Atlas package is composed before `next build`; no core source file is
copied or patched.

Create an `atlas-skein-secrets` Secret through the deployment secret manager.
Use `secrets.env.example` only as a list of required names. Do not commit secret
values. `ATLAS_API_TOKEN` is sent only by the private `AtlasHttpClient` when
`ATLAS_API_URL` is configured.

The example declares separate `skein-data` and `atlas-skein-data` persistent
volume claims. The private extension database is mounted at `/atlas-data`. It
does not share the core `/data` volume.

Render or apply the overlay from its common parent. This keeps the content
files inside the standard Kustomize load boundary:

```sh
kubectl kustomize examples/workplace-extension
kubectl apply -k examples/workplace-extension
```

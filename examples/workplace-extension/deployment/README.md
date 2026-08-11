# Atlas deployment overlay

This directory is an example. It uses released Skein backend and frontend-host
images as its bases. It installs only the private Atlas wheel, frontend package,
and content.

The Kustomize patch changes the application entry point to
`atlas_skein.app:app`. That module calls `create_app(modules=(atlas_module(...),))`.
It does not modify the default Skein application.

Build `Dockerfile` as `atlas-skein:1.0.0`. Build `Frontend.Dockerfile` as
`atlas-skein-frontend:1.0.0`. The frontend build argument
`SKEIN_FRONTEND_HOST` names the compatible, versioned Skein host image. The
unchanged Atlas package is composed before `next build`; no core source file is
copied or patched.

Create an `atlas-skein-secrets` Secret through the deployment secret manager.
Use `secrets.env.example` only as a list of required names. Do not commit secret
values.

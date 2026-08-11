# Atlas deployment overlay

This directory is an example. It uses a released Skein image as its base and
installs only the private Atlas wheel and content.

The Kustomize patch changes the application entry point to
`atlas_skein.app:app`. That module calls `create_app(modules=(atlas_module(...),))`.
It does not modify the default Skein application.

Create an `atlas-skein-secrets` Secret through the deployment secret manager.
Use `secrets.env.example` only as a list of required names. Do not commit secret
values.

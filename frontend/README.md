# Skein frontend

This package is the Next.js host for Skein. It also publishes as `@miloctl/skein-frontend-host` for trusted workplace builds.

## Work on the core frontend

```bash
npm install
cp .env.local.example .env.local
npm run dev
npm run build
```

The development server uses port 3000. The backend uses port 8000 by default.

## Build a workplace frontend

Use Node 22 for a workplace build. The `@miloctl` packages are public on npmjs.com and install with no token.

The workplace root pins these packages directly:

- `@miloctl/skein-frontend-host@0.5.0`
- `@miloctl/skein-extension-api@1.0.0`
- `next@16.2.11`
- `react@19.2.4`
- `react-dom@19.2.4`
- The private frontend extension package.

Add these exact root overrides:

```json
{
  "overrides": {
    "postcss": "8.5.23",
    "sharp": "0.35.3"
  }
}
```

Overrides from an installed package have no effect. The host command refuses a root that omits these pins or overrides.

The workplace project owns its npm lock. It compiles each extension before it runs the host command.

```sh
skein-frontend-build @workplace/skein-extension
```

The command writes `dist/frontend`. It does not install packages or change the workplace lock.

The command reads standard workplace `.env` files during the build. It removes these files from the standalone runtime output.

The command supports production builds only. Skein does not load frontend extensions at runtime.

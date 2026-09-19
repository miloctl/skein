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

## Browser validation

Run the complete default and OIDC Playwright suites against fresh test stacks.
Keep production builds, browser profiles, and test databases separate from a
running development instance. `playwright.config.ts` documents the server setup.

The locked axe-core 4.12.1 has a known sticky-position issue in the Guided First
Week phone scan. After scrolling, it can report `target-size` for a control
whose full hit area is 24 CSS pixels high but is obscured by the sticky header.
See [upstream issue #3720](https://github.com/dequelabs/axe-core/issues/3720).
The checked 4.13.0 release does not resolve this case.

The Guided First Week test scans the expanded page at document scroll position
zero. Every enabled axe rule and the zero-violations assertion remain active.
This changes the scan position, not the application layout or scanner results.

Separate checks measure the setup action in the original scrolled state. Native
keyboard navigation must reveal it below the header, with a visible focus ring,
a target at least 24 CSS pixels wide and high, and an unobstructed hit area.
Enter must focus the standup input below the header. After the scan, the test
restores the disclosure's scroll position and focus before it continues.

The original scrolled scan still has the upstream limitation. Do not patch the
scanner or filter violations. When an upstream fix is released, check the
original interaction before removing the scroll normalization and this note.

## Build a workplace frontend

Use Node 22 for a workplace build. The `@miloctl` packages are public on npmjs.com and install with no token.

The workplace root pins these packages directly:

- `@miloctl/skein-frontend-host@0.6.3`
- `@miloctl/skein-extension-api@1.0.0`
- `next@16.3.4`
- `react@19.2.4`
- `react-dom@19.2.4`
- The private frontend extension package.

Add these exact root overrides:

```json
{
  "overrides": {
    "postcss": "8.5.23",
    "sharp": "0.35.4"
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

import { defineConfig } from "@playwright/test";

/** Smoke depth only: a real browser walking the real app against a seeded
 *  mock-provider backend. The classes this exists for — data-integrity
 *  illusions and accessibility — are the ones component tests catch only
 *  when someone thinks to mock the right failure. Depth stays in
 *  __tests__/; keep this suite at a handful of walks. */

// explicit IPv4, never `localhost`: Node resolves localhost to ::1 first,
// and a host that DROPS connects to unbound ports instead of refusing them
// (WSL2 networking does this) hangs the health poll until webServer times out
const API = "http://127.0.0.1:8600";
const APP = "http://127.0.0.1:3600";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // one shared backend; parallel writes would interleave seeds
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: APP,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // The runner creates a disposable PostgreSQL database and seeds the demo
      // team. SKEIN_DATA_DIR alone cannot isolate rows from a running dev app.
      command:
        `bash -c 'rm -rf /tmp/skein-e2e && cd ../backend && ` +
        // trusted-header explicitly: the smoke drives the X-User name picker,
        // and the shipped default is api-key (fail closed)
        `exec env SKEIN_DATA_DIR=/tmp/skein-e2e SKEIN_AUTH_MODE=trusted-header ` +
        `SKEIN_MODEL_PROVIDER=mock SKEIN_SCHEDULER=0 ` +
        // EMBEDDINGS off like the provider is mock, and for the same reason: a
        // developer's .env turns them on against a live ollama, and one slow
        // embed call during seeding blows the 60s health budget below — the
        // deterministic stack must not depend on a live model endpoint
        `SKEIN_EMBEDDINGS=0 ` +
        `SKEIN_CORS_ORIGINS=${APP} .venv/bin/python ../scripts/e2e-backend.py'`,
      url: `${API}/health`,
      // PW_REUSE: on a host that drops connects to unbound ports (the
      // IPv4 note above), playwright's port preflight hangs too. Pre-start
      // the servers by hand and set PW_REUSE=1 to skip the preflight. CI
      // never sets it, so CI always gets a fresh seeded stack.
      reuseExistingServer: !!process.env.PW_REUSE,
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
      timeout: 60_000,
    },
    {
      // a production build, not `next dev`: Next allows one dev server per
      // directory, so dev would collide with a running local stack — and
      // the smoke walks what ships. NEXT_PUBLIC_API_URL is baked at build
      // time, which is why the build lives inside this command.
      // THE TRAP: this builds .next-e2e, and `npm run build` builds .next.
      // Editing a component and running `npm run build` does NOT change what
      // these walks see — with PW_REUSE=1 against an already-started server
      // they will keep testing the previous build, and a fix that landed
      // reads as still broken. Drop PW_REUSE to rebuild.
      command:
        `bash -c 'NEXT_DIST_DIR=.next-e2e NEXT_PUBLIC_API_URL=${API} npx next build && ` +
        `NEXT_DIST_DIR=.next-e2e npx next start --port 3600'`,
      url: APP,
      reuseExistingServer: !!process.env.PW_REUSE,
      timeout: 180_000,
    },
  ],
});

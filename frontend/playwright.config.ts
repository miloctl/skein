import { defineConfig } from "@playwright/test";

/** Smoke depth only: a real browser walking the real app against a seeded
 *  mock-provider backend. The classes this exists for — data-integrity
 *  illusions and accessibility — are the ones component tests catch only
 *  when someone thinks to mock the right failure. Depth stays in
 *  __tests__/; keep this suite at a handful of walks. */

// explicit IPv4, never `localhost`: Node resolves localhost to ::1 first,
// and on this WSL2 host a connect to an unbound ::1 port HANGS rather than
// refuses — the health poll then never completes and webServer times out
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
      // seed.py builds the demo team the walks assert against; the data dir
      // is fresh per run so a rerun never sees yesterday's state
      command:
        `bash -c 'rm -rf /tmp/skein-e2e && mkdir -p /tmp/skein-e2e && ` +
        `cd ../backend && ` +
        `SKEIN_DATA_DIR=/tmp/skein-e2e SKEIN_MODEL_PROVIDER=mock SKEIN_SCHEDULER=0 ` +
        `.venv/bin/python seed.py && ` +
        `SKEIN_DATA_DIR=/tmp/skein-e2e SKEIN_MODEL_PROVIDER=mock SKEIN_SCHEDULER=0 ` +
        `SKEIN_CORS_ORIGINS=${APP} .venv/bin/uvicorn app.main:app --port 8600'`,
      url: `${API}/health`,
      // PW_REUSE: this WSL2 host sometimes DROPS connects to unbound ports
      // instead of refusing them, which wedges playwright's port preflight.
      // Pre-starting the servers by hand and setting PW_REUSE=1 sidesteps
      // it; CI never sets it, so CI always gets a fresh seeded stack.
      reuseExistingServer: !!process.env.PW_REUSE,
      timeout: 60_000,
    },
    {
      // a production build, not `next dev`: Next allows one dev server per
      // directory, so dev would collide with a running local stack — and the
      // smoke should walk what ships anyway. NEXT_PUBLIC_API_URL is baked
      // at build time, which is why the build lives inside this command.
      command:
        `bash -c 'NEXT_DIST_DIR=.next-e2e NEXT_PUBLIC_API_URL=${API} npx next build && ` +
        `NEXT_DIST_DIR=.next-e2e npx next start --port 3600'`,
      url: APP,
      reuseExistingServer: !!process.env.PW_REUSE,
      timeout: 180_000,
    },
  ],
});

import { defineConfig } from "@playwright/test";

/** The oidc sign-in walk, in its own config and its own stack.
 *
 *     npx playwright test --config playwright.oidc.config.ts
 *
 * Separate from playwright.config.ts because NEXT_PUBLIC_API_URL is baked at
 * build time (that config's own note explains why): an oidc walk needs a
 * SECOND backend and a SECOND build, and folding them into the default suite
 * would add a full `next build` to every smoke run.
 *
 * oidc is the production auth mode (OpenShift), and every other suite walks
 * trusted-header or api-key. Without this, the mode the deployment actually
 * uses is the one no browser ever renders.
 */

const IDP = process.env.SKEIN_OIDC_IDP_URL ?? "http://127.0.0.1:8610";
const API = process.env.SKEIN_OIDC_API_URL ?? "http://127.0.0.1:8601";
const APP = process.env.SKEIN_OIDC_APP_URL ?? "http://127.0.0.1:3601";
const IDP_PORT = new URL(IDP).port;
const API_PORT = new URL(API).port;
const APP_PORT = new URL(APP).port;
const AUDIENCE = "skein";

// Only the backend receives the database administrator URL. Build tools, the
// identity provider, and browser processes must not inherit it.
const CLEAN_ENV: Record<string, string> = {};
for (const [key, value] of Object.entries(process.env))
  if (value !== undefined && key !== "SKEIN_DATABASE_URL") CLEAN_ENV[key] = value;
const BACKEND_ENV = {
  ...CLEAN_ENV,
  SKEIN_DATABASE_URL:
    process.env.SKEIN_DATABASE_URL ?? "postgresql://skein:skein@127.0.0.1:5432/skein",
};

export default defineConfig({
  testDir: "./e2e-oidc",
  testIgnore: process.env.SKEIN_WORKPLACE_RUNTIME
    ? undefined
    : "workplace-runtime.spec.ts",
  fullyParallel: false, // one backend, one stub IdP, one sign-in at a time
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: APP, trace: "retain-on-failure" },
  webServer: [
    {
      // scripts/stub-idp.py signs with a real RS256 key the backend verifies
      // against its JWKS — a stub that skipped the signature would pass here
      // and prove nothing about app/oidc.py
      command: `../backend/.venv/bin/python ../scripts/stub-idp.py ${IDP_PORT} ${AUDIENCE}`,
      env: CLEAN_ENV,
      url: `${IDP}/jwks`,
      reuseExistingServer: !!process.env.PW_REUSE,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
      timeout: 30_000,
    },
    {
      command:
        `bash -c 'rm -rf /tmp/skein-oidc && mkdir -p /tmp/skein-oidc && ` +
        `cd ../backend && ` +
        `SKEIN_DATA_DIR=/tmp/skein-oidc SKEIN_MODEL_PROVIDER=mock SKEIN_SCHEDULER=0 SKEIN_EMBEDDINGS=0 ` +
        `.venv/bin/python seed.py && ` +
        `SKEIN_DATA_DIR=/tmp/skein-oidc SKEIN_AUTH_MODE=oidc SKEIN_OIDC_ISSUER=${IDP} ` +
        `.venv/bin/python -m app.bind_oidc ava=ava && ` +
        `SKEIN_DATA_DIR=/tmp/skein-oidc SKEIN_MODEL_PROVIDER=mock SKEIN_SCHEDULER=0 SKEIN_EMBEDDINGS=0 ` +
        `SKEIN_AUTH_MODE=oidc SKEIN_OIDC_ISSUER=${IDP} SKEIN_OIDC_AUDIENCE=${AUDIENCE} ` +
        `SKEIN_OIDC_CLIENT_ID=skein-web SKEIN_OIDC_ADMIN_GROUP=skein-admins ` +
        `SKEIN_CORS_ORIGINS=${APP} exec .venv/bin/uvicorn app.main:app --port ${API_PORT}'`,
      env: BACKEND_ENV,
      url: `${API}/health`,
      reuseExistingServer: !!process.env.PW_REUSE,
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
      timeout: 60_000,
    },
    {
      // its own dist dir, so this build never collides with .next (dev) or
      // .next-e2e (the default smoke suite)
      command:
        `bash -c 'NEXT_DIST_DIR=.next-oidc NEXT_PUBLIC_API_URL=${API} NEXT_PUBLIC_API_TOKEN= npx next build && ` +
        `exec env NEXT_DIST_DIR=.next-oidc NEXT_PUBLIC_API_TOKEN= node node_modules/next/dist/bin/next start --port ${APP_PORT}'`,
      env: CLEAN_ENV,
      url: APP,
      reuseExistingServer: !!process.env.PW_REUSE,
      gracefulShutdown: { signal: "SIGTERM", timeout: 10_000 },
      timeout: 180_000,
    },
  ],
});

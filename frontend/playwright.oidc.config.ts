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

const IDP = "http://127.0.0.1:8610";
const API = "http://127.0.0.1:8601";
const APP = "http://127.0.0.1:3601";
const AUDIENCE = "skein";

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
      command: `../backend/.venv/bin/python ../scripts/stub-idp.py 8610 ${AUDIENCE}`,
      url: `${IDP}/jwks`,
      reuseExistingServer: !!process.env.PW_REUSE,
      timeout: 30_000,
    },
    {
      command:
        `bash -c 'rm -rf /tmp/skein-oidc && mkdir -p /tmp/skein-oidc && ` +
        `cd ../backend && ` +
        `SKEIN_DATA_DIR=/tmp/skein-oidc SKEIN_MODEL_PROVIDER=mock SKEIN_SCHEDULER=0 ` +
        `.venv/bin/python seed.py && ` +
        `SKEIN_DATA_DIR=/tmp/skein-oidc SKEIN_MODEL_PROVIDER=mock SKEIN_SCHEDULER=0 ` +
        `SKEIN_AUTH_MODE=oidc SKEIN_OIDC_ISSUER=${IDP} SKEIN_OIDC_AUDIENCE=${AUDIENCE} ` +
        `SKEIN_OIDC_CLIENT_ID=skein-web SKEIN_OIDC_ADMIN_GROUP=skein-admins ` +
        `SKEIN_CORS_ORIGINS=${APP} .venv/bin/uvicorn app.main:app --port 8601'`,
      url: `${API}/health`,
      reuseExistingServer: !!process.env.PW_REUSE,
      timeout: 60_000,
    },
    {
      // its own dist dir, so this build never collides with .next (dev) or
      // .next-e2e (the default smoke suite)
      command:
        `bash -c 'NEXT_DIST_DIR=.next-oidc NEXT_PUBLIC_API_URL=${API} npx next build && ` +
        `NEXT_DIST_DIR=.next-oidc npx next start --port 3601'`,
      url: APP,
      reuseExistingServer: !!process.env.PW_REUSE,
      timeout: 180_000,
    },
  ],
});

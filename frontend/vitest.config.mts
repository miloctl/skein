import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // resolves the "@/..." alias the app imports with, straight from
  // tsconfig.json. Vite supports this natively now and warns that the
  // vite-tsconfig-paths plugin is redundant.
  resolve: { tsconfigPaths: true },
  test: {
    environment: "jsdom",
    // no globals: tests import describe/it/expect, so a test file reads the
    // same as an app file and eslint needs no test-only env
    setupFiles: ["./__tests__/setup.ts"],
    include: ["__tests__/**/*.test.{ts,tsx}"],
    coverage: {
      // A ratchet just under the measured number, the way the backend's 90
      // tracks its measured 91 (.gitea/workflows/ci.yml). A floor's first
      // job is stopping a regression, not certifying quality — waiting for
      // a number worth defending leaves the current one defended by
      // nothing. Raise it as coverage rises; never lower it to pass.
      // Measured 56.7% statements on 2026-08-14.
      thresholds: { statements: 55 },
      // the app's own source only. extensions/generated.ts is written by
      // scripts/compose-extensions.mjs and is a list of imports, so its
      // coverage tracks the deployment's extension allowlist rather than
      // anything a test could pin.
      include: ["app/**", "components/**", "lib/**"],
      exclude: ["**/*.d.ts", "extensions/generated.ts"],
    },
  },
});

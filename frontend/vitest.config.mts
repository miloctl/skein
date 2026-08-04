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
  },
});

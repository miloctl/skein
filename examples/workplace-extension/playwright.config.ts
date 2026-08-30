import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.SKEIN_CONTRACT_APP_URL || "http://127.0.0.1:3601",
    trace: "retain-on-failure",
  },
});

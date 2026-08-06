import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // standalone is the minimal server bundle the Docker image copies out of
  // .next/standalone — and `next start` refuses to serve it, warning on every
  // boot. Only the e2e build sets NEXT_DIST_DIR, and it is the only build that
  // runs `next start`, so gating on that leaves the shipped image untouched
  // while the browser walks stop testing a server Next told us not to run.
  output: process.env.NEXT_DIST_DIR ? undefined : "standalone",
  // e2e builds into their own dist dir (playwright.config.ts) so a running
  // dev server's .next/ is never trampled mid-session
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

export default nextConfig;

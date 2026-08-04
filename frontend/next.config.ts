import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // minimal server bundle for the Docker image
  // e2e builds into their own dist dir (playwright.config.ts) so a running
  // dev server's .next/ is never trampled mid-session
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

export default nextConfig;

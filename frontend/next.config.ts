import path from "node:path";

import type { NextConfig } from "next";

const packagedBuild = Boolean(process.env.SKEIN_FRONTEND_WORKSPACE_ROOT);
const workspaceRoot = packagedBuild
  ? path.resolve(process.env.SKEIN_FRONTEND_WORKSPACE_ROOT || ".")
  : __dirname;

const nextConfig: NextConfig = {
  // Next infers the workspace root by walking UP for a lockfile, so ANY stray
  // package-lock.json in a parent directory silently becomes the root. The
  // packaged build widens this only to the workplace root, where its one lock
  // and installed extension packages live.
  turbopack: { root: workspaceRoot },
  outputFileTracingRoot: workspaceRoot,
  transpilePackages: ["@miloctl/skein-extension-api"],
  // The packaged command owns one production shape regardless of inherited
  // Playwright variables. Source-tree e2e builds keep their separate dist dir.
  output: packagedBuild || !process.env.NEXT_DIST_DIR ? "standalone" : undefined,
  distDir: packagedBuild ? ".next" : process.env.NEXT_DIST_DIR || ".next",
  // The backstop for components/thread.tsx: model output is untrusted, and an
  // <img> the model authored fetches its URL on render, carrying whatever the
  // agent read in the query string. thread.tsx renders those inert; this line
  // is what still refuses the request if a later renderer forgets. The app
  // loads no remote image at all — the mark is inline SVG (components/mark.tsx)
  // and every texture is a gradient (app/globals.css) — so 'self' costs
  // nothing here. NOT default-src: connect-src has to reach the API on its own
  // origin (NEXT_PUBLIC_API_URL), which is a deploy-time value this file
  // cannot see.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: "img-src 'self' data:; object-src 'none'; base-uri 'self'",
          },
          { key: "X-Robots-Tag", value: "noindex, nofollow" },
        ],
      },
    ];
  },
};

export default nextConfig;

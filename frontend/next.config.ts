import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next infers the workspace root by walking UP for a lockfile, so ANY stray
  // package-lock.json in a parent directory (a bare `npm` run in $HOME leaves
  // one) silently becomes the root. Two things break when it does: the
  // standalone bundle lands at .next/standalone/<path-from-that-root>/server.js
  // instead of .next/standalone/server.js, which is where Dockerfile line 26
  // copies from and `node server.js` expects it; and Turbopack widens module
  // resolution and file watching to that parent. Pinning the root keeps both
  // fixed to this directory no matter what sits above it.
  turbopack: { root: __dirname },
  transpilePackages: ["@skein/extension-api"],
  // standalone is the minimal server bundle the Docker image copies out of
  // .next/standalone — and `next start` refuses to serve it, warning on every
  // boot. Only the e2e build sets NEXT_DIST_DIR, and it is the only build that
  // runs `next start`, so gating on that leaves the shipped image untouched
  // while the browser walks stop testing a server Next told us not to run.
  output: process.env.NEXT_DIST_DIR ? undefined : "standalone",
  // e2e builds into their own dist dir (playwright.config.ts) so a running
  // dev server's .next/ is never trampled mid-session
  distDir: process.env.NEXT_DIST_DIR || ".next",
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
            value:
              "img-src 'self' data:; object-src 'none'; base-uri 'self'",
          },
        ],
      },
    ];
  },
};

export default nextConfig;

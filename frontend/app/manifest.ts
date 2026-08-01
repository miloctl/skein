import type { MetadataRoute } from "next";

// background_color/theme_color are the loom-light page and thread values. They
// cannot follow the pack the way the app does — the manifest is read once at
// install time, long before any theme preference exists.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Skein",
    short_name: "Skein",
    description: "Many strands. One formation. — team platform for humans + AI agents",
    start_url: "/",
    display: "standalone",
    background_color: "#faf9f6",
    theme_color: "#3b4dbf",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}

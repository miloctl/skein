import type { Metadata, Viewport } from "next";
import {
  Bricolage_Grotesque,
  Fraunces,
  Geist,
  Geist_Mono,
  Pixelify_Sans,
  Source_Serif_4,
} from "next/font/google";
import "./globals.css";

import { CapturePalette } from "@/components/capture-palette";
import { Nav } from "@/components/nav";
import { StatusRegion } from "@/components/status-region";
import { ThemeSync } from "@/components/theme-sync";
import { themeBootScript } from "@/lib/theme-boot";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// preload: false on the four theme-pack faces below: the active pack
// renders at most two of them (globals.css maps --font-heading/--font-body
// per pack), and preloading all six makes every cold load pay for fonts it
// never draws. Geist sans/mono stay preloaded — every pack uses them.
// The cost: a pack whose heading face is here (Bricolage on the default
// loom pack included) swaps in after first paint instead of before.
const bricolage = Bricolage_Grotesque({
  variable: "--font-bricolage",
  subsets: ["latin"],
  preload: false,
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  // variable axes: Ledger wears the hard newsprint cut (SOFT 0, WONK 0),
  // Atelier the soft wonky one (SOFT 80, WONK 1) — same family, two voices
  axes: ["SOFT", "WONK", "opsz"],
  preload: false,
});

const sourceSerif = Source_Serif_4({
  variable: "--font-source-serif",
  subsets: ["latin"],
  preload: false,
});

// Hermes wears a pixel display face — the closest open cut to the source
// dashboard's Mondwest brand chrome
const pixelify = Pixelify_Sans({
  variable: "--font-pixelify",
  subsets: ["latin"],
  preload: false,
});

export const metadata: Metadata = {
  // metadataBase silences the build warning about resolving OG URLs against
  // localhost. It follows the same env-var path NEXT_PUBLIC_API_URL already
  // takes from SKEIN_HOST, so the two can't drift.
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: "Skein",
  applicationName: "Skein",
  description: "Many strands. One formation. — team platform for humans + AI agents",
  openGraph: {
    title: "Skein",
    description: "Many strands. One formation. — team platform for humans + AI agents",
    siteName: "Skein",
    type: "website",
  },
  // No `icons` field: app/favicon.ico, app/icon.svg and app/apple-icon.png are
  // file conventions Next already emits links for. Declaring them here too
  // produces duplicate, conflicting <link rel="icon"> tags.
};

// The address bar on mobile takes its colour from this. A static pair would
// be wrong for every pack but loom (phosphor #f2f5ef, contrast #ffffff, ...),
// so lib/theme.ts overwrites the tag from the resolved page colour after
// applying prefs; this is only the pre-hydration fallback.
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#faf9f6" },
    { media: "(prefers-color-scheme: dark)", color: "#141311" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${bricolage.variable} ${fraunces.variable} ${sourceSerif.variable} ${pixelify.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        {/* Applies saved theme prefs before first paint. GENERATED from
            lib/theme.ts — the ids, keys and formulas are never written twice.
            See lib/theme-boot.ts for why this cannot simply import them. */}
        <script dangerouslySetInnerHTML={{ __html: themeBootScript() }} />
      </head>
      <body className="min-h-full flex flex-col">
        <ThemeSync />
        {/* one live region for the whole app — every surface reports through
            lib/status.ts rather than calling window.alert() */}
        <StatusRegion />
        {/* every header control precedes the page content — a keyboard user
            tabs through all of them on every page without a bypass */}
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-lg focus:border focus:border-line-strong focus:bg-card focus:px-3 focus:py-2 focus:text-sm"
        >
          Skip to content
        </a>
        <Nav />
        <CapturePalette />
        {children}
      </body>
    </html>
  );
}

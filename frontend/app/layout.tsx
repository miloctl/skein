import type { Metadata } from "next";
import {
  Bricolage_Grotesque,
  Fraunces,
  Geist,
  Geist_Mono,
  Source_Serif_4,
} from "next/font/google";
import "./globals.css";

import { CapturePalette } from "@/components/capture-palette";
import { Nav } from "@/components/nav";
import { ThemeSync } from "@/components/theme-sync";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const bricolage = Bricolage_Grotesque({
  variable: "--font-bricolage",
  subsets: ["latin"],
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  // variable axes: Ledger wears the hard newsprint cut (SOFT 0, WONK 0),
  // Atelier the soft wonky one (SOFT 80, WONK 1) — same family, two voices
  axes: ["SOFT", "WONK", "opsz"],
});

const sourceSerif = Source_Serif_4({
  variable: "--font-source-serif",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Skein",
  description: "Many strands. One formation. — team platform for humans + AI agents",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${bricolage.variable} ${fraunces.variable} ${sourceSerif.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        {/* apply saved theme prefs before first paint (mirrors lib/theme.ts —
            keep the key names and allow-lists in sync) */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var d=document.documentElement,t=localStorage.getItem("skein-theme");if(["madder","verdigris","graphite"].indexOf(t)>=0)d.dataset.theme=t;else if(t==="custom"){var c=JSON.parse(localStorage.getItem("skein-custom")||"{}"),th=((Math.round(+c.thread)%360)+360)%360,w=((Math.round(+c.weld)%360)+360)%360;if(isFinite(th)&&isFinite(w)){d.dataset.theme="custom";d.style.setProperty("--thread","light-dark(oklch(0.44 0.13 "+th+"), oklch(0.8 0.09 "+th+"))");d.style.setProperty("--thread-solid","light-dark(oklch(0.44 0.13 "+th+"), oklch(0.5 0.13 "+th+"))");d.style.setProperty("--weld","light-dark(oklch(0.47 0.09 "+w+"), oklch(0.78 0.09 "+w+"))")}}var a=localStorage.getItem("skein-appearance");if(a==="light"||a==="dark")d.dataset.appearance=a;var p=localStorage.getItem("skein-pack");if(["ledger","phosphor","contrast","atelier"].indexOf(p)>=0)d.dataset.pack=p;if(localStorage.getItem("skein-chat-sidebar-collapsed")==="1")d.dataset.chatSidebar="collapsed"}catch(e){}})()`,
          }}
        />
      </head>
      <body className="min-h-full flex flex-col">
        <ThemeSync />
        {/* 9 header stops precede every page — a keyboard user needs a bypass */}
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

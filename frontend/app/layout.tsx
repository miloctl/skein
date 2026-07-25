import type { Metadata } from "next";
import { Bricolage_Grotesque, Geist, Geist_Mono } from "next/font/google";
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
      className={`${geistSans.variable} ${geistMono.variable} ${bricolage.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        {/* apply saved theme prefs before first paint (mirrors lib/theme.ts —
            keep the key names and allow-lists in sync) */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var d=document.documentElement,t=localStorage.getItem("skein-theme");if(["madder","verdigris","graphite"].indexOf(t)>=0)d.dataset.theme=t;var a=localStorage.getItem("skein-appearance");if(a==="light"||a==="dark")d.dataset.appearance=a}catch(e){}})()`,
          }}
        />
      </head>
      <body className="min-h-full flex flex-col">
        <ThemeSync />
        <Nav />
        <CapturePalette />
        {children}
      </body>
    </html>
  );
}

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
            __html: `(function(){try{var d=document.documentElement,t=localStorage.getItem("skein-theme");if(["madder","verdigris","graphite"].indexOf(t)>=0)d.dataset.theme=t;else if(t==="custom"){var c=JSON.parse(localStorage.getItem("skein-custom")||"{}"),th=((Math.round(+c.thread)%360)+360)%360,w=((Math.round(+c.weld)%360)+360)%360;if(isFinite(th)&&isFinite(w)){d.dataset.theme="custom";d.style.setProperty("--thread","light-dark(oklch(0.44 0.13 "+th+"), oklch(0.8 0.09 "+th+"))");d.style.setProperty("--thread-solid","light-dark(oklch(0.44 0.13 "+th+"), oklch(0.5 0.13 "+th+"))");d.style.setProperty("--weld","light-dark(oklch(0.47 0.09 "+w+"), oklch(0.78 0.09 "+w+"))")}}var a=localStorage.getItem("skein-appearance");if(a==="light"||a==="dark")d.dataset.appearance=a;var p=localStorage.getItem("skein-pack");if(["ledger","phosphor","contrast"].indexOf(p)>=0)d.dataset.pack=p}catch(e){}})()`,
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

import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Strands Team Platform",
  description: "AI team coordination platform built on the Strands Agents SDK",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-white text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-zinc-200 bg-white/80 px-6 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
          <Link href="/" className="text-sm font-bold tracking-tight">
            🧵 Strands <span className="font-normal text-zinc-400">Team Platform</span>
          </Link>
          <nav className="flex gap-4 text-sm text-zinc-500">
            <Link href="/" className="hover:text-zinc-900 dark:hover:text-zinc-100">
              Chat
            </Link>
            <Link
              href="/dashboard"
              className="hover:text-zinc-900 dark:hover:text-zinc-100"
            >
              Dashboard
            </Link>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { api, getUser, setUser } from "@/lib/api";

const LINKS = [
  { href: "/", label: "My Day" },
  { href: "/chat", label: "Chat" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/portfolio", label: "Portfolio" },
  { href: "/agents", label: "Agents" },
  { href: "/review", label: "Review" },
  { href: "/intake", label: "Intake" },
];

export function Nav() {
  const pathname = usePathname();
  const [user, setUserState] = useState("anonymous");
  const [attention, setAttention] = useState(0);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    setUserState(getUser());
    let generation = 0;
    const poll = () => {
      const g = ++generation;
      api<{ count: number }>("/api/attention")
        .then((r) => {
          if (g === generation) setAttention(r.count); // ignore stale responses
        })
        .catch(() => {});
    };
    poll();
    const t = setInterval(poll, 30_000);
    return () => {
      generation++; // invalidate in-flight responses
      clearInterval(t);
    };
  }, []);

  return (
    <header className="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-zinc-200 bg-white/80 px-6 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80">
      <Link href="/" className="text-sm font-bold tracking-tight">
        🧵 Skein{" "}
        <span className="hidden font-normal text-zinc-400 sm:inline">
          many strands · one formation
        </span>
      </Link>
      <nav className="flex items-center gap-4 text-sm text-zinc-500">
        {LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={
              pathname === l.href
                ? "font-semibold text-zinc-900 dark:text-zinc-100"
                : "hover:text-zinc-900 dark:hover:text-zinc-100"
            }
          >
            {l.label}
            {l.href === "/review" && attention > 0 ? (
              <span className="ml-1 rounded-full bg-red-500 px-1.5 text-xs font-semibold text-white">
                {attention}
              </span>
            ) : null}
          </Link>
        ))}
        <span className="text-zinc-300 dark:text-zinc-700">·</span>
        {editing ? (
          <input
            autoFocus
            defaultValue={user === "anonymous" ? "" : user}
            placeholder="your name"
            className="w-28 rounded border border-zinc-300 bg-transparent px-2 py-0.5 text-sm outline-none dark:border-zinc-700"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                const name = (e.target as HTMLInputElement).value;
                setUser(name);
                setUserState(name.trim() || "anonymous");
                setEditing(false);
                window.location.reload();
              }
              if (e.key === "Escape") setEditing(false);
            }}
            onBlur={() => setEditing(false)}
          />
        ) : (
          <button
            onClick={() => setEditing(true)}
            className="rounded-full bg-zinc-100 px-2.5 py-0.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
            title="Click to change who you are"
          >
            👤 {user}
          </button>
        )}
        <span
          className="hidden text-xs text-zinc-400 md:inline"
          title="Quick capture"
        >
          ⌘K capture
        </span>
      </nav>
    </header>
  );
}

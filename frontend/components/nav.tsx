"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

import { api, getApiKey, getUser, setUser } from "@/lib/api";

// notifies on cross-tab changes; same-tab changes reload the page
function subscribeUser(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

// grouped by job: daily driving | reading the state | deciding | people & rules
const GROUPS: { href: string; label: string }[][] = [
  [
    { href: "/", label: "My Day" },
    { href: "/chat", label: "Chat" },
  ],
  [
    { href: "/dashboard", label: "Dashboard" },
    { href: "/portfolio", label: "Portfolio" },
    { href: "/insights", label: "Insights" },
  ],
  [
    { href: "/review", label: "Review" },
    { href: "/intake", label: "Intake" },
    { href: "/ingest", label: "Notes" },
  ],
  [
    { href: "/agents", label: "Agents" },
    { href: "/people", label: "People" },
    { href: "/charter", label: "Charter" },
  ],
];

function NavLink({
  href,
  label,
  active,
  badge,
}: {
  href: string;
  label: string;
  active: boolean;
  badge?: number;
}) {
  return (
    <Link
      href={href}
      className={
        "relative flex h-14 items-center whitespace-nowrap text-[13px] transition-colors " +
        (active ? "font-medium text-ink" : "text-ink-2 hover:text-ink")
      }
    >
      {label}
      {badge ? (
        <span
          aria-label={`${badge} items needing review`}
          className="ml-1.5 rounded-full border border-danger/25 bg-danger/10 px-1.5 py-px font-mono text-[10px] tabular-nums text-danger"
        >
          {badge}
        </span>
      ) : null}
      {active && (
        <span
          aria-hidden
          className="absolute inset-x-0 bottom-0 h-0.5 bg-thread-solid"
        />
      )}
    </Link>
  );
}

export function Nav() {
  const pathname = usePathname();
  const user = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  const [attention, setAttention] = useState(0);
  const [editing, setEditing] = useState(false);
  // localStorage is client-only; same-tab changes reload the page (setApiKey callers)
  const hasKey = useSyncExternalStore(
    subscribeUser,
    () => Boolean(getApiKey()),
    () => false,
  );

  useEffect(() => {
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

  const anonymous = user === "anonymous";

  return (
    <header className="sticky top-0 z-10 bg-page/85 backdrop-blur">
      <div className="flex h-14 items-center justify-between gap-6 px-6">
        <Link href="/" className="flex items-baseline gap-2 whitespace-nowrap">
          <span className="font-display text-[15px] font-semibold tracking-tight text-ink">
            Skein
          </span>
          <span className="hidden font-mono text-[11px] tracking-[0.08em] text-ink-3 xl:inline">
            many strands · one formation
          </span>
        </Link>
        <nav className="flex items-center gap-4">
          {GROUPS.map((group, gi) => (
            <div key={gi} className="flex items-center gap-3">
              {gi > 0 && <span aria-hidden className="h-4 w-px bg-line" />}
              {group.map((l) => (
                <NavLink
                  key={l.href}
                  href={l.href}
                  label={l.label}
                  active={pathname === l.href}
                  badge={l.href === "/review" ? attention : undefined}
                />
              ))}
            </div>
          ))}
          <span aria-hidden className="h-4 w-px bg-line" />
          {editing ? (
            <input
              autoFocus
              defaultValue={anonymous ? "" : user}
              placeholder="your name"
              aria-label="Your name"
              className="w-28 rounded-lg border border-line-strong bg-transparent px-2 py-0.5 text-sm outline-none focus:border-thread-solid"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const name = (e.target as HTMLInputElement).value;
                  setUser(name);
                  setEditing(false);
                  window.location.reload();
                }
                if (e.key === "Escape") setEditing(false);
              }}
              onBlur={(e) => {
                // clicking away must not silently discard a typed name
                const name = e.target.value.trim();
                if (name && name !== user) {
                  setUser(name);
                  window.location.reload();
                }
                setEditing(false);
              }}
            />
          ) : (
            <button
              onClick={() => setEditing(true)}
              className="flex items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-2 text-[13px] text-ink-2 hover:bg-raised hover:text-ink"
              title="Click to change who you are"
            >
              <span
                className={
                  "flex size-5 items-center justify-center rounded-full font-mono text-[10px] uppercase " +
                  (anonymous
                    ? "border border-dashed border-line-strong text-ink-3"
                    : "bg-thread-solid/15 text-thread")
                }
              >
                {anonymous ? "?" : user[0]}
              </span>
              {anonymous ? <span className="text-ink-3">anonymous</span> : user}
            </button>
          )}
          <Link
            href="/settings"
            className="relative text-ink-3 hover:text-ink"
            aria-label={
              hasKey ? "Settings — strong identity active" : "Settings"
            }
            title={
              hasKey
                ? "Settings — strong identity active"
                : "Settings — identity, API key, calendar"
            }
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
            {hasKey && (
              <span
                aria-hidden
                className="absolute -right-0.5 -top-0.5 size-1.5 rounded-full bg-ok"
              />
            )}
          </Link>
          <kbd
            className="hidden rounded border border-line-strong bg-raised px-1.5 py-0.5 font-mono text-[11px] text-ink-2 md:inline"
            title="Quick capture"
          >
            ⌘K
          </kbd>
        </nav>
      </div>
      <div className="selvage" id="selvage" aria-hidden />
    </header>
  );
}

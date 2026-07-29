"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

// identity/key changes notify via the storage event (cross-tab natively,
// same-tab dispatched by the lib/api writers)
import { api, getApiKey, getUser, setUser, subscribeUser } from "@/lib/api";

// five destinations, grouped by job: my work | team work | needs a verdict |
// people & rules. Former top-level pages live on as tabs inside Work
// (Health/Browse/Insights), Inbox (Approvals/Requests/Paste notes), and Team
// (Agents/1:1s/Charter) — their URLs are unchanged.
const GROUPS: { href: string; label: string; paths: string[] }[][] = [
  [
    { href: "/", label: "My Day", paths: ["/"] },
    { href: "/chat", label: "Chat", paths: ["/chat"] },
  ],
  [
    {
      href: "/portfolio",
      label: "Work",
      paths: ["/portfolio", "/dashboard", "/insights"],
    },
    { href: "/review", label: "Inbox", paths: ["/review", "/intake", "/ingest"] },
  ],
  [
    { href: "/agents", label: "Team", paths: ["/agents", "/people", "/charter"] },
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
      aria-current={active ? "page" : undefined}
      className={
        "relative flex h-10 items-center whitespace-nowrap text-[13px] transition-colors md:h-14 " +
        (active ? "font-medium text-ink" : "text-ink-2 hover:text-ink")
      }
    >
      {label}
      {badge ? (
        <>
          <span
            aria-hidden
            className="ml-1.5 rounded-full border border-danger/25 bg-danger/10 px-1.5 py-px font-mono text-[10px] tabular-nums text-danger"
          >
            {badge}
          </span>
          <span className="sr-only">, {badge} awaiting a verdict</span>
        </>
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
    // a backgrounded phone tab shouldn't wake for this, and coming back
    // should refresh immediately rather than show a stale badge for 30s
    const tick = () => document.visibilityState === "visible" && poll();
    const t = setInterval(tick, 30_000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      generation++; // invalidate in-flight responses
      clearInterval(t);
      document.removeEventListener("visibilitychange", tick);
    };
  }, []);

  const anonymous = user === "anonymous";

  return (
    <header className="sticky top-0 z-10 bg-page/85 backdrop-blur">
      <div className="flex min-h-[var(--nav-h)] flex-wrap items-center px-4 sm:px-6 md:min-h-0">
        <Link href="/" className="flex h-14 items-center gap-2 whitespace-nowrap">
          <span className="font-display text-[15px] font-semibold tracking-tight text-ink">
            Skein
          </span>
          <span className="hidden font-mono text-[11px] tracking-[0.08em] text-ink-3 xl:inline">
            many strands · one formation
          </span>
        </Link>
        <div className="ml-auto flex h-14 items-center gap-3 md:order-2 md:ml-4">
          <span aria-hidden className="hidden h-4 w-px bg-line md:block" />
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
                // commit on blur so a typed name isn't silently discarded, but
                // NEVER reload from a focus change (WCAG 3.2.2) — the header
                // and every subscriber already track identity live
                const name = e.target.value.trim();
                if (name && name !== user) setUser(name);
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
              {anonymous ? (
                <span className="text-ink-3">anonymous</span>
              ) : (
                <span className="max-w-[9rem] truncate">{user}</span>
              )}
            </button>
          )}
          <Link
            href="/settings"
            className="relative flex h-11 w-8 items-center justify-center text-ink-3 hover:text-ink md:h-auto md:w-auto"
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
          <button
            onClick={(e) => {
              // Safari doesn't focus buttons on click — focus explicitly so
              // the palette can hand focus back here when it closes
              e.currentTarget.focus();
              window.dispatchEvent(new Event("skein-capture-open"));
            }}
            aria-label="Quick capture"
            title="Quick capture (⌘K)"
            className="rounded border border-line-strong bg-raised px-2 py-1.5 font-mono text-[11px] text-ink-2 hover:bg-line hover:text-ink md:px-1.5 md:py-0.5"
          >
            <span className="md:hidden">+ Capture</span>
            <span className="hidden md:inline">⌘K</span>
          </button>
        </div>
        <nav
          aria-label="Primary"
          className="-mx-4 flex w-full items-center gap-4 overflow-x-auto px-4 py-1.5 sm:-mx-6 sm:px-6 md:order-1 md:mx-0 md:ml-auto md:w-auto md:overflow-visible md:px-0 md:py-0"
        >
          {GROUPS.map((group, gi) => (
            <div key={gi} className="flex items-center gap-3">
              {gi > 0 && <span aria-hidden className="h-4 w-px bg-line" />}
              {group.map((l) => (
                <NavLink
                  key={l.href}
                  href={l.href}
                  label={l.label}
                  active={l.paths.includes(pathname)}
                  badge={l.href === "/review" ? attention : undefined}
                />
              ))}
            </div>
          ))}
        </nav>
      </div>
      <div className="selvage" id="selvage" aria-hidden />
    </header>
  );
}

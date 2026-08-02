"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { SkeinMark } from "@/components/mark";
// identity/key changes notify via the storage event (cross-tab natively,
// same-tab dispatched by the lib/api writers)
import { api, getApiKey, getUser, subscribeUser } from "@/lib/api";

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
    { href: "/agents", label: "Team", paths: ["/agents", "/people", "/charter", "/activity"] },
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
          className="absolute inset-x-0 bottom-0 h-0.5 bg-thread"
        />
      )}
    </Link>
  );
}

export function Nav() {
  const pathname = usePathname();
  const user = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  const [attention, setAttention] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  // fetched lazily on first menu open — the nav must not add a request to
  // every page load for a number that only matters inside the menu
  const [guideMeta, setGuideMeta] = useState<{ tied_count: number; total: number } | null>(null);
  const idBtnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  // focus the first item once per open — not via a ref callback, which would
  // steal focus back on every re-render (e.g. when the guide count arrives)
  useEffect(() => {
    if (menuOpen)
      menuRef.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
  }, [menuOpen]);
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
          {/* the mark carries the fixed identity; the wordmark stays live text
              so it keeps being re-cut per pack (Fraunces under ledger/atelier,
              glowing mono under phosphor) — freezing it would make it the one
              non-parametric piece of type on screen */}
          <SkeinMark size={17} className="text-thread" />
          <span className="font-display text-[15px] font-semibold tracking-tight text-ink">
            Skein
          </span>
          <span className="hidden font-mono text-[11px] tracking-[0.08em] text-ink-3 xl:inline">
            many strands · one formation
          </span>
        </Link>
        <div className="ml-auto flex h-14 items-center gap-3 md:order-2 md:ml-4">
          <span aria-hidden className="hidden h-4 w-px bg-line md:block" />
          <div className="relative">
            <button
              ref={idBtnRef}
              onClick={() => {
                const opening = !menuOpen;
                setMenuOpen(opening);
                if (opening && !guideMeta && !anonymous)
                  api<{ tied_count: number; total: number }>("/api/field-guide/hint")
                    .then((h) => setGuideMeta({ tied_count: h.tied_count, total: h.total }))
                    .catch(() => {});
              }}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              title={anonymous ? "Who are you?" : `You — ${user}`}
              className="relative flex min-h-11 items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-2 text-[13px] text-ink-2 hover:bg-raised hover:text-ink md:min-h-0"
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
              {hasKey && (
                <span
                  aria-hidden
                  title="Strong identity active"
                  className="absolute left-4 top-1 size-1.5 rounded-full bg-ok"
                />
              )}
            </button>
            {menuOpen && (
              <div
                ref={menuRef}
                role="menu"
                aria-label="You"
                onBlur={(e) => {
                  // Tab-out closes; Escape below returns focus to the button
                  if (!e.currentTarget.contains(e.relatedTarget as Node))
                    setMenuOpen(false);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setMenuOpen(false);
                    idBtnRef.current?.focus();
                  }
                  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                    e.preventDefault();
                    const items = [
                      ...(menuRef.current?.querySelectorAll<HTMLElement>(
                        "[role=menuitem]",
                      ) ?? []),
                    ];
                    const i = items.indexOf(document.activeElement as HTMLElement);
                    const next =
                      e.key === "ArrowDown"
                        ? items[(i + 1) % items.length]
                        : items[(i - 1 + items.length) % items.length];
                    next?.focus();
                  }
                }}
                className="absolute right-0 top-full z-20 mt-1 w-56 rounded-xl border border-line bg-card p-1 shadow-float"
              >
                <Link
                  href="/settings"
                  role="menuitem"
                  onClick={() => setMenuOpen(false)}
                  className="block w-full rounded px-2.5 py-2 text-left text-[13px] text-ink-2 hover:bg-raised focus:bg-raised md:py-1.5"
                >
                  <span aria-hidden>⚙ </span>
                  {anonymous ? "Pick your name…" : "Settings"}
                </Link>
                <Link
                  href="/guide"
                  role="menuitem"
                  onClick={() => setMenuOpen(false)}
                  className="flex w-full items-baseline justify-between rounded px-2.5 py-2 text-left text-[13px] text-ink-2 hover:bg-raised focus:bg-raised md:py-1.5"
                >
                  <span>
                    <span aria-hidden>🧶 </span>Field guide
                  </span>
                  {guideMeta && (
                    <span className="font-mono text-[10px] tabular-nums text-ink-3">
                      {guideMeta.tied_count}/{guideMeta.total}
                    </span>
                  )}
                </Link>
                <p className="mt-1 border-t border-line px-2.5 pb-1 pt-1.5 text-[11px] text-ink-3">
                  {anonymous
                    ? "No name picked — writes will not be yours"
                    : hasKey
                      ? "Strong identity active"
                      : "Weak identity — no API key"}
                </p>
              </div>
            )}
          </div>
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

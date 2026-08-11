"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { SkeinMark } from "@/components/mark";
import { NavSearch } from "@/components/nav-search";
import { Shortcut } from "@/components/shortcut";
// identity/key changes notify via the storage event (cross-tab natively,
// same-tab dispatched by the lib/api writers)
import { api, getApiKey, getUser, subscribeUser } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { authConfig, isSignedIn, signIn, signOut } from "@/lib/auth";
import { isGated, subscribeGated } from "@/lib/gated";
import { useFrontendExtensions } from "@/lib/extensions/context";

// five destinations, grouped by job: my work | team work | needs a verdict |
// people & rules. Former top-level pages live on as tabs inside Work
// (Health/Browse/Insights), Inbox (Approvals/Requests/Paste notes), and Team
// (1:1s/Charter beside Agents and the Activity feed, which was born a tab)
// — their URLs are unchanged.
const GROUPS: { href: string; label: string; paths: string[] }[][] = [
  [
    { href: "/", label: "My Day", paths: ["/"] },
    { href: "/chat", label: "Chat", paths: ["/chat"] },
  ],
  [
    {
      href: "/portfolio",
      label: "Work",
      paths: ["/planning", "/portfolio", "/dashboard", "/insights", "/artifacts"],
    },
    {
      href: "/review",
      label: "Inbox",
      paths: ["/review", "/intake", "/ingest"],
    },
  ],
  [
    {
      href: "/agents",
      label: "Team",
      paths: ["/agents", "/people", "/charter", "/activity"],
    },
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
  const { navigation } = useFrontendExtensions();
  const pathname = usePathname();
  const user = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  // two independent numbers — see the poll below for why they cannot be one
  const [attention, setAttention] = useState({ inbox: 0, yours: 0 });
  const [menuOpen, setMenuOpen] = useState(false);
  // fetched lazily on first menu open — the nav must not add a request to
  // every page load for a number that only matters inside the menu
  const [guideMeta, setGuideMeta] = useState<{
    tied_count: number;
    total: number;
  } | null>(null);
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
  const signedIn = useSyncExternalStore(subscribeUser, isSignedIn, () => false);
  // the deployment's identity model. Until it arrives the nav shows nothing
  // mode-specific rather than guessing, because guessing wrong offers a
  // sign-in that cannot work or hides one that is required.
  const [mode, setMode] = useState("");
  useEffect(() => {
    authConfig().then((c) => setMode(c.mode));
  }, []);

  const gated = useSyncExternalStore(subscribeGated, isGated, () => false);

  useEffect(() => {
    // nothing to count while the auth gate stands: this can only 401, and the
    // number it would carry describes a workspace the reader cannot open. The
    // `gated` dependency also makes signing in re-poll at once, so the badge
    // is current the moment the workspace comes back.
    if (gated) return;
    let generation = 0;
    const poll = () => {
      const g = ++generation;
      // two numbers, two readers: `inbox` is what the Inbox badge promises
      // (proposals + triage, the rows that page actually shows) and `yours` is
      // what is addressed to this person by name. The tab title carries
      // `yours` — it is the only part of Skein visible from an editor, and it
      // said "3" about a queue nobody had assigned to the reader.
      api<{ inbox: number; yours: number }>("/api/attention")
        .then((r) => {
          if (g === generation) setAttention({ inbox: r.inbox, yours: r.yours }); // ignore stale responses
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
  }, [gated]);

  // The attention count in the TAB TITLE, which is the only part of Skein a
  // person sees while they are in their editor. Without it the immediate
  // notification tier means "the next time you happen to open the app".
  //
  // A MutationObserver, not a plain assignment: Next re-applies the route's
  // metadata title on every navigation, and whether that lands before or
  // after this effect is not ours to order. Observing the node means the
  // count survives whoever writes last. Re-entry is bounded — the callback
  // only writes when the text differs from what it wants.
  useEffect(() => {
    const el = document.querySelector("title");
    if (!el) return;
    const base = () => el.textContent?.replace(/^\(\d+\)\s+/, "") || "Skein";
    const apply = () => {
      // the count is dropped while the gate stands, not just left to go
      // stale: a session that expires mid-task keeps its last number, and a
      // locked-out reader would sit in front of a tab promising them three
      // things to do at a workspace that will not open.
      const wanted =
        attention.yours && !gated ? `(${attention.yours}) ${base()}` : base();
      if (el.textContent !== wanted) el.textContent = wanted;
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(el, { childList: true, characterData: true, subtree: true });
    return () => observer.disconnect();
  }, [attention.yours, gated]);

  const anonymous = user === "anonymous";
  const headerRef = useRef<HTMLElement>(null);
  // inert while the auth gate stands: the gate COVERS the header rather than
  // unmounting it, so without this a keyboard user tabs into links hidden
  // under the overlay and focus lands on nothing visible.
  //
  // An EFFECT writing the ATTRIBUTE, never a rendered inert={gated} prop.
  // task-peek.tsx strips the inert attribute from every body sibling when its
  // panel closes, and React does not re-apply an attribute it believes is
  // already set — as a prop this survived until the first ?task= link and
  // then never came back. React runs every cleanup in a commit before any
  // effect body, so re-asserting here always lands after that strip, and both
  // writers have to name the same thing for that to work.
  useEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    if (gated) el.setAttribute("inert", "");
    else el.removeAttribute("inert");
  }, [gated]);

  return (
    <header ref={headerRef} className="sticky top-0 z-10 bg-page/85 backdrop-blur">
      <div className="flex min-h-[var(--nav-h)] flex-wrap items-center px-4 sm:px-6 md:min-h-0">
        {/* Logo and identity share ONE non-wrapping row; only the nav below
            wraps. Without this they are two flex items of a flex-wrap parent,
            and flex breaks lines using hypothetical sizes BEFORE it shrinks
            anything — so no min-w-0 on the name can stop the identity being
            pushed onto a third row when a pack re-cuts the type wider
            (phosphor uppercases the wordmark; hermes ships a pixel face).
            md:contents dissolves this wrapper on desktop, where the parent's
            own order-1/order-2 places the same children. */}
        <div className="flex w-full min-w-0 items-center md:contents">
          <Link
            href="/"
            className="flex h-14 shrink-0 items-center gap-2 whitespace-nowrap"
          >
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
          {/* min-w-0 so this cluster SHRINKS instead of wrapping the header onto
            a third row. A fixed cap on the name is not enough: a pack re-cuts
            the type, and phosphor (mono) and hermes (pixel display) render the
            same characters wider, which pushed the cluster past the row and
            drifted --nav-h by 56px — one whole row. Shrinking makes the name's
            truncate absorb whatever the pack costs. */}
          <div className="ml-auto flex h-14 min-w-0 items-center gap-3 md:order-2 md:ml-4">
            {/* hidden for an anonymous visitor: every result is scoped to a
                caller, so an unnamed one would search as nobody and read an
                empty index as "the team has nothing" */}
            {!anonymous && <NavSearch />}
            <span aria-hidden className="hidden h-4 w-px bg-line md:block" />
            <div className="relative min-w-0">
              <button
                ref={idBtnRef}
                onClick={() => {
                  const opening = !menuOpen;
                  setMenuOpen(opening);
                  if (opening && !guideMeta && !anonymous)
                    api<{ tied_count: number; total: number }>(
                      "/api/field-guide/hint",
                    )
                      .then((h) =>
                        setGuideMeta({
                          tied_count: h.tied_count,
                          total: h.total,
                        }),
                      )
                      .catch(() => {});
                }}
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                title={anonymous ? "Pick your name" : `You — ${user}`}
                className="relative flex min-h-11 min-w-0 items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-2 text-[13px] text-ink-2 hover:bg-raised hover:text-ink md:min-h-0"
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
                  // no search box for an anonymous visitor, so the row has the
                  // width to say this — and it must, because picking a name is
                  // the one thing that visitor has to do
                  <span className="text-ink-3">anonymous</span>
                ) : (
                  // Read, not seen, below `sm`. The header holds the logo, the
                  // search field, this chip and the capture button in 328px of
                  // content box at 360px; the name at 7rem left the search
                  // field 10px wide. The avatar carries identity on a phone and
                  // the menu spells it out, so the name stays in the
                  // accessibility tree rather than being dropped: sr-only keeps
                  // the button's accessible name, which is what a screen reader
                  // and voice control both read.
                  <span className="sr-only min-w-0 sm:not-sr-only sm:block sm:max-w-[9rem] sm:truncate">
                    {user}
                  </span>
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
                      const i = items.indexOf(
                        document.activeElement as HTMLElement,
                      );
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
                    {/* only trusted-header mode lets a person type who they are.
                      Offering it elsewhere invites a name the server ignores. */}
                    {anonymous && mode === "trusted-header"
                      ? "Pick your name…"
                      : "Settings"}
                  </Link>
                  {mode === "oidc" && (
                    <button
                      role="menuitem"
                      onClick={() => {
                        setMenuOpen(false);
                        if (signedIn) {
                          signOut();
                          // A full navigation, and no setUser("anonymous").
                          // signOut clears the GET cache, but nothing
                          // re-fetches a card that already rendered — signing
                          // out on /people left the private 1:1 notes painted
                          // on screen. The load lands on the auth gate, which
                          // shows the signed-out state (auth-gate.tsx reads
                          // the reason signOut just recorded). The display
                          // name stays: oidc mode ignores X-User, so
                          // overwriting it only destroys what the person typed.
                          window.location.assign("/");
                        } else {
                          // signIn resolves to a message only when it could not
                          // start: an unconfigured deployment, or a config it
                          // could not read. Never a success path.
                          signIn(pathname).then((m) => m && reportStatus(m));
                        }
                      }}
                      className="block w-full rounded px-2.5 py-2 text-left text-[13px] text-ink-2 hover:bg-raised focus:bg-raised md:py-1.5"
                    >
                      <span aria-hidden>{signedIn ? "⇥ " : "⇤ "}</span>
                      {signedIn ? "Sign out" : "Sign in"}
                    </button>
                  )}
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
                    {/* what the SERVER will make of this caller. "Weak identity"
                      is true only where the name picker is the identity. */}
                    {signedIn
                      ? "Signed in — strong identity"
                      : mode === "oidc"
                        ? hasKey
                          ? "Strong identity active"
                          : "Signed out — sign in to open the workspace"
                        : mode === "api-key"
                          ? hasKey
                            ? "Strong identity active"
                            : "No API key — this deployment needs one"
                          : anonymous
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
              // no shortcut here: a title is a plain string, so it cannot
              // carry the per-keyboard spelling the button itself renders,
              // and a hard-coded ⌘K names the wrong key on most keyboards
              title="Quick capture"
              className="flex shrink-0 items-center gap-1 rounded border border-line-strong bg-raised px-2 py-1.5 font-mono text-[11px] text-ink-2 hover:bg-line hover:text-ink md:px-1.5 md:py-0.5"
            >
              {/* The visible text must NAME the action, not the keystroke.
                  This button read "⌘K" on desktop while its accessible name
                  was "Quick capture" — a WCAG 2.5.3 Label in Name failure
                  (nothing on screen matched the name a voice-control user
                  must speak), and next to a search box a bare shortcut reads
                  as the command-palette convention it is not: this writes a
                  row. The shortcut stays as a hint, aria-hidden so it cannot
                  get back into the accessible name and re-break the match. */}
              <span>+ Capture</span>
              <span aria-hidden className="hidden text-ink-3 md:inline">
                <Shortcut />
              </span>
            </button>
          </div>
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
                  badge={l.href === "/review" ? attention.inbox : undefined}
                />
              ))}
            </div>
          ))}
          {navigation.length > 0 && (
            <div className="flex items-center gap-3">
              <span aria-hidden className="h-4 w-px bg-line" />
              {navigation.map((item) => (
                <NavLink
                  key={item.id}
                  href={item.href}
                  label={item.label}
                  active={item.activePaths.includes(pathname)}
                />
              ))}
            </div>
          )}
        </nav>
      </div>
      <div className="selvage" id="selvage" aria-hidden />
    </header>
  );
}

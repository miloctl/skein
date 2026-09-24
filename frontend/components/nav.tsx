"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { NAVIGATION, ROUTE_TITLES, activeNavigation } from "@/lib/navigation";

import { SkeinMark } from "@/components/mark";
import { NavSearch } from "@/components/nav-search";
import { PageHelp } from "@/components/page-help";
// identity/key changes notify via the storage event (cross-tab natively,
// same-tab dispatched by the lib/api writers)
import { actionError, api, getUser, subscribeUser } from "@/lib/api";
import { bridgeAttentionChange } from "@/lib/attention";
import { reportStatus } from "@/lib/status";
import { authConfig, isSignedIn, sessionLocked, sessionSnapshot, signIn, signOut, subscribeSession } from "@/lib/auth";
import { isGated, subscribeGated } from "@/lib/gated";
import { useFrontendExtensions } from "@/lib/extensions/context";

const DESKTOP = "(min-width: 1024px)";
const COLLAPSE_KEY = "skein-navigation-collapsed";
function subscribeDesktop(changed: () => void) {
  const media = window.matchMedia?.(DESKTOP);
  media?.addEventListener("change", changed);
  return () => media?.removeEventListener("change", changed);
}
function subscribePreference(changed: () => void) {
  window.addEventListener("storage", changed);
  return () => window.removeEventListener("storage", changed);
}
function readCollapsed() {
  try { return localStorage.getItem(COLLAPSE_KEY) === "true"; } catch { return false; }
}

function NavigationDestination({ href, onClick, ...props }: Omit<React.ComponentProps<typeof Link>, "href"> & { href: string }) {
  return <Link href={href} {...props} onClick={(event) => {
    onClick?.(event);
    if (event.defaultPrevented) return;
    const [target, fragment] = href.split("#");
    // Next appends a link's fragment to its cached canonical URL, which can
    // already carry one: /settings#settings-team became
    // #settings-team#settings-you. A same-document fragment jump replaces it
    // and fires hashchange for the page's own listeners. Any other href stays
    // a soft navigation, so a contributed link whose query differs from the
    // address bar never reloads the document.
    if (fragment !== undefined && target === window.location.pathname + window.location.search) {
      event.preventDefault();
      window.location.hash = fragment;
    }
  }} />;
}

function NavigationIcon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    day: "M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    chat: "M4 4h16v12H9l-5 4V4Z",
    work: "M4 7h16v13H4V7Zm4 0V4h8v3M4 12h16",
    inbox: "m4 4-2 11v5h20v-5L20 4H4Zm-2 11h6l2 3h4l2-3h6",
    team: "M15 7a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM5 21v-3a7 7 0 0 1 14 0v3M3 7a3 3 0 0 0 0 6m18-6a3 3 0 0 1 0 6",
    settings: "M4 6h16M4 12h16M4 18h16M8 3v6m8 0v6m-6 0v6",
    collapse: "m14 6-6 6 6 6M19 4v16",
    expand: "m10 6 6 6-6 6M5 4v16",
    menu: "M4 6h16M4 12h16M4 18h16",
    close: "m6 6 12 12M6 18 18 6",
    guide: "M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15",
  };
  return <svg aria-hidden width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
    <path d={paths[name] ?? "M4 4h16v16H4V4Zm4 4h8v8H8V8Z"} />
  </svg>;
}

export function Nav({ children }: { children?: React.ReactNode }) {
  const { navigation } = useFrontendExtensions();
  const pathname = usePathname();
  const user = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  // two independent numbers — see the poll below for why they cannot be one
  const [attention, setAttention] = useState({ inbox: 0, yours: 0, chats: 0 });
  const [menuOpen, setMenuOpen] = useState(false);
  // fetched lazily whenever the menu opens. Keeping the first answer forever
  // showed another identity's progress after a same-tab name change.
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
  const strongSession = useSyncExternalStore(
    subscribeUser,
    () => sessionSnapshot().authenticated && sessionSnapshot().strong,
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
  const locked = useSyncExternalStore(subscribeSession, sessionLocked, () => false);

  useEffect(() => {
    // nothing to count while the session is locked: this can only 401, and the
    // number it would carry describes a workspace the reader cannot open. The
    // `locked` dependency also makes signing in re-poll at once, so the badge
    // is current the moment the workspace comes back. `locked`, not `gated`:
    // the gate publishes `gated` from its effect, which runs AFTER this one.
    if (locked) return;
    let generation = 0;
    const poll = () => {
      const g = ++generation;
      // two numbers, two readers: `inbox` is what the Inbox badge promises
      // (proposals + triage, the rows that page actually shows) and `yours` is
      // what is addressed to this person by name. The tab title carries
      // `yours` — it is the only part of Skein visible from an editor, and it
      // said "3" about a queue nobody had assigned to the reader.
      api<{ inbox: number; yours: number; chats?: number }>("/api/attention")
        .then((r) => {
          if (g === generation)
            setAttention({ inbox: r.inbox, yours: r.yours, chats: r.chats ?? 0 }); // ignore stale responses
        })
        .catch(() => {});
    };
    poll();
    // a backgrounded phone tab shouldn't wake for this, and coming back
    // should refresh immediately rather than show a stale badge for 30s
    const tick = () => document.visibilityState === "visible" && poll();
    const t = setInterval(tick, 30_000);
    document.addEventListener("visibilitychange", tick);
    window.addEventListener("skein-attention-change", poll);
    // read cursors and invitations move the Chat badge; the shared-chat
    // surfaces announce those with this event, not skein-attention-change
    window.addEventListener("skein-shared-chat-activity", poll);
    // nav mounts on every page, so this one bridge relays another tab's
    // change into this tab's window event for every listener at once
    const unbridge = bridgeAttentionChange();
    return () => {
      generation++; // invalidate in-flight responses
      clearInterval(t);
      document.removeEventListener("visibilitychange", tick);
      window.removeEventListener("skein-attention-change", poll);
      window.removeEventListener("skein-shared-chat-activity", poll);
      unbridge();
    };
  }, [locked]);

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
    const extension = navigation.find(
      (item) => item.href.split(/[?#]/)[0] === pathname,
    );
    const routeLabel =
      ROUTE_TITLES[pathname] ??
      extension?.label ??
      (pathname.startsWith("/engagement/") ? "Engagement" : "Skein");
    const mapped = routeLabel === "Skein" ? "Skein" : `${routeLabel} — Skein`;
    const known = new Set([
      "Skein",
      ...Object.values(ROUTE_TITLES).map((label) => `${label} — Skein`),
    ]);
    const clean = () => el.textContent?.replace(/^\(\d+\)\s+/, "") || "Skein";
    let chatTitle =
      pathname === "/chat" && !known.has(clean()) ? clean() : "";
    const base = () => (pathname === "/chat" && chatTitle ? chatTitle : mapped);
    const apply = () => {
      // ThreadTitle writes a conversation name after the route title. Preserve
      // that one dynamic title; every known static title belongs to a route
      // transition and must not leak into the new tab.
      const current = clean();
      if (pathname === "/chat" && current !== mapped && !known.has(current))
        chatTitle = current;
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
  }, [attention.yours, gated, navigation, pathname]);

  const anonymous = user === "anonymous";
  const desktop = useSyncExternalStore(subscribeDesktop, () => window.matchMedia?.(DESKTOP).matches ?? true, () => true);
  const savedCollapsed = useSyncExternalStore(subscribePreference, readCollapsed, () => false);
  const [collapseOverride, setCollapseOverride] = useState<boolean | null>(null);
  const collapsed = desktop && (collapseOverride ?? savedCollapsed);
  const [drawerPath, setDrawerPath] = useState<string | null>(null);
  const drawerOpen = drawerPath === pathname && !desktop && !gated;
  const headerRef = useRef<HTMLElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const drawerRef = useRef<HTMLDialogElement>(null);
  const drawerButton = useRef<HTMLButtonElement>(null);
  const activeGroup = activeNavigation(pathname);

  useEffect(() => {
    // An effect, never a rendered inert={gated} prop: React skips re-applying
    // an attribute it believes is already set, so one removal by any other
    // owner would be permanent (task-peek.tsx strips inert on cleanup).
    for (const el of [headerRef.current, sidebarRef.current]) {
      if (gated) el?.setAttribute("inert", "");
      else el?.removeAttribute("inert");
    }
  }, [gated, desktop]);

  useEffect(() => {
    const dialog = drawerRef.current;
    if (!dialog || !drawerOpen) return;
    dialog.showModal();
    dialog.querySelector<HTMLButtonElement>("button")?.focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      dialog.close();
      document.body.style.overflow = overflow;
    };
  }, [drawerOpen]);

  useEffect(() => {
    const dismiss = () => {
      // Close the native top layer before another overlay takes focus.
      drawerRef.current?.close();
      setDrawerPath(null);
      setMenuOpen(false);
    };
    // TaskPeek listens to these history/peek events too. Close the native
    // top layer synchronously, before its effect focuses the incoming panel.
    const events = ["skein-search-focus", "skein-capture-open", "skein-peek", "popstate", "hashchange"];
    events.forEach((event) => window.addEventListener(event, dismiss));
    return () => events.forEach((event) => window.removeEventListener(event, dismiss));
  }, []);

  const closeDrawer = () => {
    drawerRef.current?.close();
    setDrawerPath(null);
    setMenuOpen(false);
  };
  const link = (href: string, label: string, icon?: string, current?: "page" | "location", extension = false, badge = 0, key = href) => {
    return <NavigationDestination key={key} href={href} aria-current={current}
      className={"shell-row " + (icon ? "" : "shell-child ") + (current ? "shell-current" : "")}
      onClick={(event) => {
        if (!extension && pathname === href) event.preventDefault();
        closeDrawer();
        setMenuOpen(false);
      }}>
      {icon && <NavigationIcon name={icon} />}
      <span className={icon ? "shell-label" : ""}>{label}</span>
      {badge > 0 && <>
        <span aria-hidden className={"shell-badge ml-auto rounded-full px-1.5 font-mono text-[10px] " + (label === "Inbox" ? "bg-danger/10 text-danger" : "bg-thread/10 text-thread")}>{badge}</span>
        <span className="sr-only">, {badge} {label === "Chat" ? "waiting in private shared chats" : "awaiting a verdict"}</span>
      </>}
    </NavigationDestination>;
  };
  const contents = <>
    <div className="flex h-[var(--nav-h)] shrink-0 items-center gap-2 px-3">
      <Link href="/" aria-label="Skein — My Day" className="shell-brand flex min-w-0 items-center gap-2 font-display text-base font-semibold" onClick={closeDrawer}>
        <SkeinMark size={22} className="shrink-0 text-thread" /><span className="shell-wordmark">Skein</span>
      </Link>
      {!desktop && <button type="button" aria-label="Close navigation" className="ml-auto flex min-h-11 min-w-11 items-center justify-center rounded hover:bg-raised" onClick={closeDrawer}><NavigationIcon name="close" /></button>}
    </div>
    <nav aria-label="Primary" className="shell-links space-y-1 p-2">
      {NAVIGATION.map((group) => <div key={group.href}>
        {link(group.href, group.label, group.icon,
          activeGroup === group ? group.children.length ? "location" : "page" : undefined,
          false, group.label === "Inbox" ? attention.inbox : group.label === "Chat" ? attention.chats : 0)}
        {!collapsed && activeGroup === group && group.children.length > 0 && <div className="my-1 ml-4 border-l border-line pl-2">
          {group.children.map((child) => link(child.href, child.label, undefined, pathname === child.href ? "page" : undefined))}
        </div>}
      </div>)}
      {navigation.length > 0 && <div className="mt-3 space-y-1 border-t border-line pt-3">
        {navigation.map((item) => link(item.href, item.label, "extension", item.activePaths.includes(pathname) ? "page" : undefined, true, 0, item.id))}
      </div>}
    </nav>
    <div className="mt-auto space-y-1 border-t border-line p-2">
      {link("/settings", "Settings", "settings", pathname === "/settings" ? "page" : undefined)}
      {link("/guide", "Field guide", "guide", pathname === "/guide" ? "page" : undefined)}
            <div className="relative min-w-0">
              <button
                ref={idBtnRef}
                data-identity-control
                onClick={() => {
                  const opening = !menuOpen;
                  setMenuOpen(opening);
                  if (opening && !anonymous)
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
                className="shell-identity shell-row relative w-full min-w-0 text-left"
              >
                <span
                  aria-hidden
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
                  <span className="shell-label text-ink-3">anonymous</span>
                ) : (
                  <span className="shell-label min-w-0 truncate">
                    {user}
                  </span>
                )}
                {strongSession && (
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
                      e.preventDefault();
                      e.stopPropagation();
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
                  className="shell-account-menu absolute bottom-full left-0 z-20 mb-2 w-56 max-w-[calc(100vw-2rem)] rounded-xl border border-line bg-card p-1 shadow-float"
                >
                  <NavigationDestination
                    href={anonymous && mode === "trusted-header" ? "/settings" : "/settings#settings-you"}
                    role="menuitem"
                    aria-describedby="account-access-summary"
                    onClick={closeDrawer}
                    className="block w-full rounded px-2.5 py-2 text-left text-[13px] text-ink-2 hover:bg-raised focus:bg-raised md:py-1.5"
                  >
                    <span aria-hidden>⚙ </span>
                    {/* only trusted-header mode lets a person type who they are.
                      Offering it elsewhere invites a name the server ignores. */}
                    {anonymous && mode === "trusted-header"
                      ? "Pick your name…"
                      : "Settings & access"}
                  </NavigationDestination>
                  {(mode === "oidc" || signedIn) && (
                    <button
                      role="menuitem"
                      onClick={async () => {
                        setMenuOpen(false);
                        if (signedIn) {
                          try {
                            // signOut already left for the provider's
                            // sign-out page, which returns here. A full
                            // load, never router.push: it drops every
                            // in-memory copy of the signed-out person's data
                            // before the next person uses this browser.
                            // eslint-disable-next-line @next/next/no-location-assign-relative-destination
                            if (!(await signOut())) window.location.assign("/");
                          } catch (error) {
                            reportStatus(actionError(error));
                          }
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
                    onClick={closeDrawer}
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
                  <p
                    id="account-access-summary"
                    className="mt-1 border-t border-line px-2.5 pb-1 pt-1.5 text-[11px] text-ink-3"
                  >
                    {!mode ? "Checking access…" : signedIn
                      ? "Signed in with a browser session"
                      : mode === "oidc" || mode === "api-key"
                        ? "Signed out — sign in to open the workspace"
                        : anonymous ? "No name picked — writes will not be yours"
                          : "Name-only access — team-visible work is available"}
                  </p>
                </div>
              )}
            </div>

      {desktop && <button type="button" className="shell-row w-full" aria-label={collapsed ? "Expand navigation" : "Collapse navigation"}
        onClick={() => {
          const next = !collapsed;
          setCollapseOverride(next);
          try { localStorage.setItem(COLLAPSE_KEY, String(next)); } catch {}
        }}>
        <NavigationIcon name={collapsed ? "expand" : "collapse"} />
        <span className="shell-label">{collapsed ? "Expand navigation" : "Collapse navigation"}</span>
      </button>}
    </div>
  </>;

  return <div className="app-shell" data-collapsed={collapsed ? "true" : "false"}>
    {desktop && <aside ref={sidebarRef} aria-label="Workspace navigation" className="shell-sidebar">{contents}</aside>}
    <dialog ref={drawerRef} id="navigation-drawer" aria-label="Navigation" className="shell-drawer"
      onCancel={(event) => { event.preventDefault(); closeDrawer(); }}
      onClose={() => setDrawerPath(null)}
      onClick={(event) => { if (event.target === event.currentTarget) closeDrawer(); }}>
      {!desktop && <div className="flex min-h-full flex-col">{contents}</div>}
    </dialog>
    <div className="shell-content">
      <header ref={headerRef} className="sticky top-0 z-10 bg-page/95 backdrop-blur">
        <div className="flex h-[var(--nav-h)] min-w-0 items-center gap-2 px-4 sm:gap-3 sm:px-6">
          {!desktop && <button ref={drawerButton} type="button" aria-label="Open navigation" aria-haspopup="dialog" aria-expanded={drawerOpen} aria-controls="navigation-drawer"
            className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg border border-line text-ink-2 hover:bg-raised"
            onClick={(event) => {
              event.currentTarget.focus();
              window.dispatchEvent(new Event("skein-navigation-open"));
              setDrawerPath(pathname);
            }}><NavigationIcon name="menu" /></button>}
          {!anonymous && <NavSearch />}
          <div className="ml-auto flex shrink-0 items-center gap-2 sm:gap-3">
            <PageHelp key={pathname} />
            <button title="Quick capture" className="min-h-11 shrink-0 rounded-lg border border-line-strong bg-raised px-2 text-xs text-ink-2 hover:bg-line hover:text-ink"
              onClick={(event) => {
                event.currentTarget.focus();
                window.dispatchEvent(new Event("skein-capture-open"));
              }}>+ Capture</button>
          </div>
        </div>
        <div className="selvage" id="selvage" aria-hidden />
      </header>
      {children}
    </div>
  </div>;
}

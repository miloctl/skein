/** Chat sidebar visibility, shared between the page's header toggle and the
 *  sidebar itself. Same manually-dispatched-storage-event pattern the rest of
 *  the app uses (lib/api.ts, lib/theme.ts) so cross-tab changes work too.
 *  The key and its remove-on-expand semantics are unchanged, so preferences
 *  saved by the old in-sidebar toggle stay valid. */
const KEY = "skein-chat-sidebar-collapsed";

export function subscribeChatLayout(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

export function getSidebarCollapsed(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

/** Server render can't read localStorage; the pre-paint script in layout.tsx
 *  stamps the same preference onto <html> so there's no visible snap. */
export function serverSidebarCollapsed(): boolean {
  return false;
}

export function toggleSidebar() {
  try {
    if (getSidebarCollapsed()) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, "1");
    document.documentElement.dataset.chatSidebar = getSidebarCollapsed()
      ? "collapsed"
      : "";
  } catch {
    /* private mode: in-memory for this session is fine */
  }
  window.dispatchEvent(new Event("storage"));
}

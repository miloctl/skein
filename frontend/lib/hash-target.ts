/** Land on the ROW a `#…` deep link named.
 *
 *  The browser honours a fragment only for an element that exists at
 *  navigation time, and every row on these pages arrives from a fetch — so
 *  "question #12 is still open" on My Day dropped the reader at the top of a
 *  thirteen-section page to find #12 by eye. Pass the fetched rows as `ready`:
 *  the effect runs again when they land, and by then the element exists.
 *
 *  focus(), not scrollIntoView(): a screen reader announces what it lands on,
 *  so the reader hears the row rather than arriving mid-list in silence. The
 *  target MUST carry tabIndex={-1} — focus() on an element with no tabindex
 *  silently does nothing and the reader is left at the top of the page, which
 *  is the exact failure this hook exists to end.
 *
 *  app/charter/page.tsx does the same work by hand and stays that way: a
 *  decision link can name a row outside the category slice on screen, so that
 *  page widens the slice first and there is no element to focus until it has.
 */
import { useEffect, useRef } from "react";

export function useHashTarget(ready: unknown) {
  // the row focus was last moved to. Without it, every background refresh
  // (answering a question refetches the collection) re-ran the effect and
  // pulled focus back to the deep-linked row out from under the reader.
  const landed = useRef("");

  useEffect(() => {
    const land = (id: string, force: boolean) => {
      if (!id || (!force && landed.current === id)) return;
      const el = document.getElementById(id);
      // recorded only on a HIT: the first run happens before the fetch
      // settles, and marking a miss as landed would spend the one attempt on
      // an empty page and never retry when the rows arrive
      if (!el) return;
      landed.current = id;
      el.focus();
    };
    land(window.location.hash.slice(1), false);
    // Both events, because neither covers the other: `hashchange` is the
    // browser's own (a typed address, Back between two fragments) and
    // `skein-hash` is what an in-app link announces — a next/link soft
    // navigation fires neither of the browser's (components/nav-search.tsx).
    const onHash = (ev: Event) => {
      // the anchor from the event when an in-app link sent one, the address
      // bar otherwise. Next updates the URL inside a transition that finishes
      // after the click handler, so reading location there is a frame early
      // and lands on the PREVIOUS fragment.
      const sent = (ev as CustomEvent<{ anchor?: string }>).detail?.anchor;
      land(sent ?? window.location.hash.slice(1), true);
    };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("skein-hash", onHash);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("skein-hash", onHash);
    };
  }, [ready]);
}

/** The classes a hook target needs to be focusable and to show where focus
 *  went. `focus:`, not `focus-visible:` — Chrome does not match
 *  :focus-visible on an element focused PROGRAMMATICALLY after a mouse click,
 *  so `outline-none` won and focus teleported to a row with nothing drawn on
 *  it. A tabIndex={-1} row is only ever focused deliberately, so the wider
 *  selector costs no stray outlines. */
export const HASH_TARGET =
  "outline-none focus:rounded focus:ring-2 focus:ring-thread-solid";

"use client";

import { useEffect } from "react";

import { reportStatus } from "@/lib/status";
import { adoptServerTheme, applyPrefs, syncThemeColor } from "@/lib/theme";
import { markHydrated } from "@/lib/whimsy";

// A theme change in another tab fires a real storage event here — repaint
// this tab too, not just the Settings buttons. On mount, a browser with no
// local prefs adopts the theme saved on the user's profile; when that
// happens the restyle gets one transient line of explanation so it reads as
// "your theme arrived", not a rendering glitch.
export function ThemeSync() {
  useEffect(() => {
    let alive = true;
    // the pre-paint script already set the data attributes; only the
    // address-bar colour still needs deriving from them
    syncThemeColor();
    // hydration has committed by the time an effect runs — only now may the
    // pack-aware empty-state voice differ from what the server rendered
    markHydrated();
    adoptServerTheme().then((adopted) => {
      // StatusRegion renders and expires this: lib/status.ts owns the pill,
      // the role, and the 6s lifetime. A second renderer here would show
      // the note twice.
      if (alive && adopted === "profile") {
        reportStatus("Applied the theme saved on your profile.", "confirmation");
      }
    });
    window.addEventListener("storage", applyPrefs);
    return () => {
      alive = false;
      window.removeEventListener("storage", applyPrefs);
    };
  }, []);
  return null;
}

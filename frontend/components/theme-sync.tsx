"use client";

import { useEffect, useState } from "react";

import { adoptServerTheme, applyPrefs, syncThemeColor } from "@/lib/theme";
import { markHydrated } from "@/lib/whimsy";

// A theme change in another tab fires a real storage event here — repaint
// this tab too, not just the Settings buttons. On mount, a browser with no
// local prefs adopts the theme saved on the user's profile; when that
// happens the restyle gets one transient line of explanation so it reads as
// "your theme arrived", not a rendering glitch.
export function ThemeSync() {
  const [note, setNote] = useState("");
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // the pre-paint script already set the data attributes; only the
    // address-bar colour still needs deriving from them
    syncThemeColor();
    // hydration has committed by the time an effect runs — only now may the
    // pack-aware empty-state voice differ from what the server rendered
    markHydrated();
    adoptServerTheme().then((adopted) => {
      if (!alive || adopted !== "profile") return;
      setNote("Applied the theme saved on your profile.");
      timer = setTimeout(() => {
        if (alive) setNote("");
      }, 6000);
    });
    window.addEventListener("storage", applyPrefs);
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      window.removeEventListener("storage", applyPrefs);
    };
  }, []);
  if (!note) return null;
  return (
    <p
      role="status"
      className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 rounded-full border border-line bg-card px-4 py-1.5 text-xs text-ink-2 shadow-float"
    >
      {note}
    </p>
  );
}

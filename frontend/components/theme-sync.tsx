"use client";

import { useEffect } from "react";

import { adoptServerTheme, applyPrefs } from "@/lib/theme";

// A theme change in another tab fires a real storage event here — repaint
// this tab too, not just the Settings buttons. On mount, a browser with no
// local prefs adopts the theme saved on the user's profile.
export function ThemeSync() {
  useEffect(() => {
    adoptServerTheme();
    window.addEventListener("storage", applyPrefs);
    return () => window.removeEventListener("storage", applyPrefs);
  }, []);
  return null;
}

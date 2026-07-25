"use client";

import { useEffect } from "react";

import { applyPrefs } from "@/lib/theme";

// A theme change in another tab fires a real storage event here — repaint
// this tab too, not just the Settings buttons.
export function ThemeSync() {
  useEffect(() => {
    window.addEventListener("storage", applyPrefs);
    return () => window.removeEventListener("storage", applyPrefs);
  }, []);
  return null;
}

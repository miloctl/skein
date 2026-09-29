"use client";

import { useEffect, useRef, type ReactNode } from "react";

/** Floating disclosure panel: focuses its first control, closes on Escape.
 *  Deliberately NOT role="menu" — plain buttons with Tab-through. */
export function MenuPanel({
  label,
  onClose,
  children,
}: {
  label: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("button, input")?.focus();
  }, []);
  return (
    <div
      ref={ref}
      data-menu
      role="group"
      aria-label={label}
      onBlur={(e) => {
        // Tab-out closes: pointerdown/Escape alone left it floating
        if (!e.currentTarget.contains(e.relatedTarget as Node)) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
      }}
      className="absolute left-0 right-0 top-full z-10 mt-1 rounded-xl border border-line bg-card p-1 shadow-float"
    >
      {children}
    </div>
  );
}

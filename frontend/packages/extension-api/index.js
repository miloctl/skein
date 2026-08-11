"use client";

import { createElement, useId } from "react";

export const FRONTEND_EXTENSION_API = "1.0";

export function Card({
  title,
  className = "",
  titleClassName = "",
  children,
}) {
  const headingId = useId();
  return createElement(
    "section",
    {
      "aria-labelledby": title ? headingId : undefined,
      className: `rounded-xl border border-line bg-card p-4 shadow-card ${className}`,
    },
    title
      ? createElement(
          "h2",
          {
            id: headingId,
            className: `mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3 ${titleClassName}`,
          },
          title,
        )
      : null,
    children,
  );
}

export function EmptyState({ children }) {
  return createElement(
    "p",
    {
      className:
        "rounded-xl border border-dashed border-line-strong p-8 text-center text-sm text-ink-3",
    },
    children,
  );
}

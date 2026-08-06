"use client";

import { useId } from "react";

/** The one card. Every surface that groups content uses this — six pages had
 *  byte-identical private copies, which is how a design system quietly drifts.
 *  `className` carries the two legitimate modifiers: md:col-span-2 and loom-band.
 *
 *  A titled card is a NAMED region. `<section>` maps to `region` only with an
 *  accessible name, so without this the whole app exposed zero landmarks and a
 *  screen-reader user navigating by region found nothing at all. */
export function Card({
  title,
  className = "",
  titleClassName = "",
  children,
}: {
  title?: string;
  className?: string;
  titleClassName?: string;
  children: React.ReactNode;
}) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={`rounded-xl border border-line bg-card p-4 shadow-card ${className}`}
    >
      {title && (
        <h2
          id={headingId}
          className={`mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3 ${titleClassName}`}
        >
          {title}
        </h2>
      )}
      {children}
    </section>
  );
}

/** Dashed placeholder for "nothing here yet" — one padding, one voice. */
export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-xl border border-dashed border-line-strong p-8 text-center text-sm text-ink-3">
      {children}
    </p>
  );
}

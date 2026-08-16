"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ShortcutText } from "@/components/shortcut";
import { actionError, api } from "@/lib/api";

type Card = {
  id: string;
  feature: string;
  knot: string;
  pitch: string;
  how: string;
  link: string;
};

export function PageHelp() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [cards, setCards] = useState<Card[] | null>(null);
  const [live, setLive] = useState(false);
  const [error, setError] = useState("");
  const buttonRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [open]);

  if (pathname === "/chat" || pathname === "/guide") return null;

  const load = () => {
    setOpen(true);
    setError("");
    if (cards !== null) return;
    api<{ cards: Card[] }>(
      `/api/field-guide/for?path=${encodeURIComponent(pathname)}`,
    )
      .then((response) => setCards(response.cards))
      .catch((reason) => setError(actionError(reason)));
    api<{ provider: string; provider_error: string }>("/api/agents/status")
      .then((status) =>
        setLive(status.provider !== "mock" && !status.provider_error),
      )
      .catch(() => {});
  };

  const dismiss = (restoreFocus = false) => {
    setOpen(false);
    if (restoreFocus) buttonRef.current?.focus();
  };
  const compose = `/as bosun I am on the ${pathname} page. `;

  return (
    <div
      ref={rootRef}
      className="relative shrink-0"
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node)) dismiss();
      }}
      onKeyDown={(event) => {
        if (open && event.key === "Escape") {
          event.preventDefault();
          dismiss(true);
        }
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-label="Help for this page"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="page-help"
        title="Help for this page"
        onClick={() => (open ? dismiss() : load())}
        className="flex size-8 items-center justify-center rounded-full border border-dashed border-line-strong font-mono text-xs text-ink-2 hover:bg-raised hover:text-ink"
      >
        ?
      </button>
      {open && (
        <section
          id="page-help"
          role="dialog"
          aria-labelledby="page-help-title"
          className="fixed inset-x-4 top-[calc(var(--nav-h)+var(--selvage-h,2px)+0.5rem)] z-30 max-h-[calc(100dvh-var(--nav-h)-var(--selvage-h,2px)-1rem)] overflow-y-auto overscroll-contain rounded-xl border border-line bg-card p-3 shadow-float md:absolute md:left-auto md:right-0 md:top-full md:mt-2 md:w-[22rem]"
        >
          <div className="mb-2 flex items-center gap-3 border-b border-line pb-2">
            <h2
              id="page-help-title"
              className="font-display text-sm font-semibold text-ink"
            >
              Help for this page
            </h2>
            <span className="ml-auto font-mono text-[10px] text-ink-3">
              {pathname}
            </span>
            <button
              ref={closeRef}
              type="button"
              aria-label="Close page help"
              onClick={() => dismiss(true)}
              className="rounded px-1.5 py-1 text-xs text-ink-2 hover:bg-raised hover:text-ink"
            >
              Close
            </button>
          </div>

          {cards === null ? (
            <p role={error ? "alert" : "status"} className={error ? "text-xs text-danger" : "text-xs text-ink-3"}>
              {error || "Loading page help…"}
            </p>
          ) : null}
          {cards?.length === 0 ? (
            <p className="text-xs text-ink-3">No field-guide cards match this page.</p>
          ) : null}
          {cards && cards.length > 0 ? (
            <ul className="space-y-3">
              {cards.map((card) => (
                <li key={card.id} className="border-b border-line pb-3 last:border-0 last:pb-0">
                  <div className="flex items-baseline justify-between gap-2">
                    <h3 className="text-xs font-semibold text-ink">{card.feature}</h3>
                    <span className="shrink-0 font-mono text-[9px] uppercase tracking-wide text-ink-3">
                      {card.knot}
                    </span>
                  </div>
                  <p className="mt-1 text-xs text-ink">
                    <ShortcutText text={card.pitch} />
                  </p>
                  <p className="mt-1 text-xs text-ink-2">
                    <ShortcutText text={card.how} />
                  </p>
                  <Link
                    href={card.link}
                    onClick={() => dismiss()}
                    className="mt-1.5 inline-block text-xs font-medium text-thread underline hover:opacity-80"
                  >
                    Open {card.feature}
                  </Link>
                </li>
              ))}
            </ul>
          ) : null}

          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2">
            <Link
              href="/guide"
              onClick={() => dismiss()}
              className="text-xs text-ink-2 underline hover:text-ink"
            >
              Open the field guide
            </Link>
            {live ? (
              <Link
                href={{ pathname: "/chat", query: { compose } }}
                onClick={() => dismiss()}
                className="text-xs font-medium text-thread underline hover:opacity-80"
              >
                Ask the Bosun about this page
              </Link>
            ) : null}
          </div>
        </section>
      )}
    </div>
  );
}

"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { startFirstWatch } from "@/lib/first-watch";

type Suggestion = { id: string; feature: string; pitch: string; link: string };

/** One quiet line on My Day: a single untried feature, rotating weekly.
 *  Dismissing suppresses that suggestion permanently (server-side, follows
 *  the person). Uses the side-effect-free /hint endpoint — the full guide
 *  GET would consume the "newly tied" strip before the page ever shows it. */
export function GuideHint() {
  const [s, setS] = useState<Suggestion | null>(null);
  // a click must beat a slow in-flight fetch — a dismissed suggestion
  // resurrected by a late response would make × look broken
  const dismissed = useRef(false);

  useEffect(() => {
    api<{ suggestion: Suggestion | null }>("/api/field-guide/hint")
      .then((g) => {
        if (!dismissed.current) setS(g.suggestion);
      })
      .catch(() => {});
  }, []);

  if (!s) return null;
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-ink-3">
      <span>
        <span aria-hidden>🧶 </span>
        Something you have not tried yet:{" "}
        <span className="font-medium text-ink-2">{s.feature}</span> — {s.pitch}
      </span>
      {s.id === "first_watch" ? (
        <button type="button" onClick={startFirstWatch} className="underline hover:text-ink-2">
          Start First Watch
        </button>
      ) : (
        <Link href={s.link} className="underline hover:text-ink-2">
          Try it
        </Link>
      )}
      <Link href="/guide" className="underline hover:text-ink-2">
        Field guide
      </Link>
      <button
        onClick={() => {
          const prev = s;
          dismissed.current = true;
          setS(null);
          api("/api/field-guide/dismiss", {
            method: "POST",
            body: JSON.stringify({ knot: prev.id }),
          }).catch(() => {
            // the dismissal didn't stick — showing it again is the truth
            dismissed.current = false;
            setS(prev);
          });
        }}
        aria-label={`Never suggest ${s.feature} again`}
        title="Never suggest this one again"
        className="min-h-6 min-w-6 text-ink-3 hover:text-ink"
      >
        ×
      </button>
    </p>
  );
}

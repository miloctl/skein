"use client";

import Link from "next/link";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { startFirstWatch } from "@/lib/first-watch";
import { ShortcutText } from "@/components/shortcut";
import { api, getUser, loadError, subscribeUser } from "@/lib/api";

type Knot = {
  id: string;
  feature: string;
  knot: string;
  set: "loops" | "hitches" | "bends" | "stoppers" | "manager";
  pitch: string;
  how: string;
  link: string;
  role: string;
  tied: boolean;
  tied_on: string;
};

type Guide = {
  cards: Knot[];
  newly_tied: { id: string; feature: string; knot: string }[];
  tied_count: number;
  total: number;
  known: boolean;
};

const SETS: { key: Knot["set"]; title: string; tagline: string }[] = [
  { key: "loops", title: "Loops", tagline: "the solo basics" },
  { key: "hitches", title: "Hitches", tagline: "attaching work to an agent" },
  { key: "bends", title: "Bends", tagline: "where two systems join" },
  { key: "stoppers", title: "Stoppers", tagline: "the art of finishing" },
  { key: "manager", title: "For managers", tagline: "behind the manager toggle" },
];

export default function GuidePage() {
  const me = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState<string | null>(null);

  // One fetch per actual identity. `me` transitions "anonymous"→<name> at
  // hydration while getUser() (the X-User header) is the same both times —
  // an unguarded [me] effect fetches twice, and the FIRST response consumes
  // "newly tied" server-side while the second (now empty) wins the render.
  // Keying on the header value fetches once, yet still refetches on a real
  // cross-tab identity switch. last-request-wins guards the switch race.
  const lastFetched = useRef<string | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    const who = getUser();
    if (lastFetched.current === who) return;
    lastFetched.current = who;
    const g = ++generation.current;
    api<Guide>("/api/field-guide")
      .then((res) => {
        if (g === generation.current) {
          setGuide(res);
          setError(null); // a recovered refetch must not keep the danger line
        }
      })
      .catch((e) => {
        if (g === generation.current) setError(loadError(e));
      });
  }, [me]);

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Field guide
      </h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Every card is a Skein feature. Tied means you used it — untied
        cards show how. Only you can see your guide.
        {guide && (
          <span className="ml-2 font-mono text-[11px]">
            {guide.tied_count} of {guide.total} tied
          </span>
        )}
      </p>

      {me !== "anonymous" ? (
        <button
          type="button"
          onClick={startFirstWatch}
          className="mb-4 min-h-8 rounded-md border border-line-strong px-3 py-1.5 text-xs font-medium text-thread hover:bg-raised"
        >
          Start or resume First Watch
        </button>
      ) : null}

      {error && <p className="mb-4 text-sm text-danger">{error}</p>}

      {me === "anonymous" && (
        <p className="mb-4 rounded-xl border border-weld/40 bg-weld/10 p-4 text-sm text-weld">
          The guide is per-person — pick your name in{" "}
          <Link href="/settings" className="font-medium underline">
            Settings
          </Link>{" "}
          and your history ties its own knots.
        </p>
      )}

      {guide && !guide.known && me !== "anonymous" && (
        <p className="mb-4 rounded-xl border border-line bg-raised p-4 text-sm text-ink-2">
          Skein has not seen you write anything yet — the guide starts
          tying itself after your first capture. Untied is where everyone
          starts.
        </p>
      )}

      {guide && guide.newly_tied.length > 0 && (
        <p className="mb-4 rounded-xl border border-ok/30 bg-ok/10 p-3 text-sm text-ok">
          Newly tied since your last visit:{" "}
          {guide.newly_tied.map((n) => n.feature).join(" · ")}
        </p>
      )}

      {guide &&
        SETS.map((s) => {
          const cards = guide.cards.filter((c) => c.set === s.key);
          if (cards.length === 0) return null;
          return (
            <section key={s.key} className="mb-8">
              <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
                {s.title} <span className="normal-case">— {s.tagline}</span>
              </h2>
              <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                {cards.map((c) => (
                  <li
                    key={c.id}
                    className={`rounded-xl border p-4 shadow-card ${
                      c.tied ? "border-line bg-raised" : "border-line-strong bg-card"
                    }`}
                  >
                    <div className="mb-1 flex items-baseline justify-between gap-2">
                      <span className="text-sm font-semibold text-ink">
                        {c.tied ? "✓ " : ""}
                        {c.feature}
                      </span>
                      <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide text-ink-2">
                        {c.knot}
                      </span>
                    </div>
                    <p className="mb-2 text-xs text-ink">
                      <ShortcutText text={c.pitch} />
                    </p>
                    {/* how: stays after the card ties. It is the only place
                        some grammars are written down — the search card
                        teaches `#42` — and hiding it on first use deletes the
                        documentation exactly when someone starts using it.
                        ink-2, not ink-3: a tied card sits on surface-raised,
                        where 12px ink-3 measures 4.48:1 and misses AA. */}
                    {/* knots.yaml writes the ⌘K token, and `how:` is where
                        several grammars are documented at all — the key it
                        names must be the one on the reader's keyboard */}
                    <p className="mb-2 text-xs text-ink-2">
                      <ShortcutText text={c.how} />
                    </p>
                    {c.id === "first_watch" ? (
                      <button
                        type="button"
                        onClick={startFirstWatch}
                        className="text-xs font-medium text-thread underline hover:opacity-80"
                      >
                        {c.tied ? "Replay First Watch" : "Start First Watch"}
                      </button>
                    ) : c.tied ? (
                      <p className="text-xs text-ink-2">Tied · {c.tied_on}</p>
                    ) : (
                      <Link
                        href={c.link}
                        aria-label={`Try it: ${c.feature}`}
                        className="text-xs font-medium text-thread underline hover:opacity-80"
                      >
                        Try it <span aria-hidden>→</span>
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
    </main>
  );
}

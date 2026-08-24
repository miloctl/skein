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
  suggestion: { id: string; feature: string; pitch: string; link: string } | null;
  tied_count: number;
  total: number;
  known: boolean;
};

const SETS: { key: Knot["set"]; title: string; knot: string }[] = [
  { key: "loops", title: "The solo basics", knot: "Loops" },
  { key: "hitches", title: "Working with agents", knot: "Hitches" },
  { key: "bends", title: "Where systems join", knot: "Bends" },
  { key: "stoppers", title: "Finishing work", knot: "Stoppers" },
  { key: "manager", title: "For managers", knot: "Manager knots" },
];

export default function GuidePage() {
  const me = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState<"all" | "untied" | "tied">("all");
  const [roleFilter, setRoleFilter] = useState<"all" | "teammate" | "manager">("all");
  const [setFilter, setSetFilter] = useState<"all" | Knot["set"]>("all");

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
    setGuide(null);
    setError(null);
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

  const needle = query.trim().toLowerCase();
  const filteredCards =
    guide?.cards.filter((card) => {
      if (stateFilter === "tied" && !card.tied) return false;
      if (stateFilter === "untied" && card.tied) return false;
      if (roleFilter === "manager" && card.role !== "manager") return false;
      if (roleFilter === "teammate" && card.role === "manager") return false;
      if (setFilter !== "all" && card.set !== setFilter) return false;
      return (
        !needle ||
        [card.feature, card.knot, card.pitch, card.how]
          .join(" ")
          .toLowerCase()
          .includes(needle)
      );
    }) ?? [];

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Field guide
      </h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Every card is a Skein feature. Explored means you used it. Not tried
        cards show how. Only you can see your guide.
        {guide && (
          <span className="ml-2 font-mono text-[11px]">
            {guide.tied_count} {guide.tied_count === 1 ? "feature" : "features"} explored
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
      {!guide && !error ? (
        <p role="status" className="mb-4 text-sm text-ink-3">
          Loading your Field Guide…
        </p>
      ) : null}

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
          Newly explored since your last visit:{" "}
          {guide.newly_tied.map((n) => n.feature).join(" · ")}
        </p>
      )}

      {guide?.suggestion ? (
        <section className="mb-4 rounded-xl border border-thread/30 bg-thread/5 p-4">
          <h2 className="font-display text-base font-semibold text-ink">
            Recommended next
          </h2>
          <p className="mt-1 text-sm font-medium text-ink">
            {guide.suggestion.feature}
          </p>
          <p className="text-xs text-ink-2">{guide.suggestion.pitch}</p>
          <Link
            href={guide.suggestion.link}
            className="mt-2 inline-block text-xs font-medium text-thread underline"
          >
            Try {guide.suggestion.feature}
          </Link>
        </section>
      ) : null}

      {guide ? (
        <div className="mb-5 grid gap-2 rounded-xl border border-line bg-card p-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs font-medium text-ink-2 sm:col-span-2 lg:col-span-1">
            Search the Field Guide
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="mt-1 w-full rounded-lg border border-line-strong bg-transparent px-2 py-1.5 text-sm outline-none focus:border-thread-solid"
            />
          </label>
          <label className="text-xs font-medium text-ink-2">
            Feature state
            <select
              value={stateFilter}
              onChange={(event) =>
                setStateFilter(event.target.value as "all" | "untied" | "tied")
              }
              className="mt-1 w-full rounded-lg border border-line-strong bg-card px-2 py-1.5 text-sm"
            >
              <option value="all">All</option>
              <option value="untied">Not tried</option>
              <option value="tied">Explored</option>
            </select>
          </label>
          <label className="text-xs font-medium text-ink-2">
            Audience
            <select
              value={roleFilter}
              onChange={(event) =>
                setRoleFilter(event.target.value as "all" | "teammate" | "manager")
              }
              className="mt-1 w-full rounded-lg border border-line-strong bg-card px-2 py-1.5 text-sm"
            >
              <option value="all">Everyone</option>
              <option value="teammate">Teammates</option>
              <option value="manager">Managers</option>
            </select>
          </label>
          <label className="text-xs font-medium text-ink-2">
            Category
            <select
              value={setFilter}
              onChange={(event) =>
                setSetFilter(event.target.value as "all" | Knot["set"])
              }
              className="mt-1 w-full rounded-lg border border-line-strong bg-card px-2 py-1.5 text-sm"
            >
              <option value="all">All categories</option>
              {SETS.map((set) => (
                <option key={set.key} value={set.key}>
                  {set.title}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}

      {guide ? (
        <p role="status" aria-live="polite" className="mb-4 text-xs text-ink-3">
          {filteredCards.length === 0
            ? "No cards match."
            : `${filteredCards.length} ${filteredCards.length === 1 ? "card" : "cards"} shown.`}
        </p>
      ) : null}

      {guide && filteredCards.length === 0 ? (
        <p className="mb-4 rounded-xl border border-dashed border-line-strong p-6 text-center text-sm text-ink-3">
          No Field Guide card matches these filters.
        </p>
      ) : null}

      {guide &&
        SETS.map((s) => {
          const cards = filteredCards.filter((card) => card.set === s.key);
          if (cards.length === 0) return null;
          return (
            <section key={s.key} className="mb-8">
              <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
                {s.title} <span className="normal-case">— {s.knot}</span>
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
                      <h3 className="text-sm font-semibold text-ink">
                        {c.tied ? "✓ " : ""}
                        {c.feature}
                      </h3>
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
                      <p className="text-xs text-ink-2">Explored · {c.tied_on}</p>
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

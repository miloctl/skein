"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

export type Tier = { visibility: string; crew_id: number };

/** Everyone on the roster, one crew, or nobody but you.
 *
 *  "everyone on the roster", not "everyone": docs/VISIBILITY.md named the tier
 *  `workspace` precisely so no reader takes it for public, and a bare
 *  "everyone" hands that reading straight back.
 *
 *  "only you" is last, and it is the same string the badge renders. It keeps
 *  work out of the digest, the readout, the context pack and search, which
 *  makes it the deliberate choice rather than the safe one. */
export function VisibilityPicker({
  value,
  onChange,
  label,
}: {
  value: Tier;
  onChange: (t: Tier) => void;
  label: string;
}) {
  // null, never []: an empty array would render "you are in no crew" before
  // the request answers, and the picker would vanish and reappear
  const [crews, setCrews] = useState<{ id: number; name: string }[] | null>(
    null,
  );

  useEffect(() => {
    let live = true;
    Promise.all([
      api<{ id: number; name: string }[]>("/api/crews"),
      api<number[]>("/api/crews/mine"),
    ])
      .then(([all, mine]) => {
        if (!live) return;
        // only crews the caller belongs to: the server refuses a write to any
        // other, so listing them offers a choice that always fails
        setCrews(all.filter((c) => mine.includes(c.id)));
      })
      .catch(() => live && setCrews([]));
    return () => {
      live = false;
    };
  }, []);

  // The one thing this control must never do is describe a tier it is not
  // sending. `value` is the parent's, the option list is ours, and nothing
  // reconciled them: with a crew selected, an identity change (or a reopen as
  // somebody in no crew) left the select falling back to "everyone on the
  // roster" while the parent still held `crew_id: 1` and submitted it. Told
  // here, so the parent's state and the label agree again.
  //
  // onChange through a ref: callers pass an inline arrow, so depending on it
  // re-runs this effect every render and the reset fights the parent forever.
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });
  const missing =
    crews !== null &&
    value.visibility === "crew" &&
    !crews.some((c) => c.id === value.crew_id);
  useEffect(() => {
    if (missing) onChangeRef.current({ visibility: "workspace", crew_id: 0 });
  }, [missing]);

  // null is still loading. An empty crew list is NOT a reason to hide the
  // picker any more — "only you" is a choice a person with no crew can make.
  if (crews === null) return null;

  return (
    // min-w-0 and a shrinkable select: mounted in a flex row beside an
    // input, an unshrinkable picker pushed the row 157px sideways at 360px
    <label className="flex min-w-0 items-center gap-1.5 text-xs text-ink-3">
      <span>Visible to</span>
      <select
        aria-label={`Who can see this ${label}`}
        value={
          value.visibility === "crew"
            ? `crew:${value.crew_id}`
            : value.visibility
        }
        onChange={(e) => {
          const v = e.target.value;
          onChange(
            v.startsWith("crew:")
              ? { visibility: "crew", crew_id: Number(v.slice(5)) }
              : { visibility: v, crew_id: 0 },
          );
        }}
        className="min-w-0 flex-1 truncate rounded-lg border border-line-strong bg-transparent px-2 py-1 text-xs outline-none focus:border-thread-solid"
      >
        <option value="workspace">everyone on the roster</option>
        {crews.map((c) => (
          <option key={c.id} value={`crew:${c.id}`}>
            {c.name} only
          </option>
        ))}
        <option value="private">only you</option>
      </select>
    </label>
  );
}

// One request for the whole page, not one per badge: a Browse listing renders
// a badge per row. The promise is the cache, so N badges mounting in the same
// tick share the one in flight.
//
// This is an id-to-name map, NOT an authorization decision: /api/crews is the
// whole deployment's roster of crews (scope.UNSCOPED classifies `crews` that
// way), and what keeps a crew row off the page is the server filtering the ROW
// out of the listing, before any badge exists.
let crewNames: Promise<Record<number, string>> | null = null;

// Dropped on the same signal lib/api.ts drops its GET cache. Both the identity
// picker and the API-key writer dispatch "storage", and without this a crew
// renamed in Settings kept its old name in every badge for the rest of the
// session — the badge would then disagree with the picker that set it.
if (typeof window !== "undefined") {
  window.addEventListener("storage", () => {
    crewNames = null;
  });
}

function useCrewName(crewId?: number): string {
  const [name, setName] = useState("");
  // `tick` re-runs the effect after an invalidation, so a badge that is
  // already mounted picks the new name up too. Without it only a fresh mount
  // ever recovered, which meant a failed /api/crews left every visible badge
  // reading "one crew only" until the page was reloaded.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const bump = () => setTick((n) => n + 1);
    window.addEventListener("storage", bump);
    return () => window.removeEventListener("storage", bump);
  }, []);
  useEffect(() => {
    if (!crewId) return;
    crewNames ??= api<{ id: number; name: string }[]>("/api/crews")
      .then((all) => Object.fromEntries(all.map((c) => [c.id, c.name])))
      .catch(() => {
        // a failed fetch must not poison the cache for the rest of the session
        crewNames = null;
        return {};
      });
    let live = true;
    crewNames.then((m) => live && setName(m[crewId] ?? ""));
    return () => {
      live = false;
    };
  }, [crewId, tick]);
  return name;
}

/** The badge a scoped row carries in a list. Nothing renders for the
 *  workspace tier: a marker on every row is a marker nobody reads.
 *
 *  The crew NAME, not "one crew": the picker that set this said "Platform
 *  only", and a badge that reads "one crew only" beside it looks like a
 *  different setting. `crewName` wins when a caller already has it. */
export function VisibilityBadge({
  visibility,
  crewId,
  crewName,
}: {
  visibility?: string;
  crewId?: number;
  crewName?: string;
}) {
  const resolved = useCrewName(crewName ? undefined : crewId);
  if (visibility !== "crew" && visibility !== "private") return null;
  const own = visibility === "private";
  const label = crewName || resolved;
  return (
    <span
      className={
        "ml-1.5 rounded-full border px-1.5 py-px font-mono text-[10px] " +
        (own
          ? "border-weld/30 bg-weld/10 text-weld"
          : "border-thread/30 bg-thread/10 text-thread")
      }
    >
      {own ? "only you" : label ? `${label} only` : "one crew only"}
    </span>
  );
}

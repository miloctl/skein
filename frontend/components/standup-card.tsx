"use client";

import { useState } from "react";

import { VisibilityPicker } from "@/components/visibility-picker";
import {
  isTierChoice,
  ONLY_YOU,
  ROSTER,
  useRememberedAudience,
  useStrongIdentity,
} from "@/lib/audience";
import { actionError, api } from "@/lib/api";
import { reportStatus } from "@/lib/status";

/** The one daily write, so it lives on My Day. `suggestion` prefills the
 *  "yesterday" field from real activity — derived, not asked for. */
export function StandupComposer({
  suggestion = "",
  onPosted,
}: {
  suggestion?: string;
  onPosted?: () => void;
}) {
  const [yesterday, setYesterday] = useState("");
  const [today, setToday] = useState("");
  const [blockers, setBlockers] = useState("");
  const [posted, setPosted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tier, setTier] = useRememberedAudience(
    "standup",
    useStrongIdentity() ? ONLY_YOU : ROSTER,
    isTierChoice,
  );

  const post = async () => {
    // in-flight guard: a held Enter key must not file N standups (each
    // blockers line would raise its own escalating blocker)
    if (!today.trim() || busy || posted) return;
    setBusy(true);
    try {
      await api("/api/standups", {
        method: "POST",
        body: JSON.stringify({
          yesterday: yesterday || suggestion,
          today,
          blockers,
          ...tier,
        }),
      });
      setPosted(true);
      reportStatus("Standup posted.", "confirmation");
      setTimeout(() => {
        setPosted(false);
        setToday("");
        setBlockers("");
        setYesterday("");
        // `tier` is not reset: lib/audience.ts remembers the last choice, and
        // the picker on this card shows it.
        onPosted?.();
      }, 700);
    } catch (e) {
      reportStatus(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <label className="block text-xs text-ink-3">
        Yesterday (optional)
      <input
        name="standup-yesterday"
        aria-label="Standup: yesterday (optional)"
        value={yesterday}
        maxLength={2000}
        onChange={(e) => setYesterday(e.target.value)}
        placeholder={
          suggestion ? `yesterday — ${suggestion}` : "yesterday (optional)"
        }
        className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
      />
      </label>
      <label className="block text-xs text-ink-3">
        What are you on today?
      <input
        id="standup-today"
        name="standup-today"
        aria-label="Standup: what are you on today?"
        value={today}
        maxLength={2000}
        onChange={(e) => setToday(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && post()}
        placeholder="today — what are you on?"
        className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
      />
      </label>
      <div className="flex flex-wrap items-end gap-1.5">
        <label className="min-w-0 flex-1 basis-40 text-xs text-ink-3">
          Blockers
        <input
          name="standup-blockers"
          aria-label="Standup: blockers"
          value={blockers}
          maxLength={2000}
          onChange={(e) => setBlockers(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && post()}
          placeholder="blockers — auto-filed with an escalation clock"
          className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
        />
        </label>
        <VisibilityPicker value={tier} onChange={setTier} label="standup" />
        <button
          onClick={post}
          disabled={!today.trim() || busy}
          aria-live="polite"
          className="rounded-lg bg-thread-solid px-3 py-1 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
        >
          {posted ? "✓ posted" : busy ? "…" : "Post"}
        </button>
      </div>
    </div>
  );
}

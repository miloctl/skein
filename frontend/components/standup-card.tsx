"use client";

import { useState } from "react";

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

  const post = async () => {
    // in-flight guard: a held Enter key must not file N standups (each
    // blockers line would raise its own escalating blocker)
    if (!today.trim() || busy || posted) return;
    setBusy(true);
    try {
      await api("/api/standups", {
        method: "POST",
        body: JSON.stringify({ yesterday: yesterday || suggestion, today, blockers }),
      });
      setPosted(true);
      setTimeout(() => {
        setPosted(false);
        setToday("");
        setBlockers("");
        setYesterday("");
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
      <input
        name="standup-yesterday"
        aria-label="Standup: yesterday (optional)"
        value={yesterday}
        maxLength={2000}
        onChange={(e) => setYesterday(e.target.value)}
        placeholder={suggestion ? `yesterday — ${suggestion}` : "yesterday (optional)"}
        className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
      />
      <input
        name="standup-today"
        aria-label="Standup: what are you on today?"
        value={today}
        maxLength={2000}
        onChange={(e) => setToday(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && post()}
        placeholder="today — what are you on?"
        className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
      />
      <div className="flex gap-1.5">
        <input
          name="standup-blockers"
          aria-label="Standup: blockers"
          value={blockers}
        maxLength={2000}
          onChange={(e) => setBlockers(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && post()}
          placeholder="blockers — auto-filed with an escalation clock"
          className="flex-1 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
        />
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

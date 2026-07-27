"use client";

import { useState } from "react";

import { api } from "@/lib/api";

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

  const post = async () => {
    if (!today.trim()) return;
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
      alert(String(e));
    }
  };

  return (
    <div className="space-y-1.5">
      <input
        name="standup-yesterday"
        value={yesterday}
        onChange={(e) => setYesterday(e.target.value)}
        placeholder={suggestion ? `yesterday — ${suggestion}` : "yesterday (optional)"}
        className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
      />
      <input
        name="standup-today"
        value={today}
        onChange={(e) => setToday(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && post()}
        placeholder="today — what are you on?"
        className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
      />
      <div className="flex gap-1.5">
        <input
          name="standup-blockers"
          value={blockers}
          onChange={(e) => setBlockers(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && post()}
          placeholder="blockers — auto-filed with an escalation clock"
          className="flex-1 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
        />
        <button
          onClick={post}
          disabled={!today.trim()}
          className="rounded-lg bg-thread-solid px-3 py-1 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
        >
          {posted ? "✓" : "Post"}
        </button>
      </div>
    </div>
  );
}

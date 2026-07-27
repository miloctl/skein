"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { SectionTabs } from "@/components/section-tabs";

type Decision = {
  id: number;
  title: string;
  decision: string;
  context: string;
  decided_by: string;
  review_by: string | null;
  status: string;
  created_at: string;
};

export default function CharterPage() {
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [reviewBy, setReviewBy] = useState(
    () => new Date(Date.now() + 90 * 86400_000).toISOString().slice(0, 10),
  );

  const load = useCallback(() => {
    api<Decision[]>("/api/decisions?category=charter")
      .then(setDecisions)
      .catch((e) => setError(String(e)));
  }, []);
  useEffect(load, [load]);

  const add = async () => {
    if (!title.trim() || !text.trim()) return;
    try {
      await api("/api/decisions", {
        method: "POST",
        body: JSON.stringify({
          title,
          decision: text,
          review_by: reviewBy,
          category: "charter",
        }),
      });
      setTitle("");
      setText("");
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  return (
    <main className="mx-auto w-full max-w-3xl p-6">
      <SectionTabs set="team" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Team charter & decision rights</h1>
      <p className="mb-6 text-sm text-ink-3">
        Mission, ownership, escalation rules, working agreements — recorded as
        decisions with review dates, so they get reconfirmed or superseded
        instead of silently rotting.
      </p>
      {error && <p className="text-sm text-danger">{error}</p>}

      <div className="mb-6 space-y-2 rounded-xl border border-line bg-card p-4 shadow-card">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Production incident escalation path"
          className="w-full rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
        />
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="The agreement itself…"
          className="w-full rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
        />
        <div className="flex items-center gap-2 text-xs text-ink-2">
          <label htmlFor="charter-review-by">
            Review by — charter entries go stale like decisions do:
          </label>
          <input
            id="charter-review-by"
            name="charter-review-by"
            type="date"
            value={reviewBy}
            onChange={(e) => setReviewBy(e.target.value)}
            className="rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
          />
        </div>
        <button
          onClick={add}
          disabled={!title.trim() || !text.trim() || !reviewBy}
          className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
        >
          Record charter entry
        </button>
      </div>

      <ul className="space-y-3">
        {decisions.map((d) => (
          <li
            key={d.id}
            className="rounded-xl border border-line bg-card p-4 text-sm shadow-card"
          >
            <div className="mb-1 flex items-center justify-between">
              <span className="font-semibold">{d.title}</span>
              <span
                className={
                  "text-xs " + (d.status === "stale" ? "text-weld" : "text-ink-3")
                }
              >
                {d.status === "stale" ? "⚠ stale — reconfirm or supersede" : d.status}
                {d.review_by ? ` · review by ${d.review_by}` : ""}
              </span>
            </div>
            <p className="text-ink-2">{d.decision}</p>
            <p className="mt-1 text-xs text-ink-3">
              by {d.decided_by || "unrecorded"} · {d.created_at.slice(0, 10)}
            </p>
          </li>
        ))}
        {decisions.length === 0 && (
          <li className="rounded-xl border border-dashed border-line-strong p-8 text-center text-sm text-ink-3">
            No charter entries yet. Start with: who owns what, how we escalate,
            what quality bar we hold.
          </li>
        )}
      </ul>
    </main>
  );
}

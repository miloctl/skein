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
  const [superseding, setSuperseding] = useState<number | null>(null);
  const [busy, setBusy] = useState(false); // a held Enter must not file N entries
  const [newText, setNewText] = useState("");

  const load = useCallback(() => {
    api<Decision[]>("/api/decisions?category=charter")
      .then(setDecisions)
      .catch((e) => setError(String(e)));
  }, []);
  useEffect(load, [load]);

  const add = async () => {
    if (busy || !title.trim() || !text.trim()) return;
    setBusy(true);
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
    } finally {
      setBusy(false);
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
          maxLength={200}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Production incident escalation path"
          className="w-full rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
        />
        <textarea
          value={text}
          maxLength={2000}
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
          disabled={busy || !title.trim() || !text.trim() || !reviewBy}
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
            {d.status !== "superseded" && (
              <div className="mt-2 flex gap-2 text-xs">
                {d.status === "stale" && (
                  <button
                    onClick={async () => {
                      try {
                        await api(`/api/decisions/${d.id}/reconfirm`, {
                          method: "POST",
                          body: JSON.stringify({}),
                        });
                        load();
                      } catch (e) {
                        alert(String(e));
                      }
                    }}
                    className="rounded bg-ok/15 px-2 py-1 font-medium text-ok hover:bg-ok/20"
                  >
                    still true — reconfirm
                  </button>
                )}
                {superseding === d.id ? null : (
                  <button
                    onClick={() => {
                      setSuperseding(Number(d.id));
                      setNewText("");
                    }}
                    className="rounded bg-raised px-2 py-1 text-ink-2 hover:bg-line"
                  >
                    supersede…
                  </button>
                )}
              </div>
            )}
            {superseding === d.id && (
              <div
                className="mt-2 space-y-1.5"
                onKeyDown={(e) => e.key === "Escape" && setSuperseding(null)}
              >
                <textarea
                  autoFocus
                  name="supersede-text"
                  aria-label="The replacement agreement"
                  value={newText}
                  maxLength={2000}
                  onChange={(e) => setNewText(e.target.value)}
                  rows={2}
                  placeholder="The replacement agreement — the old one stays in the chain"
                  className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1.5 text-sm outline-none focus:border-thread-solid"
                />
                <div className="flex gap-2 text-xs">
                  <button
                    disabled={busy || !newText.trim()}
                    onClick={async () => {
                      if (busy) return;
                      setBusy(true);
                      try {
                        await api(`/api/decisions/${d.id}/supersede`, {
                          method: "POST",
                          body: JSON.stringify({ title: d.title, decision: newText.trim() }),
                        });
                        setSuperseding(null);
                        load();
                      } catch (e) {
                        alert(String(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                    className="rounded bg-thread-solid px-2 py-1 font-medium text-white hover:opacity-90 disabled:opacity-40"
                  >
                    Replace it
                  </button>
                  <button
                    onClick={() => setSuperseding(null)}
                    className="text-ink-3 hover:text-ink"
                  >
                    cancel
                  </button>
                </div>
              </div>
            )}
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

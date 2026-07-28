"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { api, getUser, subscribeUser } from "@/lib/api";
import { SectionTabs } from "@/components/section-tabs";
import { timeAgo } from "@/lib/time";
import { emptyState } from "@/lib/whimsy";

function cell(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

type Change = {
  id: number;
  entity: string;
  entity_id: number | null;
  action: string;
  payload: Record<string, unknown>;
  summary: string;
  proposed_by: string;
  requested_by: string | null;
  origin: string;
  created_at: string;
  sponsor?: string; // task_completion only: whose verdict this is
  reviewed_by?: string | null;
  reviewed_override?: number; // 1: judged by someone other than the sponsor
};

export default function ReviewPage() {
  // tracks cross-tab identity switches too, like the nav's name chip
  const me = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  const [changes, setChanges] = useState<Change[]>([]);
  const [history, setHistory] = useState<Change[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [diffs, setDiffs] = useState<
    Record<number, { current: Record<string, unknown>; proposed: Record<string, unknown> }>
  >({});

  const load = useCallback(() => {
    api<Change[]>("/api/review?status=pending")
      .then((rows) => {
        setChanges(rows);
        if (rows.length > 0)
          // a human is now looking — starts the active-review clock
          api("/api/review/seen", {
            method: "POST",
            body: JSON.stringify({ ids: rows.map((r) => r.id) }),
          }).catch(() => {});
        rows
          .filter((r) => r.action === "update")
          .forEach((r) =>
            api<{ diff: { current: Record<string, unknown>; proposed: Record<string, unknown> } | null }>(
              `/api/review/${r.id}/diff`,
            )
              .then((d) => {
                if (d.diff) setDiffs((prev) => ({ ...prev, [r.id]: d.diff! }));
              })
              .catch(() => {}),
          );
      })
      .catch((e) => setError(String(e)));
    api<Change[]>("/api/review?status=approved").then(setHistory).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const approveBatch = async () => {
    if (selected.size === 0) return;
    try {
      const r = await api<{ results: { id: number; status: string; detail?: string }[] }>(
        "/api/review/approve-batch",
        { method: "POST", body: JSON.stringify({ ids: [...selected] }) },
      );
      const failed = r.results.filter((x) => x.status === "error");
      if (failed.length > 0)
        alert(failed.map((f) => `#${f.id}: ${f.detail}`).join("\n"));
      setSelected(new Set());
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  // rejecting — and accepting on a sponsor's behalf — needs a reason the
  // record will keep; asked inline, not via a browser prompt
  const [asking, setAsking] = useState<{ id: number; verb: "approve" | "reject" } | null>(null);
  const [askNote, setAskNote] = useState("");

  const act = async (id: number, verb: "approve" | "reject", note = "") => {
    try {
      await api(`/api/review/${id}/${verb}`, {
        method: "POST",
        body: JSON.stringify({ note }),
      });
      setAsking(null);
      setAskNote("");
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  // acceptance verdicts belong to the sponsor; anyone else must say why
  const forSponsor = (c: Change) => (c.sponsor && c.sponsor !== me ? c.sponsor : "");

  // dismissing the reason input hands focus back to the button that opened
  // it — a keyboard user must not be dropped at the top of the page
  const closeAsk = () => {
    if (!asking) return;
    const { id, verb } = asking;
    setAsking(null);
    setTimeout(() => document.getElementById(`verdict-${verb}-${id}`)?.focus(), 0);
  };

  return (
    <main className="mx-auto w-full max-w-5xl p-4 sm:p-6">
      <SectionTabs set="inbox" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Approvals</h1>
      <p className="mb-6 text-sm text-ink-3">
        Proposed changes from agents (and cautious humans). Approving applies
        the change and records that a human verified it.
      </p>
      {/* reading column: cards cap at 3xl inside the standard page shell */}
      <div className="max-w-3xl">
      {error && <p className="text-sm text-danger">{error}</p>}

      {selected.size > 0 && (
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-line bg-raised px-4 py-2 text-sm">
          <span>{selected.size} selected</span>
          <button
            onClick={approveBatch}
            className="rounded-lg bg-ok px-3 py-1 text-sm font-medium text-white hover:opacity-90"
          >
            Approve selected
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="text-ink-3 hover:text-ink"
          >
            clear
          </button>
        </div>
      )}

      {changes.length === 0 && !error && (
        <p className="rounded-xl border border-dashed border-line-strong p-8 text-center text-sm text-ink-3">
          {emptyState("review")}
          <span className="mt-1 block text-xs">
            When agents (or careful humans) propose changes, they wait here
            for a person to approve them.
          </span>
        </p>
      )}

      <ul className="space-y-4">
        {changes.map((c) => (
          <li
            key={c.id}
            className="rounded-xl border border-line bg-card p-4 shadow-card"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="flex items-center gap-2 text-sm font-semibold">
                <input
                  type="checkbox"
                  checked={selected.has(c.id)}
                  onChange={() => toggle(c.id)}
                  disabled={!!forSponsor(c)}
                  aria-label={`Select #${c.id} ${c.action} ${c.entity} for batch approval`}
                  title={
                    forSponsor(c)
                      ? `sponsored by ${c.sponsor} — accept individually with a reason`
                      : undefined
                  }
                  className="h-4 w-4 disabled:opacity-40"
                />
                #{c.id} · {c.action} {c.entity}
                {c.entity_id ? ` #${c.entity_id}` : ""}
              </span>
              <span className="text-xs text-ink-3">
                by {c.proposed_by}
                {c.requested_by ? ` · asked by ${c.requested_by}` : ""}
                {c.sponsor ? ` · sponsor ${c.sponsor}` : ""} ·{" "}
                <time dateTime={c.created_at} title={c.created_at}>{timeAgo(c.created_at)}</time>
              </span>
            </div>
            {c.summary && <p className="mb-2 text-sm text-ink-2">{c.summary}</p>}
            {diffs[c.id] ? (
              <div className="mb-3 overflow-x-auto">
              <table className="w-full rounded-lg bg-raised text-xs">
                <thead>
                  <tr className="text-left text-ink-3">
                    <th className="p-2">field</th>
                    <th className="p-2">current</th>
                    <th className="p-2">proposed</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(diffs[c.id].proposed).map((k) => (
                    <tr key={k} className="align-top">
                      <td className="p-2 font-medium">{k}</td>
                      <td className="p-2 text-danger">
                        {cell(diffs[c.id].current[k])}
                      </td>
                      <td className="p-2 text-ok">
                        {cell(diffs[c.id].proposed[k])}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            ) : (
              <pre className="mb-3 overflow-x-auto rounded-lg bg-raised p-3 text-xs">
                {JSON.stringify(c.payload, null, 2)}
              </pre>
            )}
            {asking?.id === c.id ? (
              <div className="flex items-center gap-2">
                <input
                  autoFocus
                  name="verdict-reason"
                  value={askNote}
                  onChange={(e) => setAskNote(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && askNote.trim())
                      act(c.id, asking.verb, askNote.trim());
                    if (e.key === "Escape") closeAsk();
                  }}
                  aria-label={
                    asking.verb === "reject"
                      ? "Rejection reason — sent back to the proposer"
                      : "Reason for accepting on the sponsor's behalf"
                  }
                  placeholder={
                    asking.verb === "reject"
                      ? "Why? — sent back to the proposer"
                      : `Why are you accepting for ${c.sponsor}? — goes on the record`
                  }
                  className="flex-1 rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
                />
                <button
                  onClick={() => act(c.id, asking.verb, askNote.trim())}
                  disabled={!askNote.trim()}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40 ${
                    asking.verb === "reject" ? "bg-danger" : "bg-ok"
                  }`}
                >
                  {asking.verb === "reject" ? "Reject" : "Accept"}
                </button>
                <button
                  onClick={closeAsk}
                  className="text-sm text-ink-3 hover:text-ink"
                >
                  cancel
                </button>
              </div>
            ) : (
              <div className="flex gap-2">
                {forSponsor(c) ? (
                  <button
                    id={`verdict-approve-${c.id}`}
                    onClick={() => {
                      setAsking({ id: c.id, verb: "approve" });
                      setAskNote("");
                    }}
                    title={`You're not the sponsor — your reason goes on the record and the verdict won't count toward ${c.proposed_by}'s trust streak`}
                    className="rounded-lg bg-ok px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
                  >
                    Accept for {c.sponsor}…
                  </button>
                ) : (
                  <button
                    onClick={() => act(c.id, "approve")}
                    className="rounded-lg bg-ok px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
                  >
                    Approve
                  </button>
                )}
                <button
                  id={`verdict-reject-${c.id}`}
                  onClick={() => {
                    setAsking({ id: c.id, verb: "reject" });
                    setAskNote("");
                  }}
                  className="rounded-lg bg-danger/15 px-3 py-1.5 text-sm font-medium text-danger hover:bg-danger/20"
                >
                  Reject…
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>

      {history.length > 0 && (
        <>
          <h2 className="mb-2 mt-8 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
            Recently approved
          </h2>
          <ul className="space-y-1">
            {history.slice(0, 10).map((c) => (
              <li key={c.id} className="text-xs text-ink-3">
                ✅ #{c.id} {c.summary} <span className="text-ink-3">by {c.proposed_by}</span>
                {c.reviewed_override && c.sponsor
                  ? ` · accepted by ${c.reviewed_by} for ${c.sponsor}`
                  : ""}
              </li>
            ))}
          </ul>
        </>
      )}
      </div>
    </main>
  );
}

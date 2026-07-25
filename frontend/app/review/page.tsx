"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
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
  origin: string;
  created_at: string;
};

export default function ReviewPage() {
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

  const act = async (id: number, verb: "approve" | "reject") => {
    let note = "";
    if (verb === "reject") {
      const answer = prompt("Why? (sent back to the proposer)");
      if (answer === null) return; // cancelled — don't reject
      note = answer;
    }
    try {
      await api(`/api/review/${id}/${verb}`, {
        method: "POST",
        body: JSON.stringify({ note }),
      });
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  return (
    <main className="mx-auto w-full max-w-3xl p-6">
      <h1 className="mb-1 text-xl font-bold">Review inbox</h1>
      <p className="mb-6 text-sm text-zinc-500">
        Proposed changes from agents (and cautious humans). Approving applies
        the change with origin <code>agent_verified</code>.
      </p>
      {error && <p className="text-sm text-red-600">{error}</p>}

      {selected.size > 0 && (
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-zinc-200 bg-zinc-50 px-4 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900">
          <span>{selected.size} selected</span>
          <button
            onClick={approveBatch}
            className="rounded-lg bg-green-600 px-3 py-1 text-sm font-medium text-white hover:bg-green-500"
          >
            Approve selected
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="text-zinc-500 hover:text-zinc-700"
          >
            clear
          </button>
        </div>
      )}

      {changes.length === 0 && !error && (
        <p className="rounded-xl border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-400 dark:border-zinc-700">
          {emptyState("review")}
          <span className="mt-1 block text-xs">
            Agent proposals land here when
            <code className="mx-1">STRANDS_AGENT_REVIEW=1</code>.
          </span>
        </p>
      )}

      <ul className="space-y-4">
        {changes.map((c) => (
          <li
            key={c.id}
            className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="flex items-center gap-2 text-sm font-semibold">
                <input
                  type="checkbox"
                  checked={selected.has(c.id)}
                  onChange={() => toggle(c.id)}
                  className="h-4 w-4"
                />
                #{c.id} · {c.action} {c.entity}
                {c.entity_id ? ` #${c.entity_id}` : ""}
              </span>
              <span className="text-xs text-zinc-400">
                by {c.proposed_by} · {c.created_at}
              </span>
            </div>
            {c.summary && <p className="mb-2 text-sm text-zinc-600 dark:text-zinc-300">{c.summary}</p>}
            {diffs[c.id] ? (
              <table className="mb-3 w-full rounded-lg bg-zinc-50 text-xs dark:bg-zinc-800">
                <thead>
                  <tr className="text-left text-zinc-400">
                    <th className="p-2">field</th>
                    <th className="p-2">current</th>
                    <th className="p-2">proposed</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(diffs[c.id].proposed).map((k) => (
                    <tr key={k} className="align-top">
                      <td className="p-2 font-medium">{k}</td>
                      <td className="p-2 text-red-700 dark:text-red-400">
                        {cell(diffs[c.id].current[k])}
                      </td>
                      <td className="p-2 text-green-700 dark:text-green-400">
                        {cell(diffs[c.id].proposed[k])}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <pre className="mb-3 overflow-x-auto rounded-lg bg-zinc-50 p-3 text-xs dark:bg-zinc-800">
                {JSON.stringify(c.payload, null, 2)}
              </pre>
            )}
            <div className="flex gap-2">
              <button
                onClick={() => act(c.id, "approve")}
                className="rounded-lg bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-500"
              >
                Approve
              </button>
              <button
                onClick={() => act(c.id, "reject")}
                className="rounded-lg bg-red-100 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-200"
              >
                Reject
              </button>
            </div>
          </li>
        ))}
      </ul>

      {history.length > 0 && (
        <>
          <h2 className="mb-2 mt-8 text-sm font-semibold uppercase tracking-wide text-zinc-500">
            Recently approved
          </h2>
          <ul className="space-y-1">
            {history.slice(0, 10).map((c) => (
              <li key={c.id} className="text-xs text-zinc-500">
                ✅ #{c.id} {c.summary} <span className="text-zinc-400">by {c.proposed_by}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  );
}

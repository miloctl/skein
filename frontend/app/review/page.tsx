"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

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

  const load = useCallback(() => {
    api<Change[]>("/api/review?status=pending").then(setChanges).catch((e) => setError(String(e)));
    api<Change[]>("/api/review?status=approved").then(setHistory).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const act = async (id: number, verb: "approve" | "reject") => {
    const note = verb === "reject" ? prompt("Why? (sent back to the proposer)") ?? "" : "";
    if (verb === "reject" && note === null) return;
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

      {changes.length === 0 && !error && (
        <p className="rounded-xl border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-400 dark:border-zinc-700">
          Nothing pending. Agent proposals land here when
          <code className="mx-1">STRANDS_AGENT_REVIEW=1</code>.
        </p>
      )}

      <ul className="space-y-4">
        {changes.map((c) => (
          <li
            key={c.id}
            className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-semibold">
                #{c.id} · {c.action} {c.entity}
                {c.entity_id ? ` #${c.entity_id}` : ""}
              </span>
              <span className="text-xs text-zinc-400">
                by {c.proposed_by} · {c.created_at}
              </span>
            </div>
            {c.summary && <p className="mb-2 text-sm text-zinc-600 dark:text-zinc-300">{c.summary}</p>}
            <pre className="mb-3 overflow-x-auto rounded-lg bg-zinc-50 p-3 text-xs dark:bg-zinc-800">
              {JSON.stringify(c.payload, null, 2)}
            </pre>
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

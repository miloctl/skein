"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

type Req = {
  id: number;
  title: string;
  detail: string;
  requester: string;
  project_class: string;
  reach: number;
  impact: number;
  confidence: number;
  effort: number;
  score: number;
  status: string;
  disposition_reason: string;
};

const STATUS_COLORS: Record<string, string> = {
  submitted: "bg-amber-100 text-amber-700",
  scored: "bg-blue-100 text-blue-700",
  accepted: "bg-green-100 text-green-700",
  deferred: "bg-zinc-200 text-zinc-700",
  declined: "bg-red-100 text-red-700",
};

export default function IntakePage() {
  const [reqs, setReqs] = useState<Req[]>([]);
  const [form, setForm] = useState({ title: "", detail: "", project_class: "" });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Req[]>("/api/intake").then(setReqs).catch((e) => setError(String(e)));
  }, []);
  useEffect(load, [load]);

  const submit = async () => {
    if (!form.title.trim()) return;
    await api("/api/intake", { method: "POST", body: JSON.stringify(form) });
    setForm({ title: "", detail: "", project_class: "" });
    load();
  };

  const score = async (id: number) => {
    const raw = prompt("Score reach,impact,confidence,effort (each 1-5):", "3,3,3,3");
    if (!raw) return;
    const [reach, impact, confidence, effort] = raw.split(",").map((n) => parseInt(n.trim(), 10));
    try {
      await api(`/api/intake/${id}/score`, {
        method: "POST",
        body: JSON.stringify({ reach, impact, confidence, effort }),
      });
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const disposition = async (id: number, d: string) => {
    const reason = prompt(`Reason for "${d}" (requesters see this):`);
    if (!reason) return;
    try {
      await api(`/api/intake/${id}/disposition`, {
        method: "POST",
        body: JSON.stringify({ disposition: d, reason }),
      });
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  return (
    <main className="mx-auto w-full max-w-3xl p-6">
      <h1 className="mb-1 text-xl font-bold">Engagement intake</h1>
      <p className="mb-6 text-sm text-zinc-500">
        The team&apos;s front door. Score with RICE-lite (reach × impact ×
        confidence ÷ effort), then accept, defer, or decline — with a reason
        the requester sees. Accepting creates an engagement.
      </p>

      <div className="mb-8 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
          New request
        </h2>
        <div className="flex flex-col gap-2">
          <input
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="What are you asking the team to do?"
            className="rounded-lg border border-zinc-300 bg-transparent px-3 py-2 text-sm outline-none dark:border-zinc-700"
          />
          <textarea
            value={form.detail}
            onChange={(e) => setForm({ ...form, detail: e.target.value })}
            placeholder="Context, goals, constraints…"
            rows={2}
            className="rounded-lg border border-zinc-300 bg-transparent px-3 py-2 text-sm outline-none dark:border-zinc-700"
          />
          <div className="flex items-center gap-2">
            <select
              value={form.project_class}
              onChange={(e) => setForm({ ...form, project_class: e.target.value })}
              className="rounded-lg border border-zinc-300 bg-transparent px-2 py-2 text-sm outline-none dark:border-zinc-700 dark:bg-zinc-900"
            >
              <option value="">class: unknown</option>
              <option value="prototype">prototype</option>
              <option value="incident">incident</option>
              <option value="migration">migration</option>
              <option value="diligence">diligence</option>
            </select>
            <button
              onClick={submit}
              disabled={!form.title.trim()}
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
            >
              Submit
            </button>
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      <ul className="space-y-3">
        {reqs.map((r) => (
          <li
            key={r.id}
            className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold">
                #{r.id} {r.title}
                {r.project_class && (
                  <span className="ml-2 text-xs font-normal text-zinc-400">
                    [{r.project_class}]
                  </span>
                )}
              </span>
              <span className="flex items-center gap-2">
                {r.status !== "submitted" && (
                  <span className="text-xs text-zinc-400" title="reach×impact×confidence÷effort">
                    score {r.score}
                  </span>
                )}
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[r.status]}`}
                >
                  {r.status}
                </span>
              </span>
            </div>
            {r.detail && <p className="mt-1 text-sm text-zinc-500">{r.detail}</p>}
            <p className="mt-1 text-xs text-zinc-400">requested by {r.requester}</p>
            {r.disposition_reason && (
              <p className="mt-1 text-xs italic text-zinc-500">↳ {r.disposition_reason}</p>
            )}
            {(r.status === "submitted" || r.status === "scored") && (
              <div className="mt-2 flex gap-2">
                <button onClick={() => score(r.id)}
                        className="rounded bg-blue-100 px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-200">
                  score
                </button>
                {r.status === "scored" && (
                  <>
                    <button onClick={() => disposition(r.id, "accepted")}
                            className="rounded bg-green-100 px-2 py-1 text-xs font-medium text-green-700 hover:bg-green-200">
                      accept
                    </button>
                    <button onClick={() => disposition(r.id, "deferred")}
                            className="rounded bg-zinc-200 px-2 py-1 text-xs font-medium text-zinc-700 hover:bg-zinc-300">
                      defer
                    </button>
                    <button onClick={() => disposition(r.id, "declined")}
                            className="rounded bg-red-100 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-200">
                      decline
                    </button>
                  </>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}

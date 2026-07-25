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
  submitted: "bg-warn/15 text-warn",
  scored: "bg-thread/15 text-thread",
  accepted: "bg-ok/15 text-ok",
  deferred: "bg-raised text-ink-2",
  declined: "bg-danger/15 text-danger",
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
    try {
      await api("/api/intake", { method: "POST", body: JSON.stringify(form) });
      setForm({ title: "", detail: "", project_class: "" });
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  const score = async (id: number) => {
    const raw = prompt("Score reach,impact,confidence,effort (each 1-5):", "3,3,3,3");
    if (!raw) return;
    const parts = raw.split(",").map((n) => parseInt(n.trim(), 10));
    if (parts.length !== 4 || parts.some((n) => Number.isNaN(n) || n < 1 || n > 5)) {
      alert("Need exactly four numbers, each 1-5 (e.g. 3,4,2,3).");
      return;
    }
    const [reach, impact, confidence, effort] = parts;
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

  const disposition = async (id: number, d: string, asExperiment = false) => {
    const reason = prompt(`Reason for "${d}" (requesters see this):`);
    if (reason === null) return; // cancelled
    if (!reason.trim()) {
      alert("A reason is required — requesters see it.");
      return;
    }
    let kind = "delivery";
    let timebox_end = "";
    let outcome = "";
    let lead = "";
    let kill_criteria = "";
    if (d === "accepted") {
      if (asExperiment) {
        const tb = prompt("Experiment timebox end (YYYY-MM-DD):");
        if (tb === null) return; // cancelled
        if (!tb.trim()) {
          alert("Experiments need a timebox.");
          return;
        }
        kind = "experiment";
        timebox_end = tb.trim();
        kill_criteria =
          prompt("Kill criteria (optional — what result stops this early?):") ?? "";
      }
      lead = prompt("Lead (optional — who owns the engagement?):") ?? "";
      outcome = prompt("Outcome statement (optional — what result would success show?):") ?? "";
    }
    try {
      await api(`/api/intake/${id}/disposition`, {
        method: "POST",
        body: JSON.stringify({
          disposition: d,
          reason,
          kind,
          timebox_end,
          outcome,
          lead: lead.trim(),
          kill_criteria: kill_criteria.trim(),
        }),
      });
      load();
    } catch (e) {
      alert(String(e));
    }
  };

  return (
    <main className="mx-auto w-full max-w-3xl p-6">
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Engagement intake</h1>
      <p className="mb-6 text-sm text-ink-3">
        The team&apos;s front door. Score with RICE-lite (reach × impact ×
        confidence ÷ effort), then accept, defer, or decline — with a reason
        the requester sees. Accepting creates an engagement.
      </p>

      <div className="mb-8 rounded-xl border border-line bg-card p-4 shadow-card">
        <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
          New request
        </h2>
        <div className="flex flex-col gap-2">
          <input
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="What are you asking the team to do?"
            className="rounded-lg border border-line-strong bg-transparent px-3 py-2 text-sm outline-none focus:border-thread-solid"
          />
          <textarea
            value={form.detail}
            onChange={(e) => setForm({ ...form, detail: e.target.value })}
            placeholder="Context, goals, constraints…"
            rows={2}
            className="rounded-lg border border-line-strong bg-transparent px-3 py-2 text-sm outline-none focus:border-thread-solid"
          />
          <div className="flex items-center gap-2">
            <select
              value={form.project_class}
              onChange={(e) => setForm({ ...form, project_class: e.target.value })}
              className="rounded-lg border border-line-strong bg-card px-2 py-2 text-sm outline-none"
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
              className="rounded-lg bg-thread-solid px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
            >
              Submit
            </button>
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}
      <ul className="space-y-3">
        {reqs.map((r) => (
          <li
            key={r.id}
            className="rounded-xl border border-line bg-card p-4 shadow-card"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold">
                #{r.id} {r.title}
                {r.project_class && (
                  <span className="ml-2 text-xs font-normal text-ink-3">
                    [{r.project_class}]
                  </span>
                )}
              </span>
              <span className="flex items-center gap-2">
                {r.status !== "submitted" && (
                  <span className="text-xs text-ink-3" title="reach×impact×confidence÷effort">
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
            {r.detail && <p className="mt-1 text-sm text-ink-3">{r.detail}</p>}
            <p className="mt-1 text-xs text-ink-3">requested by {r.requester}</p>
            {r.disposition_reason && (
              <p className="mt-1 text-xs italic text-ink-3">↳ {r.disposition_reason}</p>
            )}
            {(r.status === "submitted" || r.status === "scored") && (
              <div className="mt-2 flex gap-2">
                <button onClick={() => score(r.id)}
                        className="rounded bg-thread/15 px-2 py-1 text-xs font-medium text-thread hover:bg-thread/20">
                  score
                </button>
                {r.status === "scored" && (
                  <>
                    <button onClick={() => disposition(r.id, "accepted")}
                            className="rounded bg-ok/15 px-2 py-1 text-xs font-medium text-ok hover:bg-ok/20">
                      accept
                    </button>
                    <button onClick={() => disposition(r.id, "accepted", true)}
                            title="Accept as a timeboxed experiment — invalidated on time is a success, not a slip"
                            className="rounded bg-weld/15 px-2 py-1 text-xs font-medium text-weld hover:bg-weld/20">
                      🧪 accept as experiment
                    </button>
                    <button onClick={() => disposition(r.id, "deferred")}
                            className="rounded bg-raised px-2 py-1 text-xs font-medium text-ink-2 hover:bg-line">
                      defer
                    </button>
                    <button onClick={() => disposition(r.id, "declined")}
                            className="rounded bg-danger/15 px-2 py-1 text-xs font-medium text-danger hover:bg-danger/20">
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

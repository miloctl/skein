"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { actionError, api, getUser, loadError, subscribeUser } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { EmptyState } from "@/components/card";
import { SectionTabs } from "@/components/section-tabs";
import { timeAgo } from "@/lib/time";
import { emptyState } from "@/lib/whimsy";

function cell(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  // a reviewer reads these values to decide — JSON.stringify put `[2]` in
  // front of them for the weekly plan's task list
  if (Array.isArray(v)) return v.length ? v.map(cell).join(", ") : "—";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

type Diff = {
  current: Record<string, unknown>;
  proposed: Record<string, unknown>;
};

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
  label: string; // services/lexicon.py — what this write is called
  sponsor?: string; // task_completion only: whose verdict this is
  reviewed_by?: string | null;
  reviewed_override?: number; // 1: judged by someone other than the sponsor
};

/** How the proposal was written, which the proposer's NAME does not answer:
 *  pasted meeting notes arrive as `human` under the person who pasted them
 *  (services/ingest.py), and everything a tool proposed arrives as `agent`.
 *  A reviewer reads those two differently — one is a transcription to check,
 *  the other is a model's judgment to check — and the queue showed neither.
 *  Any other value renders as itself rather than being mapped to a guess. */
function OriginChip({ origin }: { origin: string }) {
  const said =
    origin === "agent"
      ? { word: "agent", why: "An agent tool proposed this write." }
      : origin === "human"
        ? { word: "person", why: "A person proposed this write." }
        : { word: origin, why: `Recorded origin: ${origin}.` };
  return (
    <span
      title={said.why}
      className="rounded-full bg-raised px-1.5 py-0.5 text-[10px] text-ink-3"
    >
      {said.word}
    </span>
  );
}

/** The reason input owns its draft (EditRow idiom, app/dashboard/page.tsx):
 *  keystrokes re-render this row, not the whole approvals list. */
function VerdictAsk({
  verb,
  sponsor,
  onSubmit,
  onCancel,
}: {
  verb: "approve" | "reject";
  sponsor?: string;
  onSubmit: (note: string) => void;
  onCancel: () => void;
}) {
  const [note, setNote] = useState("");
  return (
    <div className="flex items-center gap-2">
      <input
        autoFocus
        name="verdict-reason"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && note.trim()) onSubmit(note.trim());
          if (e.key === "Escape") onCancel();
        }}
        aria-label={
          verb === "reject"
            ? "Rejection reason — sent back to the proposer"
            : "Reason for accepting on the sponsor's behalf"
        }
        placeholder={
          verb === "reject"
            ? "Why? — sent back to the proposer"
            : `Why are you accepting for ${sponsor}? — goes on the record`
        }
        className="flex-1 rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
      />
      <button
        onClick={() => onSubmit(note.trim())}
        disabled={!note.trim()}
        className={`rounded-lg px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40 ${
          verb === "reject" ? "bg-danger" : "bg-ok"
        }`}
      >
        {verb === "reject" ? "Reject" : "Accept"}
      </button>
      <button onClick={onCancel} className="text-sm text-ink-3 hover:text-ink">
        cancel
      </button>
    </div>
  );
}

export default function ReviewPage() {
  // tracks cross-tab identity switches too, like the nav's name chip
  const me = useSyncExternalStore(subscribeUser, getUser, () => "anonymous");
  // null until the fetch settles: [] is a real answer ("nothing is waiting"),
  // and starting there flashed that empty state on every navigation and left
  // it standing after a failed load — an empty queue is a claim, not a blank
  const [changes, setChanges] = useState<Change[] | null>(null);
  const [history, setHistory] = useState<Change[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [batchFailures, setBatchFailures] = useState<{ id: number; detail?: string }[]>([]);
  const [diffs, setDiffs] = useState<Record<number, Diff>>({});

  const load = useCallback(() => {
    api<Change[]>("/api/review?status=pending")
      .then(async (rows) => {
        setChanges(rows);
        setError(null); // a recovered backend must not leave the old banner above fresh data
        if (rows.length > 0)
          // a human is now looking — starts the active-review clock
          api("/api/review/seen", {
            method: "POST",
            body: JSON.stringify({ ids: rows.map((r) => r.id) }),
          }).catch(() => {});
        // one state commit for all diffs: a setDiffs per row re-renders
        // the whole page once per pending change
        const entries: [number, Diff][] = [];
        await Promise.all(
          rows
            .filter((r) => r.action === "update")
            .map(async (r) => {
              try {
                const d = await api<{ diff: Diff | null }>(`/api/review/${r.id}/diff`);
                if (d.diff) entries.push([r.id, d.diff]);
              } catch {}
            }),
        );
        setDiffs(Object.fromEntries(entries));
      })
      .catch((e) => {
        setChanges([]);           // settled, with the error shown below
        setError(loadError(e));
      });
    api<Change[]>("/api/review?status=approved")
      .then((h) => {
        setHistory(h);
        setHistoryError(null);
      })
      // swallowing this hid the whole section, and a missing "Recently
      // approved" list reads as "nothing was approved" — a claim
      .catch((e) => setHistoryError(`Cannot load the recently approved list. ${actionError(e)}`));
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
      // one row per failure, in the page: a batch of 20 can fail 20
      // different ways, each naming a different proposal, and the status
      // region holds one line.
      setBatchFailures(r.results.filter((x) => x.status === "error"));
      setSelected(new Set());
      load();
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  // rejecting — and accepting on a sponsor's behalf — needs a reason the
  // record will keep; asked inline, not via a browser prompt
  const [asking, setAsking] = useState<{ id: number; verb: "approve" | "reject" } | null>(null);

  const act = async (id: number, verb: "approve" | "reject", note = "") => {
    try {
      await api(`/api/review/${id}/${verb}`, {
        method: "POST",
        body: JSON.stringify({ note }),
      });
      setAsking(null);
      load();
    } catch (e) {
      reportStatus(actionError(e));
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
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <SectionTabs set="inbox" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Approvals</h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Proposed changes from agents (and careful humans). When you approve,
        Skein applies the change and records that a human verified it.
      </p>
      {error && <p className="text-sm text-danger">{error}</p>}

      {batchFailures.length > 0 && (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-sm text-danger"
        >
          <div className="flex items-start justify-between gap-3">
            <p className="font-medium">
              {batchFailures.length === 1
                ? "1 proposal was not approved:"
                : `${batchFailures.length} proposals were not approved:`}
            </p>
            <button onClick={() => setBatchFailures([])} className="shrink-0 text-xs underline">
              dismiss
            </button>
          </div>
          <ul className="mt-1 list-disc pl-5">
            {batchFailures.map((f) => (
              <li key={f.id}>
                #{f.id}: {f.detail || "no reason given"}
              </li>
            ))}
          </ul>
        </div>
      )}

      {selected.size > 0 && (
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-line bg-raised px-4 py-2 text-sm">
          <span>{selected.size} selected</span>
          <button
            onClick={approveBatch}
            className="rounded-lg bg-ok-solid px-3 py-1 text-sm font-medium text-white hover:opacity-90"
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

      {changes === null && !error && (
        <p className="text-sm text-ink-3">Loading…</p>
      )}
      {changes !== null && changes.length === 0 && !error && (
        <EmptyState>
          {emptyState("review")}
          <span className="mt-1 block text-xs">
            When agents (or careful humans) propose changes, they wait here
            for a person to approve them.
          </span>
        </EmptyState>
      )}

      <ul className="space-y-4">
        {(changes ?? []).map((c) => (
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
                  aria-label={`Select #${c.id} ${c.label} for batch approval`}
                  title={
                    forSponsor(c)
                      ? `sponsored by ${c.sponsor} — accept individually with a reason`
                      : undefined
                  }
                  className="h-4 w-4 disabled:opacity-40"
                />
                #{c.id} · {c.label}
                {/* the id after a task_completion names the TASK the sponsor is
                    accepting, not this proposal — the bare "#10" read as a
                    second proposal number */}
                {c.entity_id
                  ? c.entity === "task_completion"
                    ? ` on task #${c.entity_id}`
                    : ` #${c.entity_id}`
                  : ""}
              </span>
              <span className="text-xs text-ink-3">
                by {c.proposed_by} <OriginChip origin={c.origin} />
                {c.requested_by ? ` · asked by ${c.requested_by}` : ""}
                {c.sponsor
                  ? ` · sponsor ${c.sponsor}${forSponsor(c) ? " (accept individually with a reason)" : ""}`
                  : ""} ·{" "}
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
              /* a create proposal's payload IS the change — render it as
                 fields, not as a JSON dump a phone user has to parse */
              <div className="mb-3 overflow-x-auto rounded-lg bg-raised p-3">
                <table className="w-full text-xs">
                  <tbody>
                    {Object.entries(c.payload).map(([k, v]) => (
                      <tr key={k} className="align-top">
                        <td className="w-32 py-0.5 pr-3 font-medium text-ink-3">
                          {k.replace(/_/g, " ")}
                        </td>
                        <td className="py-0.5 text-ink-2">{cell(v)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {Object.keys(c.payload).length === 0 && (
                  <p className="text-xs text-ink-3">No fields — see the summary above.</p>
                )}
              </div>
            )}
            {asking?.id === c.id ? (
              <VerdictAsk
                verb={asking.verb}
                sponsor={c.sponsor}
                onSubmit={(note) => act(c.id, asking.verb, note)}
                onCancel={closeAsk}
              />
            ) : (
              <div className="flex gap-2">
                {forSponsor(c) ? (
                  <button
                    id={`verdict-approve-${c.id}`}
                    onClick={() => setAsking({ id: c.id, verb: "approve" })}
                    title={`You are not the sponsor — your reason goes on the record and the verdict will not count toward ${c.proposed_by}'s trust streak`}
                    className="rounded-lg bg-ok-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
                  >
                    Accept for {c.sponsor}…
                  </button>
                ) : (
                  <button
                    onClick={() => act(c.id, "approve")}
                    className="rounded-lg bg-ok-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
                  >
                    Approve
                  </button>
                )}
                <button
                  id={`verdict-reject-${c.id}`}
                  onClick={() => setAsking({ id: c.id, verb: "reject" })}
                  className="rounded-lg bg-danger/15 px-3 py-1.5 text-sm font-medium text-danger hover:bg-danger/20"
                >
                  Reject…
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>

      {(history.length > 0 || historyError) && (
        <>
          <h2 className="mb-2 mt-8 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
            Recently approved
          </h2>
          {historyError && <p className="text-xs text-danger">{historyError}</p>}
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
    </main>
  );
}

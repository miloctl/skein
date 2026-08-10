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
  // task_completion only: what the sponsor is judging. The verdict controls
  // were here and the evidence was two navigations away (services/review.py).
  evidence?: {
    id: number;
    title: string;
    status: string;
    delegated_agent: string | null;
    forge_url: string | null;
    worklog: { id: number; author: string; note: string; created_at: string }[];
    // set only when the sponsor CHANGED after the work was submitted —
    // authority follows the current sponsor by design, so this is a receipt
    sponsor_was: string;
  };
  reviewed_by?: string | null;
  reviewed_override?: number; // 1: judged by someone other than the sponsor
  // this proposer's settled verdicts on THIS entity. null when none have
  // settled — services/review.py sends no zeroed record, because "0 of 0
  // approved" is a claim about a history that does not exist
  record?: {
    approved: number;
    proposed: number;
    approval_rate: number;
    streak: number;
    // why no streak CAN form, when none can — services/delegation.py's own
    // sentence, not a second one invented here
    streak_blocked: string;
    level: string;
    promotes_at: number; // 0 unless this verdict is the one that earns it
  } | null;
};

/** What the proposer's past verdicts on this entity say, at the moment the
 *  next one is being made.
 *
 *  The numbers existed already and rendered on Team → Agents — two pages from
 *  the one screen where they decide something. A reviewer approving a fourth
 *  proposal in a row could not see that it was the fourth.
 *
 *  Counts, never a verdict of its own. `proposed` is the SETTLED count
 *  (services/delegation.py selects `status != 'pending'`), so the sentence
 *  says settled rather than letting "3 of 4" read as four proposals made.
 *
 *  When no streak can form the run is WITHHELD and the reason is given: in
 *  trusted-header mode every verdict is weak, so a bare "no run of approvals"
 *  beside "8 of 8 approved" states a perfect record and no run in one breath.
 *  Team → Agents shows the same NUMBERS as a stat row rather than this
 *  sentence — a dense list is not prose and takes the label form. What both
 *  must agree on is the zero case: neither may print a bare `streak 0`,
 *  which reads as a score rather than as "the last verdict was not an
 *  approval". */
function TrackRecord({
  record,
  label,
}: {
  record: NonNullable<Change["record"]>;
  /** what this write is CALLED (services/lexicon.py), never the raw entity
   *  slug — the row already resolves it so the header, the checkbox and the
   *  notification cannot drift, and "settled task_completion proposals" is a
   *  column value no reader has met */
  label: string;
}) {
  const settled = `${record.approved} of ${record.proposed}`;
  return (
    <p className="mt-1 text-xs text-ink-3">
      {/* "proposals to <verb phrase>", not "settled <label> proposals" —
          services/lexicon.py stores every entity as a VERB phrase ("add a
          task", "make a promise"), so slotting one into a noun position
          reads "settled add a task proposals" on every real entity */}
      <span className="tabular-nums">{settled}</span> settled proposal
      {record.proposed === 1 ? "" : "s"} to {label} approved (
      {Math.round(record.approval_rate * 100)}%).{" "}
      {record.streak_blocked ? (
        <span>{record.streak_blocked}</span>
      ) : record.streak === 0 ? (
        "The last settled verdict was not an approval."
      ) : (
        <>
          {record.streak} approval{record.streak === 1 ? "" : "s"} in a row.
          {record.promotes_at > 0 ? (
            // text-ok, not a raw palette green: scripts/check_theme_contrast.py
            // sweeps the tokens parsed out of globals.css and theme.ts
            <span className="ml-1 font-medium text-ok">
              One more approval makes {record.promotes_at} in a row, which is
              the streak that files a promotion proposal.
            </span>
          ) : null}
        </>
      )}
    </p>
  );
}

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
    // sr-only text, NOT aria-label: the attribute is prohibited on a bare
    // span (role=generic) and Chrome drops it from the tree entirely, so the
    // distinction this chip exists to draw — a transcription to check versus
    // a model's judgment to check — reached no screen-reader user at all.
    // axe does not flag it, because its aria-prohibited-attr rule skips
    // elements that have text content.
    <span
      title={said.why}
      className="rounded-full bg-raised px-1.5 py-0.5 text-[10px] text-ink-3"
    >
      {said.word}
      <span className="sr-only"> — {said.why}</span>
    </span>
  );
}

/** What a sponsor is actually judging, beside the verdict controls.
 *
 *  An acceptance proposal says "mark a delegated task done". The evidence for
 *  that call is the agent's own worklog, and the only other web surface that
 *  shows it is the task peek — so the sponsor left this screen, found the
 *  task, read the notes, came back and voted from memory. The last few notes
 *  answer the common case here; the task link above opens the rest.
 */
function AcceptanceEvidence({
  evidence,
}: {
  evidence: NonNullable<Change["evidence"]>;
}) {
  return (
    <div className="mb-3 rounded-lg border border-line bg-raised p-2.5 text-xs">
      <p className="mb-1 text-ink-2">
        <span className="font-medium">{evidence.title}</span>
        <span className="text-ink-3">
          {" "}
          · now {evidence.status}
          {evidence.delegated_agent ? ` · by ${evidence.delegated_agent}` : ""}
        </span>
      </p>
      {evidence.sponsor_was ? (
        // authority follows the CURRENT sponsor by design, so this is a
        // receipt and not a refusal — but a verdict that moved to somebody who
        // never watched the work is the thing to say before Approve is pressed
        <p className="mb-1 text-weld">
          {evidence.sponsor_was} sponsored this when the work was submitted.
          The verdict moved with the delegation.
        </p>
      ) : null}
      {evidence.forge_url ? (
        // bare href is safe: services/forge.py::_clean_url is the only writer
        // and admits bounded http(s) only
        <a
          href={evidence.forge_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-ink-3 underline hover:text-ink-2"
        >
          code <span aria-hidden>↗</span>
        </a>
      ) : null}
      {evidence.worklog.length > 0 ? (
        <ul className="mt-1 space-y-1">
          {evidence.worklog.map((w) => (
            <li key={w.id} className="text-ink-2">
              <span className="text-ink-3">{w.author}:</span>{" "}
              <span className="whitespace-pre-wrap break-words">{w.note}</span>
            </li>
          ))}
        </ul>
      ) : (
        // stated, never omitted: an acceptance with no progress notes is the
        // case a sponsor most needs to notice, and a blank block reads as
        // "nothing to see" rather than "this agent reported nothing"
        <p className="mt-1 text-ink-3">
          No progress notes were filed on this task.
        </p>
      )}
    </div>
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
                {c.entity_id ? (
                  c.entity === "task_completion" ? (
                    <>
                      {" on "}
                      {/* a link, not text. The panel behind it holds the full
                          worklog, the forge link and the delegation — the
                          evidence block below carries the last few notes so
                          the common verdict needs no navigation at all */}
                      <a
                        href={`?task=${c.entity_id}`}
                        className="underline decoration-line-strong underline-offset-2 hover:decoration-ink-2"
                      >
                        task #{c.entity_id}
                      </a>
                    </>
                  ) : (
                    ` #${c.entity_id}`
                  )
                ) : (
                  ""
                )}
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
            {/* below the header row, not inside it: that row is
                justify-between, and a third child there lands at the right
                edge and shoves the byline into the middle */}
            {c.record ? <TrackRecord record={c.record} label={c.label} /> : null}
            {c.summary && <p className="mb-2 text-sm text-ink-2">{c.summary}</p>}
            {c.evidence ? <AcceptanceEvidence evidence={c.evidence} /> : null}
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

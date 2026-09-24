"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Card as Section } from "@/components/card";
import { actionError, api, authenticatedFetch } from "@/lib/api";
import { checkSessionRevision, sessionRevision } from "@/lib/auth";
import { size } from "@/lib/size";
import { reportStatus } from "@/lib/status";

type Summary = { counts: Record<string, number>; file_bytes: number };
type PrivateRow = { id: number; label: string; created_at: string };

// services/my_data.py::LABELS: the kinds this card lists and deletes
const LISTED: Record<string, string> = {
  standups: "Standups",
  tasks: "Tasks",
  notes: "Notes",
  questions: "Questions",
  decisions: "Decisions",
  promises: "Promises",
  blockers: "Blockers",
  intake_requests: "Intake requests",
  lessons: "Lessons",
  engagements: "Engagements",
  milestones: "Milestones",
  events: "Events",
  absences: "Time away",
  task_worklog: "Worklog entries",
};
// kinds another surface manages, and where to find it
const ELSEWHERE: Record<string, [string, string]> = {
  artifacts: ["Attached files", "Attached files, above"],
  memories: ["Memories addressed to you", "Agents, then Memory"],
  solo_chats: ["Solo chats", "Chat"],
  journal_notes: ["1:1 notes you wrote", "People"],
  private_proposals: ["Proposals only you can see", "Review"],
  mcp_servers: ["Your MCP servers", "Settings, then Connections"],
  notifications: ["Notifications", "My Day"],
};

/** What Skein holds that only you can read, with a delete for each private
 *  record and a download of your own data (services/my_data.py). Mounted
 *  for a strong identity only: every route behind it is StrongUser. */
export function MyDataCard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [rows, setRows] = useState<PrivateRow[] | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const intro = useRef<HTMLParagraphElement>(null);

  // the generation guard components/attached-files-card.tsx uses: a slow
  // load resolving after a delete would put the deleted row back
  const gen = useRef(0);
  const load = useCallback(() => {
    const mine = ++gen.current;
    api<Summary>("/api/my-data")
      .then((body) => {
        if (mine !== gen.current) return;
        setSummary(body);
        setError("");
      })
      .catch((e) => {
        if (mine === gen.current) setError(`Cannot load your data. ${actionError(e)}`);
      });
  }, []);
  useEffect(load, [load]);

  const listGen = useRef(0);
  const show = (kind: string) => {
    const mine = ++listGen.current;
    setOpen(kind);
    setRows(null);
    setConfirming(null);
    api<PrivateRow[]>(`/api/my-data/${kind}`)
      .then((body) => {
        if (mine === listGen.current) setRows(body);
      })
      .catch((e) => {
        if (mine === listGen.current) {
          setOpen(null);
          reportStatus(actionError(e));
        }
      });
  };

  const remove = async (kind: string, row: PrivateRow) => {
    if (busy) return;
    setBusy(true);
    try {
      await api(`/api/my-data/${kind}/${row.id}`, { method: "DELETE" });
      setConfirming(null);
      setRows((current) => (current ? current.filter((r) => r.id !== row.id) : current));
      load();
      // the focused button leaves with its row: the intro stays
      setTimeout(() => intro.current?.focus(), 0);
      reportStatus(`${LISTED[kind]}: one private record is deleted.`, "confirmation");
    } catch (e) {
      reportStatus(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  // a link sends no CSRF header: download on the authenticated path, the
  // same way components/attached-files-card.tsx downloads a file
  const download = async () => {
    try {
      const owner = sessionRevision();
      const res = await authenticatedFetch("/api/my-data/export");
      if (!res.ok) throw new Error(`The export did not download (${res.status}). Try again.`);
      const blob = await res.blob();
      checkSessionRevision(owner);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `skein-my-data-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const counts = summary?.counts ?? {};
  return (
    <Section title="Your data" headingLevel={3}>
      <p ref={intro} id="my-data-intro" tabIndex={-1} className="mb-2 text-sm text-ink-3">
        What Skein holds that only you can read. You can delete a private record here. Records
        you shared stay, because others can rely on them.
      </p>
      {error ? <p className="mb-2 text-sm text-danger">{error}</p> : null}
      {summary === null && !error ? (
        <p className="text-sm text-ink-3">Loading your data…</p>
      ) : null}
      {summary ? (
        <ul className="mb-3 space-y-1 text-sm">
          {Object.entries(LISTED).map(([kind, label]) => (
            <li key={kind} className="flex items-center justify-between gap-2">
              <span>
                {label}: <span className="tabular-nums">{counts[kind] ?? 0}</span>
              </span>
              {(counts[kind] ?? 0) > 0 ? (
                <button
                  type="button"
                  aria-expanded={open === kind}
                  aria-controls={open === kind ? "my-data-rows" : undefined}
                  onClick={() => (open === kind ? setOpen(null) : show(kind))}
                  className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                >
                  {open === kind ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
                </button>
              ) : null}
            </li>
          ))}
          {Object.entries(ELSEWHERE).map(([kind, [label, where]]) => (
            <li key={kind} className="text-ink-2">
              {label}: <span className="tabular-nums">{counts[kind] ?? 0}</span>
              {kind === "artifacts" ? ` (${size(summary.file_bytes)})` : ""}
              <span className="text-xs text-ink-3"> · manage in {where}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {open ? (
        <div id="my-data-rows" className="mb-3 rounded-lg bg-raised p-3">
          {rows === null ? (
            <p className="text-sm text-ink-3">Loading…</p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-ink-3">No private {LISTED[open].toLowerCase()} are left.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {rows.map((row) => (
                <li key={row.id} className="flex items-center justify-between gap-2">
                  <span className="min-w-0 break-words">{row.label || `#${row.id}`}</span>
                  {confirming === row.id ? (
                    <span className="flex shrink-0 items-center gap-1 text-xs">
                      <span id={`my-data-${row.id}-consequence`} className="text-ink-3">
                        You cannot undo this.
                      </span>
                      <button
                        type="button"
                        autoFocus
                        aria-disabled={busy}
                        aria-describedby={`my-data-${row.id}-consequence`}
                        onClick={() => void remove(open, row)}
                        className="rounded bg-danger-solid px-2 py-0.5 font-medium text-white hover:opacity-90"
                      >
                        Delete for good
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(null)}
                        className="text-ink-3 hover:text-ink"
                      >
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      aria-label={`Delete ${row.label || `#${row.id}`}`}
                      onClick={() => setConfirming(row.id)}
                      className="shrink-0 text-xs text-ink-3 hover:text-danger"
                    >
                      Delete…
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
      <button
        type="button"
        onClick={() => void download()}
        aria-describedby="my-data-export-help"
        className="rounded border border-line px-3 py-1.5 text-sm"
      >
        Download my data
      </button>
      <p id="my-data-export-help" className="mt-1 text-xs text-ink-3">
        One JSON file with the records you wrote, your solo chats, memories addressed to you, and
        your 1:1 notes. Files are listed by name. Download each file from Attached files.
      </p>
    </Section>
  );
}

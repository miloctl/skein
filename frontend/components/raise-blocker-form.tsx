"use client";

import { useState } from "react";

import { actionError, api } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { PersonInput } from "@/components/person-input";

// mirrors blockers.py::IMPACTS. Impact sets the escalation clock, so it is
// asked for here rather than left at the default.
const IMPACTS = ["low", "medium", "high", "critical"];

/** Raise a blocker against one task. The service sets the task to Blocked
 *  (services/blockers.py::raise_blocker), so this is how the task panel puts
 *  a task there: a bare status leaves a task Blocked with no reason, no owner
 *  and no escalation clock.
 *
 *  The blocker takes the task's own tier: the service refuses a blocker wider
 *  than its task (scope.assert_relationship_contains), and a narrower one
 *  hides the cause from people who can see the task is blocked. */
export function RaiseBlockerForm({
  task,
  onRaised,
  onCancel,
}: {
  task: { id: number; visibility?: string; crew_id?: number | null };
  onRaised: () => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [impact, setImpact] = useState("medium");
  const [owner, setOwner] = useState("");
  const [busy, setBusy] = useState(false);
  const field =
    "rounded-lg border border-line-strong bg-transparent px-2 py-0.5 text-xs outline-none focus:border-thread-solid";

  const raise = async () => {
    setBusy(true);
    try {
      const blocker = await api<{ id: number }>("/api/blockers", {
        method: "POST",
        body: JSON.stringify({
          title: title.trim(),
          impact,
          owner: owner.trim(),
          task_id: task.id,
          visibility: task.visibility ?? "workspace",
          crew_id: task.crew_id ?? 0,
        }),
      });
      // not "the task is blocked": from a stale panel the service files the
      // blocker and leaves a finished task finished, and the reload shows it
      reportStatus(`Blocker #${blocker.id} is open on task #${task.id}.`, "confirmation");
      onRaised();
    } catch (e) {
      // the draft stays: the refusal names what to fix, and retyping the
      // reason is the cost a failed save must not add
      reportStatus(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-1.5 text-xs"
      onKeyDown={(e) => {
        // stopped here: TaskPeek closes the whole panel on Escape at the
        // document, which would drop the draft along with the form
        if (e.key === "Escape") {
          e.stopPropagation();
          onCancel();
        }
      }}
    >
      <label htmlFor={`blocker-title-${task.id}`} className="text-ink-3">
        What blocks it?
      </label>
      <input
        autoFocus
        id={`blocker-title-${task.id}`}
        value={title}
        maxLength={200}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && title.trim() && !busy) raise();
        }}
        className={field}
      />
      <label htmlFor={`blocker-impact-${task.id}`} className="text-ink-3">
        Impact
      </label>
      <select
        id={`blocker-impact-${task.id}`}
        value={impact}
        onChange={(e) => setImpact(e.target.value)}
        className={field}
      >
        {IMPACTS.map((i) => (
          <option key={i} value={i}>
            {i}
          </option>
        ))}
      </select>
      <label htmlFor={`blocker-owner-${task.id}`} className="text-ink-3">
        Who can clear it
      </label>
      <PersonInput
        id={`blocker-owner-${task.id}`}
        name={`blocker-owner-${task.id}`}
        value={owner}
        onChange={(e) => setOwner(e.target.value)}
        className={field}
      />
      <span aria-hidden />
      <span className="flex gap-1.5">
        <button
          disabled={busy || !title.trim()}
          onClick={raise}
          className="rounded-lg bg-thread-solid px-2 py-0.5 font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          Raise blocker
        </button>
        <button onClick={onCancel} className="text-ink-3 hover:text-ink">
          cancel
        </button>
      </span>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";

import { Card as Section } from "@/components/card";
import { actionError, api } from "@/lib/api";
import { reportStatus } from "@/lib/status";

type Request = { id: number; source: string; target: string; created_at: string };

/** A merge both accounts agree to (services/merges.py). Signed in as the
 *  duplicate, a person asks; signed in as the account they keep, they
 *  confirm. It is the one merge that carries data only the duplicate could
 *  read, which is why an administrator cannot run it for them. */
export function MergeRequestsCard({ me }: { me: string }) {
  const [data, setData] = useState<{ outgoing: Request[]; incoming: Request[] } | null>(null);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<number | null>(null);

  const load = useCallback(() => {
    api<{ outgoing: Request[]; incoming: Request[] }>("/api/merge-requests")
      .then(setData)
      .catch((e) => reportStatus(actionError(e)));
  }, []);
  useEffect(load, [load]);

  const act = async (path: string, body: object | null, done: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await api(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
      reportStatus(done, "confirmation");
      setConfirming(null);
      setTarget("");
      load();
    } catch (e) {
      reportStatus(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Merge a duplicate account" headingLevel={3}>
      <p className="mb-3 text-sm text-ink-3">
        If you have two accounts, sign in as the one to remove and ask to merge
        it into the other. Then sign in as the other to confirm. Everything
        moves to the account you keep: private notes, chats, memories, files,
        rooms, crews, and the sign-in. The keys of the removed account stop
        working.
      </p>
      <ul className="mb-3 space-y-2 text-sm">
        {(data?.outgoing ?? []).map((r) => (
          <li key={r.id} className="flex flex-wrap items-center justify-between gap-2">
            <span>
              You asked to merge {me} into {r.target}. It waits for {r.target} to confirm.
            </span>
            <button
              aria-disabled={busy}
              onClick={() => act(`/api/merge-requests/${r.id}/settle`, null, "Merge request cancelled.")}
              className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line aria-disabled:opacity-40"
            >
              Cancel request
            </button>
          </li>
        ))}
        {(data?.incoming ?? []).map((r) =>
          confirming === r.id ? (
            <li key={r.id} className="space-y-1">
              <p id={`merge-${r.id}-consequence`} className="text-xs text-ink-2">
                Merge {r.source} into {me}? Everything {r.source} holds moves to
                this account, including private notes, chats, memories and
                files. The {r.source} account is deleted, and its keys stop
                working. You cannot undo this.
              </p>
              <span className="flex gap-1">
                <button
                  autoFocus
                  aria-disabled={busy}
                  aria-describedby={`merge-${r.id}-consequence`}
                  onClick={() => act(`/api/merge-requests/${r.id}/confirm`, null, `${r.source} is merged into ${me}.`)}
                  className="rounded bg-danger-solid px-2 py-0.5 text-xs font-medium text-white hover:opacity-90 aria-disabled:opacity-40"
                >
                  Merge {r.source} into {me}
                </button>
                <button
                  onClick={() => setConfirming(null)}
                  className="rounded px-2 py-0.5 text-xs text-ink-3 hover:text-ink"
                >
                  Cancel
                </button>
              </span>
            </li>
          ) : (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2">
              <span>{r.source} asks to merge that account into yours.</span>
              <span className="flex gap-1">
                <button
                  onClick={() => setConfirming(r.id)}
                  className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                >
                  Confirm…
                </button>
                <button
                  aria-disabled={busy}
                  aria-label={`Decline the merge request from ${r.source}`}
                  onClick={() => act(`/api/merge-requests/${r.id}/settle`, null, "Merge request declined.")}
                  className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line aria-disabled:opacity-40"
                >
                  Decline
                </button>
              </span>
            </li>
          ),
        )}
      </ul>
      {data && (data.outgoing ?? []).length === 0 && (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (target.trim())
              void act("/api/merge-requests", { target: target.trim() }, `Asked. Sign in as ${target.trim()} to confirm.`);
          }}
        >
          <label className="flex min-w-0 flex-col gap-1 text-xs text-ink-3">
            Merge this account into
            <input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              aria-label="Merge this account into"
              className="rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
            />
          </label>
          <button
            type="submit"
            aria-disabled={busy || !target.trim()}
            className="rounded-lg bg-raised px-3 py-1 text-sm text-ink-2 hover:bg-line aria-disabled:opacity-40"
          >
            Ask to merge
          </button>
        </form>
      )}
    </Section>
  );
}

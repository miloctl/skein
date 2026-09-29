"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ReceiptLine } from "@/components/receipt";
import { actionError, api, backendUnreachable, getUser, isUnreachable } from "@/lib/api";
import type { EntityRef } from "@/lib/entity-ref";
import { HASH_TARGET, useHashTarget } from "@/lib/hash-target";
import { reportStatus } from "@/lib/status";
import { timeAgo } from "@/lib/time";

type Comment = {
  id: number;
  created_by: string;
  origin: string;
  body: string;
  created_at: string;
  edited_at: string | null;
  deleted_at: string | null;
  deleted_by: string;
  can_edit: boolean;
  can_delete: boolean;
  refs: EntityRef[];
};

type Posted = { id: number; notified: string[]; woke: string };

// the task panel's page keeps its own row anchors (a blocker row, a charter
// entry): landing here on one of those would pull focus from the page's hook
const onlyComments = (id: string) => id.startsWith("comment-");

/** One flat thread on a task, a decision or a blocker (services/comments.py).
 *  The body renders through ReceiptLine, never as markup: people write it,
 *  and a reference links only when the server found it readable. */
const READ_FAILED = "Skein could not read the comments. Reload the page to try again.";

export function CommentThread({
  parent,
  id,
  delegatedAgent = "",
  status = "",
  revision = 0,
  onPosted,
}: {
  parent: "task" | "decision" | "blocker";
  id: number;
  delegatedAgent?: string;
  status?: string;
  /** bumps when the host rereads its record (task-peek.tsx's nonce), so an
   *  agent's reply arrives with the panel's own refresh */
  revision?: number;
  /** the host rereads its record: a post can queue an agent turn, which the
   *  task panel shows and polls */
  onPosted?: () => void;
}) {
  const [rows, setRows] = useState<Comment[] | null>(null);
  const [failure, setFailure] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ id: number; text: string } | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [reload, setReload] = useState(0);
  const field = useRef<HTMLTextAreaElement>(null);
  const inFlight = useRef(false);
  const path = `/api/${parent}s/${id}/comments`;

  useEffect(() => {
    let live = true;
    // no-store: a thread is a conversation, and the 15-second GET cache
    // (lib/api.ts) would hide a reply posted from another tab
    api<Comment[]>(path, { cache: "no-store" })
      .then((data) => {
        if (!live) return;
        // a section must not take the task panel down with it: an answer
        // that is not a list is this thread's failure, shown here
        if (Array.isArray(data)) {
          setRows(data);
          setFailure("");
        } else setFailure(READ_FAILED);
      })
      .catch((e) => {
        // the rows already on screen stay: a failed REload must not hide a
        // thread the reader was in the middle of
        if (live) setFailure(isUnreachable(e) ? backendUnreachable(e) : READ_FAILED);
      });
    return () => {
      live = false;
    };
  }, [path, reload, revision]);

  useHashTarget(rows, { reveal: onlyComments, comments: true });

  const focusItem = (commentId: number) =>
    setTimeout(() => document.getElementById(`comment-${commentId}`)?.focus(), 0);

  // One write at a time, guarded here: the write buttons are aria-disabled
  // while it runs, never disabled, because a disabled button drops focus to
  // the page. A failed write rereads the thread: the refusal is usually that
  // the comment changed (deleted meanwhile), and the editor must not stay
  // open over a tombstone.
  const run = useCallback(async (work: () => Promise<void>, failed = actionError) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await work();
    } catch (e) {
      reportStatus(failed(e));
      setReload((n) => n + 1);
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, []);

  const post = () =>
    run(
      async () => {
        const out = await api<Posted>(path, {
          method: "POST",
          body: JSON.stringify({ body: draft }),
        });
        setDraft("");
        setReload((n) => n + 1);
        field.current?.focus();
        onPosted?.();
        reportStatus(
          out.woke
            ? `Comment posted. Skein queued one agent turn for ${out.woke}.`
            : out.notified.length
              ? `Comment posted. Notified: ${out.notified.join(", ")}.`
              : "Comment posted.",
          "confirmation",
        );
      },
      (e) => (isUnreachable(e) ? backendUnreachable(e) : `Skein could not post the comment. ${actionError(e)}`),
    );

  const save = (commentId: number, text: string) =>
    run(async () => {
      await api(`/api/comments/${commentId}`, {
        method: "PATCH",
        body: JSON.stringify({ body: text }),
      });
      setEditing(null);
      setReload((n) => n + 1);
      focusItem(commentId);
      reportStatus("Comment saved.", "confirmation");
    });

  const remove = (commentId: number) =>
    run(async () => {
      await api(`/api/comments/${commentId}`, { method: "DELETE" });
      setConfirming(null);
      // the text leaves the screen now, not when the reload answers
      setRows((current) =>
        (current ?? []).map((row) =>
          row.id === commentId
            ? { ...row, body: "", deleted_at: new Date().toISOString(), deleted_by: getUser(), can_edit: false, can_delete: false }
            : row,
        ),
      );
      setReload((n) => n + 1);
      focusItem(commentId);
      reportStatus("Comment deleted.", "confirmation");
    });

  const control =
    "rounded px-2 py-0.5 text-xs text-ink-3 hover:text-ink aria-disabled:opacity-50";
  return (
    <section aria-labelledby={`comments-${parent}-${id}`} className="space-y-2">
      <h3 id={`comments-${parent}-${id}`} className="mt-2 skein-section-title text-ink-3">
        Comments
      </h3>
      {failure ? <p className="text-xs text-danger">{failure}</p> : null}
      {rows === null ? (
        failure ? null : (
          <p className="text-xs text-ink-3">Loading…</p>
        )
      ) : rows.length === 0 ? (
        <p className="text-xs text-ink-3">No comments yet. Nobody has pulled on this line.</p>
      ) : (
        <ol className="space-y-2">
          {rows.map((c) => (
            <li key={c.id} id={`comment-${c.id}`} tabIndex={-1} className={`text-xs ${HASH_TARGET}`}>
              <span className="text-ink-3">
                {c.created_by}
                {c.origin !== "human" ? (
                  <span className="ml-1 rounded bg-raised px-1">agent</span>
                ) : null}{" "}
                ·{" "}
                <time dateTime={c.created_at} title={c.created_at}>
                  {timeAgo(c.created_at)}
                </time>
                {c.edited_at && !c.deleted_at ? (
                  <>
                    {" · "}
                    <span>edited</span>
                  </>
                ) : null}
              </span>
              {c.deleted_at ? (
                <p className="italic text-ink-3">{c.deleted_by} deleted this comment.</p>
              ) : editing?.id === c.id ? (
                <div className="mt-1 space-y-1">
                  <label htmlFor={`comment-edit-${c.id}`} className="sr-only">
                    Text of comment #{c.id}
                  </label>
                  <textarea
                    id={`comment-edit-${c.id}`}
                    autoFocus
                    maxLength={4000}
                    rows={3}
                    value={editing.text}
                    onChange={(e) => setEditing({ id: c.id, text: e.target.value })}
                    onKeyDown={(e) => {
                      // preventDefault, not stopPropagation: TaskPeek listens
                      // on the document, the node React itself listens on, so
                      // stopping propagation cannot reach it. It checks
                      // defaultPrevented instead (task-peek.tsx).
                      if (e.key === "Escape") {
                        e.preventDefault();
                        setEditing(null);
                        focusItem(c.id);
                      }
                    }}
                    className="w-full rounded-lg border border-line-strong bg-transparent p-2 text-xs outline-none focus:border-thread-solid"
                  />
                  <div className="flex gap-2">
                    <button
                      type="button"
                      aria-disabled={busy || undefined}
                      onClick={() => save(c.id, editing.text)}
                      className={control}
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setEditing(null);
                        focusItem(c.id);
                      }}
                      className={control}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <p className="whitespace-pre-wrap break-words text-ink-2">
                  <ReceiptLine receipt={{ message: c.body, refs: c.refs }} />
                </p>
              )}
              {!c.deleted_at && editing?.id !== c.id && (c.can_edit || c.can_delete) ? (
                // keyed: without a key React reuses the "Delete" button node as
                // "Delete comment", and a double press skips the question
                confirming === c.id ? (
                  <div key="confirm" className="mt-1 flex flex-wrap items-center gap-2">
                    <span id={`comment-confirm-${c.id}`} className="text-ink-2">
                      Delete this comment? Skein deletes the text.
                    </span>
                    <button
                      type="button"
                      aria-describedby={`comment-confirm-${c.id}`}
                      aria-disabled={busy || undefined}
                      onClick={() => remove(c.id)}
                      className="rounded bg-danger-solid px-2 py-0.5 text-xs font-medium text-white hover:opacity-90 aria-disabled:opacity-50"
                    >
                      Delete comment
                    </button>
                    <button
                      type="button"
                      autoFocus
                      onClick={() => {
                        setConfirming(null);
                        focusItem(c.id);
                      }}
                      className={control}
                    >
                      Keep
                    </button>
                  </div>
                ) : (
                  <div key="actions" className="mt-0.5 flex gap-1">
                    {c.can_edit ? (
                      <button
                        type="button"
                        aria-label={`Edit comment #${c.id}`}
                        onClick={() => setEditing({ id: c.id, text: c.body })}
                        className={control}
                      >
                        Edit
                      </button>
                    ) : null}
                    {c.can_delete ? (
                      <button
                        type="button"
                        aria-label={`Delete comment #${c.id}`}
                        onClick={() => setConfirming(c.id)}
                        className={control}
                      >
                        Delete
                      </button>
                    ) : null}
                  </div>
                )
              ) : null}
            </li>
          ))}
        </ol>
      )}
      <div className="space-y-1">
        <label htmlFor={`comment-new-${parent}-${id}`} className="block text-xs text-ink-3">
          Add a comment <span className="sr-only">on {parent} #{id}</span>
        </label>
        {/* the rule the server applies (services/comments.py::_wake): an open
            delegated task, and the agent named with @ */}
        {delegatedAgent && status !== "done" && status !== "void" ? (
          <p id={`comment-hint-${parent}-${id}`} className="text-xs text-ink-3">
            Write @{delegatedAgent} to ask the agent. Skein queues one agent turn.
          </p>
        ) : null}
        <textarea
          ref={field}
          id={`comment-new-${parent}-${id}`}
          aria-describedby={
            delegatedAgent && status !== "done" && status !== "void"
              ? `comment-hint-${parent}-${id}`
              : undefined
          }
          maxLength={4000}
          rows={2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // a draft survives Escape: the task panel closes on it otherwise,
            // and the text goes with it (see the edit box above)
            if (e.key === "Escape" && draft.trim()) e.preventDefault();
          }}
          className="w-full rounded-lg border border-line-strong bg-transparent p-2 text-xs outline-none focus:border-thread-solid"
        />
        {/* disabled while the draft is empty: focus is in the field then,
            never on this button */}
        <button
          type="button"
          disabled={!draft.trim()}
          aria-disabled={busy || undefined}
          onClick={post}
          className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50 aria-disabled:opacity-50"
        >
          Post comment <span className="sr-only">on {parent} #{id}</span>
        </button>
      </div>
    </section>
  );
}

"use client";

import { useState } from "react";

import { CommentThread } from "@/components/comment-thread";

/** A decision's or a blocker's thread, closed until the reader opens it.
 *  The row is where refHref and the thread notices send a reader
 *  (frontend/lib/entity-ref.ts), so the thread mounts there. */
export function CommentsToggle({ parent, id }: { parent: "decision" | "blocker"; id: number }) {
  const [open, setOpen] = useState(false);
  const panel = `comments-panel-${parent}-${id}`;
  return (
    <div className="mt-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen((on) => !on)}
        className="rounded px-2 py-0.5 text-xs text-ink-3 hover:text-ink aria-expanded:bg-raised aria-expanded:text-ink"
      >
        Comments <span className="sr-only">on {parent} #{id}</span>
      </button>
      {open ? (
        <div id={panel}>
          <CommentThread parent={parent} id={id} />
        </div>
      ) : null}
    </div>
  );
}

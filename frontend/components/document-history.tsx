"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ArtifactMarkdown } from "@/components/artifact-markdown";
import { UnifiedDiff } from "@/components/unified-diff";
import { actionError, api, loadError } from "@/lib/api";
import { refHref } from "@/lib/entity-ref";
import { reportStatus } from "@/lib/status";
import { timeAgo } from "@/lib/time";

type Revision = {
  revision: number;
  author: string;
  origin: string;
  change_id: number | null;
  restored_from: number | null;
  created_at: string;
};

type History = { id: number; head: number; revisions: Revision[] };

type OneRevision = Revision & { markdown: string; diff: string };

function Origin({ revision }: { revision: Revision }) {
  if (revision.origin === "human") return <>Person</>;
  if (revision.origin === "agent") return <>Agent</>;
  if (!revision.change_id) return <>Agent, approved</>;
  const href = refHref({ entity: "proposal", id: revision.change_id });
  return (
    <>
      Agent, approved in{" "}
      {href ? (
        <Link href={href} className="text-thread underline">
          proposal #{revision.change_id}
        </Link>
      ) : (
        `proposal #${revision.change_id}`
      )}
    </>
  );
}

/** Every revision of one document, each with who made it, and a restore that
 *  copies an earlier one as the new head (POST .../restore). A restore keeps
 *  the bad revision, so restoring the previous head undoes it. `onChanged`
 *  runs when the head moved: a restore, or a refusal that found a newer one. */
export function DocumentHistory({
  artifactId,
  onChanged,
}: {
  artifactId: number;
  onChanged: () => void;
}) {
  const [history, setHistory] = useState<History | null>(null);
  const [failure, setFailure] = useState("");
  const [selected, setSelected] = useState<OneRevision | null>(null);
  const [busy, setBusy] = useState(false);
  // aria-disabled, never disabled, for the reason components/document-editor.tsx gives
  const inFlight = useRef(false);
  const [reload, setReload] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const opened = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    let live = true;
    // no-store: a restore posts the head this list shows, so the list must be
    // the live head or the restore is a 409 against a revision it never showed
    api<History>(`/api/documents/${artifactId}/revisions`, { cache: "no-store" })
      .then((data) => {
        if (live) setHistory(data);
      })
      .catch((e) => {
        if (live) setFailure(loadError(e));
      });
    return () => {
      live = false;
    };
  }, [artifactId, reload]);

  const open = (revision: number) =>
    api<OneRevision>(`/api/documents/${artifactId}/revisions/${revision}`, { cache: "no-store" })
      .then(setSelected)
      .catch((e) => reportStatus(actionError(e)));

  // after the commit: the revision opens below a list that can be 100 rows
  // long, and a keyboard reader otherwise stays on the button above it
  useEffect(() => {
    if (selected) opened.current?.focus();
  }, [selected]);

  const restore = async (revision: number) => {
    if (!history || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const out = await api<{ revision: number; unchanged: boolean }>(
        `/api/documents/${artifactId}/revisions/${revision}/restore`,
        { method: "POST", body: JSON.stringify({ base_revision: history.head }) },
      );
      reportStatus(
        out.unchanged
          ? `Revision ${revision} is already the current text.`
          : `Restored revision ${revision} as revision ${out.revision}.`,
        "confirmation",
      );
      setSelected(null);
      setReload((n) => n + 1);
      onChanged();
      heading.current?.focus();
    } catch (e) {
      reportStatus(actionError(e));
      // a stale head is the usual refusal: the list must show the revision
      // that is newer, or the next Restore sends the same stale base, and the
      // page must show its text
      setReload((n) => n + 1);
      onChanged();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby={`document-history-${artifactId}`} className="space-y-3">
      <h3
        id={`document-history-${artifactId}`}
        ref={heading}
        tabIndex={-1}
        className="font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3"
      >
        History
      </h3>
      {failure ? (
        <p className="text-sm text-danger">{failure}</p>
      ) : !history ? (
        <p className="text-sm text-ink-3">Loading…</p>
      ) : history.revisions.length === 0 ? (
        <p className="text-sm text-ink-3">
          This document has one revision. Its history starts at its next save.
        </p>
      ) : (
        <ol className="space-y-1">
          {history.revisions.map((r) => (
            <li key={r.revision} className="flex flex-wrap items-center gap-2 text-sm">
              <button
                type="button"
                onClick={() => open(r.revision)}
                aria-current={selected?.revision === r.revision ? "true" : undefined}
                className="rounded-lg px-2 py-1 text-left hover:bg-raised aria-[current=true]:bg-raised"
              >
                Revision {r.revision}
                {r.revision === history.head ? " (current)" : ""}
              </button>
              <span className="text-xs text-ink-3">
                {r.author} · <Origin revision={r} />
                {r.restored_from ? ` · Restored from revision ${r.restored_from}` : ""} ·{" "}
                <time dateTime={r.created_at} title={r.created_at}>
                  {timeAgo(r.created_at)}
                </time>
              </span>
              {r.revision !== history.head ? (
                <button
                  type="button"
                  aria-disabled={busy || undefined}
                  onClick={() => restore(r.revision)}
                  className="min-h-6 rounded-lg border border-line px-2 py-0.5 text-xs text-ink-2 hover:border-line-strong aria-disabled:opacity-50"
                >
                  Restore revision {r.revision}
                </button>
              ) : null}
            </li>
          ))}
        </ol>
      )}
      {selected ? (
        <div className="space-y-2 rounded-lg border border-line p-3">
          <h4 ref={opened} tabIndex={-1} className="text-sm font-medium text-ink">
            Revision {selected.revision}
          </h4>
          <UnifiedDiff diff={selected.diff} label={`Changes in revision ${selected.revision}`} />
          <div className="overflow-x-auto">
            <ArtifactMarkdown markdown={selected.markdown} />
          </div>
        </div>
      ) : null}
    </section>
  );
}

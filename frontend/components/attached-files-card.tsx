"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Card as Section, EmptyState } from "@/components/card";
import { API_URL, actionError, api, bearer, userHeader } from "@/lib/api";
import { reportStatus } from "@/lib/status";
// moved to lib/size.ts so the activity feed can humanize the byte counts
// its ledger rows carry without importing this card's tree
import { size } from "@/lib/size";

type StoredFile = {
  id: number;
  title: string;
  mime: string;
  size: number;
  created_at: string;
};

type FileList = {
  files: StoredFile[];
  used: number;
  quota: number;
  max_file: number;
};


/** The one surface that shows a person their own attached files.
 *
 *  An upload is private, so it appears nowhere else — not in Reports, not in
 *  search, not in export. Without this card the quota is a wall with no door:
 *  the refusal says to delete a file, and nothing tells the reader which
 *  files they have. */
export function AttachedFilesCard({
  headingLevel = 2,
}: {
  headingLevel?: 2 | 3;
} = {}) {
  const [state, setState] = useState<FileList | null>(null);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const triggers = useRef(new Map<number, HTMLButtonElement | null>());
  const heading = useRef<HTMLParagraphElement>(null);

  // the generation guard components/crews-card.tsx uses: a slow first load
  // resolving after a delete-triggered reload would put the deleted file back
  // on screen
  const gen = useRef(0);
  const load = useCallback(() => {
    const mine = ++gen.current;
    api<FileList>("/api/files")
      .then((body) => {
        if (mine !== gen.current) return;
        setState(body);
        setError("");
      })
      .catch((e) => {
        if (mine !== gen.current) return;
        // names the card, not the page: one failed card on /settings must not
        // report that the whole page could not load
        setError(`Cannot load your attached files. ${actionError(e)}`);
      });
  }, []);
  useEffect(load, [load]);

  /** A plain <a href> cannot do this: the API is its own origin and the
   *  download carries identity (X-User, or a bearer token), which a link
   *  never sends. So the bytes are fetched with the same credentials every
   *  other request uses and handed to the browser as a blob — the same shape
   *  the export button in components/backup-card.tsx uses. */
  const download = async (file: StoredFile) => {
    try {
      const auth = await bearer();
      const res = await fetch(`${API_URL}/api/files/${file.id}/download`, {
        headers: {
          ...userHeader(),
          ...(auth ? { Authorization: `Bearer ${auth}` } : {}),
        },
      });
      if (!res.ok)
        throw new Error(`The file did not download (${res.status}).`);
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = file.title;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(actionError(e));
    }
  };

  const remove = async (file: StoredFile) => {
    setBusy(file.id);
    try {
      await api(`/api/files/${file.id}`, { method: "DELETE" });
      setConfirming(null);
      load();
      // the focused "Delete for good" button disappears with its row, which
      // drops focus to <body> and loses a keyboard reader's place. The heading
      // is the nearest thing that still exists after any row goes.
      setTimeout(() => heading.current?.focus(), 0);
      reportStatus(`${file.title} is deleted.`, "confirmation");
    } catch (e) {
      setError(actionError(e));
    } finally {
      setBusy(null);
    }
  };

  const files = state?.files ?? [];
  const percent =
    state && state.quota ? Math.min(100, (state.used / state.quota) * 100) : 0;

  return (
    <Section title="Attached files" headingLevel={headingLevel}>
      <p ref={heading} tabIndex={-1} className="mb-2 text-sm text-ink-3">
        Files you attached to a chat message. Only you can read them, and they
        stay until you delete them.
      </p>
      {error ? <p className="mb-2 text-sm text-danger">{error}</p> : null}
      {state ? (
        <div className="mb-3">
          <div className="mb-1 flex items-baseline justify-between text-xs text-ink-2">
            <span>
              {size(state.used)} of {size(state.quota)} used
            </span>
            <span className="text-ink-3">
              {files.length} file{files.length === 1 ? "" : "s"} ·{" "}
              {size(state.max_file)} per file
            </span>
          </div>
          {/* the meter is decoration over the sentence above it, which is the
              accessible copy — a second reading of the same numbers */}
          <div
            aria-hidden="true"
            className="h-1.5 overflow-hidden rounded-full bg-raised"
          >
            <div
              className="h-full rounded-full bg-thread-solid"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      ) : null}

      {state && files.length === 0 ? (
        <EmptyState>
          Nothing attached yet. The + in a chat composer adds a file.
        </EmptyState>
      ) : null}

      <ul className="divide-y divide-line">
        {files.map((file) => (
          <li key={file.id} className="flex items-center gap-3 py-2 text-sm">
            <span className="min-w-0 flex-1 truncate" title={file.title}>
              {file.title}
            </span>
            <span className="shrink-0 text-xs text-ink-3">
              {size(file.size)}
            </span>
            <button
              onClick={() => download(file)}
              aria-label={`Download ${file.title}`}
              className="shrink-0 text-xs underline text-ink-2"
            >
              Download
            </button>
            {confirming === file.id ? (
              <>
                <button
                  autoFocus
                  disabled={busy === file.id}
                  onClick={() => remove(file)}
                  className="shrink-0 rounded bg-danger-solid px-2 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
                >
                  Delete for good
                </button>
                <button
                  onClick={() => {
                    setConfirming(null);
                    setTimeout(() => triggers.current.get(file.id)?.focus(), 0);
                  }}
                  className="shrink-0 rounded px-2 py-1 text-xs text-ink-2 hover:bg-line"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                ref={(el) => {
                  triggers.current.set(file.id, el);
                }}
                onClick={() => setConfirming(file.id)}
                aria-label={`Delete ${file.title}`}
                className="shrink-0 rounded bg-danger/15 px-2 py-1 text-xs font-medium text-danger hover:bg-danger/20"
              >
                Delete…
              </button>
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}

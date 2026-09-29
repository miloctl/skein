"use client";

import { useEffect, useRef, useState } from "react";

import { ArtifactMarkdown } from "@/components/artifact-markdown";
import { actionError, api, loadError } from "@/lib/api";
import { reportStatus } from "@/lib/status";

/** Edit a document's Markdown as its next revision (PUT /api/documents/{id}).
 *
 *  No editor library: a textarea and the renderer Reports already uses. The
 *  save names the revision it started from, so two people saving at once
 *  cannot overwrite each other: the second gets a 409 and keeps the text. */
export function DocumentEditor({
  artifactId,
  onSaved,
  onCancel,
}: {
  artifactId: number;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [base, setBase] = useState<{ markdown: string; revision: number } | null>(null);
  const [loadFailure, setLoadFailure] = useState("");
  const [draft, setDraft] = useState("");
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let live = true;
    // no-store: the base must be the live head. The page's own read can be a
    // 15-second cached body (lib/api.ts), and a save from it is a 409 against
    // a revision somebody else wrote in the meantime.
    api<{ markdown: string; revision: number }>(`/api/artifacts/${artifactId}`, {
      cache: "no-store",
    })
      .then((data) => {
        if (!live) return;
        setBase({ markdown: data.markdown, revision: data.revision });
        setDraft(data.markdown);
      })
      .catch((e) => {
        if (live) setLoadFailure(loadError(e));
      });
    return () => {
      live = false;
    };
  }, [artifactId]);

  useEffect(() => {
    if (base) area.current?.focus();
  }, [base]);

  const dirty = base !== null && draft !== base.markdown;
  useEffect(() => {
    if (!dirty) return;
    const guard = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  const save = async () => {
    if (!base) return;
    setBusy(true);
    try {
      const out = await api<{ revision: number; unchanged: boolean }>(
        `/api/documents/${artifactId}`,
        {
          method: "PUT",
          body: JSON.stringify({ content: draft, base_revision: base.revision }),
        },
      );
      reportStatus(
        out.unchanged
          ? `Nothing changed. Revision ${out.revision} is the current text.`
          : `Saved revision ${out.revision}.`,
        "confirmation",
      );
      onSaved();
    } catch (e) {
      // the text stays: the refusal names the newer revision, and retyping
      // is the cost a failed save must not add
      reportStatus(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  if (loadFailure) return <p className="text-sm text-danger">{loadFailure}</p>;
  if (!base) return <p className="text-sm text-ink-3">Loading…</p>;

  const toggle = (on: boolean) =>
    "rounded-lg px-3 py-1.5 text-xs font-medium " +
    (on ? "bg-raised text-ink" : "text-ink-3 hover:text-ink");
  return (
    <div className="space-y-3">
      <div role="group" aria-label="Editor view" className="flex gap-1">
        <button type="button" aria-pressed={!preview} onClick={() => setPreview(false)} className={toggle(!preview)}>
          Write
        </button>
        <button type="button" aria-pressed={preview} onClick={() => setPreview(true)} className={toggle(preview)}>
          Preview
        </button>
      </div>
      <label htmlFor={`document-text-${artifactId}`} className={preview ? "sr-only" : "block text-xs text-ink-3"}>
        Document text, Markdown
      </label>
      {/* hidden, never unmounted: the textarea's own undo history lives in the
          element, and Preview must not throw it away */}
      <textarea
        ref={area}
        id={`document-text-${artifactId}`}
        hidden={preview}
        value={draft}
        rows={20}
        onChange={(e) => setDraft(e.target.value)}
        className="w-full rounded-lg border border-line-strong bg-transparent p-3 font-mono text-xs leading-5 outline-none focus:border-thread-solid"
      />
      {preview ? (
        <div className="overflow-x-auto rounded-lg border border-line p-3">
          <ArtifactMarkdown markdown={draft} />
        </div>
      ) : null}
      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy || !draft.trim()}
          onClick={save}
          className="rounded-lg bg-thread-solid px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          Save
        </button>
        <button type="button" onClick={onCancel} className="rounded-lg px-3 py-1.5 text-xs text-ink-3 hover:text-ink">
          Cancel
        </button>
      </div>
    </div>
  );
}

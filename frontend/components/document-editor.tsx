"use client";

import { useEffect, useRef, useState } from "react";

import { ArtifactMarkdown } from "@/components/artifact-markdown";
import { VisibilityPicker } from "@/components/visibility-picker";
import { actionError, api, ApiError, loadError } from "@/lib/api";
import { isTierChoice, ONLY_YOU, ROSTER, useRememberedAudience, useStrongIdentity } from "@/lib/audience";
import { reportStatus } from "@/lib/status";

/** Edit a document's Markdown as its next revision (PUT /api/documents/{id}),
 *  or write a new one with a chosen audience (POST /api/documents) when
 *  `artifactId` is null.
 *
 *  No editor library: a textarea and the renderer Reports already uses. A
 *  save names the revision it started from, so two people saving at once
 *  cannot overwrite each other: the second gets a 409 and keeps the text.
 *
 *  Unsaved text lives in `drafts`, which the page owns, so closing the
 *  editor (History, another report, New document off) keeps it. Only a save
 *  and a confirmed Cancel delete it. */
export type Draft = { text: string; title: string; base: number };

export function DocumentEditor({
  artifactId,
  onSaved,
  onCancel,
  drafts,
}: {
  artifactId: number | null;
  onSaved: (id: number) => void;
  onCancel: () => void;
  drafts: Map<number | "new", Draft>;
}) {
  const creating = artifactId === null;
  const key = artifactId ?? "new";
  const [base, setBase] = useState<{ markdown: string; revision: number } | null>(
    creating ? { markdown: "", revision: 0 } : null,
  );
  const [loadFailure, setLoadFailure] = useState("");
  const [title, setTitle] = useState(() => (creating ? (drafts.get("new")?.title ?? "") : ""));
  const [draft, setDraft] = useState(() => (creating ? (drafts.get("new")?.text ?? "") : ""));
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [confirming, setConfirming] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const titleField = useRef<HTMLInputElement>(null);
  const strong = useStrongIdentity();
  // "only you" first for a signed-in person, as every personal record starts
  // (routes/api.py::_personal_default). A weak identity reads no private row,
  // so it would write a document its own author cannot open.
  const [tier, setTier] = useRememberedAudience(
    "document",
    strong ? ONLY_YOU : ROSTER,
    (v) => isTierChoice(v) && (strong || (v as { visibility: string }).visibility !== "private"),
  );

  useEffect(() => {
    if (artifactId === null) return;
    let live = true;
    // no-store: the base must be the live head. The page's own read can be a
    // 15-second cached body (lib/api.ts), and a save from it is a 409 against
    // a revision somebody else wrote in the meantime.
    api<{ markdown: string; revision: number }>(`/api/artifacts/${artifactId}`, {
      cache: "no-store",
    })
      .then((data) => {
        if (!live) return;
        // a kept draft keeps the revision it was written against: the head
        // read now would let its save write over a revision nobody showed
        const kept = drafts.get(artifactId);
        setBase({ markdown: data.markdown, revision: kept?.base ?? data.revision });
        setDraft(kept?.text ?? data.markdown);
      })
      .catch((e) => {
        if (live) setLoadFailure(loadError(e));
      });
    return () => {
      live = false;
    };
  }, [artifactId, drafts]);

  // once, on load: a 409 moves the base, and focus must stay on Save
  const loaded = base !== null;
  useEffect(() => {
    if (!loaded) return;
    if (creating) titleField.current?.focus();
    else area.current?.focus();
  }, [loaded, creating]);

  const dirty = base !== null && (draft !== base.markdown || title !== "");
  useEffect(() => {
    // not while loading: `dirty` is false then, and a kept draft would go
    if (!base) return;
    if (dirty) drafts.set(key, { text: draft, title, base: base.revision });
    else drafts.delete(key);
  }, [base, dirty, draft, title, drafts, key]);

  const save = async () => {
    if (!base) return;
    setBusy(true);
    try {
      if (artifactId === null) {
        const made = await api<{ id: number }>("/api/documents", {
          method: "POST",
          body: JSON.stringify({
            title,
            content: draft,
            visibility: tier.visibility,
            crew_id: tier.crew_id,
          }),
        });
        drafts.delete(key);
        reportStatus(`Created document #${made.id}.`, "confirmation");
        onSaved(made.id);
        return;
      }
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
      drafts.delete(key);
      onSaved(artifactId);
    } catch (e) {
      // the text stays: the refusal names what to fix (a newer revision, a
      // crew you left), and retyping is the cost a failed save must not add
      reportStatus(actionError(e));
      if (e instanceof ApiError && e.status === 409 && artifactId !== null) await rebase(artifactId);
    } finally {
      setBusy(false);
    }
  };

  // The newer head becomes the base, so a second Save writes the person's
  // text over it on purpose. The newer revision stays in History.
  const rebase = async (id: number) => {
    try {
      const head = await api<{ markdown: string; revision: number }>(`/api/artifacts/${id}`, {
        cache: "no-store",
      });
      setBase({ markdown: head.markdown, revision: head.revision });
      setNotice(
        `Somebody saved revision ${head.revision} after you started. Your text is not saved.` +
          ` To keep your text, select Save again. Revision ${head.revision} stays in History.`,
      );
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const discard = () => {
    drafts.delete(key);
    onCancel();
  };

  if (loadFailure) return <p className="text-sm text-danger">{loadFailure}</p>;
  if (!base) return <p className="text-sm text-ink-3">Loading…</p>;

  const toggle = (on: boolean) =>
    "rounded-lg px-3 py-1.5 text-xs font-medium " +
    (on ? "bg-raised text-ink" : "text-ink-3 hover:text-ink");
  return (
    <div className="space-y-3">
      {creating ? (
        <div className="space-y-2">
          <label htmlFor="document-title-new" className="block text-xs text-ink-3">
            Title
          </label>
          <input
            ref={titleField}
            id="document-title-new"
            value={title}
            maxLength={120}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
          />
          <VisibilityPicker value={tier} onChange={setTier} label="document" allowPrivate={strong} />
        </div>
      ) : null}
      <div role="group" aria-label="Editor view" className="flex gap-1">
        <button type="button" aria-pressed={!preview} onClick={() => setPreview(false)} className={toggle(!preview)}>
          Write
        </button>
        <button type="button" aria-pressed={preview} onClick={() => setPreview(true)} className={toggle(preview)}>
          Preview
        </button>
      </div>
      <label htmlFor={`document-text-${key}`} hidden={preview} className="block text-xs text-ink-3">
        Document text, Markdown
      </label>
      {/* hidden, never unmounted: the textarea's own undo history lives in the
          element, and Preview must not throw it away */}
      <textarea
        ref={area}
        id={`document-text-${key}`}
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
      {notice ? <p className="text-sm text-warn">{notice}</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || !draft.trim()}
          onClick={save}
          className="rounded-lg bg-thread-solid px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {creating ? "Create document" : "Save"}
        </button>
        {confirming ? (
          <>
            <span className="text-xs text-ink-2">Your changes are not saved.</span>
            <button
              type="button"
              autoFocus
              onClick={discard}
              className="rounded-lg bg-danger-solid px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
            >
              Delete my changes
            </button>
            <button
              type="button"
              onClick={() => {
                setConfirming(false);
                area.current?.focus();
              }}
              className="rounded-lg px-3 py-1.5 text-xs text-ink-3 hover:text-ink"
            >
              Keep editing
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => (dirty ? setConfirming(true) : discard())}
            className="rounded-lg px-3 py-1.5 text-xs text-ink-3 hover:text-ink"
          >
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

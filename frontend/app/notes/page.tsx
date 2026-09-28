"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { ArtifactMarkdown } from "@/components/artifact-markdown";
import { Card } from "@/components/card";
import { VisibilityBadge } from "@/components/visibility-picker";
import { WeakIdentityNotice } from "@/components/weak-identity-notice";
import { actionError, api, loadError } from "@/lib/api";
import { useCaptureKeyEnabled } from "@/lib/capture-key";
import { reportStatus } from "@/lib/status";
import { timeAgo } from "@/lib/time";

type Note = {
  id: number;
  topic: string;
  content: string;
  author: string;
  created_at: string;
  visibility: string;
  crew_id: number | null;
};

// GET /api/notes answers a bare list, newest first, this many rows by
// default. A full page is the only sign that an older one can exist.
const PAGE = 25;

function notesPath(q: string, before = 0, limit = 0) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (before) params.set("before", String(before));
  if (limit) params.set("limit", String(limit));
  const query = params.toString();
  return query ? `/api/notes?${query}` : "/api/notes";
}

/** Reports `?note=<id>`, the note a search hit names
 *  (components/nav-search.tsx), or 0 for the list. Router state, not
 *  window.location with popstate: a next/link click to /notes, Back, and
 *  history.replaceState all update it. A soft navigation fires no
 *  popstate, so a view read from popstate outlives the link that left it. It sits
 *  in its OWN Suspense boundary: useSearchParams client-renders up to the
 *  nearest one, and a boundary around the page would prerender it empty,
 *  which is why components/task-peek.tsx avoids the hook. */
function NoteParam({ onChange }: { onChange: (id: number) => void }) {
  const raw = useSearchParams().get("note");
  const id = Number(raw);
  const note = raw && Number.isInteger(id) && id > 0 ? id : 0;
  useEffect(() => onChange(note), [note, onChange]);
  return null;
}

/** Markdown syntax only, so "#42" and "a -> b" read as written. Capped
 *  because the preview sits inside the note's toggle button, and a screen
 *  reader announces the whole text as the button's name. */
function preview(content: string) {
  return content
    .replace(/^\s*(#{1,6}\s|>\s?|[-*+]\s|```\w*)/gm, "")
    .replace(/\*\*|`/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 200);
}

function NoteEditor({
  note,
  onSave,
  onCancel,
}: {
  note: Note;
  onSave: (topic: string, content: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [topic, setTopic] = useState(note.topic);
  const [content, setContent] = useState(note.content);
  const [busy, setBusy] = useState(false);
  // The service reads an empty field as "no change", so a field the reader
  // cleared would save as its old text while the form showed it blank. A
  // field that was empty to begin with stays empty either way.
  const lostTopic = !topic.trim() && Boolean(note.topic.trim());
  const lostContent = !content.trim() && Boolean(note.content.trim());
  const blocked = lostTopic
    ? "The topic is empty. Type a topic to save the note."
    : lostContent
      ? "The content is empty. Type some content to save the note."
      : "";
  const field =
    "mt-0.5 block w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm text-ink outline-none focus:border-thread-solid";
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        if (busy) return;
        // through the page's status region, which is always mounted: a live
        // region inserted with its text already in it is often not read
        if (blocked) {
          reportStatus(blocked);
          return;
        }
        setBusy(true);
        await onSave(topic, content);
        setBusy(false);
      }}
      onKeyDown={(e) => {
        // Escape also ends an IME composition, and closing here would drop
        // the whole edit with it
        if (e.key === "Escape" && !e.nativeEvent.isComposing) onCancel();
      }}
      className="space-y-2"
    >
      <label className="block text-xs text-ink-3">
        Topic
        <input
          autoFocus
          value={topic}
          maxLength={200}
          onChange={(e) => setTopic(e.target.value)}
          className={field}
        />
      </label>
      <label className="block text-xs text-ink-3">
        Note content (markdown)
        <textarea
          rows={8}
          value={content}
          maxLength={20_000}
          onChange={(e) => setContent(e.target.value)}
          className={field}
        />
      </label>
      {blocked && (
        <p id={`note-${note.id}-blocked`} className="text-xs text-danger">
          {blocked}
        </p>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          aria-disabled={busy || Boolean(blocked)}
          aria-describedby={blocked ? `note-${note.id}-blocked` : undefined}
          className="rounded bg-thread-solid px-2 py-1 text-xs font-medium text-white hover:opacity-90 aria-disabled:opacity-40"
        >
          Save note
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded px-2 py-1 text-xs text-ink-3 hover:text-ink"
        >
          Cancel edit
        </button>
      </div>
    </form>
  );
}

export default function NotesPage() {
  const captureKey = useCaptureKeyEnabled();
  const [notes, setNotes] = useState<Note[]>([]);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  // The one note a search hit opened, 0 for the list, and null until
  // NoteParam reports: a load before that fetched the whole list for a page
  // that was about to show one note.
  const [noteId, setNoteId] = useState<number | null>(null);
  const view = noteId ? `note:${noteId}` : `q:${query}`;
  // The view the shown rows answer. Until the rows for a new search
  // arrive, the old ones would read as its results.
  const [rowsFor, setRowsFor] = useState<string | null>(null);
  const [failure, setFailure] = useState<{ for: string; message: string } | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  // A new search replaces the list. An answer to an older search, or an
  // older page for it, must not land in the new one.
  const generation = useRef(0);
  // Focus moves after React commits, never on a timer: a timer can fire
  // before the commit, find no element, and leave focus on <body>.
  const pendingFocus = useRef<string | null>(null);
  useEffect(() => {
    const id = pendingFocus.current;
    if (!id) return;
    pendingFocus.current = null;
    document.getElementById(id)?.focus();
  });
  const focusAfterRender = (id: string) => {
    pendingFocus.current = id;
  };

  const load = useCallback(() => {
    if (noteId === null) return;
    const current = ++generation.current;
    // the newest note older than id + 1 is that note, when the reader can
    // read it, so one note needs no route of its own
    const path = noteId ? notesPath("", noteId + 1, 1) : notesPath(query);
    api<Note[]>(path)
      .then((rows) => {
        if (current !== generation.current) return;
        const shown = noteId ? rows.filter((r) => r.id === noteId) : rows;
        setNotes(shown);
        setHasMore(!noteId && rows.length === PAGE);
        setRowsFor(view);
        setFailure(null);
        if (shown.length && noteId) {
          setOpenId(noteId);
          // a slow answer must not pull focus out of a field the reader
          // has started to use
          const active = document.activeElement;
          if (!active || active === document.body || active.id === "content")
            focusAfterRender(`note-${noteId}-toggle`);
        }
      })
      .catch((e) => {
        if (current === generation.current) setFailure({ for: view, message: loadError(e) });
      });
  }, [noteId, query, view]);
  useEffect(load, [load]);

  // Set here as well as through NoteParam: the URL change reaches router
  // state, but the list must not wait for that round trip. Left in the URL,
  // a reload of /notes?note=<id> opens the one note again.
  const showList = () => {
    setNoteId(0);
    if (new URLSearchParams(window.location.search).has("note"))
      window.history.replaceState(null, "", "/notes");
  };

  // Only the notes newer than the top row are added. A reload would drop
  // every older page and unmount an open editor with its unsaved text.
  const mergeNewest = useCallback(() => {
    const current = generation.current;
    api<Note[]>(notesPath(query))
      .then((rows) => {
        if (current !== generation.current) return;
        setNotes((cur) => [...rows.filter((r) => r.id > (cur[0]?.id ?? 0)), ...cur]);
        setFailure(null);
      })
      .catch((e) => {
        if (current === generation.current) setFailure({ for: view, message: loadError(e) });
      });
  }, [query, view]);

  // A capture from any page can file a note, and the capture palette
  // announces every write with this event (lib/attention.ts). An empty list
  // has no top row to merge under, so it loads, which also sets hasMore.
  const empty = notes.length === 0;
  useEffect(() => {
    // one note opened from search is not a list a capture can join
    if (noteId) return;
    const onChange = empty ? load : mergeNewest;
    window.addEventListener("skein-attention-change", onChange);
    return () => window.removeEventListener("skein-attention-change", onChange);
  }, [noteId, empty, load, mergeNewest]);

  // GET /api/notes also serves Work → Browse, so this page ties its own
  // field-guide card. A failed mark leaves the card untied, nothing more.
  useEffect(() => {
    api("/api/field-guide/notes", { method: "POST" }).catch(() => {});
  }, []);

  const more = async () => {
    const last = notes[notes.length - 1];
    if (!last || loadingMore) return;
    const current = generation.current;
    setLoadingMore(true);
    try {
      const rows = await api<Note[]>(notesPath(query, last.id));
      if (current !== generation.current) return;
      setNotes((cur) => [...cur, ...rows]);
      setHasMore(rows.length === PAGE);
      setFailure(null);
      // the button leaves with the last page, and its focus with it
      if (rows.length < PAGE) focusAfterRender(rows[0] ? `note-${rows[0].id}-toggle` : "content");
    } catch (e) {
      if (current === generation.current) setFailure({ for: view, message: loadError(e) });
    } finally {
      setLoadingMore(false);
    }
  };

  const save = async (note: Note, topic: string, content: string) => {
    // only what changed: every write lands in the hash-chained ledger and
    // rescans the text for @mentions
    const changed: Partial<Note> = {};
    if (topic !== note.topic) changed.topic = topic;
    if (content !== note.content) changed.content = content;
    if (Object.keys(changed).length) {
      try {
        await api(`/api/notes/${note.id}`, {
          method: "PATCH",
          body: JSON.stringify(changed),
        });
      } catch (e) {
        reportStatus(actionError(e));
        return;
      }
      setNotes((cur) => cur.map((n) => (n.id === note.id ? { ...n, ...changed } : n)));
    }
    setEditingId(null);
    focusAfterRender(`note-${note.id}-edit`);
  };

  const remove = async (id: number) => {
    try {
      await api(`/api/notes/${id}`, { method: "DELETE" });
      setNotes((cur) => cur.filter((n) => n.id !== id));
      setDeletingId(null);
      setOpenId(null);
      if (noteId === id) showList();
      // the last loaded row gone with older pages still unread: the empty
      // state would claim there are no notes, so load the next page instead
      else if (notes.length === 1 && hasMore) load();
      reportStatus("Note deleted.", "confirmation");
      focusAfterRender("content");
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const cancelDelete = (id: number) => {
    setDeletingId(null);
    focusAfterRender(`note-${id}-delete`);
  };

  const failed = failure?.for === view ? failure.message : null;
  const current = rowsFor === view;

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6"
    >
      <div className="mb-4">
        <h1 className="font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
          Notes
        </h1>
        <p className="mt-0.5 text-sm text-ink-3">
          Every note you can read, newest first. With a key or a sign-in, the
          agent in your own chat can search your private notes too.
        </p>
      </div>

      <WeakIdentityNotice className="mb-3" />
      <Suspense fallback={null}>
        <NoteParam onChange={setNoteId} />
      </Suspense>

      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          const next = draft.trim();
          // the same words set no new state and would send nothing, so a
          // failed search could never be tried again
          if (!noteId && next === query) load();
          showList();
          setQuery(next);
        }}
        className="mb-3 flex gap-2"
      >
        <input
          type="search"
          name="q"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          aria-label="Search notes"
          placeholder="Search notes"
          className="min-w-0 flex-1 rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
        />
        <button
          type="submit"
          className="shrink-0 rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        >
          Search
        </button>
      </form>

      {Boolean(noteId) && (
        <p className="mb-3 text-sm text-ink-2">
          This is one note from search.{" "}
          <button
            onClick={() => {
              showList();
              focusAfterRender("content");
            }}
            className="font-medium text-thread underline"
          >
            Show all notes
          </button>
        </p>
      )}
      {failed && <p className="mb-3 text-sm text-danger">{failed}</p>}
      {!current && !failed && (
        <Card>
          <p className="text-sm text-ink-3">Loading…</p>
        </Card>
      )}
      {current && !failed && notes.length === 0 && (
        <Card>
          <p className="text-sm text-ink-3">
            {noteId
              ? `There is no note #${noteId} that you can read.`
              : query
              ? "No notes match this search."
              : `No notes yet. ${captureKey ? "Press C or select Capture" : "Select Capture"}, then start the line with note: and it lands here.`}
          </p>
        </Card>
      )}

      {current && notes.length > 0 && (
        <Card>
          <ul className="divide-y divide-line">
            {notes.map((n) => {
              const open = openId === n.id;
              const title = n.topic || "Untitled note";
              return (
                <li key={n.id} className="py-2">
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    {/* the whole first line at phone width, so the badge and
                        the time wrap under the title instead of squeezing it
                        to one word per line */}
                    <button
                      id={`note-${n.id}-toggle`}
                      onClick={() => {
                        setOpenId(open ? null : n.id);
                        setEditingId(null);
                        setDeletingId(null);
                      }}
                      aria-expanded={open}
                      aria-controls={`note-${n.id}-body`}
                      className="min-w-0 basis-full text-left sm:basis-0 sm:flex-1"
                    >
                      <span className="font-medium text-ink [overflow-wrap:anywhere]">
                        {title}
                      </span>
                      {!open && (
                        <span className="block truncate text-xs text-ink-3">
                          {preview(n.content)}
                        </span>
                      )}
                    </button>
                    <VisibilityBadge
                      visibility={n.visibility}
                      crewId={n.crew_id ?? undefined}
                      share={{
                        kind: "notes",
                        id: n.id,
                        label: title,
                        onShared: () =>
                          setNotes((cur) =>
                            cur.map((row) =>
                              row.id === n.id
                                ? { ...row, visibility: "workspace", crew_id: null }
                                : row,
                            ),
                          ),
                      }}
                    />
                    <time
                      dateTime={n.created_at}
                      title={n.created_at}
                      className="shrink-0 text-xs text-ink-3"
                    >
                      {timeAgo(n.created_at)}
                    </time>
                  </div>
                  {open && (
                    <div
                      id={`note-${n.id}-body`}
                      className="mt-2 min-w-0 rounded-lg bg-raised/40 px-3 py-2 [overflow-wrap:anywhere]"
                    >
                      {editingId === n.id ? (
                        <NoteEditor
                          note={n}
                          onSave={(topic, content) => save(n, topic, content)}
                          onCancel={() => {
                            setEditingId(null);
                            focusAfterRender(`note-${n.id}-edit`);
                          }}
                        />
                      ) : (
                        <>
                          <ArtifactMarkdown markdown={n.content} />
                          {n.author && (
                            <p className="mt-2 text-xs text-ink-3">By {n.author}</p>
                          )}
                          <div className="mt-2 flex flex-wrap items-start gap-2">
                            <button
                              id={`note-${n.id}-edit`}
                              aria-label={`Edit note: ${title}`}
                              onClick={() => {
                                setDeletingId(null);
                                setEditingId(n.id);
                              }}
                              className="min-h-6 min-w-6 rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                            >
                              edit…
                            </button>
                            {deletingId === n.id ? (
                              <span
                                onKeyDown={(e) => e.key === "Escape" && cancelDelete(n.id)}
                                className="flex max-w-sm flex-col gap-1 text-xs"
                              >
                                <span id={`note-${n.id}-consequence`}>
                                  Delete this note? It will leave the knowledge base and
                                  search. The activity record can keep its topic, and
                                  backups can keep the note.
                                </span>
                                <span className="flex gap-3 md:gap-1.5">
                                  <button
                                    autoFocus
                                    aria-describedby={`note-${n.id}-consequence`}
                                    onClick={() => remove(n.id)}
                                    className="rounded bg-danger-solid px-2 py-1.5 font-medium text-white hover:opacity-90 md:py-0.5"
                                  >
                                    Delete note
                                  </button>
                                  <button
                                    onClick={() => cancelDelete(n.id)}
                                    className="rounded px-2 py-0.5 text-ink-3 hover:text-ink"
                                  >
                                    Cancel deletion
                                  </button>
                                </span>
                              </span>
                            ) : (
                              <button
                                id={`note-${n.id}-delete`}
                                aria-label={`Delete note: ${title}`}
                                onClick={() => setDeletingId(n.id)}
                                className="min-h-6 min-w-6 rounded bg-raised px-2 py-0.5 text-xs text-danger hover:bg-line"
                              >
                                delete…
                              </button>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {hasMore && (
            // aria-disabled, not disabled: Chrome blurs a focused button the
            // moment it turns disabled, and a keyboard reader lands on <body>
            <button
              onClick={more}
              aria-disabled={loadingMore}
              className="mt-3 w-full rounded-lg border border-line py-1.5 text-xs text-ink-2 hover:bg-raised aria-disabled:opacity-50"
            >
              {loadingMore ? "Loading…" : "Older notes"}
            </button>
          )}
        </Card>
      )}
    </main>
  );
}

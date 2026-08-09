"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { actionError, api } from "@/lib/api";
import { PeekLink } from "@/components/task-peek";
import { Shortcut } from "@/components/shortcut";

/** Search and /ask, in the nav.
 *
 *  This box is the only consumer of GET /api/search and GET /api/ask. With
 *  no surface, the answer to "where did we decide that" is Slack scrollback,
 *  which is the leak Skein exists to close.
 *
 *  One input, two backends. A leading `?` asks /api/ask, which answers with
 *  citations rather than rows; anything else searches. The prefix is the same
 *  grammar the capture palette already teaches, so it costs no new concept.
 */

type Hit = { entity: string; entity_id: number; title: string; snippet: string };
type Citation = { ref: string; title: string; snippet: string };
type Answer = { question: string; citations: Citation[]; note: string };

/** FTS5 wraps matches in <b>. Rendered as HTML that would be an injection
 *  sink for every indexed row, so the tags are parsed into text runs and the
 *  emphasis is applied by React. Anything that is not a tag stays literal. */
function Snippet({ text }: { text: string }) {
  // A capturing split alternates plain, marked, plain, marked… so the
  // emphasis is derived from the index rather than carried in a flag the
  // render mutates. An unclosed <b> matches nothing and stays literal text,
  // which is the safe direction to fail. [\s\S] rather than the /s flag:
  // the tsconfig target predates it, and a snippet can carry a newline.
  const parts = String(text ?? "").split(/<b>([\s\S]*?)<\/b>/);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 ? (
          <mark key={i} className="bg-thread/20 text-ink">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

/** entity -> the page that lists it, for every indexed entity except task
 *  (the peek) and lesson (no list surface yet — /dashboard shows only a
 *  count, so a link would promise a row the page cannot show). An entity
 *  missing here renders as dead text, which is the exact complaint this
 *  table exists to close — services/search.py::_ENTITY_TABLE is the list
 *  to mirror when a new entity is indexed. */
const ENTITY_PAGE: Record<string, string> = {
  blocker: "/",
  decision: "/charter",
  engagement: "/dashboard",
  event: "/dashboard",
  intake: "/intake",
  memory: "/agents",
  milestone: "/dashboard",
  note: "/dashboard",
  promise: "/portfolio",
  question: "/dashboard",
  standup: "/dashboard",
};

/** `#42` and `task 42` already jump straight to the row server-side, and the
 *  peek is where a task belongs — so a task hit opens the panel instead of
 *  dropping the reader on a page to hunt for it. Every other entity is a
 *  link to the page that lists it. */
function EntityLink({
  entity,
  entityId,
  onDone,
  children,
}: {
  entity: string;
  entityId: number;
  onDone: () => void;
  children: React.ReactNode;
}) {
  if (entity === "task")
    return (
      // onDone on the BUTTON, not a wrapping span: on the span, a click
      // landing in its padding closed the dropdown without opening anything
      <PeekLink taskId={entityId} onActivate={onDone}>
        {children}
      </PeekLink>
    );
  // charter rows carry id="charter-entry-N" (app/charter/page.tsx), so an
  // already-loaded charter scrolls to the row; a fresh navigation lands at
  // the top because the rows are not in the DOM when the scroll fires.
  const page =
    entity === "decision"
      ? `${ENTITY_PAGE.decision}#charter-entry-${entityId}`
      : ENTITY_PAGE[entity];
  if (!page) return <span>{children}</span>;
  return (
    <Link
      href={page}
      onClick={onDone}
      className="underline decoration-line-strong underline-offset-2 hover:decoration-ink-3"
    >
      {children}
    </Link>
  );
}

/** `task #12` in a citation becomes a peek link, `decision #3` a charter
 *  link, and so on through ENTITY_PAGE. Parsed rather than typed, because
 *  /api/ask returns `ref` as one string (services/search.py) and the entity
 *  is the half before the number. */
function CitationRef({
  refText,
  title,
  onDone,
}: {
  refText: string;
  title: string;
  onDone: () => void;
}) {
  const label = (
    <>
      <span className="text-ink-3">{refText}</span> {title}
    </>
  );
  const m = /^([a-z]+) #(\d+)$/.exec(refText.trim());
  if (!m) return label;
  return (
    <EntityLink entity={m[1]} entityId={Number(m[2])} onDone={onDone}>
      {label}
    </EntityLink>
  );
}

/** An empty result is the moment read intent turns into write intent: the
 *  reader looked for a record, there is none, and filing one is the next
 *  useful move. Outside the nav button this is the only place the shortcut
 *  is taught, and it hides on touch — where the nav's Capture button is the
 *  door and no key exists to press (same split as the empty task list in
 *  app/page.tsx). */
function CaptureHint() {
  return (
    <p className="mt-1 text-xs text-ink-3">
      To file a new record, use quick capture
      <span className="[@media(any-pointer:coarse)]:hidden">
        {" ("}
        <Shortcut />
        {")"}
      </span>
      .
    </p>
  );
}

export function NavSearch() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const run = async () => {
    const query = q.trim();
    if (!query) return;
    setBusy(true);
    setError("");
    setOpen(true);
    const asking = query.startsWith("?");
    try {
      if (asking) {
        setHits(null);
        setAnswer(await api<Answer>(`/api/ask?q=${encodeURIComponent(query.slice(1).trim())}`));
      } else {
        setAnswer(null);
        setHits(await api<Hit[]>(`/api/search?q=${encodeURIComponent(query)}`));
      }
    } catch (e) {
      setError(actionError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={boxRef} className="relative">
      <label className="sr-only" htmlFor="nav-search">
        Search Skein, or start with ? to ask a question
      </label>
      <input
        id="nav-search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && run()}
        onFocus={() => (hits || answer || error) && setOpen(true)}
        placeholder="Search — or ? to ask"
        className="w-36 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-xs outline-none focus:border-thread-solid sm:w-52"
      />
      {open && (
        <div
          role="region"
          aria-label="Search results"
          // a landmark announces NOTHING when it appears, so pressing Enter
          // was met with silence — no result count, no "nothing matches", no
          // error. Not a combobox: activation here is Tab-then-Enter, and the
          // role would promise arrow-key navigation that does not exist.
          aria-live="polite"
          aria-busy={busy}
          className="absolute right-0 z-50 mt-1 max-h-96 w-80 overflow-y-auto rounded-xl border border-line bg-card p-3 shadow-card sm:w-96"
        >
          {busy ? (
            <p className="text-sm text-ink-3">Searching…</p>
          ) : error ? (
            <p className="text-sm text-danger">{error}</p>
          ) : answer ? (
            <>
              {/* every answer is snippets citing a row — the product never
                  asserts a fact it cannot point at */}
              {answer.citations.length === 0 ? (
                <>
                  <p className="text-sm text-ink-3">
                    {answer.note || "Nothing matches those words."}
                  </p>
                  <CaptureHint />
                </>
              ) : (
                <ul className="space-y-2">
                  {answer.citations.map((c) => (
                    <li key={c.ref} className="text-sm">
                      {/* a citation names a row; the ones that are tasks land
                          in the peek, like the search hits below. Answering
                          with a reference the reader cannot open is the dead
                          end this box was built to close. */}
                      <CitationRef refText={c.ref} title={c.title} onDone={() => setOpen(false)} />
                      <p className="text-xs text-ink-3">
                        <Snippet text={c.snippet} />
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : hits === null ? null : hits.length === 0 ? (
            <>
              <p className="text-sm text-ink-3">Nothing matches those words.</p>
              <CaptureHint />
            </>
          ) : (
            <ul className="space-y-2">
              {hits.map((h) => (
                <li key={`${h.entity}-${h.entity_id}`} className="text-sm">
                  <EntityLink
                    entity={h.entity}
                    entityId={h.entity_id}
                    onDone={() => setOpen(false)}
                  >
                    <span className="text-ink-3">
                      {h.entity} #{h.entity_id}
                    </span>{" "}
                    {h.title}
                  </EntityLink>
                  {h.snippet ? (
                    <p className="text-xs text-ink-3">
                      <Snippet text={h.snippet} />
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

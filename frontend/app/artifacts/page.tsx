"use client";

import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ArtifactMarkdown } from "@/components/artifact-markdown";
import { Card, EmptyState } from "@/components/card";
import { SectionTabs } from "@/components/section-tabs";
import { api, loadError } from "@/lib/api";
import { timeAgo } from "@/lib/time";

/** Reports: the record of every ritual that already ran.
 *
 *  The digest, the Monday brief, the Friday close-out, the exec readout and
 *  handoff packages were all being written daily and weekly to files under
 *  data/artifacts/ with a row pointing at each one — and no surface read them
 *  back. The digest in particular had NO reader at all: the scheduler wrote it
 *  at 07:00 every day and the only way to see one was to shell into the
 *  container. The two that did have buttons dumped raw markdown into a <pre>.
 *
 *  Composition only. Nothing here generates an artifact; the rituals that
 *  produce them keep their own homes on Work → Health. */

type Artifact = {
  id: number;
  engagement_id: number | null;
  kind: string;
  title: string;
  path: string;
  created_by: string;
  created_at: string;
};

type Body = Artifact & { markdown: string };

/** What each kind IS, in the words the rest of the app uses for it. A bare
 *  `readout` is a column value, not a name a reader has met.
 *
 *  These four are every kind anything writes — digest.py, readout.py,
 *  handoff.py and rituals.py are the only INSERTs into `artifacts`. Both week
 *  rituals share the single kind `ritual` and are told apart by their titles
 *  ("Week open …" / "Week close-out …"), so there is deliberately no entry per
 *  ritual: one would render for no row. A kind absent here falls through to
 *  itself rather than to a guess. */
const KIND_LABEL: Record<string, string> = {
  digest: "Daily digest",
  readout: "Exec readout",
  handoff: "Handoff",
  ritual: "Week ritual",
};

const kindLabel = (kind: string) => KIND_LABEL[kind] ?? kind;

const PARAM = "id";

/** services/handoff.py::list_artifacts caps the unfiltered read here. Kept in
 *  step by the card title, which stops calling the length a total once the
 *  response is exactly this long. */
const ARTIFACT_PAGE = 50;

/** window.location, not useSearchParams: the latter puts the route behind a
 *  Suspense boundary for a value that is never prerendered — the reasoning
 *  components/task-peek.tsx and app/auth/callback record. */
function idFromUrl(): number | null {
  if (typeof window === "undefined") return null;
  const raw = new URLSearchParams(window.location.search).get(PARAM);
  const id = Number(raw);
  return raw && Number.isInteger(id) && id > 0 ? id : null;
}

export default function ArtifactsPage() {
  // null until the fetch settles: [] would render "No report yet" during the
  // first paint and again after a failed load — a verdict about data that
  // never arrived (the idiom app/portfolio/page.tsx records).
  const [list, setList] = useState<Artifact[] | null>(null);
  const [listError, setListError] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  // Both carry the id they belong to. A second click while the first read is
  // in flight resolves in whatever order the network returns, and keying the
  // result means a late arrival is ignored at RENDER time — no clearing on the
  // way in, which is what would make this a cascading-render effect.
  const [body, setBody] = useState<{ id: number; data: Body } | null>(null);
  const [bodyError, setBodyError] = useState<{ id: number; message: string } | null>(
    null,
  );

  useEffect(() => {
    api<Artifact[]>("/api/artifacts")
      .then((rows) => {
        setList(rows);
        setListError("");
        // A link from elsewhere names an artifact; anything else opens the
        // newest, because a reader arriving at Reports wants today's, not a
        // list to click before reading anything.
        const first = idFromUrl() ?? rows[0]?.id ?? null;
        setOpenId((cur) => cur ?? first);
        // REPLACE, so the first history entry already names what is open.
        // Pushed instead, Back would return to a bare /artifacts, and popstate
        // would read no id and leave the pane loading with nothing selected.
        if (first !== null && idFromUrl() === null) {
          const url = new URL(window.location.href);
          url.searchParams.set(PARAM, String(first));
          window.history.replaceState({}, "", url);
        }
      })
      .catch((e) => {
        // stays null: [] would render "No report yet" beside the failure — a
        // verdict about data that never arrived, which is the exact thing the
        // null-until-settled state above exists to prevent
        setListError(loadError(e));
      });
  }, []);

  useEffect(() => {
    if (openId === null) return;
    const id = openId;
    api<Body>(`/api/artifacts/${id}`)
      .then((data) => setBody({ id, data }))
      .catch((e) => setBodyError({ id, message: loadError(e) }));
  }, [openId]);

  const open = useCallback((id: number) => {
    setOpenId(id);
    // a retry of the report that just failed must look like a retry: without
    // this the keyed error still matches and the pane shows the old failure
    // with no loading state, so the click reads as a no-op
    setBodyError((cur) => (cur?.id === id ? null : cur));
    // a real history entry, so Back returns to the previously read report and
    // the URL can be pasted to a teammate
    const url = new URL(window.location.href);
    url.searchParams.set(PARAM, String(id));
    window.history.pushState({}, "", url);
  }, []);

  useEffect(() => {
    const onPop = () => setOpenId(idFromUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // The Reports section tab is a same-route <Link>, so this component never
  // remounts and popstate never fires — the pane kept its open report while
  // the URL lost `?id=`. This page exists to be pasted to a teammate, and the
  // tab it renders itself defeated that. `usePathname()` changes identity on
  // every same-route navigation, which is the signal the URL alone does not
  // give.
  const pathname = usePathname();
  useEffect(() => {
    if (openId === null || idFromUrl() !== null) return;
    const url = new URL(window.location.href);
    url.searchParams.set(PARAM, String(openId));
    window.history.replaceState({}, "", url);
  }, [pathname, openId]);

  // only the result that belongs to the report currently open — a body or an
  // error left over from the previous pick renders as neither
  const shown = body?.id === openId ? body.data : null;
  const failure = bodyError?.id === openId ? bodyError.message : "";

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <SectionTabs set="work" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Reports
      </h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Every digest, brief, close-out, readout and handoff the team has
        produced. Skein writes these on a schedule — this is where you read
        them.
      </p>

      {/* One state at a time. The failure is not "still loading" — it is where
          the loading stopped — and printing both leaves the reader waiting for
          a list that is never coming (__tests__/loading-states.test.tsx). */}
      {listError ? (
        <p className="text-sm text-danger">{listError}</p>
      ) : list === null ? (
        <p className="text-sm text-ink-3">Loading…</p>
      ) : list.length === 0 ? (
        <EmptyState>
          No report yet. The daily digest files one at 07:00, and the week
          rituals file one on Monday and one on Friday.
        </EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]">
          {/* `list_artifacts` returns `ORDER BY id DESC LIMIT 50`, and the
              digest files one every day — so past 50 this is a page, not a
              total, and a bare count would read as one (services/handoff.py) */}
          <Card
            title={
              list.length >= ARTIFACT_PAGE
                ? `Reports (newest ${list.length})`
                : `Reports (${list.length})`
            }
          >
            <ul className="space-y-1">
              {list.map((a) => (
                <li key={a.id}>
                  <button
                    type="button"
                    onClick={() => open(a.id)}
                    aria-current={a.id === openId ? "true" : undefined}
                    className={
                      "w-full rounded-lg px-2 py-1.5 text-left text-sm transition-colors " +
                      (a.id === openId
                        ? "bg-raised font-medium text-ink"
                        : "text-ink-2 hover:bg-raised")
                    }
                  >
                    {a.title}
                    <span className="block text-xs text-ink-3">
                      {kindLabel(a.kind)} ·{" "}
                      <time dateTime={a.created_at} title={a.created_at}>
                        {timeAgo(a.created_at)}
                      </time>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>

          {/* the list and the pane are a master/detail pair: picking a row
              replaces this whole card with no focus move, so a screen reader
              is told the new report arrived rather than left to go looking */}
          <Card
            title={shown ? kindLabel(shown.kind) : "Report"}
            className="min-w-0"
          >
            {/* the ANNOUNCEMENT is this line, not the document below it: a
                live region wrapped around the body re-reads the whole report
                on every pick, which is the opposite of telling a reader that
                a new one arrived */}
            <p aria-live="polite" className="sr-only">
              {failure
                ? failure
                : shown
                  ? `${kindLabel(shown.kind)}: ${shown.title}`
                  : "Loading the report."}
            </p>
            {failure ? (
              <p className="text-sm text-danger">{failure}</p>
            ) : !shown ? (
              <p className="text-sm text-ink-3">Loading…</p>
            ) : (
              <>
                <p className="mb-3 text-xs text-ink-3">
                  Filed by {shown.created_by} ·{" "}
                  <time dateTime={shown.created_at} title={shown.created_at}>
                    {timeAgo(shown.created_at)}
                  </time>
                </p>
                {/* the body is wide content (long lines, indented lists), so it
                    scrolls inside its own box rather than pushing the page
                    sideways */}
                <div className="overflow-x-auto">
                  <ArtifactMarkdown markdown={shown.markdown} />
                </div>
              </>
            )}
          </Card>
        </div>
      )}
    </main>
  );
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { actionError, api, loadError, subscribeUser } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { SectionTabs } from "@/components/section-tabs";
import { Card, EmptyState } from "@/components/card";

type Note = {
  id: number;
  person: string;
  kind: "note" | "feedback";
  body: string;
  created_at: string;
};

type Brief = {
  person: string;
  since: string;
  standups: { id: number; created_at: string; yesterday: string; today: string; blockers: string }[];
  open_blockers: { id: number; title: string; status: string }[];
  open_questions: { id: number; question: string }[];
  in_progress: { id: number; title: string; updated_at: string }[];
  recently_done: { id: number; title: string; completed_at: string }[];
  promises_made: { id: number; promise: string; status: string }[];
  feedback_gap_days: number | null;
  nudge: string;
};

type User = { name: string; kind: string };

export default function PeoplePage() {
  const [people, setPeople] = useState<User[] | null>(null);
  const [peopleError, setPeopleError] = useState("");
  const [person, setPerson] = useState("");
  const [notes, setNotes] = useState<Note[]>([]);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [briefError, setBriefError] = useState("");
  const [draft, setDraft] = useState("");
  const [kind, setKind] = useState<"note" | "feedback">("note");
  const [error, setError] = useState<string | null>(null);
  const [strong, setStrong] = useState<boolean | null>(null);
  // last-request-wins: clicking Alice then Bob quickly must never render
  // Alice's private notes under Bob's chip
  const generation = useRef(0);
  const identityGeneration = useRef(0);

  useEffect(() => {
    const refreshIdentity = () => {
      const current = ++identityGeneration.current;
      ++generation.current;
      setStrong(null);
      setPerson("");
      setNotes([]);
      setBrief(null);
      setBriefError("");
      setError(null);
      api<{ strong: boolean }>("/api/whoami")
        .then((identity) => {
          // A response started before a key or sign-in change belongs to the
          // previous owner and must not restore that owner's private state.
          if (current === identityGeneration.current) setStrong(identity.strong);
        })
        .catch(() => {
          if (current === identityGeneration.current) setStrong(null);
        });
    };
    refreshIdentity();
    api<User[]>("/api/users")
      .then((rows) => {
        setPeople(rows);
        setPeopleError("");
      })
      .catch((e) => {
        setPeople(null);
        setPeopleError(loadError(e));
      });
    return subscribeUser(refreshIdentity);
  }, []);
  const load = useCallback((p: string) => {
    if (!p) return;
    const g = ++generation.current;
    api<Note[]>(`/api/private/notes?person=${encodeURIComponent(p)}`)
      .then((n) => {
        if (g !== generation.current) return;
        setNotes(n);
        setError(null);
      })
      .catch((e) => {
        if (g === generation.current) setError(loadError(e));
      });
    api<Brief>(`/api/private/brief/${encodeURIComponent(p)}`)
      .then((b) => {
        if (g !== generation.current) return;
        setBrief(b);
        setBriefError("");
      })
      .catch((e) => {
        // a refused or failed fetch is not an empty brief: null used to
        // render "no brief available", a claim about data never received
        if (g !== generation.current) return;
        setBrief(null);
        setBriefError(loadError(e));
      });
  }, []);

  useEffect(() => {
    // never under weak identity: both endpoints refuse it, and firing them
    // rendered the same refusal twice — once per card — over a notes form
    // whose submit was going to collect a third copy
    if (person && strong !== false) load(person);
  }, [person, strong, load]);

  const [saving, setSaving] = useState(false);
  const teammates = people?.filter((user) => user.kind !== "agent") ?? [];

  const addNote = async () => {
    if (saving || !draft.trim() || !person) return;
    setSaving(true); // a held Enter must not file N private notes
    try {
      await api("/api/private/notes", {
        method: "POST",
        body: JSON.stringify({ person, body: draft, kind }),
      });
      setDraft("");
      load(person);
    } catch (e) {
      setError(actionError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <SectionTabs set="team" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">1:1s</h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Private 1:1 prep and feedback journal. Only you can read what you
        write here — it lives outside search, digests, context packs, exports, and
        every agent surface.
      </p>

      {strong === false && (
        <div className="mb-4 rounded-xl border border-weld/40 bg-weld/10 p-4 text-sm text-weld">
          <p>
            Private notes require strong identity. If deployment sign-in is
            available, use it. Otherwise, use a personal API key. Open{" "}
            <a href="/settings" className="font-medium underline">
              Settings
            </a>{" "}
            to request or save a key.
          </p>
          <button
            onClick={async () => {
              try {
                // reads already_pending, like Settings does for the same call:
                // ignoring it claimed a fresh request every time, so clicking
                // twice reported two requests where the backend filed one.
                // Same wording as Settings — one condition, one wording.
                const r = await api<{ already_pending: boolean }>("/api/keys/request", {
                  method: "POST",
                });
                setError(null);
                reportStatus(
                  r.already_pending
                    ? "Already asked — the request is still on the team's My Day."
                    : "Asked — the request (with the exact command) is now on the team's My Day.",
                  "confirmation",
                );
              } catch (e) {
                setError(actionError(e));
              }
            }}
            className="mt-2 rounded-lg bg-weld-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90"
          >
            Request a key
          </button>
        </div>
      )}

      {/* nothing below the identity banner renders under weak identity: the
          picker fired two requests that both refused, and the notes form
          collected an entry whose submit was going to refuse too — a wall of
          the same sentence three times, around a dead control */}
      {strong !== false && (
        <>
          {people === null ? (
            <p
              role={peopleError ? "alert" : "status"}
              className={peopleError ? "text-sm text-danger" : "text-sm text-ink-3"}
            >
              {peopleError || "Loading the team roster…"}
            </p>
          ) : teammates.length === 0 ? (
            <EmptyState>No teammates are on the roster.</EmptyState>
          ) : (
            <>
      <div className="mb-6 flex flex-wrap gap-2">
        {teammates.map((u) => (
            <button
              key={u.name}
              onClick={() => {
                if (u.name === person) return; // no-op switch would blank the panel
                // clear before switching: stale content here would be another
                // person's PRIVATE notes under the wrong name, and an errored
                // fetch would leave them there indefinitely
                setNotes([]);
                setBrief(null);
                setBriefError("");
                setPerson(u.name);
              }}
              className={
                "rounded-full px-3 py-1 text-sm " +
                (person === u.name
                  ? "bg-thread-solid text-white"
                  : "bg-raised text-ink-2 hover:bg-line")
              }
            >
              {u.name}
            </button>
          ))}
      </div>

      {error && <p className="mb-4 text-sm text-danger">{error}</p>}

      {!person && (
        <EmptyState>
          Pick a teammate above to see their brief and your private notes.
        </EmptyState>
      )}

      {person && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Card title="Since last time">
            {brief === null ? (
              briefError ? (
                <p className="text-sm text-danger">{briefError}</p>
              ) : (
                <p className="text-sm text-ink-3">Loading…</p>
              )
            ) : (
              <div className="space-y-3 text-sm">
                {brief.nudge && (
                  <p className="rounded-lg bg-weld/10 px-3 py-2 text-weld">
                    {brief.nudge}
                  </p>
                )}
                <BriefList
                  title="Open blockers"
                  items={brief.open_blockers.map((b) => `#${b.id} ${b.title}`)}
                />
                <BriefList
                  title="Questions waiting on them"
                  items={brief.open_questions.map((q) => `#${q.id} ${q.question}`)}
                />
                <BriefList
                  title="In progress"
                  items={brief.in_progress.map((t) => `#${t.id} ${t.title}`)}
                />
                <BriefList
                  title="Recently done"
                  items={brief.recently_done.map((t) => `#${t.id} ${t.title}`)}
                />
                <BriefList
                  title="Recent standups"
                  items={brief.standups.map((s) => `${s.created_at.slice(0, 10)}: ${s.today}`)}
                />
                <BriefList
                  title="Promises they made"
                  items={brief.promises_made.map((c) => `${c.promise} (${c.status})`)}
                />
              </div>
            )}
          </Card>

          <Card title="Your private notes">
            <div className="mb-3 flex gap-2">
              <select
                aria-label="Note type"
                value={kind}
                onChange={(e) => setKind(e.target.value as "note" | "feedback")}
                className="rounded-lg border border-line-strong bg-transparent px-2 py-1.5 text-sm focus:border-thread-solid"
              >
                <option value="note">1:1 note</option>
                <option value="feedback">feedback</option>
              </select>
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addNote()}
                aria-label={kind === "feedback" ? "Feedback note" : "1:1 note"}
                placeholder={kind === "feedback" ? "great pushback in design review…" : "agenda item, observation…"}
                className="flex-1 rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
              />
              <button
                onClick={addNote}
                className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
              >
                Add
              </button>
            </div>
            <ul className="space-y-2">
              {notes.map((n) => (
                <li
                  key={n.id}
                  className="rounded-xl border border-line bg-card p-4 text-sm shadow-card"
                >
                  <span className="mr-2 text-xs text-ink-3">
                    {n.kind === "feedback" ? "feedback" : "1:1 note"} ·{" "}
                    {n.created_at.slice(0, 10)}
                  </span>
                  {n.body}
                </li>
              ))}
              {notes.length === 0 && (
                <li><EmptyState>
                  No notes for {person} yet. <code>fb: {person} — …</code> in
                  quick capture works too.
                </EmptyState></li>
              )}
            </ul>
          </Card>
        </div>
      )}
            </>
          )}
        </>
      )}
    </main>
  );
}

function BriefList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="font-medium text-ink-2">{title}</p>
      <ul className="ml-4 list-disc text-ink-2">
        {items.slice(0, 6).map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

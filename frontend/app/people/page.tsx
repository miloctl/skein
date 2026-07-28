"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { api, getApiKey } from "@/lib/api";
import { SectionTabs } from "@/components/section-tabs";

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
  commitments_made: { id: number; promise: string; status: string }[];
  feedback_gap_days: number | null;
  nudge: string;
};

type User = { name: string; kind: string };

export default function PeoplePage() {
  const [people, setPeople] = useState<User[]>([]);
  const [person, setPerson] = useState("");
  const [notes, setNotes] = useState<Note[]>([]);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [draft, setDraft] = useState("");
  const [kind, setKind] = useState<"note" | "feedback">("note");
  const [error, setError] = useState<string | null>(null);
  const needsKey = useSyncExternalStore(
    (cb) => {
      window.addEventListener("storage", cb);
      return () => window.removeEventListener("storage", cb);
    },
    () => !getApiKey(),
    () => false,
  );

  useEffect(() => {
    api<User[]>("/api/users").then(setPeople).catch(() => {});
  }, []);

  // last-request-wins: clicking Alice then Bob quickly must never render
  // Alice's private notes under Bob's chip
  const generation = useRef(0);
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
        if (g === generation.current) setError(String(e));
      });
    api<Brief>(`/api/private/brief/${encodeURIComponent(p)}`)
      .then((b) => {
        if (g === generation.current) setBrief(b);
      })
      .catch(() => {
        if (g === generation.current) setBrief(null);
      });
  }, []);

  useEffect(() => {
    if (person) load(person);
  }, [person, load]);

  const [saving, setSaving] = useState(false);
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
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-5xl p-6">
      <SectionTabs set="team" />
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">1:1s</h1>
      <p className="mb-6 text-sm text-ink-3">
        Private 1:1 prep and feedback journal. Only you can read what you
        write here — it lives outside search, digests, packs, exports, and
        every agent surface.
      </p>

      {needsKey && (
        <div className="mb-4 rounded-xl border border-weld/40 bg-weld/10 p-4 text-sm text-weld">
          <p>
            Private notes need your personal API key — so nobody can read or
            write them by just typing your name.{" "}
            <a href="/settings" className="font-medium underline">
              Settings
            </a>{" "}
            walks you through getting one.
          </p>
          <button
            onClick={async () => {
              try {
                await api("/api/keys/request", { method: "POST" });
                setError(null);
                alert("Asked — the request is now on the team's My Day.");
              } catch (e) {
                setError(String(e));
              }
            }}
            className="mt-2 rounded-lg bg-weld px-3 py-1 text-xs font-medium text-white hover:opacity-90"
          >
            Request a key
          </button>
        </div>
      )}

      <div className="mb-6 flex flex-wrap gap-2">
        {people
          .filter((u) => u.kind !== "agent")
          .map((u) => (
            <button
              key={u.name}
              onClick={() => {
                if (u.name === person) return; // no-op switch would blank the panel
                // clear before switching: stale content here would be another
                // person's PRIVATE notes under the wrong name, and an errored
                // fetch would leave them there indefinitely
                setNotes([]);
                setBrief(null);
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

      {person && (
        <div className="grid gap-6 md:grid-cols-2">
          <section>
            <h2 className="mb-2 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
              Since last time
            </h2>
            {brief === null ? (
              <p className="text-sm text-ink-3">no brief available</p>
            ) : (
              <div className="space-y-3 text-sm">
                {brief.nudge && (
                  <p className="rounded-lg bg-weld/10 px-3 py-2 text-weld">
                    💡 {brief.nudge}
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
                  title="Commitments they made"
                  items={brief.commitments_made.map((c) => `${c.promise} (${c.status})`)}
                />
              </div>
            )}
          </section>

          <section>
            <h2 className="mb-2 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
              Your private notes
            </h2>
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
                  className="rounded-xl border border-line bg-card p-3 text-sm shadow-card"
                >
                  <span className="mr-2 text-xs text-ink-3">
                    {n.kind === "feedback" ? "💬" : "📝"} {n.created_at.slice(0, 10)}
                  </span>
                  {n.body}
                </li>
              ))}
              {notes.length === 0 && (
                <li className="rounded-xl border border-dashed border-line-strong p-6 text-center text-sm text-ink-3">
                  No notes for {person} yet. <code>fb: {person} — …</code> in
                  ⌘K capture works too.
                </li>
              )}
            </ul>
          </section>
        </div>
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

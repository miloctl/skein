"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

import { api, getApiKey } from "@/lib/api";

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

  const load = useCallback((p: string) => {
    if (!p) return;
    api<Note[]>(`/api/private/notes?person=${encodeURIComponent(p)}`)
      .then((n) => {
        setNotes(n);
        setError(null);
      })
      .catch((e) => setError(String(e)));
    api<Brief>(`/api/private/brief/${encodeURIComponent(p)}`)
      .then(setBrief)
      .catch(() => setBrief(null));
  }, []);

  useEffect(() => {
    if (person) load(person);
  }, [person, load]);

  const addNote = async () => {
    if (!draft.trim() || !person) return;
    try {
      await api("/api/private/notes", {
        method: "POST",
        body: JSON.stringify({ person, body: draft, kind }),
      });
      setDraft("");
      load(person);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <main className="mx-auto w-full max-w-4xl p-6">
      <h1 className="mb-1 text-xl font-bold">People</h1>
      <p className="mb-4 text-sm text-zinc-500">
        Private 1:1 prep and feedback journal. Only you can read what you
        write here — it lives outside search, digests, packs, exports, and
        every agent surface.
      </p>

      {needsKey && (
        <p className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
          This page needs your personal API key (strong identity). Get your
          first one from whoever runs the box (
          <code>python -m app.bootstrap_key you</code>), then set it with the
          🔑 button in the top bar.
        </p>
      )}

      <div className="mb-6 flex flex-wrap gap-2">
        {people
          .filter((u) => u.kind !== "agent")
          .map((u) => (
            <button
              key={u.name}
              onClick={() => setPerson(u.name)}
              className={
                "rounded-full px-3 py-1 text-sm " +
                (person === u.name
                  ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
                  : "bg-zinc-100 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-200")
              }
            >
              {u.name}
            </button>
          ))}
      </div>

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {person && (
        <div className="grid gap-6 md:grid-cols-2">
          <section>
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-500">
              Since last time
            </h2>
            {brief === null ? (
              <p className="text-sm text-zinc-400">no brief available</p>
            ) : (
              <div className="space-y-3 text-sm">
                {brief.nudge && (
                  <p className="rounded-lg bg-amber-50 px-3 py-2 text-amber-800 dark:bg-amber-950 dark:text-amber-200">
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
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-500">
              Your private notes
            </h2>
            <div className="mb-3 flex gap-2">
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as "note" | "feedback")}
                className="rounded-lg border border-zinc-300 bg-transparent px-2 py-1.5 text-sm dark:border-zinc-700"
              >
                <option value="note">1:1 note</option>
                <option value="feedback">feedback</option>
              </select>
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addNote()}
                placeholder={kind === "feedback" ? "great pushback in design review…" : "agenda item, observation…"}
                className="flex-1 rounded-lg border border-zinc-300 bg-transparent px-3 py-1.5 text-sm outline-none dark:border-zinc-700"
              />
              <button
                onClick={addNote}
                className="rounded-lg bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
              >
                Add
              </button>
            </div>
            <ul className="space-y-2">
              {notes.map((n) => (
                <li
                  key={n.id}
                  className="rounded-xl border border-zinc-200 bg-white p-3 text-sm dark:border-zinc-800 dark:bg-zinc-900"
                >
                  <span className="mr-2 text-xs text-zinc-400">
                    {n.kind === "feedback" ? "💬" : "📝"} {n.created_at.slice(0, 10)}
                  </span>
                  {n.body}
                </li>
              ))}
              {notes.length === 0 && (
                <li className="rounded-xl border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-400 dark:border-zinc-700">
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
      <p className="font-medium text-zinc-700 dark:text-zinc-300">{title}</p>
      <ul className="ml-4 list-disc text-zinc-600 dark:text-zinc-400">
        {items.slice(0, 6).map((it, i) => (
          <li key={i}>{it}</li>
        ))}
      </ul>
    </div>
  );
}

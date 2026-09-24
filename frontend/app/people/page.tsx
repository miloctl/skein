"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { actionError, api, loadError } from "@/lib/api";
import { subscribeIdentity } from "@/lib/shared-chats";
import { reportStatus } from "@/lib/status";
import { Card, EmptyState } from "@/components/card";
import { timeAgo } from "@/lib/time";

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
// services/pairings.py: a lead pulls a brief only while its subject accepted
type Pairs = {
  leading: { id: number; subject: string; status: string }[];
  subject_of: { id: number; lead: string; status: string; last_brief_at: string | null }[];
};
type Draft = { body: string; kind: "note" | "feedback" };

export default function PeoplePage() {
  const [people, setPeople] = useState<User[] | null>(null);
  const [peopleError, setPeopleError] = useState("");
  const [person, setPerson] = useState("");
  // null until the read settles: an empty list is the claim "no notes yet"
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [briefError, setBriefError] = useState("");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [saving, setSaving] = useState(false);
  const draft = drafts[person]?.body ?? "";
  const kind = drafts[person]?.kind ?? "note";
  const [error, setError] = useState<string | null>(null);
  const [strong, setStrong] = useState<boolean | null>(null);
  const [pairs, setPairs] = useState<Pairs | null>(null);
  const [pairBusy, setPairBusy] = useState(false);
  // read by load(): a brief is fetched only under an accepted pairing, and
  // each refused fetch still spends the `brief` rate cap
  const acceptedLeads = useRef<string[]>([]);
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
      setDrafts({});
      setSaving(false);
      setNotes(null);
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
    return subscribeIdentity(refreshIdentity);
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
    if (!acceptedLeads.current.includes(p)) {
      setBrief(null);
      setBriefError("");
      return;
    }
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

  const loadPairs = useCallback(() => {
    const owner = identityGeneration.current;
    return api<Pairs>("/api/private/pairs")
      .then((p) => {
        if (owner !== identityGeneration.current) return;
        acceptedLeads.current = p.leading
          .filter((x) => x.status === "accepted")
          .map((x) => x.subject);
        setPairs(p);
      })
      .catch((e) => {
        if (owner === identityGeneration.current) setError(loadError(e));
      });
  }, []);

  useEffect(() => {
    if (strong === true) void loadPairs();
  }, [strong, loadPairs]);

  const pairAction = async (path: string, body: object | null, done: string) => {
    if (pairBusy) return;
    setPairBusy(true);
    try {
      await api(path, {
        method: "POST",
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      reportStatus(done, "confirmation");
      await loadPairs();
      if (person) load(person);
    } catch (e) {
      reportStatus(actionError(e));
    } finally {
      setPairBusy(false);
    }
  };
  const leadPair = pairs?.leading.find((p) => p.subject === person);
  const subjectPair = pairs?.subject_of.find((p) => p.lead === person);

  useEffect(() => {
    // never under weak identity: both endpoints refuse it, and firing them
    // rendered the same refusal twice — once per card — over a notes form
    // whose submit was going to collect a third copy
    if (person && strong !== false) load(person);
  }, [person, strong, load, pairs]);

  const teammates = people?.filter((user) => user.kind !== "agent") ?? [];

  const addNote = async () => {
    if (saving || !draft.trim() || !person || strong !== true) return;
    const owner = identityGeneration.current;
    const selected = generation.current;
    const submitted = drafts[person];
    setSaving(true); // a held Enter must not file N private notes
    try {
      await api("/api/private/notes", {
        method: "POST",
        body: JSON.stringify({ person, body: draft, kind }),
      });
      if (owner !== identityGeneration.current) return;
      setDrafts((current) => current[person] === submitted
        ? { ...current, [person]: { ...submitted, body: "" } }
        : current);
      // A save belongs to the selection that started it. Starting a new
      // load here after a switch would make the old person win the read race.
      if (selected === generation.current) load(person);
    } catch (e) {
      if (owner === identityGeneration.current && selected === generation.current)
        setError(actionError(e));
    } finally {
      if (owner === identityGeneration.current) setSaving(false);
    }
  };

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">1:1s</h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        Private 1:1 prep and feedback journal. Only you can read what you
        write here — it lives outside search, digests, context packs, exports, and
        every agent surface. A teammate&apos;s brief opens after they accept a 1:1
        pairing, and they see when you last opened it.
      </p>

      {strong === false && (
        <div className="mb-4 rounded-xl border border-weld/40 bg-weld/10 p-4 text-sm text-weld">
          <p>
            Private notes require strong identity. If deployment sign-in is
            available, use it. Otherwise, use a personal API key. Open{" "}
            <a href="/settings" className="font-medium underline">
              Settings
            </a>{" "}
            to request a key or sign in.
          </p>
          <button
            onClick={async () => {
              try {
                // reads already_pending, like Settings does for the same call:
                // ignoring it claimed a fresh request every time, so clicking
                // twice reported two requests where the backend filed one.
                // Same wording as Settings — one condition, one wording.
                const r = await api<{ already_pending: boolean; to_team?: boolean }>(
                  "/api/keys/request",
                  { method: "POST" },
                );
                setError(null);
                reportStatus(
                  r.already_pending
                    ? "Already asked. The request is still waiting for whoever runs the server."
                    : r.to_team
                      ? "Asked. No administrator is named, so every teammate now has the request and the exact command."
                      : "Asked. Whoever runs the server now has the request and the exact command.",
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
      {pairs && (pairs.leading.length > 0 || pairs.subject_of.length > 0) && (
        <Card title="1:1 partners" className="mb-6">
          <ul className="space-y-2 text-sm">
            {pairs.subject_of.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  {p.status === "proposed"
                    ? `${p.lead} asks to prepare 1:1s with you.`
                    : `${p.lead} prepares 1:1s with you. Last opened your brief: ${
                        p.last_brief_at ? timeAgo(p.last_brief_at) : "never"
                      }.`}
                </span>
                <span className="flex gap-1">
                  {p.status === "proposed" && (
                    <button
                      aria-disabled={pairBusy}
                      onClick={() => pairAction(`/api/private/pairs/${p.id}/accept`, null, `${p.lead} can now open your brief.`)}
                      className="rounded bg-thread-solid px-2 py-0.5 text-xs font-medium text-white hover:opacity-90 aria-disabled:opacity-40"
                    >
                      Accept
                    </button>
                  )}
                  <button
                    aria-disabled={pairBusy}
                    aria-label={`${p.status === "proposed" ? "Decline" : "End"} the 1:1 pairing with ${p.lead}`}
                    onClick={() => pairAction(`/api/private/pairs/${p.id}/end`, null, p.status === "proposed" ? "Declined." : "Pairing ended.")}
                    className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line aria-disabled:opacity-40"
                  >
                    {p.status === "proposed" ? "Decline" : "End"}
                  </button>
                </span>
              </li>
            ))}
            {pairs.leading.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  {p.status === "proposed"
                    ? `You asked ${p.subject} for a 1:1 pairing. It waits for them.`
                    : `You prepare 1:1s with ${p.subject}.`}
                </span>
                <button
                  aria-disabled={pairBusy}
                  aria-label={`${p.status === "proposed" ? "Cancel" : "End"} the 1:1 pairing with ${p.subject}`}
                  onClick={() => pairAction(`/api/private/pairs/${p.id}/end`, null, p.status === "proposed" ? "Request cancelled." : "Pairing ended.")}
                  className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line aria-disabled:opacity-40"
                >
                  {p.status === "proposed" ? "Cancel" : "End"}
                </button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <div className="mb-6 flex flex-wrap gap-2">
        {teammates.map((u) => (
            <button
              key={u.name}
              onClick={() => {
                if (u.name === person) return; // no-op switch would blank the panel
                ++generation.current;
                // clear before switching: stale content here would be another
                // person's PRIVATE notes under the wrong name, and an errored
                // fetch would leave them there indefinitely
                setNotes(null);
                setError(null);
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
            {leadPair?.status !== "accepted" ? (
              <div className="space-y-2 text-sm text-ink-2">
                {leadPair ? (
                  <p>You asked {person} for a 1:1 pairing. Their brief opens after they accept.</p>
                ) : (
                  <>
                    <p>
                      Their brief opens after {person} accepts a 1:1 pairing.
                      They see when you open it.
                    </p>
                    <button
                      aria-disabled={pairBusy}
                      onClick={() => pairAction("/api/private/pairs", { person, role: "lead" }, `Asked ${person}.`)}
                      className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 aria-disabled:opacity-40"
                    >
                      Ask for a 1:1 pairing
                    </button>
                  </>
                )}
                {!subjectPair && (
                  <p>
                    <button
                      aria-disabled={pairBusy}
                      onClick={() => pairAction("/api/private/pairs", { person, role: "subject" }, `${person} can now open your brief.`)}
                      className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line aria-disabled:opacity-40"
                    >
                      Let {person} prepare 1:1s with me
                    </button>
                  </p>
                )}
              </div>
            ) : brief === null ? (
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
            <div className="mb-3 flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-xs text-ink-3">
                Note type
              <select
                aria-label="Note type"
                value={kind}
                onChange={(e) => setDrafts((current) => ({
                  ...current, [person]: { body: draft, kind: e.target.value as Draft["kind"] },
                }))}
                className="rounded-lg border border-line-strong bg-transparent px-2 py-1.5 text-sm focus:border-thread-solid"
              >
                <option value="note">1:1 note</option>
                <option value="feedback">feedback</option>
              </select>
              </label>
              <label className="min-w-0 flex-1 basis-40 text-xs text-ink-3">
                {kind === "feedback" ? "Feedback note" : "1:1 note"}
              <input
                value={draft}
                onChange={(e) => setDrafts((current) => ({
                  ...current, [person]: { body: e.target.value, kind },
                }))}
                onKeyDown={(e) => e.key === "Enter" && addNote()}
                aria-label={kind === "feedback" ? "Feedback note" : "1:1 note"}
                placeholder={kind === "feedback" ? "great pushback in design review…" : "agenda item, observation…"}
                className="mt-1 w-full rounded-lg border border-line-strong bg-transparent px-3 py-1.5 text-sm outline-none focus:border-thread-solid"
              />
              </label>
              <button
                onClick={addNote}
                className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
              >
                Add
              </button>
            </div>
            <ul className="space-y-2">
              {notes === null && !error && (
                <li className="text-sm text-ink-3">Loading…</li>
              )}
              {notes?.map((n) => (
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
              {notes?.length === 0 && (
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

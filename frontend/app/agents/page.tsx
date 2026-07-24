"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

type Authority = { agent: string; entity: string; level: string };

type AgentRow = {
  agent: string;
  open_tasks: number;
  pending_proposals: number;
  last_seen: string | null;
  authority: Authority[];
};

type Trust = {
  agent: string;
  entity: string;
  proposed: number;
  approved: number;
  rejected: number;
  approval_rate: number;
  recent_streak: number;
  current_level: string;
  suggestion: string;
};

type Inbox = {
  agent: string;
  delegated_tasks: { id: number; title: string; status: string; sponsor: string }[];
  open_questions: { id: number; question: string; asked_by: string }[];
  rejected_proposals: { id: number; entity: string; summary: string; review_note: string }[];
  notifications: { id: number; message: string }[];
};

const LEVELS = ["autonomous", "notify", "review", "forbidden"];
const ENTITIES = [
  "task", "milestone", "question", "decision", "note", "blocker",
  "engagement", "intake", "lesson", "commitment", "delegation",
];

const LEVEL_COLOR: Record<string, string> = {
  autonomous: "bg-green-100 text-green-700",
  notify: "bg-blue-100 text-blue-700",
  review: "bg-amber-100 text-amber-700",
  forbidden: "bg-red-100 text-red-700",
};

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function Agents() {
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [trust, setTrust] = useState<Trust[]>([]);
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [entity, setEntity] = useState("task");
  const [level, setLevel] = useState("review");
  const [targetAgent, setTargetAgent] = useState("agent");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<AgentRow[]>("/api/agents").then(setAgents).catch((e) => setError(String(e)));
    api<Trust[]>("/api/agents/trust").then(setTrust).catch(() => {});
  }, []);

  useEffect(load, [load]);

  if (error)
    return <main className="p-8 text-sm text-red-600">Backend unreachable: {error}</main>;

  return (
    <main className="mx-auto grid max-w-6xl grid-cols-1 gap-4 p-6 md:grid-cols-2">
      <Card title="Mission control">
        {agents.length === 0 ? (
          <p className="text-sm text-zinc-400">
            No agent identities yet — delegate a task or let the chat agent write something.
          </p>
        ) : (
          <ul className="space-y-3 text-sm">
            {agents.map((a) => (
              <li key={a.agent}>
                <div className="flex items-center justify-between">
                  <span className="font-medium">🤖 {a.agent}</span>
                  <button
                    onClick={() =>
                      api<Inbox>(`/api/agents/${encodeURIComponent(a.agent)}/inbox`)
                        .then(setInbox)
                        .catch(() => {})
                    }
                    className="rounded bg-zinc-100 px-2 py-0.5 text-xs hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
                  >
                    inbox
                  </button>
                </div>
                <p className="text-xs text-zinc-500">
                  {a.open_tasks} open task(s) · {a.pending_proposals} pending proposal(s)
                  {a.last_seen && ` · last seen ${a.last_seen}`}
                </p>
                {a.authority.length > 0 && (
                  <p className="mt-1 flex flex-wrap gap-1">
                    {a.authority.map((au) => (
                      <span
                        key={au.entity}
                        className={`rounded-full px-2 py-0.5 text-xs ${LEVEL_COLOR[au.level]}`}
                      >
                        {au.entity}: {au.level}
                      </span>
                    ))}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Authority matrix">
        <p className="mb-2 text-xs text-zinc-400">
          Default is <b>review</b> — every write goes through the review inbox. Promote per
          entity as trust builds; the chat agent acts as “agent”.
        </p>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <input
            value={targetAgent}
            onChange={(e) => setTargetAgent(e.target.value)}
            className="w-24 rounded border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700"
            placeholder="agent"
          />
          <select
            value={entity}
            onChange={(e) => setEntity(e.target.value)}
            className="rounded border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900"
          >
            {ENTITIES.map((e) => (
              <option key={e}>{e}</option>
            ))}
          </select>
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="rounded border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900"
          >
            {LEVELS.map((l) => (
              <option key={l}>{l}</option>
            ))}
          </select>
          <button
            onClick={() =>
              api("/api/agents/authority", {
                method: "POST",
                body: JSON.stringify({ agent: targetAgent, entity, level }),
              })
                .then(load)
                .catch(() => {})
            }
            className="rounded-lg bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-700"
          >
            Set
          </button>
        </div>
      </Card>

      <Card title="Trust (from review verdicts)">
        {trust.length === 0 ? (
          <p className="text-sm text-zinc-400">
            No reviewed proposals yet — trust is earned in the review inbox.
          </p>
        ) : (
          <ul className="space-y-2 text-sm">
            {trust.map((t) => (
              <li key={`${t.agent}-${t.entity}`}>
                <span className="font-medium">{t.agent}</span> on {t.entity}:{" "}
                {t.approved}/{t.proposed} approved ({Math.round(t.approval_rate * 100)}%)
                · streak {t.recent_streak}
                {t.suggestion && (
                  <p className="text-xs font-medium text-green-600">💡 {t.suggestion}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {inbox && (
        <Card title={`Inbox — ${inbox.agent}`}>
          <div className="space-y-3 text-sm">
            <div>
              <p className="text-xs font-medium text-zinc-500">Delegated tasks</p>
              <ul className="text-xs text-zinc-600 dark:text-zinc-300">
                {inbox.delegated_tasks.map((t) => (
                  <li key={t.id}>
                    #{t.id} {t.title} [{t.status}] (sponsor: {t.sponsor})
                  </li>
                ))}
                {inbox.delegated_tasks.length === 0 && <li className="text-zinc-400">none</li>}
              </ul>
            </div>
            <div>
              <p className="text-xs font-medium text-zinc-500">
                Rejected proposals (learn from the notes)
              </p>
              <ul className="text-xs text-zinc-600 dark:text-zinc-300">
                {inbox.rejected_proposals.map((p) => (
                  <li key={p.id}>
                    #{p.id} {p.summary} — “{p.review_note || "no note"}”
                  </li>
                ))}
                {inbox.rejected_proposals.length === 0 && <li className="text-zinc-400">none</li>}
              </ul>
            </div>
            <div>
              <p className="text-xs font-medium text-zinc-500">Questions & notifications</p>
              <ul className="text-xs text-zinc-600 dark:text-zinc-300">
                {inbox.open_questions.map((q) => (
                  <li key={q.id}>? {q.question}</li>
                ))}
                {inbox.notifications.map((n) => (
                  <li key={n.id}>🔔 {n.message}</li>
                ))}
                {inbox.open_questions.length + inbox.notifications.length === 0 && (
                  <li className="text-zinc-400">none</li>
                )}
              </ul>
            </div>
          </div>
        </Card>
      )}
    </main>
  );
}

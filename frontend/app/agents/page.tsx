"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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

const LEVEL_COLOR: Record<string, string> = {
  autonomous: "bg-green-100 text-ok",
  notify: "bg-blue-100 text-blue-700",
  review: "bg-amber-100 text-amber-700",
  forbidden: "bg-red-100 text-danger",
};

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-card p-4 shadow-card">
      <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function Agents() {
  const [agents, setAgents] = useState<AgentRow[] | null>(null);
  const [trust, setTrust] = useState<Trust[]>([]);
  const [entities, setEntities] = useState<string[]>([]);
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [entity, setEntity] = useState("task");
  const [level, setLevel] = useState("review");
  const [targetAgent, setTargetAgent] = useState("agent");
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inboxGeneration = useRef(0);

  const load = useCallback(() => {
    api<AgentRow[]>("/api/agents")
      .then(setAgents)
      .catch((e) => setBanner(`Load failed: ${e.message ?? e}`));
    api<Trust[]>("/api/agents/trust").then(setTrust).catch(() => {});
    api<string[]>("/api/agents/entities").then(setEntities).catch(() => {});
  }, []);

  useEffect(load, [load]);

  const openInbox = (agent: string) => {
    const g = ++inboxGeneration.current;
    api<Inbox>(`/api/agents/${encodeURIComponent(agent)}/inbox`)
      .then((r) => {
        if (g === inboxGeneration.current) setInbox(r); // last click wins
      })
      .catch((e) => setBanner(`${e.message ?? e}`));
  };

  const setAuthority = () => {
    const agent = targetAgent.trim();
    if (!agent) {
      setBanner("Agent name is required.");
      return;
    }
    setBusy(true);
    setBanner(null);
    api("/api/agents/authority", {
      method: "POST",
      body: JSON.stringify({ agent, entity, level }),
    })
      .catch((e) => setBanner(`${e.message ?? e}`))
      .finally(() => {
        setBusy(false);
        load();
      });
  };

  return (
    <main className="mx-auto grid max-w-6xl grid-cols-1 gap-4 p-6 md:grid-cols-2">
      {banner && (
        <div className="flex items-center justify-between rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger md:col-span-2">
          <span>{banner}</span>
          <button onClick={() => setBanner(null)} className="text-xs underline">
            dismiss
          </button>
        </div>
      )}
      <Card title="Mission control">
        {agents === null ? (
          <p className="text-sm text-ink-3">Loading…</p>
        ) : agents.length === 0 ? (
          <p className="text-sm text-ink-3">
            No agent identities yet — delegate a task or let the chat agent write something.
          </p>
        ) : (
          <ul className="space-y-3 text-sm">
            {agents.map((a) => (
              <li key={a.agent}>
                <div className="flex items-center justify-between">
                  <span className="font-medium">🤖 {a.agent}</span>
                  <button
                    onClick={() => openInbox(a.agent)}
                    className="rounded bg-zinc-100 px-2 py-0.5 text-xs hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700"
                  >
                    inbox
                  </button>
                </div>
                <p className="text-xs text-ink-3">
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
        <p className="mb-2 text-xs text-ink-3">
          Default is <b>review</b> — every write goes through the review inbox. Promote per
          entity as trust builds; the chat agent acts as “agent”.
        </p>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <input
            value={targetAgent}
            onChange={(e) => setTargetAgent(e.target.value)}
            list="agent-names"
            className="w-28 rounded border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700"
            placeholder="agent name"
          />
          <datalist id="agent-names">
            {(agents ?? []).map((a) => (
              <option key={a.agent} value={a.agent} />
            ))}
            <option value="agent" />
          </datalist>
          <select
            value={entity}
            onChange={(e) => setEntity(e.target.value)}
            className="rounded border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900"
          >
            {(entities.length ? entities : ["task"]).map((e) => (
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
            disabled={busy}
            onClick={setAuthority}
            className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy ? "Setting…" : "Set"}
          </button>
        </div>
      </Card>

      <Card title="Trust (from review verdicts)">
        {trust.length === 0 ? (
          <p className="text-sm text-ink-3">
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
              <p className="text-xs font-medium text-ink-3">Delegated tasks</p>
              <ul className="text-xs text-ink-2">
                {inbox.delegated_tasks.map((t) => (
                  <li key={t.id}>
                    #{t.id} {t.title} [{t.status}] (sponsor: {t.sponsor})
                  </li>
                ))}
                {inbox.delegated_tasks.length === 0 && <li className="text-ink-3">none</li>}
              </ul>
            </div>
            <div>
              <p className="text-xs font-medium text-ink-3">
                Rejected proposals (learn from the notes)
              </p>
              <ul className="text-xs text-ink-2">
                {inbox.rejected_proposals.map((p) => (
                  <li key={p.id}>
                    #{p.id} {p.summary} — “{p.review_note || "no note"}”
                  </li>
                ))}
                {inbox.rejected_proposals.length === 0 && <li className="text-ink-3">none</li>}
              </ul>
            </div>
            <div>
              <p className="text-xs font-medium text-ink-3">Questions & notifications</p>
              <ul className="text-xs text-ink-2">
                {inbox.open_questions.map((q) => (
                  <li key={q.id}>? {q.question}</li>
                ))}
                {inbox.notifications.map((n) => (
                  <li key={n.id}>🔔 {n.message}</li>
                ))}
                {inbox.open_questions.length + inbox.notifications.length === 0 && (
                  <li className="text-ink-3">none</li>
                )}
              </ul>
            </div>
          </div>
        </Card>
      )}
    </main>
  );
}

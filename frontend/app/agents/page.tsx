"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { ManageToggle, useManageMode } from "@/components/manage-toggle";
import { SectionTabs } from "@/components/section-tabs";
import { timeAgo } from "@/lib/time";

type Persona = {
  slug: string;
  name: string;
  description: string;
  emoji: string;
  vibe: string;
};

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

// API keeps the compact level names; people read what each one means
const LEVEL_LABEL: Record<string, string> = {
  autonomous: "acts alone",
  notify: "acts, then tells you",
  review: "needs approval",
  forbidden: "not allowed",
};

const LEVEL_COLOR: Record<string, string> = {
  autonomous: "bg-ok/15 text-ok",
  notify: "bg-thread/15 text-thread",
  review: "bg-warn/15 text-warn",
  forbidden: "bg-danger/15 text-danger",
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
  const [bench, setBench] = useState<Persona[]>([]);
  const [entity, setEntity] = useState("task");
  const [level, setLevel] = useState("review");
  const [targetAgent, setTargetAgent] = useState("agent");
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [memories, setMemories] = useState<
    { id: number; topic: string; content: string; user: string }[]
  >([]);
  const [forgetting, setForgetting] = useState<number | null>(null);
  const [status, setStatus] = useState<{
    provider: string;
    model: string;
    review_gate: boolean;
  } | null>(null);
  const manage = useManageMode();
  const inboxGeneration = useRef(0);

  const load = useCallback(() => {
    api<AgentRow[]>("/api/agents")
      .then(setAgents)
      .catch((e) => setBanner(`Load failed: ${e.message ?? e}`));
    api<Trust[]>("/api/agents/trust").then(setTrust).catch(() => {});
    api<string[]>("/api/agents/entities").then(setEntities).catch(() => {});
    api<Persona[]>("/api/personas").then(setBench).catch(() => {});
    api<{ provider: string; model: string; review_gate: boolean }>("/api/agents/status")
      .then(setStatus)
      .catch(() => {});
    api<{ id: number; topic: string; content: string; user: string }[]>("/api/memories")
      .then(setMemories)
      .catch(() => {});
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

  const changeAuthority = (agent: string, ent: string, lvl: string) => {
    setBusy(true);
    setBanner(null);
    api("/api/agents/authority", {
      method: "POST",
      body: JSON.stringify({ agent, entity: ent, level: lvl }),
    })
      .catch((e) => setBanner(`${e.message ?? e}`))
      .finally(() => {
        setBusy(false);
        load();
      });
  };

  const setAuthority = () => {
    const agent = targetAgent.trim();
    if (!agent) {
      setBanner("Agent name is required.");
      return;
    }
    changeAuthority(agent, entity, level);
  };

  return (
    <main className="mx-auto max-w-6xl p-6">
      <div className="flex items-start justify-between">
        <SectionTabs set="team" />
        <ManageToggle />
      </div>
      {status && (
        <p className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-line bg-card px-4 py-2.5 text-xs text-ink-2 shadow-card">
          <span>
            <span
              aria-hidden
              className={
                "mr-1.5 inline-block size-2 rounded-full " +
                (status.provider === "mock" ? "bg-line-strong" : "bg-ok")
              }
            />
            {status.provider === "mock"
              ? "Deterministic mode — no AI model connected; chat commands and smart capture still work"
              : `Model: ${status.model} (${status.provider})`}
          </span>
          <span>
            {status.review_gate
              ? "Review gate on — every agent write waits in Inbox → Approvals"
              : "Review gate off — agent writes apply directly (authority rules still hold)"}
          </span>
        </p>
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {banner && (
        <div className="flex items-center justify-between rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger md:col-span-2">
          <span>{banner}</span>
          <button onClick={() => setBanner(null)} className="text-xs underline">
            dismiss
          </button>
        </div>
      )}
      {bench.length > 0 && (
        <section className="rounded-xl border border-line bg-card p-4 shadow-card md:col-span-2">
          <h2 className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
            The bench
          </h2>
          <p className="mb-3 text-xs text-ink-3">
            Specialist personas you can invoke in chat — same tools, same
            review gate, their own name on every proposal. They appear in
            Mission Control below after their first use.
          </p>
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {bench.map((p) => (
              <li key={p.slug}>
                <Link
                  href={`/chat?as=${p.slug}`}
                  className="flex items-start gap-2.5 rounded-lg border border-line-strong p-2.5 transition-colors hover:border-thread-solid hover:bg-thread/5"
                >
                  <span aria-hidden className="text-lg leading-6">
                    {p.emoji}
                  </span>
                  <span className="min-w-0 text-sm">
                    <span className="flex items-baseline gap-2">
                      <span className="font-medium text-ink">{p.name}</span>
                      <code className="text-[10px] text-ink-3">/as {p.slug}</code>
                    </span>
                    <span className="block text-xs text-ink-3">{p.description}</span>
                    {p.vibe && (
                      <span className="block text-xs italic text-ink-3/80">{p.vibe}</span>
                    )}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
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
                    className="rounded bg-raised px-2 py-0.5 text-xs hover:bg-line"
                  >
                    inbox
                  </button>
                </div>
                <p className="text-xs text-ink-3">
                  {a.open_tasks === 0 && a.pending_proposals === 0
                    ? "idle — nothing assigned, nothing pending"
                    : `${a.open_tasks} open task${a.open_tasks === 1 ? "" : "s"} · ${a.pending_proposals} pending proposal${a.pending_proposals === 1 ? "" : "s"}`}
                  {a.last_seen && ` · last seen ${timeAgo(a.last_seen)}`}
                </p>
                {a.authority.length > 0 && (
                  <p className="mt-1 flex flex-wrap gap-1">
                    {a.authority.map((au) => (
                      <span
                        key={au.entity}
                        title={`${au.entity}: ${au.level}`}
                        className={`rounded-full px-2 py-0.5 text-xs ${LEVEL_COLOR[au.level]}`}
                      >
                        {au.entity}: {LEVEL_LABEL[au.level] ?? au.level}
                      </span>
                    ))}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Authority — what each agent may do alone">
        <p className="mb-2 text-xs text-ink-3">
          By default every agent write <b>needs approval</b> (it waits in
          Inbox → Approvals). Promote per entity as trust builds. The built-in
          chat agent is “agent”.
        </p>
        {(() => {
          const grants = (agents ?? []).flatMap((a) =>
            a.authority.map((au) => ({ ...au, agent: a.agent })),
          );
          return grants.length === 0 ? (
            <p className="text-sm text-ink-3">
              No overrides yet — everything an agent writes needs approval.
            </p>
          ) : (
            <ul className="mb-2 space-y-1 text-sm">
              {grants.map((g) => (
                <li key={`${g.agent}-${g.entity}`} className="flex items-center gap-2">
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">{g.agent}</span>
                    <span className="text-ink-3"> on {g.entity}</span>
                  </span>
                  {manage ? (
                    <select
                      value={g.level}
                      disabled={busy}
                      onChange={(e) => changeAuthority(g.agent, g.entity, e.target.value)}
                      className="rounded border border-line-strong bg-card px-2 py-1 text-xs"
                      aria-label={`Authority for ${g.agent} on ${g.entity}`}
                    >
                      {LEVELS.map((l) => (
                        <option key={l} value={l}>
                          {LEVEL_LABEL[l]}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className={`rounded-full px-2 py-0.5 text-xs ${LEVEL_COLOR[g.level]}`}>
                      {LEVEL_LABEL[g.level] ?? g.level}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          );
        })()}
        {manage && (
          <div className="flex flex-wrap items-center gap-2 border-t border-line pt-2 text-sm">
            <span className="text-xs text-ink-3">New rule:</span>
            <input
              value={targetAgent}
              onChange={(e) => setTargetAgent(e.target.value)}
              list="agent-names"
              name="authority-agent"
              className="w-28 rounded border border-line-strong bg-transparent px-2 py-1 text-xs"
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
              className="rounded border border-line-strong bg-transparent px-2 py-1 text-xs"
            >
              {(entities.length ? entities : ["task"]).map((e) => (
                <option key={e}>{e}</option>
              ))}
            </select>
            <select
              value={level}
              onChange={(e) => setLevel(e.target.value)}
              className="rounded border border-line-strong bg-transparent px-2 py-1 text-xs"
            >
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {LEVEL_LABEL[l]}
                </option>
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
        )}
        {!manage && (
          <p className="text-xs text-ink-3">
            Changing these needs “manager controls” (top right) and a personal
            API key.
          </p>
        )}
      </Card>

      <Card title="Trust — earned from review verdicts">
        {trust.length === 0 ? (
          <p className="text-sm text-ink-3">
            No reviewed proposals yet — trust is earned in Inbox → Approvals.
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

      <Card title="Team memory — steers agent conversations (personal ones only their owner\u2019s)">
        {memories.length === 0 ? (
          <p className="text-sm text-ink-3">
            Nothing remembered yet — /remember in chat adds one.
          </p>
        ) : (
          <ul className="space-y-1.5 text-sm">
            {memories.map((m) => (
              <li key={m.id} className="flex items-start justify-between gap-2">
                <span className="min-w-0">
                  {m.topic && <span className="mr-1.5 font-medium">[{m.topic}]</span>}
                  {m.content}
                  {m.user && (
                    <span className="ml-1.5 text-xs text-ink-3">({m.user} only)</span>
                  )}
                </span>
                {forgetting === m.id ? (
                  <span className="flex shrink-0 items-center gap-1 text-xs">
                    <button
                      autoFocus
                      aria-label={`Forget for good: ${m.topic || m.content.slice(0, 40)}`}
                      onClick={async () => {
                        try {
                          await api(`/api/memories/${m.id}`, { method: "DELETE" });
                          setMemories((ms) => ms.filter((x) => x.id !== m.id));
                        } catch (e) {
                          setBanner(String(e));
                        }
                        setForgetting(null);
                      }}
                      className="rounded bg-danger px-2 py-0.5 font-medium text-white hover:opacity-90"
                    >
                      forget for good
                    </button>
                    <button onClick={() => setForgetting(null)} className="text-ink-3 hover:text-ink">
                      keep
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={() => setForgetting(m.id)}
                    aria-label={`Forget memory: ${m.topic || m.content.slice(0, 40)}`}
                    className="shrink-0 rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line"
                  >
                    forget…
                  </button>
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
      </div>
    </main>
  );
}

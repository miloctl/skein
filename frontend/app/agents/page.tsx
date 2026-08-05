"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { actionError, api } from "@/lib/api";
import { dismissStatus, reportStatus } from "@/lib/status";
import { ManageToggle, useManageMode } from "@/components/manage-toggle";
import { Card } from "@/components/card";
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

/** With the review gate off, `review` writes apply directly: tools/_gate.py
 *  takes the direct path on `not config.AGENT_REVIEW` whatever the level, so
 *  autonomous, notify and review all collapse to "acts alone" and only
 *  forbidden still stops a write. A bare "needs approval" then promises a
 *  checkpoint the deployment does not run. gateOn is null while the status
 *  fetch is unsettled — say nothing rather than guess. */
const levelLabel = (level: string, gateOn: boolean | null) =>
  gateOn === false && level === "review"
    ? "needs approval (gate off)"
    : (LEVEL_LABEL[level] ?? level);

const LEVEL_COLOR: Record<string, string> = {
  autonomous: "bg-ok/15 text-ok",
  notify: "bg-thread/15 text-thread",
  review: "bg-warn/15 text-warn",
  forbidden: "bg-danger/15 text-danger",
};


export default function Agents() {
  const [agents, setAgents] = useState<AgentRow[] | null>(null);
  const [trust, setTrust] = useState<Trust[] | null>(null);
  const [entities, setEntities] = useState<string[]>([]);
  const [inbox, setInbox] = useState<Inbox | null>(null);
  const [bench, setBench] = useState<Persona[]>([]);
  const [entity, setEntity] = useState("task");
  const [level, setLevel] = useState("review");
  const [targetAgent, setTargetAgent] = useState("agent");
  const [busy, setBusy] = useState(false);
  const [memories, setMemories] = useState<
    { id: number; topic: string; content: string; user: string }[] | null
  >(null);
  const [forgetting, setForgetting] = useState<number | null>(null);
  const [status, setStatus] = useState<{
    provider: string;
    model: string;
    provider_error: string;
    review_gate: boolean;
    context_strategy: string;
    context_error: string;
  } | null>(null);
  const manage = useManageMode();
  // null until the status fetch settles: the authority copy below states a
  // rule that INVERTS with this flag, so guessing it tells the reader the
  // opposite of the truth about who can write without asking
  const gateOn: boolean | null = status === null ? null : status.review_gate;
  const inboxGeneration = useRef(0);
  // Every section here must distinguish "unknown" from "empty". Several
  // empty states are CLAIMS — "No reviewed proposals yet", "Nothing
  // remembered yet", "No rules yet — everything needs approval" — and on
  // the page whose job is telling you what the agents may do alone, a claim
  // rendered while the data is unknown is the most expensive wrong answer
  // in the product. Same shape as portfolio.
  const [errors, setErrors] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    // one wording for a failed SECTION, matching app/portfolio/page.tsx —
    // loadError() names the whole page, which is wrong when one card failed
    const fail = (key: string, what: string) => (e: Error) =>
      setErrors((cur) => ({ ...cur, [key]: `Cannot load ${what}. ${actionError(e)}` }));
    const ok = <T,>(set: (v: T) => void, key: string) => (v: T) => {
      set(v);
      setErrors((cur) => (key in cur ? { ...cur, [key]: "" } : cur));
    };
    api<AgentRow[]>("/api/agents")
      .then(ok(setAgents, "agents"))
      .catch((e) => {
        fail("agents", "the agents list")(e);
        // the page's headline data: also announce it, so a reader who never
        // scrolls still learns the page is not telling the truth
        reportStatus(`Cannot load the agents list. ${actionError(e)}`);
      });
    api<Trust[]>("/api/agents/trust")
      .then(ok(setTrust, "trust"))
      .catch(fail("trust", "trust scores"));
    api<string[]>("/api/agents/entities")
      .then(ok(setEntities, "entities"))
      .catch(fail("entities", "the record types an agent can write"));
    api<Persona[]>("/api/personas")
      .then(ok(setBench, "bench"))
      .catch(fail("bench", "the bench"));
    api<{
      provider: string;
      model: string;
      provider_error: string;
      review_gate: boolean;
      context_strategy: string;
      context_error: string;
    }>("/api/agents/status")
      .then(ok(setStatus, "status"))
      .catch(fail("status", "the model and review-gate status"));
    api<{ id: number; topic: string; content: string; user: string }[]>("/api/memories")
      .then(ok(setMemories, "memories"))
      .catch(fail("memories", "team memory"));
  }, []);

  /** A failed section says so; a section still loading says nothing yet. */
  const failed = (key: string) =>
    errors[key] ? <p className="text-sm text-danger">{errors[key]}</p> : null;

  useEffect(load, [load]);

  const openInbox = (agent: string) => {
    const g = ++inboxGeneration.current;
    api<Inbox>(`/api/agents/${encodeURIComponent(agent)}/inbox`)
      .then((r) => {
        if (g === inboxGeneration.current) setInbox(r); // last click wins
      })
      .catch((e) => reportStatus(actionError(e)));
  };

  const changeAuthority = (agent: string, ent: string, lvl: string) => {
    setBusy(true);
    dismissStatus();
    api("/api/agents/authority", {
      method: "POST",
      body: JSON.stringify({ agent, entity: ent, level: lvl }),
    })
      .catch((e) => reportStatus(actionError(e)))
      .finally(() => {
        setBusy(false);
        load();
      });
  };

  const setAuthority = () => {
    const agent = targetAgent.trim();
    if (!agent) {
      reportStatus("Agent name is required.");
      return;
    }
    changeAuthority(agent, entity, level);
  };

  return (
    <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <SectionTabs set="team" />
        <ManageToggle />
      </div>
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">Agents</h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        The bench, mission control, authority, and trust — agents earn
        autonomy through review verdicts. Humans hold every switch.
      </p>
      {errors.status && (
        <p className="mb-4 rounded-xl border border-danger/30 bg-danger/10 px-4 py-2.5 text-xs text-danger">
          {errors.status}
        </p>
      )}
      {status && (
        <p className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-line bg-card px-4 py-2.5 text-xs text-ink-2 shadow-card">
          <span>
            <span
              aria-hidden
              className={
                "mr-1.5 inline-block size-2 rounded-full " +
                (status.provider_error
                  ? "bg-danger"
                  : status.provider === "mock"
                    ? "bg-line-strong"
                    : "bg-ok")
              }
            />
            {status.provider_error
              ? `Model misconfigured — ${status.provider_error}. Running deterministic until it is fixed.`
              : status.provider === "mock"
                ? "Deterministic mode — no AI model connected. Chat commands and quick capture still work."
                : `Model: ${status.model} (${status.provider})`}
          </span>
          <span>
            {status.review_gate
              ? "Review gate on — every agent write waits in Inbox → Approvals"
              : "Review gate off — agent writes apply directly (authority rules still hold)"}
          </span>
          {/* the effective strategy and any config fault are SEPARATE spans:
              rendering the fault instead of the strategy let the strip assert
              a strategy the deployment was not using, because the Settings
              toggle overrides the env value the fault describes */}
          {status.context_strategy && (
            <span>
              {status.context_strategy === "summarize"
                ? "Long chats: older messages are summarized (costs one extra model call each time)"
                : "Long chats: oldest messages are dropped"}
            </span>
          )}
          {status.context_error && (
            <span className="text-danger">
              {`Long-chat settings: ${status.context_error} Correct the SKEIN_CONTEXT_* values in .env, then restart the server.`}
            </span>
          )}
        </p>
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {errors.bench && (
        <section className="mb-4 rounded-xl border border-line bg-card p-4 shadow-card">
          <h2 className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
            The bench
          </h2>
          {failed("bench")}
        </section>
      )}
      {bench.length > 0 && (
        <section className="rounded-xl border border-line bg-card p-4 shadow-card md:col-span-2">
          <h2 className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
            The bench
          </h2>
          <p className="mb-3 text-xs text-ink-3">
            Specialist personas you can invoke in chat — same tools, same
            review gate, their own name on every proposal. They appear in
            Mission control below after their first use.
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
                    {/* no alpha on ink tokens: text-3 is tuned to clear AA
                        exactly, and /80 undoes that — the axe scan in
                        e2e/smoke.spec.ts fails it */}
                    {p.vibe && (
                      <span className="block text-xs italic text-ink-3">{p.vibe}</span>
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
          errors.agents ? failed("agents") : <p className="text-sm text-ink-3">Loading…</p>
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
                        className={`rounded-full px-2 py-0.5 text-xs ${LEVEL_COLOR[au.level] ?? 'bg-raised text-ink-2'}`}
                      >
                        {au.entity}: {levelLabel(au.level, gateOn)}
                      </span>
                    ))}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Authority — what each agent can do alone">
        <p className="mb-2 text-xs text-ink-3">
          {gateOn === null ? (
            "Checking whether the review gate is on…"
          ) : gateOn ? (
            <>
              By default every agent write <b>needs approval</b> (it waits in
              Inbox → Approvals). Promote per entity as trust builds. The
              built-in chat agent is “agent”.
            </>
          ) : (
            <>
              The review gate is off, so an agent writes directly and only{" "}
              <b>not allowed</b> stops it. Four writes still wait for a human:
              delete a note, forget a memory, cancel an event, record an
              absence. To make <b>needs approval</b> hold for every entity, set{" "}
              <code>SKEIN_AGENT_REVIEW=1</code> and restart the server. The
              built-in chat agent is “agent”.
            </>
          )}
        </p>
        {agents === null ? (
          // grants derive from the agents list: while it is unknown, "No
          // rules yet" would assert a permissive default nobody has checked
          errors.agents ? failed("agents") : <p className="text-sm text-ink-3">Loading…</p>
        ) : (() => {
          const grants = agents.flatMap((a) =>
            a.authority.map((au) => ({ ...au, agent: a.agent })),
          );
          return grants.length === 0 ? (
            <p className="text-sm text-ink-3">
              {/* what "no rules" MEANS inverts with the gate, same as the
                  paragraph above — an unqualified "needs approval" here
                  contradicted it on the same screen */}
              {gateOn === null
                ? "No rules yet."
                : gateOn
                  ? "No rules yet — everything an agent writes needs approval."
                  : "No rules yet — so nothing on this page limits an agent."}
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
                          {levelLabel(l, gateOn)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span className={`rounded-full px-2 py-0.5 text-xs ${LEVEL_COLOR[g.level] ?? 'bg-raised text-ink-2'}`}>
                      {levelLabel(g.level, gateOn)}
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
            {/* the record-type list failed to load, so the select below is
                showing its fallback — say so, or the reader reads a short
                list as "these are the only record types" */}
            {errors.entities && (
              <span className="w-full text-xs text-danger">{errors.entities}</span>
            )}
            <input
              value={targetAgent}
              onChange={(e) => setTargetAgent(e.target.value)}
              list="agent-names"
              name="authority-agent"
              aria-label="Agent name"
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
              aria-label="Entity"
              onChange={(e) => setEntity(e.target.value)}
              className="rounded border border-line-strong bg-transparent px-2 py-1 text-xs"
            >
              {(entities.length ? entities : ["task"]).map((e) => (
                <option key={e}>{e}</option>
              ))}
            </select>
            <select
              value={level}
              aria-label="Authority level"
              onChange={(e) => setLevel(e.target.value)}
              className="rounded border border-line-strong bg-transparent px-2 py-1 text-xs"
            >
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {levelLabel(l, gateOn)}
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
            Changing these needs “manager controls” (top right), a personal
            API key, and administrator access.
          </p>
        )}
      </Card>

      <Card title="Trust — earned from review verdicts">
        {/* without this the card contradicts itself: services/delegation.py
            counts a streak only from reviewed_strong verdicts, so an agent
            reads "1/1 approved (100%) · streak 0" and the manager has no way
            to learn why it never gets promoted */}
        <p className="mb-2 text-xs text-ink-3">
          A streak counts only approvals made with a personal API key. A name
          from the header alone can be set by anyone, so it must not walk an
          agent toward acting alone.
        </p>
        {errors.trust ? (
          failed("trust")
        ) : trust === null ? (
          <p className="text-sm text-ink-3">Loading…</p>
        ) : trust.length === 0 ? (
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
                {/* text-ok, not a raw palette green: scripts/check_theme_contrast.py
                    sweeps the tokens parsed out of globals.css and theme.ts, so a
                    hardcoded hex is the one color here proved against no pack */}
                {t.suggestion && (
                  <p className="text-xs font-medium text-ok">💡 {t.suggestion}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Team memory — steers agent conversations (personal ones only their owner's)">
        {errors.memories ? (
          failed("memories")
        ) : memories === null ? (
          <p className="text-sm text-ink-3">Loading…</p>
        ) : memories.length === 0 ? (
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
                          setMemories((ms) => (ms ?? []).filter((x) => x.id !== m.id));
                        } catch (e) {
                          reportStatus(actionError(e));
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

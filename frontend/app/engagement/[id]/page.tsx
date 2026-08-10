"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { Card, EmptyState } from "@/components/card";
import { ReceiptLine } from "@/components/receipt";
import { PeekLink } from "@/components/task-peek";
import { SectionTabs } from "@/components/section-tabs";
import { api, loadError } from "@/lib/api";
import type { Receipt } from "@/lib/entity-ref";

/** One engagement, whole.
 *
 *  Its outcome was on Browse, its health on Work → Health, its blockers in the
 *  register, its reports on Reports, its agent work on Team → Agents, and its
 *  plan drift only at close. Answering "how is this going" meant touring all of
 *  them and assembling the answer by hand.
 *
 *  Read-only by design. Every row here links to the surface that owns the write,
 *  so there is one place to change a thing and one place to see it whole.
 */

type Row = Record<string, string | number | null>;

type Action = {
  kind: string;
  entity: string;
  entity_id: number;
  title: string;
  condition: string;
  owner: string;
  action: string;
  receipts: Receipt[];
  link: string;
};

type Brief = {
  engagement: {
    id: number;
    name: string;
    project_class: string;
    kind: string;
    status: string;
    lead: string | null;
    outcome: string | null;
    timebox_end: string | null;
    kill_criteria: string | null;
    conclusion: string | null;
  };
  health: { color: string; receipts: Receipt[]; moved_from: string | null };
  milestones: Row[];
  tasks: Row[];
  blockers: Row[];
  delegated: {
    task_id: number;
    title: string;
    agent: string;
    sponsor: string;
    status: string;
    last_note: string;
    last_note_at: string;
  }[];
  lessons: Row[];
  artifacts: { id: number; title: string; kind: string; created_at: string }[];
  // services/playbooks.py::close_out_diff. `slipped` carries rows; the three
  // task lists carry TITLES, because the diff is computed against a kickoff
  // snapshot whose ids may name rows that have since been deleted.
  plan_diff: {
    playbook?: string;
    slipped?: { title: string; days: number }[];
    unfinished_tasks?: string[];
    added_tasks?: string[];
    dropped_tasks?: string[];
    skipped_rituals?: string[];
  };
  next_actions: Action[];
};

const DOT: Record<string, string> = {
  red: "bg-danger",
  yellow: "bg-weld",
  green: "bg-ok",
};

export default function EngagementBrief({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [b, setB] = useState<Brief | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<Brief>(`/api/engagements/${id}/brief`)
      .then((d) => {
        setB(d);
        setError("");
      })
      .catch((e) => setError(loadError(e)));
  }, [id]);
  useEffect(load, [load]);

  if (error)
    return (
      <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl p-4 sm:p-6">
        <SectionTabs set="work" />
        <p className="text-sm text-danger">{error}</p>
      </main>
    );
  if (!b)
    return (
      <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl p-4 sm:p-6">
        <SectionTabs set="work" />
        <p className="text-sm text-ink-3">Loading…</p>
      </main>
    );

  const e = b.engagement;
  const drift =
    (b.plan_diff.slipped?.length ?? 0) +
    (b.plan_diff.unfinished_tasks?.length ?? 0) +
    (b.plan_diff.added_tasks?.length ?? 0) +
    (b.plan_diff.skipped_rituals?.length ?? 0);

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl xl:max-w-6xl space-y-4 p-4 sm:p-6"
    >
      <SectionTabs set="work" />

      <div>
        <h1 className="flex items-center gap-2 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
          {b.health.color ? (
            <span
              aria-hidden
              className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT[b.health.color] ?? "bg-line-strong"}`}
            />
          ) : null}
          {e.name}
          {b.health.color ? (
            <span className="sr-only">health {b.health.color}</span>
          ) : null}
        </h1>
        <p className="mt-1 text-sm text-ink-3">
          {e.project_class} · {e.kind} · {e.status}
          {e.lead ? ` · led by @${e.lead}` : " · no lead"}
          {/* stated only when there IS an earlier snapshot: an engagement
              scored for the first time never was green, and naming a change
              from it would invent a previous state */}
          {b.health.moved_from ? ` · moved from ${b.health.moved_from}` : ""}
        </p>
      </div>

      {/* What it is FOR, first. Health without the outcome tells a reader the
          engagement is late without telling them late for what. */}
      <Card title="Intended outcome">
        {e.outcome ? (
          <p className="whitespace-pre-wrap text-sm text-ink-2">{e.outcome}</p>
        ) : (
          <EmptyState>
            No outcome recorded. Add one on Work → Browse — closing needs an
            honest conclusion, and a conclusion is measured against this.
          </EmptyState>
        )}
        {e.kind === "experiment" ? (
          <p className="mt-2 text-xs text-ink-3">
            Timebox ends {e.timebox_end || "unset"} · kill criteria:{" "}
            {e.kill_criteria || "unset"}. An invalidated hypothesis concluded on
            time is a success.
          </p>
        ) : null}
        {e.conclusion ? (
          <p className="mt-2 text-xs text-ink-2">
            Concluded: {e.conclusion}
          </p>
        ) : null}
      </Card>

      {/* The same rows the portfolio queue ranks, narrowed to this engagement
          — one evidence model, so the two surfaces cannot recommend different
          things about the same work (services/engagement_brief.py). */}
      <Card title="What this needs">
        {b.next_actions.length === 0 ? (
          <p className="text-sm text-ink-3">
            Nothing here is escalated, overdue, or unowned.
          </p>
        ) : (
          <ul className="space-y-2.5 text-sm">
            {b.next_actions.map((a) => (
              <li key={`${a.entity}${a.entity_id}`}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  {a.entity === "task" ? (
                    <PeekLink taskId={a.entity_id}>
                      <span className="font-medium">{a.title}</span>
                    </PeekLink>
                  ) : (
                    <span className="font-medium">{a.title}</span>
                  )}
                  <span className="text-xs text-ink-3">
                    {a.condition}
                    {a.owner ? ` · @${a.owner}` : ""}
                  </span>
                </div>
                <p className="text-xs text-ink-2">{a.action}</p>
                {a.receipts.map((r, i) => (
                  <ReceiptLine key={i} receipt={r} className="block text-xs text-ink-3" />
                ))}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title={`Milestones (${b.milestones.length})`}>
          {b.milestones.length === 0 ? (
            <p className="text-sm text-ink-3">None recorded.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {b.milestones.map((m) => (
                <li key={String(m.id)}>
                  <span className="text-ink-3">#{m.id}</span> {m.title}
                  <span className="ml-1 text-xs text-ink-3">
                    [{m.status}]
                    {m.due_date ? ` · due ${m.due_date}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title={`Open blockers (${b.blockers.length})`}>
          {b.blockers.length === 0 ? (
            <p className="text-sm text-ink-3">Nothing is blocked.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {b.blockers.map((k) => (
                <li key={String(k.id)}>
                  <span className="text-ink-3">#{k.id}</span> {k.title}
                  <span className="ml-1 text-xs text-ink-3">
                    {k.impact} · {k.owner ? `@${k.owner}` : "unowned"}
                    {k.status === "escalated" ? " · escalated" : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title={`Open work (${b.tasks.length})`}>
        {b.tasks.length === 0 ? (
          <p className="text-sm text-ink-3">Nothing open.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {b.tasks.map((t) => (
              <li key={String(t.id)}>
                <PeekLink taskId={Number(t.id)}>
                  <span className="text-ink-3">#{t.id}</span> {t.title}
                </PeekLink>
                <span className="ml-1 text-xs text-ink-3">
                  [{t.priority}/{t.status}]
                  {t.assignee ? ` @${t.assignee}` : " unassigned"}
                  {t.waiting_on_type ? ` · waiting on ${t.waiting_on_type} #${t.waiting_on_id}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* What an agent is carrying, with its last note. A sponsor otherwise
          opened each task's peek one at a time to learn the same thing. */}
      {b.delegated.length > 0 ? (
        <Card title={`With an agent (${b.delegated.length})`}>
          <ul className="space-y-2 text-sm">
            {b.delegated.map((d) => (
              <li key={d.task_id}>
                <PeekLink taskId={d.task_id}>
                  <span className="text-ink-3">#{d.task_id}</span> {d.title}
                </PeekLink>
                <span className="ml-1 text-xs text-ink-3">
                  {d.agent} · sponsor {d.sponsor || "none"} · {d.status}
                </span>
                <p className="text-xs text-ink-2">
                  {d.last_note || "No progress note has been filed."}
                </p>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card title="Lessons that apply">
          {b.lessons.length === 0 ? (
            <p className="text-sm text-ink-3">
              None yet for this class of work.
            </p>
          ) : (
            <ul className="space-y-1.5 text-sm">
              {b.lessons.map((l) => (
                <li key={String(l.id)} id={`lesson-${l.id}`}>
                  {l.lesson}
                  {l.recommendation ? (
                    <span className="block text-xs text-ink-3">
                      → {l.recommendation}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Reports">
          {b.artifacts.length === 0 ? (
            <p className="text-sm text-ink-3">Nothing generated yet.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {b.artifacts.map((a) => (
                <li key={a.id}>
                  <Link
                    href={`/artifacts?id=${a.id}`}
                    className="hover:underline"
                  >
                    {a.title}
                  </Link>
                  <span className="ml-1 text-xs text-ink-3">
                    {a.kind} · {a.created_at.slice(0, 10)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Only for an engagement born from a playbook AND closed: the diff is
          computed against the kickoff snapshot, which nothing else can
          reconstruct (services/playbooks.py::close_out_diff). */}
      {/* Only for an engagement born from a playbook. The diff is computed
          against the kickoff snapshot, which nothing else can reconstruct —
          milestones move, tasks are added and deleted, and a cancelled ritual
          leaves no row (services/playbooks.py::close_out_diff). */}
      {drift > 0 ? (
        <Card title="Planned versus actual">
          <p className="mb-2 text-xs text-ink-3">
            Against the plan {b.plan_diff.playbook} laid out at kickoff. Drift
            is not failure — it is what the next kickoff of this class needs to
            know.
          </p>
          <ul className="space-y-1 text-sm">
            {(b.plan_diff.slipped ?? []).map((m) => (
              <li key={`s${m.title}`}>
                {m.title}
                <span className="ml-1 text-xs text-weld">
                  {m.days} day{m.days === 1 ? "" : "s"} later than planned
                </span>
              </li>
            ))}
            {(b.plan_diff.unfinished_tasks ?? []).map((t) => (
              <li key={`u${t}`}>
                {t}
                <span className="ml-1 text-xs text-ink-3">
                  planned, not finished
                </span>
              </li>
            ))}
            {(b.plan_diff.added_tasks ?? []).map((t) => (
              <li key={`a${t}`}>
                {t}
                <span className="ml-1 text-xs text-ink-3">
                  added after kickoff
                </span>
              </li>
            ))}
            {(b.plan_diff.skipped_rituals ?? []).map((r) => (
              <li key={`r${r}`}>
                {r}
                <span className="ml-1 text-xs text-ink-3">
                  planned ceremony that did not happen
                </span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </main>
  );
}

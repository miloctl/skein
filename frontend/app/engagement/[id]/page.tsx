"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { Card, EmptyState } from "@/components/card";
import { ReceiptLine } from "@/components/receipt";
import { PeekLink } from "@/components/task-peek";
import { SectionTabs } from "@/components/section-tabs";
import { actionError, api, loadError } from "@/lib/api";
import { reportStatus } from "@/lib/status";
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
  // how deep into the portfolio queue the server looked before narrowing —
  // the empty state states this window rather than asserting a fact
  queue_scanned: number;
};

// services/engagement_brief.py::TASK_CAP. A list that truncates in silence
// reads as "this is everything", and the engagement most worth reading is the
// one with too much open work.
const TASK_CAP = 50;

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
  const [busy, setBusy] = useState(false); // a held Enter must not write N packages

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

      {/* a way back to the list. `/engagement/<id>` is in no tab set, so no
          tab reads aria-current here and a reader arriving from Browse has no
          other route back to where they came from. */}
      <p className="text-xs text-ink-3">
        <Link href="/dashboard" className="hover:underline">
          ← All engagements
        </Link>
      </p>

      <div>
        {/* the health signal is NOT in the heading: heading navigation would
            announce mutable state as part of the page title, and the title
            would change as health changes */}
        <h1 className="font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
          {e.name}
        </h1>
        <p className="mt-1 text-sm text-ink-3">
          {e.project_class} · {e.kind} · {e.status}
          {e.lead ? ` · led by @${e.lead}` : " · no lead"}
        </p>
      </div>

      {/* What it is FOR, first. Health without the outcome tells a reader the
          engagement is late without telling them late for what. */}
      <Card title="Intended outcome">
        {e.outcome ? (
          <p className="whitespace-pre-wrap text-sm text-ink-2">{e.outcome}</p>
        ) : (
          <EmptyState>
            An engagement closes against its intended outcome. Add one on
            Work → Browse.
          </EmptyState>
        )}
        {e.kind === "experiment" ? (
          <p className="mt-2 text-xs text-ink-3">
            Timebox ends {e.timebox_end || "unset"} · kill criteria:{" "}
            {e.kill_criteria || "unset"}
          </p>
        ) : null}
        {e.conclusion ? (
          <p className="mt-2 text-xs text-ink-2">
            Concluded: {e.conclusion}
          </p>
        ) : null}
      </Card>

      {/* The receipts are the answer to "how is this going". A colour with no
          reason tells a reader the engagement is late without telling them
          what is late, which is the tour this page removes. Each reference
          resolves, so the milestone opens instead of being hunted for. */}
      <Card title="Health">
        {b.health.color ? (
          <>
            <p className="flex items-center gap-2 text-sm">
              {/* the word, not only the hue: hue is the entire payload for a
                  sighted reader, including anyone with a colour deficiency */}
              <span
                aria-hidden
                className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT[b.health.color] ?? "bg-line-strong"}`}
              />
              <span className="font-medium">{b.health.color}</span>
              {b.health.moved_from ? (
                <span className="text-xs text-ink-3">
                  {b.health.moved_from} → {b.health.color} since yesterday
                </span>
              ) : null}
            </p>
            {b.health.receipts.length > 0 ? (
              <ul className="mt-1.5 space-y-0.5">
                {b.health.receipts.map((r, i) => (
                  <li key={i}>
                    <ReceiptLine receipt={r} className="text-xs text-ink-2" />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-xs text-ink-3">
                No signal is firing against this engagement.
              </p>
            )}
          </>
        ) : (
          // the fourth state, named. engagement_health scores open engagements
          // only, so a closed one has no colour — and a blank line reads as
          // "green" to anybody who does not know that rule.
          <p className="text-sm text-ink-3">
            Closed engagements are not scored. Read the conclusion above.
          </p>
        )}
      </Card>

      {/* The same rows the portfolio queue ranks, narrowed to this engagement
          — one evidence model, so the two surfaces cannot recommend different
          things about the same work (services/engagement_brief.py). */}
      <Card title="What this needs">
        {b.next_actions.length === 0 ? (
          <p className="text-sm text-ink-3">
            Nothing in the portfolio queue&apos;s top {b.queue_scanned} rows
            belongs to this engagement.
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
                    <Link href={a.link} className="font-medium hover:underline">
                      {a.title}
                    </Link>
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
            <p className="text-sm text-ink-3">
              No milestone is recorded. Add one on Work → Browse.
            </p>
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
            <p className="text-sm text-ink-3">
              Nothing is blocked. Capture one with &lsquo;blocked on …&rsquo;.
            </p>
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

      <Card title={`Open work (${b.tasks.length}${b.tasks.length === TASK_CAP ? "+" : ""})`}>
        {b.tasks.length === 0 ? (
          <p className="text-sm text-ink-3">
            No work is open. Capture one with &lsquo;todo: …&rsquo;.
          </p>
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
                  {d.last_note || "The agent filed no progress note."}
                  {d.last_note_at ? (
                    // the date is why the note is worth reading: this morning
                    // and three weeks ago mean opposite things
                    <span className="ml-1 text-ink-3">
                      ({d.last_note_at.slice(0, 10)})
                    </span>
                  ) : null}
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
              No lesson is recorded for this class of work yet. Closing an
              engagement drafts one.
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
          {/* Generating one is offered HERE, on the engagement, because a
              handoff is written when ownership changes and this is the page a
              person is on when that happens. The generator existed and no
              surface called it (services/handoff.py). */}
          <button
            onClick={async () => {
              if (busy) return;
              setBusy(true);
              try {
                await api(`/api/engagements/${e.id}/handoff`, {
                  method: "POST",
                  body: JSON.stringify({}),
                });
                load();
              } catch (err) {
                reportStatus(actionError(err));
              } finally {
                setBusy(false);
              }
            }}
            disabled={busy}
            className="mb-2 rounded bg-raised px-2 py-1 text-xs text-ink-2 hover:bg-line disabled:opacity-40"
          >
            Write a handoff package
          </button>
          {b.artifacts.length === 0 ? (
            <p className="text-sm text-ink-3">
              No report is generated yet. Handoffs and readouts land here.
            </p>
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

      {/* Only for an engagement born from a playbook. The diff is computed
          against the kickoff snapshot, which nothing else can reconstruct —
          milestones move, tasks are added and deleted, and a cancelled ritual
          leaves no row (services/playbooks.py::close_out_diff). */}
      {drift > 0 ? (
        <Card title="Planned versus actual">
          <p className="mb-2 text-xs text-ink-3">
            Against the plan {b.plan_diff.playbook} laid out at kickoff, as it
            stands now. Drift is not failure. It is what the next kickoff of
            this class needs to know, and while this engagement runs it is
            still something the team can act on.
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

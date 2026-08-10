"use client";

import { useCallback, useEffect, useState } from "react";

import Link from "next/link";

import { Card } from "@/components/card";

// The cap the card STATES, and the one it asks for. A list that silently
// truncates reads as "this is everything" — the same rule the stakeholder
// card on this page follows by putting its cap in the title.
const QUEUE_CAP = 12;
import { PeekLink } from "@/components/task-peek";
import { SectionTabs } from "@/components/section-tabs";
import { actionError, api, loadError } from "@/lib/api";
import { ReceiptLine } from "@/components/receipt";
import type { Receipt } from "@/lib/entity-ref";
import { reportStatus } from "@/lib/status";

/** The Monday cockpit: the week's ritual in one room.
 *
 *  Running Monday meant touring /portfolio, /intake and /charter, each with
 *  its own load, and holding the order in your head. The order is the part
 *  that was missing, and it is load-bearing — last week's result is read
 *  BEFORE this week's plan, because committing a week before you know whether
 *  the last one landed is the mistake the ritual exists to prevent.
 *
 *  Composition only. Every number here already had a home; nothing is
 *  computed twice, and the one write is the commit the ritual ends with. */

type Intervention = {
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

type Cockpit = {
  week: {
    week: string;
    committed: number;
    done: number;
    kept_percent: number | null;
    tasks: Row[];
  };
  last_week: {
    week: string;
    committed: number;
    done: number;
    kept_percent: number | null;
    carryover: { id: number; title: string; assignee: string }[];
  };
  interrupts: {
    planned: number;
    unplanned: number;
    same_week_unplanned_share: number | null;
    carried_over: number;
    n: number;
    window_weeks: number;
  };
  capacity_ahead: {
    week: string;
    starts_on: string;
    people: { person: string; total_percent: number; detail: string }[];
    over: string[];
    away: { person: string; kind: string }[];
  }[];
  conflicts: { person: string; total_percent: number; detail: string }[];
  intake: { id: number; title: string; requester: string; score: number | null }[];
  stale_decisions: { id: number; title: string; review_by: string | null }[];
  // open threads with people outside the roster (services/stakeholders.py)
  stakeholders: {
    party: string;
    items: { kind: string; text: string; when: string }[];
  }[];
  awaiting: {
    id: number;
    promise: string;
    to_whom: string;
    due_date: string | null;
  }[];
  health: { id: number; name: string; health: string; status: string }[];
  // `from` is non-null by the time it reaches here — the service drops a
  // first-ever score, which is not a change (services/planning.py)
  health_changes: { id: number; name: string; from: string; to: string }[];
  // the open task whose finish releases the most other work. null when
  // nothing waits on anything — a zeroed row would be a sentence about work
  // that does not exist (services/planning.py)
  top_unblocking_move: {
    id: number;
    title: string;
    assignee: string;
    unblocks: number;
    // carried, not dropped: on a truncated walk the count is a FLOOR, and
    // services/planning.py says the peek and this card must not read
    // differently about the same chain
    depth_capped?: boolean;
  } | null;
  today: string;
};

type Row = Record<string, string | number | null>;

const DOT: Record<string, string> = { red: "🔴", yellow: "🟡", green: "🟢" };

// This page is an AGENDA a manager reads down in a meeting. Measured at
// 360px the uncapped outside-threads card was 65% of a 4705px page, which
// buries steps 5 and 6 under a list nobody reads in the room. The service
// sorts busiest-party first, so the head of the list is the useful end, and
// the title states the cap rather than showing a count the list contradicts
// (the same shape /artifacts uses for reports).
const PARTY_CAP = 8;

export default function Planning() {
  const [data, setData] = useState<Cockpit | null>(null);
  const [error, setError] = useState("");

  // its own request, not a field on /api/planning: the queue composes health,
  // findings, blockers and decisions, and a cockpit that could not render
  // until all four had been ranked would be slower for every reader who came
  // for last week's number. A failure here leaves the running order intact.
  const [queue, setQueue] = useState<Intervention[] | null>(null);
  const [queueError, setQueueError] = useState("");
  const load = useCallback(() => {
    api<Cockpit>("/api/planning")
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(loadError(e)));
    api<Intervention[]>(`/api/interventions?limit=${QUEUE_CAP}`)
      .then((q) => {
        setQueue(q);
        setQueueError("");
      })
      // stated, never swallowed: this card claims to be the one a manager can
      // read alone, and a failed request that renders nothing is
      // indistinguishable from a clean week
      .catch((e) => setQueueError(loadError(e)));
  }, []);
  useEffect(load, [load]);

  if (error && !data)
    return (
      <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
        <SectionTabs set="work" />
        <p className="text-sm text-danger">{error}</p>
      </main>
    );
  if (!data)
    return (
      <main id="content" tabIndex={-1} className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6">
        <SectionTabs set="work" />
        <p className="text-sm text-ink-3">Loading…</p>
      </main>
    );

  const d = data;
  const share = d.interrupts.same_week_unplanned_share;

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl xl:max-w-6xl space-y-4 p-4 sm:p-6"
    >
      <SectionTabs set="work" />
      <p className="text-sm text-ink-3">
        The week, in the order the meeting runs it. Every number here also
        lives on its own page — this is the running order, not a second copy.
      </p>


      {/* 1 — how last week went, before anything about this one */}
      <Card title={`1 · Last week (${d.last_week.week})`}>
        <p className="text-sm">
          {d.last_week.kept_percent === null ? (
            "Nothing was committed."
          ) : (
            <>
              {d.last_week.done} of {d.last_week.committed} kept (
              {d.last_week.kept_percent}%)
            </>
          )}
        </p>
        {d.last_week.carryover.length > 0 ? (
          <>
            <h3 className="mt-2 text-xs uppercase tracking-wide text-ink-3">
              Carrying forward
            </h3>
            <ul className="space-y-1 text-sm">
              {d.last_week.carryover.map((t) => (
                <li key={t.id}>
                  <PeekLink taskId={t.id}>
                    <span className="text-ink-3">#{t.id}</span> {t.title}
                  </PeekLink>
                  {t.assignee ? (
                    <span className="ml-2 text-xs text-ink-3">@{t.assignee}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {/* the receipt behind a weak kept-%: "we planned badly" and "we
            absorbed an incident" have opposite remedies */}
        {share !== null ? (
          <p className="mt-2 text-xs text-ink-3">
            {/* "of work that settled in its own week", never "of finished
                work": a task that carried over is in neither side of this
                ratio, and carryover is exactly what a weak kept-% is about.
                Withheld under n=8 by the service, like every other verdict. */}
            Over {d.interrupts.window_weeks} weeks,{" "}
            {Math.round(share * 100)}% of the work that started and finished
            inside one week was never on that week&apos;s commitment line (
            {d.interrupts.unplanned} of {d.interrupts.n}).
            {d.interrupts.carried_over > 0 ? (
              <>
                {" "}
                {d.interrupts.carried_over} more carried over from an earlier
                week and{" "}
                {d.interrupts.carried_over === 1 ? "is" : "are"} counted in
                neither number.
              </>
            ) : null}
          </p>
        ) : null}
      </Card>

      {/* 2 — what needs a decision, ranked, AFTER last week's result. The
          running order is load-bearing (this file's header, and
          services/planning.py): a room that opens on remediation commits the
          week before anyone has read whether the last one landed. Numbered
          like every other card, because the titles ARE the agenda and a reader
          working down the page in a meeting loses their place at a gap. */}
      <Card title="2 · Needs a call">
        {queueError ? (
          <p className="text-sm text-danger">{queueError}</p>
        ) : queue === null ? (
          <p className="text-sm text-ink-3">Loading…</p>
        ) : queue.length === 0 ? (
          <p className="text-sm text-ink-3">
            Nothing is escalated, overdue, or unowned. Read the cards below.
          </p>
        ) : (
          <>
            <p className="mb-2 text-xs text-ink-3">
              Ranked by consequence, {QUEUE_CAP} at a time. Each row states what is
              true, who holds it, and the next move. If you do not agree with
              the order, read the receipts.
            </p>
            <ul className="space-y-2.5 text-sm">
            {queue.map((q) => (
              <li key={`${q.entity}${q.entity_id}`}>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  {/* a task row opens the peek over this page, like every
                      other task reference in the product — a raw href here was
                      a full navigation that cost the manager their place in
                      the queue they were working */}
                  {q.entity === "task" ? (
                    <PeekLink taskId={q.entity_id}>
                      <span className="font-medium">{q.title}</span>
                    </PeekLink>
                  ) : (
                    // clamped, not truncated server-side: a findings message
                    // is several sentences and its full text belongs in the
                    // DOM for a screen reader and for search, but three lines
                    // of bold at the top of an agenda buries every row under it
                    <Link
                      href={q.link}
                      className="line-clamp-2 font-medium hover:underline"
                    >
                      {q.title}
                    </Link>
                  )}
                  <span className="text-xs text-ink-3">
                    {q.condition}
                    {/* "unowned" is a claim about work somebody must pick
                        up. A finding is nobody's by construction
                        (services/intervention.py), so it says nothing rather
                        than reporting an ownership gap that does not exist. */}
                    {q.owner
                      ? ` · @${q.owner}`
                      : q.entity === "finding"
                        ? ""
                        : " · unowned"}
                  </span>
                </div>
                <p className="text-xs text-ink-2">{q.action}</p>
                {q.receipts.map((r, i) => (
                  <ReceiptLine
                    key={i}
                    receipt={r}
                    className="block text-xs text-ink-3"
                  />
                ))}
              </li>
            ))}
            </ul>
          </>
        )}
      </Card>

      {/* 2 — what the week already holds, and whether it fits */}
      <Card title={`3 · This week (${d.week.week})`}>
        <p className="text-sm">
          {d.week.committed === 0
            ? "Nothing committed yet. Draft the plan on Work → Health."
            : `${d.week.done} of ${d.week.committed} done so far.`}
        </p>
        {d.conflicts.length > 0 ? (
          <ul className="mt-2 space-y-1 text-sm text-weld">
            {d.conflicts.map((c) => (
              <li key={c.person}>
                {c.person} at {c.total_percent}% ({c.detail})
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-xs text-ink-3">Nobody is over 100% today.</p>
        )}
      </Card>

      {/* 3 — the weeks after this one. Accepting work today against today's
          numbers is how a conflict gets noticed on the day it arrives. */}
      <Card title="4 · The weeks ahead">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">
              Allocation per person per week, for the coming weeks
            </caption>
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-ink-3">
                <th scope="col" className="py-1 pr-3">Week</th>
                <th scope="col" className="py-1 pr-3">Load</th>
                <th scope="col" className="py-1">Away</th>
              </tr>
            </thead>
            <tbody>
              {d.capacity_ahead.map((w) => (
                <tr key={w.week} className="border-t border-line align-top">
                  <td className="py-1 pr-3 whitespace-nowrap">{w.week}</td>
                  {/* `people` carries every allocated person and their
                      percent; `over` is derived from it as the subset above
                      100. The table read only `over`, so a week where three
                      people sat at 95% looked identical to an empty one —
                      which is the staffing call this card exists to make. */}
                  <td className="py-1 pr-3">
                    {w.people.length ? (
                      <span className="flex flex-wrap gap-x-3 gap-y-0.5">
                        {w.people.map((p) => (
                          <span
                            key={p.person}
                            className={
                              p.total_percent > 100 ? "text-weld" : "text-ink-2"
                            }
                            title={p.detail}
                          >
                            {p.person}{" "}
                            <span className="tabular-nums">{p.total_percent}%</span>
                          </span>
                        ))}
                      </span>
                    ) : (
                      <span className="text-ink-3">—</span>
                    )}
                  </td>
                  <td className="py-1">
                    {w.away.length ? (
                      w.away.map((a) => `${a.person} (${a.kind})`).join(", ")
                    ) : (
                      <span className="text-ink-3">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* 4 — what wants in */}
      <Card title={`5 · Waiting for triage (${d.intake.length})`}>
        {d.intake.length === 0 ? (
          <p className="text-sm text-ink-3">Nothing is waiting for triage.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.intake.map((r) => (
              <li key={r.id}>
                <span className="text-ink-3">#{r.id}</span> {r.title}
                <span className="ml-2 text-xs text-ink-3">
                  from {r.requester}
                  {r.score !== null ? ` · score ${r.score}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-ink-3">
          Accept, defer or decline on Inbox → Requests. The requester reads the
          reason you give.
        </p>
      </Card>

      {/* the one move that releases the most work. Sits with the week's plan
          rather than with the stale list: it is a choice about what to start,
          not something that has gone wrong.

          Numbered 5a, not 6: this card and 5b are CONDITIONAL, and a fixed
          number would leave a gap in the agenda on any week without them. A
          manager reads this page down in a meeting, and a gap is where they
          lose their place. */}
      {d.top_unblocking_move ? (
        <Card title="5a · The move that unblocks the most">
          <p className="text-sm">
            <PeekLink taskId={d.top_unblocking_move.id}>
              <span className="text-ink-3">#{d.top_unblocking_move.id}</span>{" "}
              {d.top_unblocking_move.title}
            </PeekLink>
            {d.top_unblocking_move.assignee ? (
              <span className="ml-2 text-xs text-ink-3">
                @{d.top_unblocking_move.assignee}
              </span>
            ) : null}
          </p>
          <p className="mt-1 text-xs text-ink-3">
            Finishing it releases {d.top_unblocking_move.unblocks} task
            {d.top_unblocking_move.unblocks === 1 ? "" : "s"} that wait on it
            {d.top_unblocking_move.depth_capped
              ? " (the chain runs deeper than Skein follows)"
              : ""}
            ,
            directly or behind another.
          </p>
        </Card>
      ) : null}

      {/* who is owed what, outside the team. Read before the week's meetings
          rather than after them, which is when it was answerable at all. */}
      {d.stakeholders.length > 0 ? (
        <Card
          title={
            d.stakeholders.length > PARTY_CAP
              ? `Open outside the team (${PARTY_CAP} of ${d.stakeholders.length}, busiest first)`
              : `Open outside the team (${d.stakeholders.length})`
          }
        >
          <ul className="space-y-2 text-sm">
            {d.stakeholders.slice(0, PARTY_CAP).map((s) => (
              <li key={s.party}>
                <span className="font-medium">{s.party}</span>
                <ul className="ml-4 list-disc text-xs text-ink-3">
                  {s.items.map((i, n) => (
                    <li key={n}>
                      {i.text}
                      <span className="ml-1">
                        ({i.kind}
                        {i.when ? `, due ${i.when}` : ""})
                      </span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {/* what the team is AWAITING (docs/LEXICON.md row 1a — `waiting on`
          is blocker vocabulary, and capture-palette.tsx routes the bare
          phrase there). Beside the triage queue rather than in it: these are
          not decisions to make, they are people to chase. */}
      {d.awaiting.length > 0 ? (
        <Card title={`5b · Awaiting from other people (${d.awaiting.length})`}>
          <ul className="space-y-1 text-sm">
            {d.awaiting.map((p) => {
              const late = p.due_date !== null && p.due_date < d.today;
              return (
                <li key={p.id}>
                  <span className={late ? "text-weld" : ""}>{p.promise}</span>
                  <span className="ml-2 text-xs text-ink-3">
                    {p.to_whom ? `${p.to_whom} owes it` : "nobody named"}
                    {p.due_date ? ` · due ${p.due_date}` : " · no date"}
                    {late ? " · overdue" : ""}
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="mt-2 text-xs text-ink-3">
            Skein chases an overdue one once a day. If two chases get no
            answer, it tells the team once, and it names nobody. Capture one
            with &ldquo;awaiting: acme corp — the signed SOW by
            YYYY-MM-DD&rdquo;.
          </p>
        </Card>
      ) : null}

      {/* 5 — what has gone stale, and which way the portfolio moved */}
      <Card title="6 · Stale decisions">
        {d.health_changes.length > 0 ? (
          <>
            <h3 className="text-xs uppercase tracking-wide text-ink-3">
              Moved in the last week
            </h3>
            <ul className="mb-2 space-y-1 text-sm">
              {d.health_changes.map((c) => (
                <li key={c.id}>
                  {DOT[c.to]}{" "}
                  <Link
                    href={`/engagement/${c.id}`}
                    className="font-medium hover:underline"
                  >
                    {c.name}
                  </Link>
                  :{" "}
                  {/* `from` is always set: services/planning.py filters a
                      first-ever score out, because it is not a change and
                      this heading says one. A fallback string here would be
                      copy no reader can reach. */}
                  {c.from} → {c.to}
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {/* Where the portfolio stands, under where it moved. The movement
            list answers "what changed" and is silent about an engagement
            that has been red all month — the one most likely to need the
            meeting's attention. */}
        {d.health.length > 0 ? (
          <>
            <h3 className="text-xs uppercase tracking-wide text-ink-3">
              Where each engagement stands
            </h3>
            <ul className="mb-2 flex flex-wrap gap-x-3 gap-y-1 text-sm">
              {d.health.map((h) => (
                <li key={h.id}>
                  {DOT[h.health]}{" "}
                  <Link href={`/engagement/${h.id}`} className="hover:underline">
                    {h.name}
                  </Link>
                  <span className="ml-1 text-xs text-ink-3">{h.status}</span>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {d.stale_decisions.length === 0 ? (
          <p className="text-sm text-ink-3">No decision is past its review date.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {d.stale_decisions.map((dec) => (
              <li key={dec.id}>
                <span className="text-ink-3">#{dec.id}</span> {dec.title}
                {dec.review_by ? (
                  <span className="ml-2 text-xs text-ink-3">
                    due {dec.review_by}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-xs text-ink-3">
          Reconfirm or supersede on Team → Charter.
        </p>
      </Card>

      {/* 6 — the one write the ritual ends with */}
      <Card title="7 · Close the meeting">
        <button
          onClick={async () => {
            try {
              // the ritual is claim-guarded per ISO week and RETURNS
              // {skipped} rather than raising, so the success path ran on a
              // run that sent nothing — and the copy below invites the second
              // click that produces it
              const out = await api<{ skipped?: string }>(
                "/api/rituals/week-open",
                { method: "POST" },
              );
              reportStatus(
                out.skipped
                  ? "The week-open brief already ran this week."
                  : "Week-open brief filed. Everyone gets their own promises, stale decisions and due work.",
              );
              load();
            } catch (e) {
              reportStatus(actionError(e));
            }
          }}
          className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        >
          File the week-open brief
        </button>
        <p className="mt-2 text-xs text-ink-3">
          The brief sends one personal notification to each teammate and
          files an artifact. It reaches everyone, so run it once.
        </p>
      </Card>
    </main>
  );
}

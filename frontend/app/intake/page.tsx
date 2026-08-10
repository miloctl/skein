"use client";

import { useCallback, useEffect, useState } from "react";

import { actionError, api, loadError } from "@/lib/api";
import { reportStatus } from "@/lib/status";
import { ManageToggle, useManageMode } from "@/components/manage-toggle";
import { EmptyState } from "@/components/card";
import { PersonInput } from "@/components/person-input";
import { SectionTabs } from "@/components/section-tabs";

type Req = {
  id: number;
  title: string;
  detail: string;
  requester: string;
  project_class: string;
  reach: number;
  impact: number;
  confidence: number;
  effort: number;
  score: number;
  status: string;
  disposition_reason: string;
};

const STATUS_COLORS: Record<string, string> = {
  submitted: "bg-warn/15 text-warn",
  scored: "bg-thread/15 text-thread",
  accepted: "bg-ok/15 text-ok",
  deferred: "bg-raised text-ink-2",
  declined: "bg-danger/15 text-danger",
};


/** Project the team's capacity if this request is accepted and staffed.
 *
 *  Deterministic: current and forward allocation, upcoming time away, and the
 *  person's own declared growth interests, which are shown as context and never
 *  as a score — services/portfolio.py::what_if states that rule.
 */
function WhatIf({ requestId }: { requestId: number }) {
  const [people, setPeople] = useState<string[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [percent, setPercent] = useState(50);
  // the shape services/portfolio.py::what_if returns, field for field
  const [out, setOut] = useState<{
    assumed_percent: number;
    projection: {
      person: string;
      current_percent: number;
      projected_percent: number;
      overcommitted: boolean;
      growth_interests: string;
      upcoming_absence: string;
    }[];
  } | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ name: string; kind: string; active: number }[]>("/api/users")
      .then((u) =>
        setPeople(u.filter((x) => x.kind !== "agent" && x.active).map((x) => x.name)),
      )
      .catch(() => {});
  }, []);

  const run = async () => {
    if (busy || picked.length === 0) return;
    setBusy(true);
    try {
      setOut(
        await api(`/api/intake/${requestId}/what-if`, {
          method: "POST",
          body: JSON.stringify({ people: picked, percent }),
        }),
      );
      setErr("");
    } catch (e) {
      setErr(actionError(e));
      setOut(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-line bg-card p-2.5">
      <p className="mb-1.5 text-xs font-medium text-ink-2">
        If you accept this, who works it?
      </p>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {people.map((n) => (
          <button
            key={n}
            aria-pressed={picked.includes(n)}
            onClick={() =>
              setPicked((p) => (p.includes(n) ? p.filter((x) => x !== n) : [...p, n]))
            }
            className={
              "rounded-full px-2 py-0.5 text-xs " +
              (picked.includes(n)
                ? "bg-thread-solid text-white"
                : "bg-raised text-ink-2 hover:bg-line")
            }
          >
            {n}
          </button>
        ))}
        <label className="ml-1 text-xs text-ink-3">
          at
          <input
            type="number"
            min={1}
            max={100}
            value={percent}
            aria-label="Percent of each person"
            // clamped here, not only by min/max: those are not enforced while
            // typing, and an empty field is Number("") === 0, which the
            // service refuses with a 400 for a value the reader never chose
            onChange={(ev) =>
              setPercent(Math.max(1, Math.min(100, Number(ev.target.value) || 1)))
            }
            className="mx-1 w-14 rounded border border-line-strong bg-transparent px-1 py-0.5 text-xs"
          />
          %
        </label>
        <button
          onClick={run}
          disabled={busy || picked.length === 0}
          className="rounded bg-raised px-2 py-0.5 text-xs text-ink-2 hover:bg-line disabled:opacity-40"
        >
          project
        </button>
      </div>
      {err ? <p className="text-xs text-danger">{err}</p> : null}
      {out ? (
        <ul className="space-y-0.5 text-xs">
          {out.projection.map((p) => (
            <li
              key={p.person}
              className={p.overcommitted ? "text-danger" : "text-ink-2"}
            >
              @{p.person}: {p.current_percent}% → {p.projected_percent}%
              {p.overcommitted ? " — over capacity" : ""}
              {p.upcoming_absence ? ` · away ${p.upcoming_absence}` : ""}
              {/* self-declared, shown as context and never as a match score
                  (services/users.py::set_growth_interests) */}
              {p.growth_interests ? ` · wants ${p.growth_interests}` : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export default function IntakePage() {
  // null until the fetch settles. Starting at [] rendered an empty list for
  // "still loading", "nothing here", and "the load failed" alike — three
  // states, one blank screen.
  const [reqs, setReqs] = useState<Req[] | null>(null);
  const [form, setForm] = useState({
    title: "",
    detail: "",
    project_class: "",
  });
  const [error, setError] = useState<string | null>(null);
  const manage = useManageMode();

  const load = useCallback(() => {
    api<Req[]>("/api/intake")
      .then((rows) => {
        setReqs(rows);
        setError(null); // a recovered backend must not leave the old banner above fresh data
      })
      .catch((e) => {
        setReqs([]); // settled, with the error shown above the list
        setError(loadError(e));
      });
  }, []);
  useEffect(load, [load]);

  const [submitting, setSubmitting] = useState(false);
  const submit = async () => {
    if (submitting || !form.title.trim()) return;
    setSubmitting(true); // a held Enter must not file N requests
    try {
      await api("/api/intake", { method: "POST", body: JSON.stringify(form) });
      setForm({ title: "", detail: "", project_class: "" });
      load();
    } catch (e) {
      reportStatus(actionError(e));
    } finally {
      setSubmitting(false);
    }
  };

  // triage happens in inline panels — one open at a time, no browser prompts
  type PanelMode = "score" | "accepted" | "deferred" | "declined";
  const [panel, setPanel] = useState<{ id: number; mode: PanelMode } | null>(
    null,
  );
  const [rice, setRice] = useState({
    reach: 3,
    impact: 3,
    confidence: 3,
    effort: 3,
  });
  const [verdict, setVerdict] = useState({
    reason: "",
    experiment: false,
    timebox_end: "",
    kill_criteria: "",
    lead: "",
    outcome: "",
  });

  /** Takes the ROW, not the id: re-scoring opened on 3/3/3/3 and threw away
   *  the numbers already on record, so correcting one of the four meant
   *  remembering and retyping the other three. An unscored row is stored as
   *  0/0/0/1 (migration 001), which the 1-5 inputs cannot represent — those
   *  open on the neutral 3s instead. `score > 0` is the same test the row's
   *  RICE chip uses to decide it has been scored at all. */
  const openPanel = (r: Req, mode: PanelMode) => {
    setPanel({ id: r.id, mode });
    setRice(
      r.score > 0
        ? {
            reach: r.reach,
            impact: r.impact,
            confidence: r.confidence,
            effort: r.effort,
          }
        : { reach: 3, impact: 3, confidence: 3, effort: 3 },
    );
    setVerdict({
      reason: "",
      experiment: false,
      timebox_end: "",
      kill_criteria: "",
      lead: "",
      outcome: "",
    });
  };

  const submitScore = async (id: number) => {
    try {
      await api(`/api/intake/${id}/score`, {
        method: "POST",
        body: JSON.stringify(rice),
      });
      setPanel(null);
      load();
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  const submitVerdict = async (id: number, d: Exclude<PanelMode, "score">) => {
    if (!verdict.reason.trim()) return;
    if (d === "accepted" && verdict.experiment && !verdict.timebox_end) return;
    try {
      await api(`/api/intake/${id}/disposition`, {
        method: "POST",
        body: JSON.stringify({
          disposition: d,
          reason: verdict.reason.trim(),
          kind:
            d === "accepted" && verdict.experiment ? "experiment" : "delivery",
          timebox_end: verdict.experiment ? verdict.timebox_end : "",
          outcome: verdict.outcome.trim(),
          lead: verdict.lead.trim(),
          kill_criteria: verdict.experiment ? verdict.kill_criteria.trim() : "",
        }),
      });
      setPanel(null);
      load();
    } catch (e) {
      reportStatus(actionError(e));
    }
  };

  return (
    <main
      id="content"
      tabIndex={-1}
      className="mx-auto w-full max-w-5xl xl:max-w-6xl p-4 sm:p-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <SectionTabs set="inbox" />
        <ManageToggle />
      </div>
      <h1 className="mb-1 font-display text-[24px]/[1.15] font-semibold tracking-[-0.01em] text-ink">
        Requests
      </h1>
      <p className="mb-6 max-w-3xl text-sm text-ink-3">
        The team&apos;s front door: ask here instead of a DM. The person who
        triages scores each request and answers it with a reason that you can
        see. An accepted request starts an engagement.
      </p>

      <div className="mb-8 rounded-xl border border-line bg-card p-4 shadow-card">
        <h2 className="mb-3 font-mono text-[11px] font-medium uppercase tracking-[0.12em] text-ink-3">
          New request
        </h2>
        <div className="flex flex-col gap-2">
          <input
            value={form.title}
            maxLength={200}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            aria-label="What are you asking the team to do?"
            placeholder="What are you asking the team to do?"
            className="rounded-lg border border-line-strong bg-transparent px-3 py-2 text-sm outline-none focus:border-thread-solid"
          />
          <textarea
            value={form.detail}
            maxLength={4000}
            onChange={(e) => setForm({ ...form, detail: e.target.value })}
            aria-label="Context, goals, constraints"
            placeholder="Context, goals, constraints…"
            rows={2}
            className="rounded-lg border border-line-strong bg-transparent px-3 py-2 text-sm outline-none focus:border-thread-solid"
          />
          <div className="flex items-center gap-2">
            <select
              // the first option reads as a placeholder but names nothing: a
              // screen reader announces the current VALUE and never says what
              // the control is for. Same idiom as the textarea above.
              aria-label="Type of work"
              value={form.project_class}
              onChange={(e) =>
                setForm({ ...form, project_class: e.target.value })
              }
              className="rounded-lg border border-line-strong bg-card px-2 py-2 text-sm outline-none"
            >
              <option value="">type of work (optional)</option>
              <option value="prototype">prototype</option>
              <option value="incident">incident</option>
              <option value="migration">migration</option>
              <option value="diligence">diligence</option>
            </select>
            <button
              onClick={submit}
              disabled={!form.title.trim()}
              className="rounded-lg bg-thread-solid px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
            >
              Submit
            </button>
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}
      {!manage &&
        (reqs ?? []).some(
          (r) => r.status === "submitted" || r.status === "scored",
        ) && (
          <p className="mb-3 rounded-lg bg-raised px-3 py-2 text-xs text-ink-2">
            {
              (reqs ?? []).filter(
                (r) => r.status === "submitted" || r.status === "scored",
              ).length
            }{" "}
            request
            {(reqs ?? []).filter(
              (r) => r.status === "submitted" || r.status === "scored",
            ).length === 1
              ? " awaits"
              : "s await"}{" "}
            triage — turn on <b>manager controls</b> (top right) to score and
            decide.
          </p>
        )}
      {reqs === null && !error && (
        <p className="text-sm text-ink-3">Loading…</p>
      )}
      {reqs !== null && reqs.length === 0 && !error && (
        <EmptyState>
          No requests yet. Anyone on the team can file one above — it lands here
          for triage, not in someone&apos;s direct messages.
        </EmptyState>
      )}
      <ul className="space-y-3">
        {(reqs ?? []).map((r) => (
          <li
            key={r.id}
            className="rounded-xl border border-line bg-card p-4 shadow-card"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold">
                #{r.id} {r.title}
                {r.project_class && (
                  <span className="ml-2 text-xs font-normal text-ink-3">
                    [{r.project_class}]
                  </span>
                )}
              </span>
              <span className="flex items-center gap-2">
                {r.score > 0 ? (
                  <span
                    className="font-mono text-xs text-thread"
                    title="reach×impact×confidence÷effort"
                  >
                    RICE {r.score}
                  </span>
                ) : (
                  r.status !== "submitted" && (
                    <span className="text-xs text-ink-3">unscored</span>
                  )
                )}
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[r.status] ?? "bg-raised text-ink-2"}`}
                >
                  {r.status}
                </span>
              </span>
            </div>
            {r.detail && <p className="mt-1 text-sm text-ink-3">{r.detail}</p>}
            <p className="mt-1 text-xs text-ink-3">
              requested by {r.requester}
            </p>
            {r.disposition_reason && (
              <p className="mt-1 text-xs italic text-ink-3">
                ↳ {r.disposition_reason}
              </p>
            )}
            {manage && (r.status === "submitted" || r.status === "scored") && (
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  onClick={() => openPanel(r, "score")}
                  className="rounded bg-thread/15 px-2 py-1 text-xs font-medium text-thread hover:bg-thread/20"
                >
                  score…
                </button>
                {r.status === "scored" && (
                  <>
                    <button
                      onClick={() => openPanel(r, "accepted")}
                      className="rounded bg-ok/15 px-2 py-1 text-xs font-medium text-ok hover:bg-ok/20"
                    >
                      accept…
                    </button>
                    <button
                      onClick={() => openPanel(r, "deferred")}
                      className="rounded bg-raised px-2 py-1 text-xs font-medium text-ink-2 hover:bg-line"
                    >
                      defer…
                    </button>
                    <button
                      onClick={() => openPanel(r, "declined")}
                      className="rounded bg-danger/15 px-2 py-1 text-xs font-medium text-danger hover:bg-danger/20"
                    >
                      decline…
                    </button>
                  </>
                )}
              </div>
            )}
            {panel?.id === r.id && panel.mode === "score" && (
              <div
                className="mt-3 rounded-lg bg-raised p-3"
                onKeyDown={(e) => e.key === "Escape" && setPanel(null)}
              >
                <p className="mb-2 text-xs text-ink-2">
                  1–5 each. Score = reach × impact × confidence ÷ effort —
                  higher effort lowers it.
                </p>
                <div className="flex flex-wrap items-end gap-3">
                  {(["reach", "impact", "confidence", "effort"] as const).map(
                    (k) => (
                      <label key={k} className="text-xs text-ink-2">
                        {k}
                        <input
                          type="number"
                          name={`rice-${k}`}
                          autoFocus={k === "reach"}
                          min={1}
                          max={5}
                          value={rice[k]}
                          onChange={(e) =>
                            setRice({
                              ...rice,
                              [k]: Math.max(
                                1,
                                Math.min(5, Number(e.target.value) || 1),
                              ),
                            })
                          }
                          className="mt-0.5 block w-14 rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
                        />
                      </label>
                    ),
                  )}
                  <output
                    aria-live="polite"
                    className="pb-1 font-mono text-xs text-thread"
                    aria-label="Computed score"
                  >
                    ={" "}
                    {Math.round(
                      ((rice.reach * rice.impact * rice.confidence) /
                        rice.effort) *
                        10,
                    ) / 10}
                  </output>
                  <button
                    onClick={() => submitScore(r.id)}
                    className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90"
                  >
                    Save score
                  </button>
                  <button
                    onClick={() => setPanel(null)}
                    className="pb-1 text-xs text-ink-2 hover:text-ink"
                  >
                    cancel
                  </button>
                </div>
              </div>
            )}
            {panel?.id === r.id && panel.mode !== "score" && (
              <div
                className="mt-3 space-y-2 rounded-lg bg-raised p-3"
                onKeyDown={(e) => e.key === "Escape" && setPanel(null)}
              >
                <input
                  autoFocus
                  name="verdict-reason"
                  aria-label="Reason — the requester sees it"
                  value={verdict.reason}
                  onChange={(e) =>
                    setVerdict({ ...verdict, reason: e.target.value })
                  }
                  placeholder={`Reason for "${panel.mode.replace("ed", "ing").replace("accepting", "accepting this")}" — the requester sees it`}
                  className="w-full rounded-lg border border-line-strong bg-transparent px-2 py-1.5 text-sm outline-none focus:border-thread-solid"
                />
                {panel.mode === "accepted" && (
                  <>
                    {/* The projection was an endpoint with no reader: accept
                        decided whether the team takes work on, and the one
                        number that answers "can we" lived behind
                        POST /api/intake/{id}/what-if with nothing calling it.
                        Asked here, before the verdict, because after it the
                        answer is a report rather than a decision. */}
                    <WhatIf requestId={r.id} />
                    <label className="flex items-center gap-2 text-xs text-ink-2">
                      <input
                        type="checkbox"
                        checked={verdict.experiment}
                        onChange={(e) =>
                          setVerdict({
                            ...verdict,
                            experiment: e.target.checked,
                          })
                        }
                      />
                      🧪 timeboxed experiment — invalidated on time is a
                      success, not a slip
                    </label>
                    {verdict.experiment && (
                      <div className="flex flex-wrap gap-2">
                        <label className="text-xs text-ink-2">
                          timebox end
                          <input
                            type="date"
                            name="timebox-end"
                            value={verdict.timebox_end}
                            onChange={(e) =>
                              setVerdict({
                                ...verdict,
                                timebox_end: e.target.value,
                              })
                            }
                            className="mt-0.5 block rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
                          />
                        </label>
                        <label className="flex-1 text-xs text-ink-2">
                          kill criteria (optional)
                          <input
                            name="kill-criteria"
                            value={verdict.kill_criteria}
                            onChange={(e) =>
                              setVerdict({
                                ...verdict,
                                kill_criteria: e.target.value,
                              })
                            }
                            placeholder="what result stops this early?"
                            className="mt-0.5 block w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
                          />
                        </label>
                      </div>
                    )}
                    <div className="flex flex-wrap gap-2">
                      <label className="text-xs text-ink-2">
                        lead (optional)
                        <PersonInput
                          name="lead"
                          value={verdict.lead}
                          onChange={(e) =>
                            setVerdict({ ...verdict, lead: e.target.value })
                          }
                          placeholder="who owns it? — teammates suggested"
                          className="mt-0.5 block rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
                        />
                      </label>
                      <label className="flex-1 text-xs text-ink-2">
                        outcome (optional)
                        <input
                          name="outcome"
                          value={verdict.outcome}
                          onChange={(e) =>
                            setVerdict({ ...verdict, outcome: e.target.value })
                          }
                          placeholder="what result shows success?"
                          className="mt-0.5 block w-full rounded-lg border border-line-strong bg-transparent px-2 py-1 text-sm outline-none focus:border-thread-solid"
                        />
                      </label>
                    </div>
                  </>
                )}
                <div className="flex gap-2">
                  <button
                    disabled={
                      !verdict.reason.trim() ||
                      (panel.mode === "accepted" &&
                        verdict.experiment &&
                        !verdict.timebox_end)
                    }
                    onClick={() =>
                      submitVerdict(
                        r.id,
                        panel.mode as "accepted" | "deferred" | "declined",
                      )
                    }
                    className="rounded-lg bg-thread-solid px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
                  >
                    {panel.mode === "accepted"
                      ? verdict.experiment
                        ? "Accept as experiment"
                        : "Accept"
                      : panel.mode === "deferred"
                        ? "Defer"
                        : "Decline"}
                  </button>
                  <button
                    onClick={() => setPanel(null)}
                    className="text-xs text-ink-3 hover:text-ink"
                  >
                    cancel
                  </button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}

"use client";

import { useEffect, useState } from "react";

import { Card as Section } from "@/components/card";
import { api, loadError } from "@/lib/api";
import { timeAgo } from "@/lib/time";

type Health = {
  auth_error: string;
  auth_warnings: string[];
  provider_error: string;
  models_error: string;
  model_prices_error: string;
  model_warnings: string[];
  embeddings_error: string;
  overlay_errors: string[];
  database_warnings: string[];
  identity_ownership_error: string;
  context_error: string;
  timezone: string;
  timezone_error: string;
  jobs: { job: string; last_success: string | null; stale: boolean }[];
  activity_chain: { verified_through: number; latest: number; unverified: number };
};

/** What the scheduler and the ledger report about the running instance.
 *
 *  Everything here was already in the authenticated /api/health response and
 *  nothing in the product read it — a backup job failing nightly, a digest
 *  that stopped firing, or a truncated activity chain was invisible until it
 *  resurfaced as a findings row in the Monday agenda. The findings rule
 *  (job_stale, services/insights.py) stays: this card is where an operator
 *  LOOKS, the finding is what interrupts when nobody does.
 */
export function OperationsCard({ headingLevel = 2 }: { headingLevel?: 2 | 3 }) {
  const [h, setH] = useState<Health | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    // the shape is tested, not assumed: an older backend behind a newer
    // bundle answers with something else, and .map on it unmounts the whole
    // Settings page for a card that is merely additive. A malformed body is
    // an error, never a normalized all-clear — an empty jobs list would
    // render "every job is on schedule" about jobs it did not see.
    api<Health>("/api/health")
      .then((body) => {
        if (body && Array.isArray(body.jobs) && body.activity_chain) setH(body);
        else
          setError(
            "The health response has an unexpected shape. Update or restart"
            + " the backend, then reload this page.",
          );
      })
      .catch((e) => setError(loadError(e)));
  }, []);

  // every standing fault the response carries, one list — a fault shown only
  // under its own key is a fault nobody scans for
  const faults = h
    ? [
        h.auth_error,
        h.provider_error,
        h.models_error,
        h.model_prices_error,
        h.embeddings_error,
        h.identity_ownership_error,
        h.context_error,
        h.timezone_error,
        ...h.auth_warnings,
        ...h.model_warnings,
        ...h.overlay_errors,
        ...h.database_warnings,
      ].filter(Boolean)
    : [];
  const staleJobs = h?.jobs.filter((j) => j.stale) ?? [];
  // NEGATIVE unverified means the chain is shorter than what was already
  // verified — truncation, the one state worth a loud line
  const truncated = (h?.activity_chain.unverified ?? 0) < 0;
  const allClear = h && faults.length === 0 && staleJobs.length === 0 && !truncated;

  return (
    <Section title="Operations (team)" headingLevel={headingLevel}>
      <p className="mb-3 text-sm text-ink-3">
        The scheduler&apos;s jobs and the instance&apos;s standing faults, from
        the same health report the deployment probes read.
      </p>
      {error ? (
        <p className="text-sm text-danger">{error}</p>
      ) : h === null ? (
        <p className="text-sm text-ink-3">Loading…</p>
      ) : (
        <>
          {allClear ? (
            <p className="mb-2 text-sm text-ok">
              Every job is on schedule, and no fault is standing. The loom hums.
            </p>
          ) : null}
          {faults.length > 0 ? (
            <ul className="mb-3 space-y-1 text-sm text-danger">
              {faults.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          ) : null}
          {truncated ? (
            <p className="mb-3 text-sm text-danger">
              The activity chain is shorter than the part already verified —
              rows were removed after verification. Compare the ledger against
              the copies in data/backups.
            </p>
          ) : null}
          <ul className="space-y-1 text-sm">
            {h.jobs.map((j) => (
              <li key={j.job} className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs">{j.job}</span>
                <span
                  className={`text-xs ${j.stale ? "font-medium text-danger" : "text-ink-3"}`}
                >
                  {j.stale
                    ? j.last_success
                      ? `stale — last success ${timeAgo(j.last_success)}`
                      : "stale — no success recorded"
                    : j.last_success
                      ? `last success ${timeAgo(j.last_success)}`
                      : "no run recorded yet"}
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-ink-3">
            Ledger: verified through {h.activity_chain.verified_through} of{" "}
            {h.activity_chain.latest}
            {h.activity_chain.unverified > 0
              ? ` — ${h.activity_chain.unverified} newer rows await the next verify run`
              : ""}
            . Timezone: {h.timezone}.
          </p>
        </>
      )}
    </Section>
  );
}

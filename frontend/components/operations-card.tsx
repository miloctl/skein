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
  jobs: {
    job: string;
    last_success: string | null;
    last_attempt: string | null;
    last_status: "ok" | "error" | null;
    stale: boolean;
  }[];
  activity_chain: {
    verified_through: number;
    latest: number;
    unverified: number;
    high_water: number;
    marks_ok: boolean;
  };
};

const STRING_FIELDS = [
  "auth_error",
  "provider_error",
  "models_error",
  "model_prices_error",
  "embeddings_error",
  "identity_ownership_error",
  "context_error",
  "timezone",
  "timezone_error",
] as const;
const ARRAY_FIELDS = [
  "auth_warnings",
  "model_warnings",
  "overlay_errors",
  "database_warnings",
] as const;

function isHealth(value: unknown): value is Health {
  if (!value || typeof value !== "object") return false;
  const body = value as Record<string, unknown>;
  if (!STRING_FIELDS.every((field) => typeof body[field] === "string"))
    return false;
  if (
    !ARRAY_FIELDS.every(
      (field) =>
        Array.isArray(body[field]) &&
        (body[field] as unknown[]).every((item) => typeof item === "string"),
    )
  )
    return false;
  if (!Array.isArray(body.jobs)) return false;
  const jobs = body.jobs as Record<string, unknown>[];
  if (
    !jobs.every(
      (job) =>
        job &&
        typeof job === "object" &&
        typeof job.job === "string" &&
        typeof job.stale === "boolean" &&
        (job.last_success === null || typeof job.last_success === "string") &&
        (job.last_attempt === null || typeof job.last_attempt === "string") &&
        (job.last_status === null ||
          job.last_status === "ok" ||
          job.last_status === "error"),
    )
  )
    return false;
  const chain = body.activity_chain as Record<string, unknown> | null;
  return Boolean(
    chain &&
      typeof chain.marks_ok === "boolean" &&
      ["verified_through", "latest", "unverified", "high_water"].every(
        (field) => typeof chain[field] === "number",
      ),
  );
}

/** The authenticated health report for scheduled work and stored marks.
 *
 *  A failed latest attempt appears before its job becomes stale. Full ledger
 *  verification stays in Insights because its cost grows with every row.
 */
export function OperationsCard({ headingLevel = 2 }: { headingLevel?: 2 | 3 }) {
  const [h, setH] = useState<Health | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    // the shape is tested, not assumed: an older backend behind a newer
    // bundle answers with something else, and .map on it unmounts the whole
    // Settings page for a card that is merely additive. A malformed body is
    // an error, never an all-clear about fields this bundle did not receive.
    api<unknown>("/api/health")
      .then((body) => {
        if (isHealth(body)) setH(body);
        else
          setError(
            "The health response has an unexpected shape. Update or restart" +
              " the backend, then reload this page.",
          );
      })
      .catch((e) => setError(loadError(e)));
  }, []);

  // Every configuration or storage fault in the response lands in one list.
  // A field that no renderer reads is an operational fault nobody can see.
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
  const failedJobs = h?.jobs.filter((j) => j.last_status === "error") ?? [];
  const chainMarkFault = h ? h.activity_chain.marks_ok !== true : false;
  const allClear =
    h &&
    faults.length === 0 &&
    staleJobs.length === 0 &&
    failedJobs.length === 0 &&
    !chainMarkFault;

  return (
    <Section title="Operations (team)" headingLevel={headingLevel}>
      <p className="mb-3 text-sm text-ink-3">
        Scheduler status and stored faults from the authenticated health report.
        Full activity-chain verification appears in Insights.
      </p>
      {error ? (
        <p className="text-sm text-danger">{error}</p>
      ) : h === null ? (
        <p className="text-sm text-ink-3">Loading…</p>
      ) : (
        <>
          {allClear ? (
            <p className="mb-2 text-sm text-ok">
              No scheduled job is stale or failed. This health report has no
              configuration or ledger-mark fault. The loom hums.
            </p>
          ) : null}
          {faults.length > 0 ? (
            <ul className="mb-3 space-y-1 text-sm text-danger">
              {faults.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          ) : null}
          {chainMarkFault ? (
            <p className="mb-3 text-sm text-danger">
              The stored activity ledger does not match its integrity marks.
              Compare the ledger with the copies in data/backups.
            </p>
          ) : null}
          <ul className="space-y-1 text-sm">
            {h.jobs.map((j) => (
              <li
                key={j.job}
                className="flex items-center justify-between gap-2"
              >
                <span className="font-mono text-xs">{j.job}</span>
                <span
                  className={`text-xs ${j.stale || j.last_status === "error" ? "font-medium text-danger" : "text-ink-3"}`}
                >
                  {j.last_status === "error"
                    ? j.last_attempt
                      ? `failed — last attempt ${timeAgo(j.last_attempt)}. Check the server log.`
                      : "failed. Check the server log."
                    : j.stale
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

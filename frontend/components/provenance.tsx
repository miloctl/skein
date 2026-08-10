"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { timeAgo } from "@/lib/time";

/** How this row came to exist, and what has happened to it since.
 *
 *  `origin` said "agent" and stopped there. That is a label. The reason to
 *  trust a row — or to look at it again — is the chain: an agent proposed it,
 *  a named person approved it on Tuesday, and it has been edited twice since.
 *  Every one of those facts was already stored, in four different tables, with
 *  no surface that put them together (services/provenance.py).
 *
 *  Lazy and collapsed: most rows are read without anybody asking where they
 *  came from, and a request per task panel to answer a question nobody asked
 *  is a cost paid on every open.
 */

type Lineage = {
  origin: string;
  created_by: string;
  created_at: string;
  proposal: {
    id: number;
    proposed_by: string;
    requested_by: string | null;
    reviewed_by: string | null;
    reviewed_at: string | null;
    reviewed_strong: number;
    reviewed_override: number;
    review_note: string;
  } | null;
  verdict_is_weak: boolean;
  // no `detail`: it is the one column in `activity` that can carry a person's
  // name from a writer that does not route through `scope.detail`, and nothing
  // here renders it, so the server stopped selecting it
  history: { actor: string; action: string; created_at: string }[];
};

const MADE_BY: Record<string, string> = {
  human: "A person wrote this.",
  agent: "An agent wrote this without a human verdict.",
  agent_verified: "An agent proposed this and a person approved it.",
};

export function Provenance({
  entity,
  entityId,
}: {
  entity: string;
  entityId: number;
}) {
  const [open, setOpen] = useState(false);
  const [d, setD] = useState<Lineage | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!open || d || failed) return;
    api<Lineage>(`/api/provenance/${entity}/${entityId}`)
      .then(setD)
      .catch(() => setFailed(true));
  }, [open, d, failed, entity, entityId]);

  if (!open)
    return (
      <button
        onClick={() => setOpen(true)}
        className="text-xs text-ink-3 underline decoration-line-strong underline-offset-2 hover:text-ink-2"
      >
        where did this come from?
      </button>
    );

  return (
    <div className="space-y-1 text-xs text-ink-3">
      {failed ? (
        <p className="text-danger">The provenance record did not load.</p>
      ) : !d ? (
        <p>Loading…</p>
      ) : (
        <>
          <p className="text-ink-2">
            {MADE_BY[d.origin] ?? `Recorded origin: ${d.origin}.`}
          </p>
          <p>
            {d.created_by || "unrecorded"} ·{" "}
            <time dateTime={d.created_at} title={d.created_at}>
              {timeAgo(d.created_at)}
            </time>
          </p>
          {d.proposal ? (
            <p>
              Proposal #{d.proposal.id} by {d.proposal.proposed_by}
              {d.proposal.requested_by ? `, asked by ${d.proposal.requested_by}` : ""}
              {d.proposal.reviewed_by ? (
                <>
                  {" "}
                  · approved by {d.proposal.reviewed_by}
                  {d.proposal.reviewed_at ? (
                    <>
                      {" "}
                      <time
                        dateTime={d.proposal.reviewed_at}
                        title={d.proposal.reviewed_at}
                      >
                        {timeAgo(d.proposal.reviewed_at)}
                      </time>
                    </>
                  ) : null}
                  {/* an override is a verdict somebody other than the sponsor
                      made, with a reason on record — it never feeds a trust
                      streak, and a reader judging this row must know that */}
                  {d.proposal.reviewed_override ? " (acting for the sponsor)" : ""}
                </>
              ) : null}
            </p>
          ) : null}
          {/* the honest limit on the approval above. In trusted-header mode a
              name is whatever the caller typed, so the verdict records a click
              and not a person — the same reason the trust score refuses to
              count it (services/delegation.py::trust_blocked). */}
          {d.verdict_is_weak && d.proposal?.reviewed_by ? (
            <p className="text-weld">
              Nobody used a personal API key for that verdict. This deployment
              identifies people by a self-asserted name.
            </p>
          ) : null}
          {d.history.length > 0 ? (
            <>
              <p className="mt-1 text-ink-2">Since then:</p>
              <ul className="space-y-0.5">
                {d.history.map((h, i) => (
                  <li key={i}>
                    {/* the actor is withheld for anybody the activity feed
                        hides (services/provenance.py). A blank subject reads
                        as a change with no author; "somebody" says the change
                        happened and the name is not this reader's to see. */}
                    {h.actor || "somebody"} {h.action.replaceAll("_", " ")} ·{" "}
                    <time dateTime={h.created_at} title={h.created_at}>
                      {timeAgo(h.created_at)}
                    </time>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p>No change is recorded since then.</p>
          )}
        </>
      )}
    </div>
  );
}

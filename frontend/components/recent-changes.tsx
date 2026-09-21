"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { actionError, api, authenticatedFetch, errorFromResponse, loadError } from "@/lib/api";
import { checkSessionRevision, sessionRevision } from "@/lib/auth";
import type { Receipt } from "@/lib/entity-ref";
import { HASH_TARGET, useHashTarget } from "@/lib/hash-target";
import { Card } from "@/components/card";
import { ReceiptLine } from "@/components/receipt";

type Item = {
  kind: string;
  entity: string;
  entity_id: number;
  headline: string;
  direction: string;
  receipts: Receipt[];
  link: string;
  rule_id?: string;
  severity?: string;
};
type Summary = {
  since: string;
  items?: Item[];
  window_start?: string;
  window_end?: string;
  snapshot_id?: string;
  review_revision?: number;
  reviewed?: boolean;
  truncated?: boolean;
};

const categories = ["Acceptance awaiting you", "Overdue team promises", "Worsening health", "Other health changes", "High-severity findings", "Medium-severity findings", "Low-severity findings", "Other changes", "Feature adoption"];

function category(item: Item) {
  if (item.kind === "acceptance_waiting") return 0;
  if (item.kind === "promise_broke") return 1;
  if (item.kind === "health_moved") return item.direction === "worse" ? 2 : 3;
  if (item.kind === "finding_new" && item.rule_id) {
    if (item.rule_id === "feature_unadopted" && item.severity === "low") return 8;
    const severity = ["high", "medium", "low"].indexOf(item.severity ?? "");
    if (severity !== -1) return 4 + severity;
  }
  return 7;
}

function Changes({ items }: { items: Item[] }) {
  return <ul className="space-y-2 text-sm">
    {items.map((item, index) => <li key={`${item.kind}-${item.entity_id}-${index}`} className="break-words">
      <Link href={item.kind === "acceptance_waiting" ? `/review?id=${item.entity_id}` : item.link} className="hover:underline">
        {item.headline}
      </Link>
      {item.receipts?.length > 0 && <details className="mt-1 text-xs text-ink-3">
        <summary className="cursor-pointer">Evidence<span className="sr-only"> for {item.headline}</span></summary>
        {item.receipts.map((receipt, n) => <ReceiptLine key={n} receipt={receipt} className="mt-1 block" />)}
      </details>}
    </li>)}
  </ul>;
}

export function RecentChanges() {
  const [data, setData] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [pending, setPending] = useState(false);
  const [compact, setCompact] = useState(false);
  const generation = useRef(0);
  const reviewing = useRef(false);
  useHashTarget(data);

  const load = useCallback(() => {
    const current = ++generation.current;
    return api<Summary>("/api/delta", { cache: "no-store" })
      .then((next) => {
        if (current !== generation.current) return;
        setData(next);
        setCompact(!!next.reviewed);
      })
      .catch((e) => {
        if (current === generation.current) setError(loadError(e));
      })
      .finally(() => {
        if (current === generation.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    void load();
    return () => { generation.current += 1; };
  }, [load]);

  const refresh = () => {
    if (loading) return;
    reviewing.current = false;
    setPending(false);
    setLoading(true);
    setError("");
    setConflict(false);
    void load();
  };

  const review = async () => {
    if (!data?.snapshot_id || data.review_revision === undefined || data.truncated || reviewing.current || loading || conflict) return;
    reviewing.current = true;
    setPending(true);
    setError("");
    const current = generation.current;
    const identity = sessionRevision();
    try {
      const response = await authenticatedFetch("/api/delta/ack", {
        method: "POST",
        body: JSON.stringify({ snapshot_id: data.snapshot_id, review_revision: data.review_revision }),
      });
      // A refresh can replace this batch while its acknowledgment is in flight.
      if (current !== generation.current) return;
      checkSessionRevision(identity);
      if (!response.ok) {
        if (response.status === 409) setConflict(true);
        throw await errorFromResponse(response);
      }
      const result = await response.json() as { snapshot_id: string; review_revision: number; reviewed: boolean };
      if (current !== generation.current) return;
      checkSessionRevision(identity);
      if (result.snapshot_id !== data.snapshot_id || !result.reviewed) throw new Error("The summary was not marked reviewed. Refresh and try again.");
      setData({ ...data, review_revision: result.review_revision, reviewed: true });
      setCompact(true);
    } catch (e) {
      if (current === generation.current) setError(actionError(e));
    } finally {
      if (current === generation.current) {
        reviewing.current = false;
        setPending(false);
      }
    }
  };

  const items = Array.isArray(data?.items) ? data.items : [];
  const groups = categories.map((label, index) => ({ label, items: items.filter((item) => category(item) === index) }));
  const primary = new Set(groups.slice(0, -1).flatMap((group) => group.items).slice(0, 5));
  const reviewable = items.length > 0 && !!data?.snapshot_id && Number.isInteger(data.review_revision);

  return <Card>
    <h2 id="recent-changes" tabIndex={-1} className={`skein-section-title mb-3 ${HASH_TARGET}`}>Recent changes</h2>
    {data?.window_start && data.window_end
      ? <p className="mb-3 text-xs text-ink-3">{data.window_start} to {data.window_end} · Team dates</p>
      : data && <p className="mb-3 text-xs text-ink-3">The server did not provide the date window.</p>}
    {loading && <p role="status" className="text-sm text-ink-3">Loading recent changes…</p>}
    {error && <p role="alert" className="mb-2 text-sm text-danger">{error} {conflict ? "Refresh the summary before you review it." : "Try again."}</p>}
    {data && <>
      {items.length > 0 && <p className="mb-3 text-xs text-ink-3">{items.length} {items.length === 1 ? "change" : "changes"} in this summary{data.truncated ? " · Partial summary" : ""}</p>}
      {data.truncated && <p className="mb-3 text-sm text-ink-2">Some changes are not included. This summary cannot be marked reviewed. Open the linked pages to check the work.</p>}
      <p role="status" className="text-sm text-ink-2">{data.reviewed ? "This summary is reviewed." : ""}</p>
      <div hidden={compact} className="space-y-3">
        {!items.length && <p className="text-sm text-ink-3">No recent changes in this summary.</p>}
        {groups.map((group) => {
          if (!group.items.length) return null;
          const count = group.items.filter((item) => primary.has(item)).length;
          const more = group.items.slice(count);
          return <div key={group.label}>
            {count > 0 && <>
              <h3 className="mb-2 text-xs font-medium text-ink-2">{group.label} ({group.items.length})</h3>
              <Changes items={group.items.slice(0, count)} />
            </>}
            {more.length > 0 && <details className="mt-2 text-ink-3">
              <summary className="cursor-pointer text-xs font-medium">{count ? `More ${group.label.toLowerCase()}` : group.label} ({more.length})</summary>
              <div className="mt-2"><Changes items={more} /></div>
            </details>}
          </div>;
        })}
      </div>
      {reviewable && !data.reviewed && <p className="mt-4 text-xs text-ink-3">This reviews the full returned summary, including collapsed details. It does not resolve work.</p>}
    </>}
    <div className="mt-3 flex flex-wrap gap-3 text-sm">
      {reviewable && <button type="button" aria-expanded={data?.reviewed ? !compact : undefined}
        aria-disabled={pending || loading || conflict || !!data?.truncated}
        onClick={() => data?.reviewed ? setCompact(!compact) : void review()}
        className="min-h-9 rounded-md border border-line-strong px-3 py-1 text-ink-2 hover:bg-raised aria-disabled:opacity-60">
        {data?.reviewed ? compact ? "Reopen summary" : "Close summary" : pending ? "Marking summary…" : "Mark this summary reviewed"}
      </button>}
      <button type="button" aria-disabled={loading} onClick={refresh} className="min-h-9 rounded-md px-2 py-1 text-ink-3 underline aria-disabled:opacity-60">{error && !data ? "Retry" : "Refresh"}</button>
    </div>
  </Card>;
}

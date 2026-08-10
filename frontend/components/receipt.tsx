"use client";

import Link from "next/link";

import { type Receipt, refHref, splitReceipt } from "@/lib/entity-ref";
import { PeekLink } from "@/components/task-peek";

/** One deterministic receipt, with its row references as links.
 *
 *  Health, findings and the planning cockpit all state their evidence as a
 *  sentence naming exact rows — "milestone #4 'Cutover' overdue since
 *  2026-08-01". The words are unchanged and stay first; the reference becomes
 *  a link in place, so the reader opens the milestone instead of hunting for
 *  it by id on a page of them.
 *
 *  Runs, never `dangerouslySetInnerHTML`: a receipt quotes titles people wrote
 *  (components/artifact-markdown.tsx and components/nav-search.tsx follow the
 *  same rule, for the same reason).
 */
export function ReceiptLine({
  receipt,
  className = "",
}: {
  receipt: Receipt;
  className?: string;
}) {
  const runs = splitReceipt(receipt);
  return (
    <span className={className}>
      {runs.map((run, i) => {
        if (!("ref" in run)) return <span key={i}>{run.text}</span>;
        // A TASK opens the peek, and only PeekLink can open it. `next/link`
        // navigates with pushState and dispatches no event, while TaskPeek
        // syncs on `popstate` and `skein-peek` alone — so a <Link href="?task=5">
        // changed the address bar and opened nothing, on every receipt this
        // app renders. It is not the same bug as the raw <a> on /review: that
        // one reloaded the page, this one does nothing at all.
        if (run.ref.entity === "task")
          return (
            <PeekLink
              key={i}
              taskId={run.ref.id}
              className="decoration-line-strong hover:decoration-ink-2"
            >
              {run.text}
            </PeekLink>
          );
        const href = refHref(run.ref);
        // an entity this build cannot render lands nowhere rather than on a
        // page that does not hold it — the id still reads
        if (!href) return <span key={i}>{run.text}</span>;
        return (
          <Link
            key={i}
            href={href}
            className="underline decoration-line-strong underline-offset-2 hover:decoration-ink-2"
          >
            {run.text}
          </Link>
        );
      })}
    </span>
  );
}

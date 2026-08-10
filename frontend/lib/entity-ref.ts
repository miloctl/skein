/** Where one entity reference lands.
 *
 *  The backend parses `<entity> #<id>` out of a generated receipt
 *  (services/refs.py) and sends `{entity, id}` alongside the sentence. This is
 *  the half that knows which surface renders one row of each kind.
 *
 *  Kept in step with `TARGETS` in services/refs.py. A word the backend sends
 *  and this file does not know renders as plain text — the safe direction. The
 *  reverse invents a link to a page that cannot show the row, which is worse
 *  than the id the reader started with.
 */
export type EntityRef = { entity: string; id: number };

/** A receipt as the API sends it: the sentence first, always.
 *  A reader must be able to act on the words without following anything —
 *  the same sentence goes into artifacts on disk, where no link exists. */
export type Receipt = { message: string; refs: EntityRef[] };

const HREF: Record<string, (id: number) => string> = {
  // the peek opens over whatever page the reader is on, so a task reference
  // never costs them their place (components/task-peek.tsx)
  task: (id) => `?task=${id}`,
  milestone: () => "/dashboard#milestones",
  blocker: () => "/dashboard#blockers",
  question: () => "/dashboard#questions",
  decision: (id) => `/charter#charter-entry-${id}`,
  promise: () => "/portfolio#promises",
  proposal: () => "/review",
  engagement: () => "/dashboard#engagements",
  lesson: (id) => `/dashboard#lesson-${id}`,
  finding: () => "/insights",
  intake: () => "/intake",
};

/** The href for a reference, or "" when this build cannot render that row. */
export function refHref(ref: EntityRef): string {
  return HREF[ref.entity]?.(ref.id) ?? "";
}

/** Split a receipt into text runs and reference runs, in reading order.
 *
 *  Returns runs rather than markup: the caller builds React elements, and a
 *  receipt quotes titles people wrote — treating it as markup would make every
 *  producer of a receipt an injection sink (the same rule the artifact renderer
 *  and the search snippet follow).
 */
export function splitReceipt(
  receipt: Receipt,
): ({ text: string } | { ref: EntityRef; text: string })[] {
  const out: ({ text: string } | { ref: EntityRef; text: string })[] = [];
  let rest = receipt.message;
  for (const ref of receipt.refs ?? []) {
    // matched case-insensitively, because the backend lowercases the entity
    // word and the sentence may capitalize it at the start
    const needle = new RegExp(`\\b${ref.entity}\\s+#${ref.id}\\b`, "i");
    const m = needle.exec(rest);
    if (!m) continue;
    if (m.index > 0) out.push({ text: rest.slice(0, m.index) });
    out.push({ ref, text: m[0] });
    rest = rest.slice(m.index + m[0].length);
  }
  if (rest) out.push({ text: rest });
  return out;
}

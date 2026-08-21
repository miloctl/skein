/** The task id an activity row names, or null.
 *
 *  Keyed on the ACTION, never on the shape of `detail`. Real rows read
 *  "escalated a blocker — #5 …" and "minted an API key — #29 bootstrap":
 *  the entity word lives in the SENTENCE the verb registry produces, and the
 *  detail is a bare `#N` for every entity alike. A parser that read the
 *  detail therefore opened the task panel on a blocker id or an API-key id —
 *  the wrong row, or a row that does not exist, and both look like the
 *  feature working.
 *
 *  The action is authoritative because services/activity.py's registry is
 *  what names the entity in the first place. An action missing from this set
 *  simply gets no link, which is the safe direction: a row with no link is a
 *  row the reader expands, exactly as before.
 *
 *  In lib, not app/activity/page.tsx where it began: My Day's "Since
 *  yesterday" digest reads the same ledger rows, and importing a page module
 *  from another page drags its whole tree into the bundle.
 */
const TASK_ACTIONS = new Set([
  "create_task",
  "update_task",
  "complete_task",
  "delegate_task",
  "claim_task",
  "report_progress",
]);

export function taskRef(action: string, detail: string): number | null {
  if (!TASK_ACTIONS.has(String(action ?? ""))) return null;
  const m = /(?:^|\s)#(\d+)\b/.exec(String(detail ?? ""));
  if (!m) return null;
  const id = Number(m[1]);
  return Number.isInteger(id) && id > 0 ? id : null;
}

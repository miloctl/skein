import { describe, expect, it } from "vitest";

/** Which activity rows get a task link.
 *
 *  Keyed on the ACTION, because the entity word lives in the sentence the
 *  verb registry produces and NOT in `detail`. These cases are taken from
 *  real rows on a running deployment — an earlier version of this test used
 *  invented strings like "blocker #3 'staging down' is escalated", passed,
 *  and shipped a parser that opened the task panel on a blocker id. */

import { taskRef } from "@/app/activity/page";

describe("a task reference in an activity row", () => {
  it("links a row whose action is about a task", () => {
    expect(taskRef("create_task", "#31 Work on the Pokemon proposal")).toBe(31);
    expect(taskRef("update_task", "#31 edited")).toBe(31);
    expect(taskRef("claim_task", "#10 chase the vendor")).toBe(10);
    expect(taskRef("report_progress", "#10 vendor replied")).toBe(10);
  });

  it("never links another entity's id — REAL rows, all bare #N", () => {
    expect(taskRef("escalate_blocker", "#5 blocked on WW vendor (open 24h)")).toBeNull();
    expect(taskRef("mint_key", "#29 bootstrap")).toBeNull();
    expect(taskRef("ask_question", "#5")).toBeNull();
    expect(taskRef("exec_readout", "artifact #8")).toBeNull();
  });

  it("says nothing when the row names no id", () => {
    expect(taskRef("create_task", "")).toBeNull();
    expect(taskRef("delete_chat", "tidied 3 chats")).toBeNull();
  });

  it("refuses a zero or malformed id rather than opening #0", () => {
    expect(taskRef("create_task", "#0 nothing")).toBeNull();
    expect(taskRef("create_task", "#abc")).toBeNull();
  });

  it("gives an unregistered action no link, rather than a guess", () => {
    expect(taskRef("some_future_action", "#12 whatever")).toBeNull();
  });
});

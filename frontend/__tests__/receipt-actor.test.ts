/** The receipt actor: rendered only when the server sent it (chat.py::
 *  _attributed decides once, server-side), and rendered the same way the
 *  stored transcript renders it — the pairing this file exists to pin.
 *  backend/tests/test_specialist_consult.py pins the Python half. */
import { describe, expect, it } from "vitest";
import { receiptLine } from "../app/runtime-provider";

describe("receiptLine actor attribution", () => {
  it("names the signer on a queued receipt, matching the stored form", () => {
    const line = receiptLine({
      kind: "queued",
      entity: "note",
      detail: "risk memo",
      ref: 7,
      actor: "code-reviewer",
    });
    // the stored transcript says "queued for review: note #7 (code-reviewer)"
    // — the chip must carry the same ref-then-actor order or history and the
    // live view disagree about one fact
    expect(line).toContain("note #7 (code-reviewer) needs a human verdict");
  });

  it("adds nothing when the server sent no actor", () => {
    const line = receiptLine({ kind: "queued", entity: "note", detail: "", ref: 3 });
    expect(line).toContain("note #3 needs a human verdict");
    // not a bare "(": the Inbox link's markdown carries one legitimately
    expect(line).not.toContain(") needs a human verdict");
  });

  it("puts the refused actor in the sentence slot, never a suffix", () => {
    // "this agent" in a consult claims the wrong refusee: the gate refused
    // the specialist, and the reader is looking at the orchestrator's turn
    const line = receiptLine({
      kind: "refused",
      entity: "note",
      detail: "code-reviewer is forbidden on note",
      ref: 0,
      actor: "code-reviewer",
    });
    expect(line).toContain("note is forbidden for code-reviewer");
    expect(line).not.toContain("(code-reviewer)");
  });

  it("keeps the refused fallback for the turn head's own refusal", () => {
    const line = receiptLine({ kind: "refused", entity: "note", detail: "", ref: 0 });
    expect(line).toContain("forbidden for this agent");
  });

  it("suffixes a failed write", () => {
    const line = receiptLine({
      kind: "failed",
      entity: "note",
      detail: "too long",
      ref: 0,
      actor: "code-reviewer",
    });
    expect(line).toContain("**Not written** — note (code-reviewer)");
  });
});

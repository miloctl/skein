import { describe, expect, it } from "vitest";

import { refHref } from "@/lib/entity-ref";

/** services/refs.py makes `event #N` a reference in receipts and agendas.
 *  Its href opens the meeting on the calendar, in whatever month it falls. */
describe("an event reference", () => {
  it("lands on the meeting's panel", () => {
    expect(refHref({ entity: "event", id: 7 })).toBe("/calendar?event=7");
  });
});

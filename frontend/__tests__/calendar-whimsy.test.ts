/** The Calendar shows these beside "No meetings, due dates or time away this
 *  month for the kinds you chose" (app/calendar/page.tsx). A kind the reader
 *  turned off, or a teammate's task under "Only my tasks", can still hold
 *  rows, so no line may claim the calendar or the team is empty. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { emptyState, markHydrated } from "@/lib/whimsy";

afterEach(() => {
  vi.useRealTimers();
  delete document.documentElement.dataset.pack;
});

describe("the calendar lines", () => {
  it.each(["loom", "phosphor", "ledger", "atelier", "claw", "hermes"])(
    "claim nothing about what the calendar holds in the %s pack",
    (pack) => {
      markHydrated();
      document.documentElement.dataset.pack = pack;
      vi.useFakeTimers();
      const lines = new Set<string>();
      for (let day = 0; day < 90; day++) {
        vi.setSystemTime(new Date(2026, 0, 1 + day, 12));
        lines.add(emptyState("calendar"));
      }
      expect(lines.size).toBe(3);
      for (const line of lines)
        expect(line).not.toMatch(/\b(no|nothing|empty|blank|clear|free|none)\b|0 /i);
    },
  );
});

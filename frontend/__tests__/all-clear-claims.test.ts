/** My Day shows the allclear line when nothing is addressed to the reader
 *  (app/page.tsx, "Needs you"). The team queue and escalated blockers can
 *  still hold work then, so no line in any pack may claim an empty inbox or
 *  zero blockers. */
import { afterEach, describe, expect, it, vi } from "vitest";

import { emptyState, markHydrated } from "@/lib/whimsy";

afterEach(() => {
  vi.useRealTimers();
  delete document.documentElement.dataset.pack;
});

describe("the allclear lines", () => {
  it.each(["loom", "phosphor", "ledger", "atelier", "claw", "hermes"])(
    "claim nothing about the inbox or blockers in the %s pack",
    (pack) => {
      markHydrated();
      document.documentElement.dataset.pack = pack;
      vi.useFakeTimers();
      const lines = new Set<string>();
      for (let day = 0; day < 90; day++) {
        vi.setSystemTime(new Date(2026, 0, 1 + day, 12));
        lines.add(emptyState("allclear"));
      }
      // every line of the pool was drawn, so the check below covers them all
      expect(lines.size).toBe(3);
      for (const line of lines) expect(line).not.toMatch(/inbox|blocker/i);
    },
  );
});

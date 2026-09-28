import { describe, expect, it } from "vitest";

import { addDays, eventDays, monthGrid, shiftMonth } from "@/lib/calendar";

describe("calendar day keys", () => {
  it("builds a six-week grid that starts on the Monday before the first", () => {
    const grid = monthGrid("2026-10");
    expect(grid).toHaveLength(42);
    expect(grid[0]).toBe("2026-09-28");
    expect(grid.at(-1)).toBe("2026-11-08");
  });

  it("steps across month and year ends in UTC, whatever the browser's zone", () => {
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
    expect(addDays("2026-03-29", 1)).toBe("2026-03-30"); // a DST change in Europe
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
  });

  it("covers the days an event touches", () => {
    // all day: the end is exclusive
    expect(eventDays("2026-10-01", "2026-10-02")).toEqual(["2026-10-01"]);
    expect(eventDays("2026-10-01", "2026-10-04")).toEqual(["2026-10-01", "2026-10-02", "2026-10-03"]);
    expect(eventDays("2026-10-01", null)).toEqual(["2026-10-01"]);
    // timed: an end at midnight does not touch the next day
    expect(eventDays("2026-10-01T22:00", "2026-10-02T00:00")).toEqual(["2026-10-01"]);
    expect(eventDays("2026-10-01T22:00", "2026-10-02T01:00")).toEqual(["2026-10-01", "2026-10-02"]);
    // an end before the start draws the start day alone
    expect(eventDays("2026-10-01T10:00", "2026-10-01T09:00")).toEqual(["2026-10-01"]);
  });
});

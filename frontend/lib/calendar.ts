/** Calendar arithmetic on `YYYY-MM-DD` day keys.
 *
 *  Every step runs in UTC, so the browser's own zone never moves a day. The
 *  team day comes from the server: GET /api/calendar answers `today`, and
 *  every event carries `starts_local`/`ends_local` on the team clock
 *  (services/schedule.py::with_local). A key built from `new Date()` here
 *  would read the browser's zone, which is not the team's.
 */

const DAY_MS = 86_400_000;

function toUtc(key: string): number {
  return Date.UTC(Number(key.slice(0, 4)), Number(key.slice(5, 7)) - 1, Number(key.slice(8, 10)));
}

function fromUtc(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

export function addDays(key: string, n: number): string {
  return fromUtc(toUtc(key) + n * DAY_MS);
}

/** `YYYY-MM` of a day key. */
export function monthOf(key: string): string {
  return key.slice(0, 7);
}

export function shiftMonth(month: string, n: number): string {
  const d = new Date(Date.UTC(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1 + n, 1));
  return d.toISOString().slice(0, 7);
}

/** The 42 days of a six-week grid that holds the month, Monday first. The
 *  span matches services/schedule.py::CALENDAR_MAX_DAYS, and a wider range
 *  is refused there. */
export function monthGrid(month: string): string[] {
  const first = `${month}-01`;
  const weekday = (new Date(toUtc(first)).getUTCDay() + 6) % 7; // Monday = 0
  const start = addDays(first, -weekday);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

export function monthLabel(month: string): string {
  return new Date(toUtc(`${month}-01`)).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function dayLabel(key: string): string {
  return new Date(toUtc(key)).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** The day keys an event covers, on the team clock.
 *
 *  A date-only start is an all-day event, and its end is EXCLUSIVE, as
 *  DTEND;VALUE=DATE is in the ICS feed: 2026-10-01 to 2026-10-02 is one day.
 *  A timed event covers each day it touches, except that an end at exactly
 *  midnight does not touch the day it starts.
 */
export function eventDays(starts: string, ends: string | null): string[] {
  const first = starts.slice(0, 10);
  let last = first;
  if (ends && ends > starts) {
    if (starts.length === 10) last = addDays(ends.slice(0, 10), -1);
    else last = ends.slice(11, 16) === "00:00" ? addDays(ends.slice(0, 10), -1) : ends.slice(0, 10);
  }
  if (last < first) last = first;
  const days: string[] = [];
  // bounded: the server serves at most a 42-day window, and an event longer
  // than that is drawn only on the days the grid shows
  for (let d = first; d <= last && days.length < 400; d = addDays(d, 1)) days.push(d);
  return days;
}

/** Moves `value` by as much as `from` moved to `to`, so an event keeps its
 *  length when its start moves. Each is a day key or a team-clock
 *  `YYYY-MM-DDTHH:MM`, read in UTC so no zone shifts the arithmetic. */
export function shiftBy(value: string, from: string, to: string): string {
  const ms = (s: string) =>
    Date.UTC(
      Number(s.slice(0, 4)),
      Number(s.slice(5, 7)) - 1,
      Number(s.slice(8, 10)),
      Number(s.slice(11, 13) || 0),
      Number(s.slice(14, 16) || 0),
    );
  if (!value || !from || !to) return value;
  const moved = new Date(ms(value) + ms(to) - ms(from)).toISOString();
  return value.length === 10 ? moved.slice(0, 10) : moved.slice(0, 16);
}

/** Inclusive `YYYY-MM-DD` days of a time-away window. */
export function spanDays(first: string, last: string): string[] {
  const days: string[] = [];
  for (let d = first; d <= last && days.length < 400; d = addDays(d, 1)) days.push(d);
  return days;
}

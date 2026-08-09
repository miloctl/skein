"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** Second-level tabs for the three grouped nav sections. Every page that was
 *  a top-level destination keeps its URL — the tabs are plain links. */
const SETS = {
  work: [
    // first in the set: it is the Monday running order, and the pages after
    // it are where the individual numbers are edited
    { href: "/planning", label: "Plan the week" },
    { href: "/portfolio", label: "Health" },
    { href: "/dashboard", label: "Browse" },
    { href: "/insights", label: "Insights" },
    // last in the set: everything here is a record of a ritual that already
    // ran, so it is read after the week is planned rather than during it
    { href: "/artifacts", label: "Reports" },
  ],
  inbox: [
    { href: "/review", label: "Approvals" },
    { href: "/intake", label: "Requests" },
    { href: "/ingest", label: "Paste notes" },
  ],
  team: [
    { href: "/agents", label: "Agents" },
    { href: "/activity", label: "Activity" },
    { href: "/people", label: "1:1s" },
    { href: "/charter", label: "Charter" },
  ],
} as const;

export function SectionTabs({ set }: { set: keyof typeof SETS }) {
  const pathname = usePathname();
  return (
    // wraps, because the Work set holds five tabs and at 360px they measure
    // 355px against 328px of content box. Wrapping over scrolling: five short
    // labels fit two rows, and a scroller hides whichever tab you are not on
    // behind a gesture nobody is told about.
    <nav aria-label="Section" className="mb-5 flex flex-wrap gap-1.5">
      {SETS[set].map((t) => (
        <Link
          key={t.href}
          href={t.href}
          // The tab for the page you are ON navigates to itself, and that
          // navigation DISCARDS the page's query state — /artifacts kept the
          // open report in its pane while `?id=` vanished from the URL, which
          // is the one thing that page exists to let you paste. Every section
          // page with query state has the same exposure. aria-current already
          // says this tab is the destination, so refusing the click costs a
          // reader nothing.
          onClick={(ev) => {
            if (pathname === t.href) ev.preventDefault();
          }}
          aria-current={pathname === t.href ? "page" : undefined}
          className={
            "rounded-full px-3 py-1 text-[13px] transition-colors " +
            (pathname === t.href
              ? "bg-thread-solid font-medium text-white"
              : "bg-raised text-ink-2 hover:bg-line hover:text-ink")
          }
        >
          {t.label}
        </Link>
      ))}
    </nav>
  );
}

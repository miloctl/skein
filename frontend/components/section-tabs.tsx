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
    <nav aria-label="Section" className="mb-5 flex gap-1.5">
      {SETS[set].map((t) => (
        <Link
          key={t.href}
          href={t.href}
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

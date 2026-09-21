export const NAVIGATION = [
  { href: "/", label: "My Day", icon: "day", children: [] },
  { href: "/chat", label: "Chat", icon: "chat", children: [] },
  {
    href: "/portfolio", label: "Work", icon: "work",
    children: [
      { href: "/planning", label: "Plan the week", title: "Planning" },
      { href: "/portfolio", label: "Health" },
      { href: "/dashboard", label: "Browse" },
      { href: "/insights", label: "Insights" },
      { href: "/artifacts", label: "Reports" },
    ],
  },
  {
    href: "/review", label: "Inbox", icon: "inbox",
    children: [
      { href: "/review", label: "Approvals" },
      { href: "/intake", label: "Requests" },
      { href: "/ingest", label: "Paste notes" },
    ],
  },
  {
    href: "/agents", label: "Team", icon: "team",
    children: [
      { href: "/agents", label: "Agents" },
      { href: "/activity", label: "Activity" },
      { href: "/people", label: "1:1s" },
      { href: "/charter", label: "Charter" },
    ],
  },
];

export const ROUTE_TITLES: Record<string, string> = Object.fromEntries([
  ...NAVIGATION.flatMap((group) => group.children.length
    ? group.children.map((child) => [child.href, "title" in child ? child.title : child.label])
    : [[group.href, group.label]]),
  ["/guide", "Field guide"],
  ["/settings", "Settings"],
]);

export function activeNavigation(pathname: string) {
  return NAVIGATION.find((group) =>
    group.href === pathname || group.children.some((child) => child.href === pathname) ||
    (group.label === "Work" && pathname.startsWith("/engagement/")),
  );
}

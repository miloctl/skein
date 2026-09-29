import Link from "next/link";

import { PeekLink } from "@/components/task-peek";

// `?task=12` and `?task=12#comment-9`: what services/comments.py and the
// task notices link to
const TASK_LINK = /^\?task=(\d+)(?:#(.*))?$/;

/** A notice's link. A task link opens the panel over the page: next/link
 *  pushes the address and fires nothing the panel listens for, so it
 *  changed the URL and opened nothing (components/receipt.tsx). */
export function NoticeLink({
  href,
  children,
  className = "hover:underline",
}: {
  href: string;
  children: React.ReactNode;
  className?: string;
}) {
  const task = TASK_LINK.exec(href);
  if (task)
    return (
      <PeekLink taskId={Number(task[1])} anchor={task[2] ?? ""} className={className}>
        {children}
      </PeekLink>
    );
  return (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}

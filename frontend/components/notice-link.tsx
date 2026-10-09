import Link from "next/link";

import { PeekLink } from "@/components/task-peek";

// `?task=12` and `?task=12#comment-9`: what services/comments.py and the
// task notices link to
const TASK_LINK = /^\?task=(\d+)(?:#(.*))?$/;

/** A notice's link. A task link opens the panel over the page: next/link
 *  pushes the address and fires nothing the panel listens for, so it would
 *  change the URL and open nothing (components/receipt.tsx). */
export function NoticeLink({
  href,
  children,
  // wrap-anywhere, not break-words: a title is one long token often enough (a
  // pasted id, a URL), and break-word keeps the token as the min-content
  // width, so the My Day column still grew past a phone's width around it
  className = "wrap-anywhere hover:underline",
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

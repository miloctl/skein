import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReceiptLine } from "@/components/receipt";

/** A task reference in a receipt must OPEN the task, not just change the URL.
 *
 *  The peek is the only surface that renders one task over the page a reader
 *  is already on, and it syncs on `popstate` and the `skein-peek` event alone
 *  (components/task-peek.tsx). A `next/link` navigates with pushState and
 *  announces neither, so a link-rendered task reference moves the address bar
 *  and opens nothing at all — on every receipt in the app.
 *
 *  Asserted through the EVENT rather than the rendered tag: what matters is
 *  that the panel is told, and a future rewrite that keeps an anchor but fires
 *  the event correctly is not a regression.
 */

afterEach(cleanup);

const receipt = {
  message: "task #12 'Wire the gate' untouched since 2026-07-28",
  refs: [{ entity: "task", id: 12 }],
};

describe("a task reference inside a receipt", () => {
  it("announces the peek instead of navigating", () => {
    const heard: number[] = [];
    const onPeek = () =>
      heard.push(Number(new URLSearchParams(window.location.search).get("task")));
    window.addEventListener("skein-peek", onPeek);

    render(<ReceiptLine receipt={receipt} />);
    fireEvent.click(screen.getByText("task #12"));

    window.removeEventListener("skein-peek", onPeek);
    expect(heard).toEqual([12]);
  });

  it("is not an anchor, which would leave the panel closed", () => {
    render(<ReceiptLine receipt={receipt} />);
    const el = screen.getByText("task #12");
    // an <a href="?task=12"> is exactly what shipped and did nothing: the URL
    // changed and TaskPeek was never told
    expect(el.tagName).toBe("BUTTON");
    expect(el.closest("a")).toBeNull();
  });

  it("still renders other entities as links", () => {
    render(
      <ReceiptLine
        receipt={{
          message: "milestone #4 'Cutover' overdue since 2026-08-01",
          refs: [{ entity: "milestone", id: 4 }],
        }}
      />,
    );
    const link = screen.getByRole("link", { name: "milestone #4" });
    expect(link.getAttribute("href")).toBe("/dashboard#milestone-4");
  });

  it("leaves the sentence readable when the entity is unknown to this build", () => {
    // the safe direction: a word the backend sends and this build cannot
    // render stays as text, so the id the reader started with survives
    render(
      <ReceiptLine
        receipt={{ message: "sprocket #9 came loose", refs: [{ entity: "sprocket", id: 9 }] }}
      />,
    );
    expect(screen.getByText(/sprocket #9/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

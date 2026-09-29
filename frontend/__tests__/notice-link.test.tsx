import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A notice about a comment links to `?task=12#comment-9`. next/link pushes
 *  that address and fires nothing the task panel listens for, so the panel
 *  stayed shut (components/receipt.tsx records the same trap). */

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

import { NoticeLink } from "@/components/notice-link";

describe("a notice link", () => {
  it("opens the task panel on the comment it names", () => {
    window.history.replaceState({}, "", "/");
    const peek = vi.fn();
    const hash = vi.fn();
    window.addEventListener("skein-peek", peek);
    window.addEventListener("skein-hash", hash);
    render(<NoticeLink href="?task=12#comment-9">ava commented on task #12.</NoticeLink>);
    const control = screen.getByRole("button", { name: /ava commented on task #12\./ });
    fireEvent.click(control);
    expect(window.location.search).toBe("?task=12");
    expect(window.location.hash).toBe("#comment-9");
    expect(peek).toHaveBeenCalledOnce();
    expect((hash.mock.calls[0][0] as CustomEvent).detail).toEqual({ anchor: "comment-9" });
    window.removeEventListener("skein-peek", peek);
    window.removeEventListener("skein-hash", hash);
  });

  it("stays a plain link for every other destination", () => {
    render(<NoticeLink href="/charter#charter-entry-41">raj commented on decision #41.</NoticeLink>);
    expect(screen.getByRole("link").getAttribute("href")).toBe("/charter#charter-entry-41");
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SectionTabs } from "@/components/section-tabs";

vi.mock("next/navigation", () => ({ usePathname: () => "/artifacts" }));

describe("the tab for the page you are already on", () => {
  it("refuses its own click, so the page keeps its query state", () => {
    // Navigating to the page you are on DISCARDS its query state: /artifacts
    // kept the open report in its pane while `?id=` vanished from the URL —
    // the one thing that page exists to let you paste to a teammate. Every
    // section page carrying query state has the same exposure, which is why
    // this is fixed in the tabs rather than in one page.
    render(<SectionTabs set="work" />);

    const current = screen.getByRole("link", { name: "Reports" });
    expect(current.getAttribute("aria-current")).toBe("page");
    // fireEvent returns false when a handler called preventDefault
    expect(fireEvent.click(current)).toBe(false);

    const other = screen.getByRole("link", { name: "Health" });
    expect(other.getAttribute("aria-current")).toBeNull();
    expect(fireEvent.click(other)).toBe(true);
  });
});

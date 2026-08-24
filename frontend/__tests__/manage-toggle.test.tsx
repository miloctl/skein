import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { ManageToggle } from "@/components/manage-toggle";

beforeEach(() => window.localStorage.clear());

describe("Management view", () => {
  it("shows its current state and keeps aria-pressed in sync", () => {
    render(<ManageToggle />);
    const toggle = screen.getByRole("button", { name: /Management view: Off/ });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");

    fireEvent.click(toggle);

    const on = screen.getByRole("button", { name: /Management view: On/ });
    expect(on.getAttribute("aria-pressed")).toBe("true");
    expect(window.localStorage.getItem("skein-manage")).toBe("1");
  });
});

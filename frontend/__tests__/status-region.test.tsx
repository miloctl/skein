import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StatusRegion } from "@/components/status-region";
import { dismissStatus, reportStatus } from "@/lib/status";

/** The replacement for window.alert keeps both live regions mounted before
 *  text arrives. A keyed child makes equal consecutive messages change the DOM
 *  without recreating the live region that assistive technology watches. */
const report = (message: string, tone?: "failure" | "confirmation") =>
  act(() => reportStatus(message, tone));

afterEach(() => act(() => dismissStatus()));

describe("StatusRegion", () => {
  it("mounts empty polite and assertive regions before a message arrives", () => {
    render(<StatusRegion />);
    expect(screen.getByRole("status").textContent).toBe("");
    expect(screen.getByRole("alert").textContent).toBe("");
  });

  it("puts a failure in the assertive region", () => {
    render(<StatusRegion />);
    report("Cannot reach the backend.");
    expect(screen.getByRole("alert").textContent).toContain("Cannot reach the backend.");
    expect(screen.getByRole("status").textContent).toBe("");
  });

  it("puts a confirmation in the polite region", () => {
    render(<StatusRegion />);
    report("Applied the theme saved on your profile.", "confirmation");
    expect(screen.getByRole("status").textContent).toContain("Applied the theme");
    expect(screen.getByRole("alert").textContent).toBe("");
  });

  it("lets a failure be dismissed, and a confirmation carries no dismiss", () => {
    render(<StatusRegion />);
    report("that failed");
    fireEvent.click(screen.getByRole("button", { name: "dismiss" }));
    expect(screen.getByRole("alert").textContent).toBe("");

    report("that worked", "confirmation");
    expect(screen.queryByRole("button", { name: "dismiss" })).toBeNull();
  });

  it("keeps the region mounted and replaces an equal repeated message", () => {
    render(<StatusRegion />);
    report("same words");
    const region = screen.getByRole("alert");
    const first = region.firstChild;
    report("same words");
    expect(screen.getByRole("alert")).toBe(region);
    expect(region.firstChild).not.toBe(first);
  });

  it("announces one message at a time, the newest", () => {
    render(<StatusRegion />);
    report("first failure");
    report("second failure");
    expect(screen.getByRole("alert").textContent).toContain("second failure");
    expect(screen.getByRole("alert").textContent).not.toContain("first failure");
  });
});

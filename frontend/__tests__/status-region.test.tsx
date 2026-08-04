import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { StatusRegion } from "@/components/status-region";
import { dismissStatus, reportStatus } from "@/lib/status";

/** The replacement for 36 window.alert() calls. window.alert IS announced by
 *  assistive tech, so a styled div with no live role would be a downgrade —
 *  these pin the two roles, the two lifetimes, and the remount that makes a
 *  repeated message announce twice.
 *
 *  act() because the store lives outside React: reportStatus notifies
 *  useSyncExternalStore subscribers directly, and without act() the re-render
 *  has not flushed by the time the assertion runs. */
const report = (message: string, tone?: "failure" | "confirmation") =>
  act(() => reportStatus(message, tone));

afterEach(() => act(() => dismissStatus()));

describe("StatusRegion", () => {
  it("renders nothing until something is reported", () => {
    const { container } = render(<StatusRegion />);
    expect(container.firstChild).toBeNull();
  });

  it("gives a failure the assertive role, so it interrupts", () => {
    render(<StatusRegion />);
    report("Cannot reach the backend.");
    expect(screen.getByRole("alert").textContent).toContain("Cannot reach the backend.");
  });

  it("gives a confirmation the polite role, so it waits its turn", () => {
    render(<StatusRegion />);
    report("Applied the theme saved on your profile.", "confirmation");
    expect(screen.getByRole("status").textContent).toContain("Applied the theme");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("lets a failure be dismissed, and a confirmation carries no dismiss", () => {
    render(<StatusRegion />);
    report("that failed");
    fireEvent.click(screen.getByRole("button", { name: "dismiss" }));
    expect(screen.queryByRole("alert")).toBeNull();

    report("that worked", "confirmation");
    expect(screen.queryByRole("button", { name: "dismiss" })).toBeNull();
  });

  it("remounts on a repeated message, or the second one is never announced", () => {
    render(<StatusRegion />);
    report("same words");
    const first = screen.getByRole("alert");
    report("same words");
    // a live region announces on DOM change; identical text in the same node
    // changes nothing, so the id must make it a new node
    expect(screen.getByRole("alert")).not.toBe(first);
  });

  it("shows one message at a time, the newest", () => {
    render(<StatusRegion />);
    report("first failure");
    report("second failure");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByRole("alert").textContent).toContain("second failure");
  });
});

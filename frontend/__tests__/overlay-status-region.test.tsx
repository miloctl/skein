import { act, fireEvent, render, screen } from "@testing-library/react";
import { createPortal } from "react-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Both overlays inert every body child outside themselves. The app shell
 *  mounts StatusRegion's nodes as body children too (app/layout.tsx), so an
 *  overlay that inerts them silences every announcement it makes and leaves
 *  the failure toast's dismiss button dead under the pointer. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path.includes("worklog")
        ? Promise.resolve([])
        : Promise.resolve({ id: 4, title: "Build the happy path", status: "todo" }),
  };
});

import { CapturePalette } from "@/components/capture-palette";
import { StatusRegion } from "@/components/status-region";
import { TaskPeek } from "@/components/task-peek";
import { dismissStatus, reportStatus } from "@/lib/status";

// a portal puts the nodes straight into <body>, where the app shell has them
const shell = () => createPortal(<StatusRegion />, document.body);

beforeEach(() => window.history.replaceState({}, "", "/"));
afterEach(() => act(() => dismissStatus()));

function expectStatusLive() {
  const live = document.body.querySelectorAll(":scope > [aria-live]");
  expect(live).toHaveLength(2);
  live.forEach((region) => expect(region.hasAttribute("inert")).toBe(false));
  expect(screen.getByRole("button", { name: "dismiss" }).closest("[inert]")).toBeNull();
}

describe("an open overlay and the status region", () => {
  it("leaves the live regions and the toast out of the task panel's inert", async () => {
    render(<>{shell()}</>);
    act(() => reportStatus("that failed"));
    window.history.replaceState({}, "", "/?task=4");
    render(<TaskPeek />);
    act(() => window.dispatchEvent(new PopStateEvent("popstate")));
    await screen.findByRole("dialog");
    expectStatusLive();
  });

  it("leaves the live regions and the toast out of the capture dialog's inert", () => {
    render(<>{shell()}</>);
    act(() => reportStatus("that failed"));
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(new Event("skein-capture-open"));
    });
    expect(screen.getByLabelText("What to capture")).toBeTruthy();
    expectStatusLive();
    fireEvent.keyDown(window, { key: "Escape" });
  });
});

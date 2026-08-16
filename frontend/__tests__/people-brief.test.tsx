import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

/** A refused or in-flight brief is not empty data. The API always returns a
 *  Brief object on success, so the card must not claim that no brief exists
 *  before it receives one. */

const identity = vi.hoisted(() => ({ strong: false }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/whoami")
        return Promise.resolve({
          user: "tester",
          strong: identity.strong,
          admin: false,
          can_administer: false,
          keys_minted: 0,
        });
      if (path === "/api/users")
        return Promise.resolve([{ name: "dana", kind: "human" }]);
      if (path.startsWith("/api/private/notes"))
        return identity.strong
          ? Promise.resolve([
              {
                id: 1,
                person: "dana",
                kind: "note",
                body: "private launch note",
                created_at: "2026-08-16T10:00:00Z",
              },
            ])
          : Promise.reject(new Error("This request requires strong identity."));
      if (path.startsWith("/api/private/brief/"))
        return identity.strong
          ? Promise.resolve({
              person: "dana",
              since: "2026-08-01",
              standups: [],
              open_blockers: [],
              open_questions: [],
              in_progress: [],
              recently_done: [],
              promises_made: [],
              feedback_gap_days: null,
              nudge: "",
            })
          : Promise.reject(new Error("This request requires strong identity."));
      return Promise.resolve([]);
    },
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => "/people",
}));

import PeoplePage from "@/app/people/page";

beforeEach(() => {
  identity.strong = false;
  window.localStorage.clear();
});

describe("the 1:1 identity boundary", () => {
  it("reports a refused brief, never an empty brief", async () => {
    render(<PeoplePage />);
    fireEvent.click(await screen.findByRole("button", { name: "dana" }));
    expect(
      (
        await screen.findAllByText(
          /Could not load this page: This request requires strong identity/,
        )
      ).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("no brief available")).toBeNull();
    expect(await axe(document.body)).toHaveNoViolations();
  });

  it("does not ask a strong deployment sign-in for a personal key", async () => {
    identity.strong = true;
    render(<PeoplePage />);

    await waitFor(() =>
      expect(screen.queryByText(/Private notes need your personal API key/)).toBeNull(),
    );
    expect(screen.queryByRole("button", { name: "Request a key" })).toBeNull();
  });

  it("gives weak identity the complete recovery action", async () => {
    render(<PeoplePage />);

    expect(
      await screen.findByText(
        /Private notes require strong identity\. If deployment sign-in is available, use it\. Otherwise, use a personal API key\./,
      ),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Settings" })).toBeTruthy();
  });

  it("clears private data and refreshes when credentials change", async () => {
    identity.strong = true;
    render(<PeoplePage />);
    fireEvent.click(await screen.findByRole("button", { name: "dana" }));
    expect(await screen.findByText("private launch note")).toBeTruthy();

    identity.strong = false;
    window.dispatchEvent(new Event("storage"));

    expect(
      await screen.findByText(/Private notes require strong identity/),
    ).toBeTruthy();
    expect(screen.queryByText("private launch note")).toBeNull();
    expect(screen.getByText(/Pick a teammate above/)).toBeTruthy();
  });
});

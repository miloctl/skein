import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  whoamiRequest: null as Promise<unknown> | null,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "local-user",
    api: (path: string) => {
      if (path === "/api/whoami")
        return (
          mocks.whoamiRequest ??
          Promise.resolve({
            user: "resolved-user",
            strong: true,
            admin: false,
            can_administer: false,
            keys_minted: 1,
          })
        );
      return Promise.reject(new Error("not part of this test"));
    },
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

import SettingsPage from "@/app/settings/page";

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("skein-user", "local-user");
  window.localStorage.setItem("skein-onboarded:local-user", "1");
  window.localStorage.setItem("skein-onboarded:resolved-user", "1");
  mocks.whoamiRequest = null;
});

describe("Guided First Week settings", () => {
  it("keeps First Watch available for returning users", async () => {
    const starts = vi.fn();
    window.addEventListener("skein-first-watch-start", starts);
    render(<SettingsPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Start or resume First Watch" }),
    );

    expect(starts).toHaveBeenCalledOnce();
    window.removeEventListener("skein-first-watch-start", starts);
  });

  it("restores the checklist for the server-resolved identity", async () => {
    render(<SettingsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bring it back" }));

    await waitFor(() =>
      expect(window.localStorage.getItem("skein-onboarded:resolved-user")).toBeNull(),
    );
    expect(window.localStorage.getItem("skein-onboarded:local-user")).toBe("1");
  });

  it("claims nothing about dismissed cards until the identity resolves", async () => {
    // The flag is keyed to the resolved user, so while whoami is in flight
    // "Nothing is dismissed" is a verdict about a key the page cannot read.
    mocks.whoamiRequest = new Promise(() => {}); // never resolves

    render(<SettingsPage />);

    expect(
      await screen.findByText(/Skein resolves your identity first/),
    ).toBeTruthy();
    expect(screen.queryByText(/Nothing is dismissed/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Bring it back" })).toBeNull();
  });
});

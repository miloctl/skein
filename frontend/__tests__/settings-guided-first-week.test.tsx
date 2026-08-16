import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "local-user",
    api: (path: string) => {
      if (path === "/api/whoami")
        return Promise.resolve({
          user: "resolved-user",
          strong: true,
          admin: false,
          keys_minted: 1,
        });
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
});

describe("Guided First Week settings", () => {
  it("restores the checklist for the server-resolved identity", async () => {
    render(<SettingsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Bring it back" }));

    await waitFor(() =>
      expect(window.localStorage.getItem("skein-onboarded:resolved-user")).toBeNull(),
    );
    expect(window.localStorage.getItem("skein-onboarded:local-user")).toBe("1");
  });
});

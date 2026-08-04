import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

/** The brief card used to render "no brief available" for a fetch that was
 *  refused (403 without a key) or still in flight — a claim about data never
 *  received. The API always returns a Brief object, so that text was never a
 *  real empty state at all. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      if (path === "/api/users")
        return Promise.resolve([{ name: "dana", kind: "human" }]);
      if (path.startsWith("/api/private/brief/"))
        return Promise.reject(new Error("this surface needs your API key"));
      return Promise.resolve([]);
    },
  };
});

vi.mock("next/navigation", () => ({
  usePathname: () => "/people",
}));

import PeoplePage from "@/app/people/page";

describe("the 1:1 brief when the fetch is refused", () => {
  it("reports the refusal, never an empty brief", async () => {
    render(<PeoplePage />);
    fireEvent.click(await screen.findByRole("button", { name: "dana" }));
    expect(
      await screen.findByText(/Could not load this page\. this surface needs your API key/),
    ).toBeTruthy();
    expect(screen.queryByText("no brief available")).toBeNull();
    expect(await axe(document.body)).toHaveNoViolations();
  });
});

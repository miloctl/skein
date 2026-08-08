import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The attention count in the tab title. The immediate notification tier
 *  reaches a person who is in their editor only through the tab, so this is
 *  the delivery half of a signal the product already computes.
 *
 *  The MutationObserver is the part a later edit breaks: Next re-applies the
 *  route's metadata title on navigation, and a plain assignment loses the
 *  count depending on which effect ran last. The last test here fails if
 *  someone simplifies it back to `document.title = ...`. */

const count = { value: 0 };

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path.startsWith("/api/attention")
        ? Promise.resolve({ count: count.value })
        : Promise.resolve({}),
    authConfig: () => Promise.resolve({ mode: "trusted-header" }),
  };
});

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

import { Nav } from "@/components/nav";

beforeEach(() => {
  document.title = "Skein";
});
afterEach(() => {
  count.value = 0;
});

describe("the tab title", () => {
  it("carries the count when work is waiting", async () => {
    count.value = 3;
    render(<Nav />);
    await waitFor(() => expect(document.title).toBe("(3) Skein"));
  });

  it("stays clean at zero — an empty inbox must not look like one item", async () => {
    count.value = 0;
    render(<Nav />);
    await waitFor(() => expect(document.title).toBe("Skein"));
  });

  it("never stacks prefixes when the title is rewritten", async () => {
    count.value = 2;
    render(<Nav />);
    await waitFor(() => expect(document.title).toBe("(2) Skein"));
    // what a route change does: the metadata title lands on top of ours
    document.title = "Skein";
    await waitFor(() => expect(document.title).toBe("(2) Skein"));
    expect(document.title.match(/\(/g)?.length).toBe(1);
  });
});

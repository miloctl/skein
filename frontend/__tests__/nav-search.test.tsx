import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The nav search box. GET /api/search and GET /api/ask both shipped working
 *  with no consumer anywhere in the app; this is the surface that delivers
 *  them.
 *
 *  The load-bearing test here is the last one. FTS5 wraps matches in <b>, and
 *  the snippet is built from indexed row text — every task title, note body
 *  and decision anyone has written. Rendered with dangerouslySetInnerHTML it
 *  would be a stored-XSS sink reachable by any teammate. */

const calls: string[] = [];
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      calls.push(path);
      if (path.startsWith("/api/ask"))
        return Promise.resolve({
          question: "q",
          citations: [
            { ref: "decision #3", title: "Postgres over MySQL", snippet: "we chose <b>postgres</b>" },
          ],
          note: "",
        });
      return Promise.resolve([
        {
          entity: "note",
          entity_id: 7,
          title: "vendor call",
          snippet: '<b>vendor</b> said <img src=x onerror="alert(1)"> next week',
        },
      ]);
    },
  };
});

import { NavSearch } from "@/components/nav-search";

describe("the nav search box", () => {
  it("searches on Enter", async () => {
    calls.length = 0;
    render(<NavSearch />);
    const input = screen.getByLabelText(/Search Skein/);
    fireEvent.change(input, { target: { value: "vendor" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("vendor call")).toBeTruthy());
    expect(calls.some((c) => c.startsWith("/api/search?q=vendor"))).toBe(true);
  });

  it("routes a leading ? to /ask, which answers with citations", async () => {
    calls.length = 0;
    render(<NavSearch />);
    const input = screen.getByLabelText(/Search Skein/);
    fireEvent.change(input, { target: { value: "?what did we pick" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(screen.getByText("decision #3")).toBeTruthy());
    const asked = calls.find((c) => c.startsWith("/api/ask"));
    expect(asked).toBeTruthy();
    // the prefix is the ROUTING, never part of the question
    expect(asked).not.toContain("%3F");
  });

  it("renders a snippet as text, never as markup", async () => {
    render(<NavSearch />);
    const input = screen.getByLabelText(/Search Skein/);
    fireEvent.change(input, { target: { value: "vendor" } });
    fireEvent.keyDown(input, { key: "Enter" });
    const region = await screen.findByRole("region", { name: "Search results" });
    // the <b> became emphasis...
    await waitFor(() => expect(region.querySelector("mark")?.textContent).toBe("vendor"));
    // ...and the tag that was NOT ours stayed literal text
    expect(region.querySelector("img")).toBeNull();
    expect(region.textContent).toContain('<img src=x onerror="alert(1)">');
  });
});

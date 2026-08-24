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
      if (path.includes("%2384"))
        return Promise.resolve([
          {
            entity: "task",
            entity_id: 84,
            title: "Exact task",
            snippet: "",
          },
          {
            entity: "note",
            entity_id: 7,
            title: "related note",
            snippet: "",
          },
        ]);
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

  it("shows how to submit and separates an exact reference", async () => {
    render(<NavSearch />);
    const input = screen.getByLabelText(/Search Skein/);
    expect(input.getAttribute("enterkeyhint")).toBe("search");

    fireEvent.change(input, { target: { value: "#84" } });
    expect(screen.getByText("Enter")).toBeTruthy();
    fireEvent.keyDown(input, { key: "Enter" });

    expect(await screen.findByText("Exact match")).toBeTruthy();
    expect(screen.getByText("Related results")).toBeTruthy();
    expect(screen.getByText("Exact task")).toBeTruthy();
    expect(screen.getByText("related note")).toBeTruthy();
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

  it("links a non-task hit to the page that lists it", async () => {
    render(<NavSearch />);
    const input = screen.getByLabelText(/Search Skein/);
    fireEvent.change(input, { target: { value: "vendor" } });
    fireEvent.keyDown(input, { key: "Enter" });
    // a span here is the dead end the box shipped with: every hit that was
    // not a task could be read but never opened
    const link = await screen.findByRole("link", { name: /note #7/ });
    expect(link.getAttribute("href")).toBe("/dashboard");
  });

  it("links a non-task citation to the page that lists it", async () => {
    render(<NavSearch />);
    const input = screen.getByLabelText(/Search Skein/);
    fireEvent.change(input, { target: { value: "?what did we pick" } });
    fireEvent.keyDown(input, { key: "Enter" });
    const link = await screen.findByRole("link", { name: /decision #3/ });
    expect(link.getAttribute("href")).toBe("/charter#charter-entry-3");
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

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  pathname: "/review",
  provider: "ollama",
  providerError: "",
  guideError: false,
  calls: [] as string[],
}));

vi.mock("next/navigation", () => ({ usePathname: () => state.pathname }));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) => {
      state.calls.push(path);
      if (path === "/api/agents/status")
        return Promise.resolve({
          provider: state.provider,
          provider_error: state.providerError,
        });
      if (path.startsWith("/api/field-guide/for")) {
        if (state.guideError) return Promise.reject(new Error("Guide unavailable"));
        return Promise.resolve({
          cards: [
            {
              id: "review",
              feature: "Review queue",
              knot: "Sheet bend",
              pitch: "Keep agent changes under human control.",
              how: "Open a proposal and record a verdict.",
              link: "/review",
            },
          ],
        });
      }
      return Promise.resolve({});
    },
  };
});

import { PageHelp } from "@/components/page-help";

beforeEach(() => {
  state.pathname = "/review";
  state.provider = "ollama";
  state.providerError = "";
  state.guideError = false;
  state.calls.length = 0;
});

describe("page help", () => {
  it("loads guidance only after the user opens it", async () => {
    render(<PageHelp />);
    expect(state.calls).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));

    expect(await screen.findByText("Review queue")).toBeTruthy();
    expect(state.calls).toContain("/api/field-guide/for?path=%2Freview");
    expect(screen.getByText("Open a proposal and record a verdict.")).toBeTruthy();
  });

  it("offers a visible Bosun handoff only with a live provider", async () => {
    const { unmount } = render(<PageHelp />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    const ask = await screen.findByRole("link", {
      name: "Ask the Bosun about this page",
    });
    const href = ask.getAttribute("href") ?? "";
    expect(href).toContain("/chat?compose=");
    expect(new URL(href, "http://skein.test").searchParams.get("compose")).toBe(
      "/as bosun I am on the /review page. ",
    );

    unmount();
    state.provider = "mock";
    render(<PageHelp />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    await waitFor(() => expect(state.calls).toContain("/api/agents/status"));
    expect(
      screen.queryByRole("link", { name: "Ask the Bosun about this page" }),
    ).toBeNull();
  });

  it("hides the Bosun handoff when the configured provider fell back to mock", async () => {
    state.provider = "anthropic";
    state.providerError = "missing credentials";
    render(<PageHelp />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    await screen.findByText("Review queue");
    await act(async () => {
      await Promise.resolve();
    });
    expect(
      screen.queryByRole("link", { name: "Ask the Bosun about this page" }),
    ).toBeNull();
  });

  it("moves focus into help and restores it after Escape", async () => {
    render(<PageHelp />);
    const trigger = screen.getByRole("button", { name: "Help for this page" });
    fireEvent.click(trigger);
    const close = await screen.findByRole("button", { name: "Close page help" });
    await waitFor(() => expect(document.activeElement).toBe(close));

    fireEvent.keyDown(close, { key: "Escape" });

    expect(screen.queryByRole("dialog", { name: "Help for this page" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("closes when keyboard focus leaves the help widget", async () => {
    render(
      <>
        <PageHelp />
        <button type="button">Next control</button>
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    const ask = await screen.findByRole("link", {
      name: "Ask the Bosun about this page",
    });
    const next = screen.getByRole("button", { name: "Next control" });

    fireEvent.blur(ask, { relatedTarget: next });
    fireEvent.focus(next);

    expect(screen.queryByRole("dialog", { name: "Help for this page" })).toBeNull();
  });

  it("announces a page-help load failure", async () => {
    state.guideError = true;
    render(<PageHelp />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Guide unavailable");
  });

  it("does not duplicate help on Chat or the full Field guide", () => {
    state.pathname = "/chat";
    const { rerender } = render(<PageHelp />);
    expect(screen.queryByRole("button", { name: "Help for this page" })).toBeNull();

    state.pathname = "/guide";
    rerender(<PageHelp />);
    expect(screen.queryByRole("button", { name: "Help for this page" })).toBeNull();
  });
});

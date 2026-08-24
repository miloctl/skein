import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  pathname: "/review",
  provider: "ollama",
  providerError: "",
  guideError: false,
  cardId: "review",
  cardFeature: "Review queue",
  cardLink: "/review",
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
              id: state.cardId,
              feature: state.cardFeature,
              knot: "Sheet bend",
              pitch: "Keep agent changes under human control.",
              how: "Open a proposal and record a verdict.",
              link: state.cardLink,
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
  state.cardId = "review";
  state.cardFeature = "Review queue";
  state.cardLink = "/review";
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

  it("starts First Watch only from its My Day card", async () => {
    state.pathname = "/";
    state.cardId = "first_watch";
    state.cardFeature = "First Watch";
    state.cardLink = "/?tour=first-watch";
    const starts = vi.fn();
    window.addEventListener("skein-first-watch-start", starts);
    render(<PageHelp />);
    const trigger = screen.getByRole("button", { name: "Help for this page" });
    fireEvent.click(trigger);

    fireEvent.click(await screen.findByRole("button", { name: "Start First Watch" }));
    expect(document.activeElement).toBe(trigger);
    await waitFor(() => expect(starts).toHaveBeenCalledOnce());
    expect(screen.queryByRole("dialog", { name: "Help for this page" })).toBeNull();
    window.removeEventListener("skein-first-watch-start", starts);
  });

  it("reloads cards when a persistent shell changes route", async () => {
    state.pathname = "/";
    state.cardId = "first_watch";
    state.cardFeature = "First Watch";
    state.cardLink = "/?tour=first-watch";
    const view = render(<PageHelp />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    await screen.findByText("First Watch");
    fireEvent.click(screen.getByRole("button", { name: "Close page help" }));

    state.pathname = "/review";
    state.cardId = "review";
    state.cardFeature = "Review queue";
    state.cardLink = "/review";
    view.rerender(<PageHelp />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));

    expect(await screen.findByText("Review queue")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start First Watch" })).toBeNull();
    expect(state.calls).toContain("/api/field-guide/for?path=%2F");
    expect(state.calls).toContain("/api/field-guide/for?path=%2Freview");
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

  it("holds the header's width on the Field guide instead of unmounting", () => {
    state.pathname = "/guide";
    const { container } = render(<PageHelp />);
    expect(screen.queryByRole("button", { name: "Help for this page" })).toBeNull();
    // shrink-0 as well as the width: the cluster is min-w-0 and the search box
    // is flex-1 below sm, so a shrinkable spacer collapses and /guide reflows
    // again on exactly the narrow viewports this was reported from
    expect(container.querySelector(".size-8.shrink-0")).not.toBeNull();
  });

  it("offers help on Chat, handing the Bosun prefill to the live composer", async () => {
    state.pathname = "/chat";
    const prefills: string[] = [];
    const onPrefill = (e: Event) =>
      prefills.push((e as CustomEvent<string>).detail);
    window.addEventListener("skein-chat-compose", onPrefill);
    render(<PageHelp />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    expect(await screen.findByText("Review queue")).toBeTruthy();

    // a same-route Link would leave a stale ?compose= and do nothing —
    // thread.tsx reads it on mount only
    const ask = screen.getByRole("button", {
      name: "Ask the Bosun about this page",
    });
    expect(ask.closest("a")).toBeNull();
    fireEvent.click(ask);
    expect(prefills).toEqual(["/as bosun I am on the /chat page. "]);
    window.removeEventListener("skein-chat-compose", onPrefill);
  });

  it("drops the card link when the card points at the current route", async () => {
    render(<PageHelp />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    // the fixture card links to /review and the reader is on /review — the
    // link would close the panel and go nowhere
    expect(await screen.findByText("Review queue")).toBeTruthy();
    expect(screen.queryByText("Open Review queue")).toBeNull();
  });

  it("keeps the card link when the card points at an anchor on this route", async () => {
    state.cardLink = "/review#proposal-4";
    render(<PageHelp />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    expect(await screen.findByText("Open Review queue")).toBeTruthy();
  });
});

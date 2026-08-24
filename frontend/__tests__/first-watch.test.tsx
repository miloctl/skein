import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { axe } from "vitest-axe";

const state = vi.hoisted(() => ({
  user: "resolved-user",
  pathname: "/",
  provider: "mock",
  providerError: "",
  statusFails: false,
  whoamiFails: false,
  pushes: [] as string[],
  calls: [] as Array<{ path: string; method: string }>,
}));

const links: Record<string, string> = {
  first_watch: "/?tour=first-watch",
  capture: "/",
  task_peek: "/dashboard",
  search: "/chat",
  review: "/review",
  activity_feed: "/activity",
  bosun: "/chat?as=bosun",
};
const steps = [
  "first_watch",
  "capture",
  "task_peek",
  "search",
  "review",
  "activity_feed",
  "bosun",
].map((id) => ({
  id,
  feature: id === "first_watch" ? "First Watch" : id,
  knot: "Knot",
  pitch: id === "first_watch" ? "Follow one real task through Skein." : `${id} pitch`,
  how: `${id} instructions`,
  link: links[id],
}));

vi.mock("next/navigation", () => ({
  usePathname: () => state.pathname,
  useRouter: () => ({ push: (path: string) => state.pushes.push(path) }),
}));
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      state.calls.push({ path, method: init?.method ?? "GET" });
      if (path === "/api/whoami")
        return state.whoamiFails
          ? Promise.reject(new Error("backend unavailable"))
          : Promise.resolve({ user: state.user });
      if (path === "/api/agents/status")
        return state.statusFails
          ? Promise.reject(new Error("status unavailable"))
          : Promise.resolve({
              provider: state.provider,
              provider_error: state.providerError,
            });
      if (path === "/api/field-guide/first-watch" && init?.method === "POST")
        return Promise.resolve({ started: true });
      if (path === "/api/field-guide/first-watch") return Promise.resolve({ steps });
      return Promise.resolve({});
    },
  };
});

import { FirstWatch } from "@/components/first-watch";
import { startFirstWatch } from "@/lib/first-watch";
import { dismissStatus, getStatus } from "@/lib/status";

beforeEach(() => {
  state.user = "resolved-user";
  state.pathname = "/";
  state.provider = "mock";
  state.providerError = "";
  state.statusFails = false;
  state.whoamiFails = false;
  state.pushes.length = 0;
  state.calls.length = 0;
  window.localStorage.clear();
  dismissStatus();
  window.history.replaceState({}, "", "/");
});

describe("First Watch", () => {
  it("does not blame a passive restoration when the backend is unavailable", async () => {
    state.whoamiFails = true;
    render(<FirstWatch />);

    await waitFor(() => expect(state.calls.some((call) => call.path === "/api/whoami")).toBe(true));
    expect(getStatus()).toBeNull();
  });

  it("reports the same failure after an explicit start action", async () => {
    state.whoamiFails = true;
    render(<FirstWatch />);
    await waitFor(() => expect(state.calls.some((call) => call.path === "/api/whoami")).toBe(true));

    act(() => startFirstWatch());

    await waitFor(() =>
      expect(getStatus()?.message).toContain("First Watch did not start"),
    );
  });

  it("starts from the fixed event under the server-resolved identity", async () => {
    window.localStorage.setItem("skein-user", "browser-name");
    render(<FirstWatch />);

    act(() => startFirstWatch());

    const heading = await screen.findByRole("heading", { name: "Bosun’s First Watch" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    expect(screen.getByText("Follow one real task through Skein.")).toBeTruthy();
    await waitFor(() =>
      expect(state.calls).toContainEqual({
        path: "/api/field-guide/first-watch",
        method: "POST",
      }),
    );
    expect(window.localStorage.getItem("skein-first-watch:browser-name")).toBeNull();
    expect(window.localStorage.getItem("skein-first-watch:resolved-user")).toContain(
      '"stepId":"first_watch"',
    );
  });

  it("consumes the start query once and preserves unrelated URL state", async () => {
    window.history.replaceState({}, "", "/?task=7&tour=first-watch#question-3");
    render(<FirstWatch />);

    await screen.findByRole("heading", { name: "Bosun’s First Watch" });

    expect(window.location.search).toBe("?task=7");
    expect(window.location.hash).toBe("#question-3");
  });

  it("pauses into one persistent resume control and starts over without touching the task", async () => {
    render(<FirstWatch />);
    act(() => startFirstWatch());
    await screen.findByRole("heading", { name: "Bosun’s First Watch" });

    fireEvent.click(screen.getByRole("button", { name: "Pause First Watch" }));
    const resume = screen.getByRole("button", { name: "Resume First Watch, introduction" });
    await waitFor(() => expect(document.activeElement).toBe(resume));
    expect(screen.getByText("Your task stays in Skein.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Start over" }));
    expect(await screen.findByRole("heading", { name: "Bosun’s First Watch" })).toBeTruthy();
    expect(window.localStorage.getItem("skein-first-watch:resolved-user")).toContain(
      '"stepId":"first_watch"',
    );
  });

  it("keeps the introduction and paused control structurally accessible", async () => {
    const view = render(<FirstWatch />);
    expect(screen.getByTestId("first-watch-status").textContent).toBe("");
    act(() => startFirstWatch());
    await screen.findByRole("heading", { name: "Bosun’s First Watch" });
    expect(screen.getByTestId("first-watch-status").textContent).toBe("");
    expect(await axe(view.container)).toHaveNoViolations();

    fireEvent.click(screen.getByRole("button", { name: "Pause First Watch" }));
    expect(await axe(view.container)).toHaveNoViolations();
  });

  it("carries only a successful task receipt into Work and Task Peek", async () => {
    const captureDetails: unknown[] = [];
    const onCapture = (event: Event) =>
      captureDetails.push((event as CustomEvent).detail);
    window.addEventListener("skein-capture-open", onCapture);
    const view = render(<FirstWatch />);
    act(() => startFirstWatch());
    await screen.findByRole("heading", { name: "Bosun’s First Watch" });
    fireEvent.click(screen.getByRole("button", { name: "Start First Watch" }));

    expect(
      screen.getByRole("heading", { name: "First Watch, step 1 of 6: capture" }),
    ).toBeTruthy();
    const captureAction = screen.getByRole("button", { name: "Open Capture" });
    fireEvent.click(captureAction);
    captureAction.focus();
    expect(captureDetails).toHaveLength(1);
    const captureDetail = captureDetails[0] as {
      text: string;
      firstWatchGeneration: number;
    };
    expect(captureDetail.text).toBe("todo: ");

    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-capture-complete", {
          detail: {
            kind: "note",
            id: 9,
            firstWatchGeneration: captureDetail.firstWatchGeneration,
          },
        }),
      );
    });
    expect(screen.getAllByText(/note was saved/)).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Continue to Work" })).toBeNull();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-capture-complete", {
          detail: {
            kind: "task",
            id: 42,
            firstWatchGeneration: captureDetail.firstWatchGeneration,
          },
        }),
      );
    });
    const work = screen.getByRole("button", { name: "Continue to Work" });
    expect(work).toBe(captureAction);
    expect(document.activeElement).toBe(work);

    state.pathname = "/dashboard";
    fireEvent.click(work);
    expect(state.pushes).toEqual(["/dashboard"]);
    view.rerender(<FirstWatch />);
    expect(
      screen.getByRole("heading", { name: "First Watch, step 2 of 6: task_peek" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open task #42" }));
    expect(new URLSearchParams(window.location.search).get("task")).toBe("42");

    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-peek-result", {
          detail: { taskId: 42, status: "loaded" },
        }),
      );
    });
    expect(screen.getByRole("button", { name: "Continue to Search" })).toBeTruthy();
    expect(window.localStorage.getItem("skein-first-watch:resolved-user")).toContain(
      '"taskId":42',
    );
    window.removeEventListener("skein-capture-open", onCapture);
  });

  it("clears an unavailable task before recapture", async () => {
    state.pathname = "/dashboard";
    window.localStorage.setItem(
      "skein-first-watch:resolved-user",
      JSON.stringify({
        version: 1,
        status: "active",
        stepId: "task_peek",
        taskId: 42,
        skippedTaskPractice: false,
      }),
    );
    render(<FirstWatch />);
    await screen.findByRole("heading", { name: /step 2 of 6/ });
    fireEvent.click(screen.getByRole("button", { name: "Open task #42" }));
    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-peek-result", {
          detail: { taskId: 42, status: "unavailable" },
        }),
      );
    });

    expect(screen.getByRole("alert").textContent).toContain("This task is not available");
    expect(screen.getByTestId("first-watch-status").textContent).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Capture another task" }));

    expect(screen.getByRole("heading", { name: /step 1 of 6/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open Capture" })).toBeTruthy();
    expect(window.localStorage.getItem("skein-first-watch:resolved-user")).not.toContain(
      '"taskId"',
    );
  });

  it("requires an exact Search-origin task result before Inbox becomes available", async () => {
    window.localStorage.setItem(
      "skein-first-watch:resolved-user",
      JSON.stringify({
        version: 1,
        status: "active",
        stepId: "search",
        taskId: 42,
        skippedTaskPractice: false,
      }),
    );
    const prefills: string[] = [];
    const onPrefill = (event: Event) =>
      prefills.push((event as CustomEvent<string>).detail);
    window.addEventListener("skein-search-prefill", onPrefill);
    render(<FirstWatch />);
    await screen.findByRole("heading", { name: "First Watch, step 3 of 6: search" });

    fireEvent.click(screen.getByRole("button", { name: "Put #42 in Search" }));
    expect(prefills).toEqual(["#42"]);
    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-search-result", {
          detail: { entity: "task", id: 9 },
        }),
      );
      window.dispatchEvent(
        new CustomEvent("skein-peek-result", {
          detail: { taskId: 42, status: "loaded" },
        }),
      );
    });
    expect(screen.queryByRole("link", { name: "Continue to Inbox" })).toBeNull();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-search-result", {
          detail: { entity: "task", id: 42 },
        }),
      );
      window.dispatchEvent(
        new CustomEvent("skein-peek-result", {
          detail: { taskId: 42, status: "loaded" },
        }),
      );
    });
    expect(screen.getByRole("link", { name: "Continue to Inbox" })).toBeTruthy();
    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-peek-close", { detail: { taskId: 42 } }),
      );
    });
    expect(screen.getByTestId("first-watch-status").textContent).toBe(
      "Task found in Search. Continue to Inbox.",
    );
    window.removeEventListener("skein-search-prefill", onPrefill);
  });

  it("waits for explicit Continue after Inbox and Team routes match", async () => {
    window.localStorage.setItem(
      "skein-first-watch:resolved-user",
      JSON.stringify({
        version: 1,
        status: "active",
        stepId: "review",
        skippedTaskPractice: true,
      }),
    );
    const view = render(<FirstWatch />);
    await screen.findByRole("heading", { name: "First Watch, step 4 of 6: review" });
    expect(screen.getByRole("link", { name: "Open Approvals" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Continue to Team" })).toBeNull();

    state.pathname = "/review";
    view.rerender(<FirstWatch />);
    const team = screen.getByRole("link", { name: "Continue to Team" });
    team.addEventListener("click", (event) => event.preventDefault());
    state.pathname = "/activity";
    fireEvent.click(team);
    view.rerender(<FirstWatch />);

    expect(
      screen.getByRole("heading", { name: "First Watch, step 5 of 6: activity_feed" }),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Continue to Chat" }));
    expect(
      screen.getByRole("heading", { name: "First Watch, step 6 of 6: bosun" }),
    ).toBeTruthy();
  });

  it("returns skipped Inbox practice to Capture and leaves browser Back independent", async () => {
    state.pathname = "/review";
    window.localStorage.setItem(
      "skein-first-watch:resolved-user",
      JSON.stringify({
        version: 1,
        status: "active",
        stepId: "review",
        skippedTaskPractice: true,
      }),
    );
    const view = render(<FirstWatch />);
    await screen.findByRole("heading", { name: "First Watch, step 4 of 6: review" });

    act(() => window.dispatchEvent(new PopStateEvent("popstate")));
    expect(screen.getByRole("heading", { name: "First Watch, step 4 of 6: review" })).toBeTruthy();

    const previous = screen.getByRole("link", { name: "Previous step" });
    previous.addEventListener("click", (event) => event.preventDefault());
    state.pathname = "/";
    fireEvent.click(previous);
    view.rerender(<FirstWatch />);

    expect(
      screen.getByRole("heading", { name: "First Watch, step 1 of 6: capture" }),
    ).toBeTruthy();
    expect(window.localStorage.getItem("skein-first-watch:resolved-user")).toContain(
      '"skippedTaskPractice":false',
    );
  });

  it("finishes in deterministic Chat only after the expected composer text is ready", async () => {
    window.localStorage.setItem(
      "skein-first-watch:resolved-user",
      JSON.stringify({
        version: 1,
        status: "active",
        stepId: "bosun",
        skippedTaskPractice: true,
      }),
    );
    render(<FirstWatch />);

    expect(
      await screen.findAllByText(
        "This workspace uses deterministic Chat help. /help is ready.",
      ),
    ).toHaveLength(2);
    const finish = screen.getByRole("link", { name: "Open Chat help" });
    const href = new URL(finish.getAttribute("href") ?? "", "http://skein.test");
    expect(href.pathname).toBe("/chat");
    expect(href.searchParams.get("compose")).toBe("/help");
    finish.addEventListener("click", (event) => event.preventDefault());
    fireEvent.click(finish);
    expect(screen.getByRole("heading", { name: /step 6 of 6/ })).toBeTruthy();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-chat-compose-ready", { detail: "/help" }),
      );
    });
    expect(screen.queryByRole("heading", { name: /First Watch/ })).toBeNull();
    expect(window.localStorage.getItem("skein-first-watch:resolved-user")).toBeNull();
  });

  it("separates provider configuration failure from status failure", async () => {
    state.provider = "anthropic";
    state.providerError = "missing credentials";
    window.localStorage.setItem(
      "skein-first-watch:resolved-user",
      JSON.stringify({
        version: 1,
        status: "active",
        stepId: "bosun",
        skippedTaskPractice: true,
      }),
    );
    const first = render(<FirstWatch />);
    expect(
      await screen.findAllByText("Bosun is unavailable. Chat help is ready."),
    ).toHaveLength(2);
    first.unmount();

    state.providerError = "";
    state.statusFails = true;
    render(<FirstWatch />);
    expect(
      await screen.findAllByText("Chat status is unavailable. /help is ready."),
    ).toHaveLength(2);
  });

  it("hands a same-route live finish to the mounted Chat composer", async () => {
    state.provider = "ollama";
    state.pathname = "/chat";
    window.localStorage.setItem(
      "skein-first-watch:resolved-user",
      JSON.stringify({
        version: 1,
        status: "active",
        stepId: "bosun",
        skippedTaskPractice: true,
      }),
    );
    const prefills: string[] = [];
    const onPrefill = (event: Event) => prefills.push((event as CustomEvent<string>).detail);
    window.addEventListener("skein-chat-compose", onPrefill);
    render(<FirstWatch />);

    fireEvent.click(await screen.findByRole("button", { name: "Ask Bosun" }));

    expect(prefills).toEqual([
      "/as bosun I finished First Watch. Which Skein feature can I try next?",
    ]);
    window.removeEventListener("skein-chat-compose", onPrefill);
  });

  it("uses live Bosun only when agent status is healthy", async () => {
    state.provider = "ollama";
    window.localStorage.setItem(
      "skein-first-watch:resolved-user",
      JSON.stringify({
        version: 1,
        status: "active",
        stepId: "bosun",
        skippedTaskPractice: true,
      }),
    );
    render(<FirstWatch />);

    const finish = await screen.findByRole("link", { name: "Ask Bosun" });
    const href = new URL(finish.getAttribute("href") ?? "", "http://skein.test");
    expect(href.searchParams.get("compose")).toBe(
      "/as bosun I finished First Watch. Which Skein feature can I try next?",
    );
  });

  it("keeps pending evidence across unrelated same-tab storage events", async () => {
    const opens: Array<{ firstWatchGeneration: number }> = [];
    const onOpen = (event: Event) =>
      opens.push((event as CustomEvent<{ firstWatchGeneration: number }>).detail);
    window.addEventListener("skein-capture-open", onOpen);
    render(<FirstWatch />);
    act(() => startFirstWatch());
    await screen.findByRole("heading", { name: "Bosun’s First Watch" });
    fireEvent.click(screen.getByRole("button", { name: "Start First Watch" }));
    fireEvent.click(screen.getByRole("button", { name: "Open Capture" }));

    act(() => window.dispatchEvent(new Event("storage")));
    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-capture-complete", {
          detail: {
            kind: "task",
            id: 42,
            firstWatchGeneration: opens[0].firstWatchGeneration,
          },
        }),
      );
    });

    expect(screen.getByRole("button", { name: "Continue to Work" })).toBeTruthy();
    window.removeEventListener("skein-capture-open", onOpen);
  });

  it("rejects delayed evidence after the resolved identity changes", async () => {
    const opens: Array<{ firstWatchGeneration: number }> = [];
    const onOpen = (event: Event) =>
      opens.push((event as CustomEvent<{ firstWatchGeneration: number }>).detail);
    window.addEventListener("skein-capture-open", onOpen);
    render(<FirstWatch />);
    act(() => startFirstWatch());
    await screen.findByRole("heading", { name: "Bosun’s First Watch" });
    fireEvent.click(screen.getByRole("button", { name: "Start First Watch" }));
    fireEvent.click(screen.getByRole("button", { name: "Open Capture" }));
    const oldGeneration = opens[0].firstWatchGeneration;

    state.user = "other-user";
    window.localStorage.setItem(
      "skein-first-watch:other-user",
      JSON.stringify({
        version: 1,
        status: "active",
        stepId: "capture",
        skippedTaskPractice: false,
      }),
    );
    act(() => window.dispatchEvent(new Event("skein-identity-change")));
    await screen.findByRole("heading", { name: /step 1 of 6/ });

    act(() => {
      window.dispatchEvent(
        new CustomEvent("skein-capture-complete", {
          detail: {
            kind: "task",
            id: 42,
            firstWatchGeneration: oldGeneration,
          },
        }),
      );
    });
    expect(screen.queryByRole("button", { name: "Continue to Work" })).toBeNull();
    expect(window.localStorage.getItem("skein-first-watch:other-user")).not.toContain(
      '"taskId"',
    );
    window.removeEventListener("skein-capture-open", onOpen);
  });

  it("does not open for an anonymous resolved identity", async () => {
    state.user = "anonymous";
    render(<FirstWatch />);

    act(() => startFirstWatch());
    await waitFor(() => expect(state.calls).toContainEqual({ path: "/api/whoami", method: "GET" }));

    expect(screen.queryByRole("heading", { name: "Bosun’s First Watch" })).toBeNull();
    expect(state.calls.some((call) => call.path === "/api/field-guide/first-watch")).toBe(false);
  });
});

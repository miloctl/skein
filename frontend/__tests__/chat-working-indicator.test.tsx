import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The bubble shows a working state before the assistant's first word. The
 *  wait is real: a thinking model streams empty text deltas for seconds (70
 *  of them, 4.4s, measured on glm-5.2), and an attached image the chat model
 *  cannot read spends a whole extra model call on the vision sidecar first.
 *  A bubble left blank through all of it reads as a hung app. */

const mocks = vi.hoisted(() => ({
  messages: [] as { role: string; attachments?: unknown[]; status?: { type: string } }[],
  isRunning: false,
}));

vi.mock("@assistant-ui/react", () => ({
  ThreadPrimitive: { Root: () => null, Viewport: () => null, Empty: () => null },
  MessagePrimitive: { Root: () => null, Parts: () => null, Attachments: () => null },
  AttachmentPrimitive: { Root: () => null, Name: () => null, Remove: () => null },
  ComposerPrimitive: { Root: () => null, Input: () => null, Send: () => null, Cancel: () => null },
  useAui: () => ({ composer: {} }),
  useAuiState: (selector: (s: { thread: { messages: unknown[]; isRunning: boolean } }) => unknown) =>
    selector({ thread: { messages: mocks.messages, isRunning: mocks.isRunning } }),
  unstable_useComposerInputHistory: () => ({}),
  unstable_useThreadMessageIds: () => [],
}));
vi.mock("@assistant-ui/react-markdown", () => ({ MarkdownTextPrimitive: () => null }));
vi.mock("@/components/mermaid-diagram", () => ({ MermaidDiagram: () => null }));
vi.mock("@/lib/api", () => ({ api: vi.fn() }));
vi.mock("@/lib/slash", () => ({ argQuery: () => null, mentionQuery: () => null }));
vi.mock("@/lib/status", () => ({ reportStatus: vi.fn() }));
vi.mock("@/lib/persona", () => ({
  findPersona: () => null,
  getActivePersona: () => null,
  setActivePersona: vi.fn(),
  setBench: vi.fn(),
  subscribePersona: () => () => {},
}));

import { reportStatus } from "@/lib/status";
import { LONG_WAIT_S, Thread, WorkingIndicator } from "@/components/thread";

describe("the working indicator", () => {
  it("says the turn is thinking when nothing was attached", async () => {
    mocks.messages = [{ role: "user" }];
    render(<WorkingIndicator />);
    expect(await screen.findByText("Thinking…")).toBeTruthy();
  });

  it("names the attachment wait, which is a whole extra model call", async () => {
    mocks.messages = [{ role: "user", attachments: [{ id: "7" }] }];
    render(<WorkingIndicator />);
    expect(await screen.findByText("Reading the attachment…")).toBeTruthy();
  });

  it("reads the LAST user message, not the first", async () => {
    mocks.messages = [
      { role: "user", attachments: [{ id: "7" }] },
      { role: "assistant" },
      { role: "user" },
    ];
    render(<WorkingIndicator />);
    expect(await screen.findByText("Thinking…")).toBeTruthy();
  });

  it("does not claim progress after the turn failed", () => {
    // the Empty slot renders for ANY status: a turn that died before its first
    // token (backend down, rate cap, 404 thread) showed pulsing dots forever,
    // claiming progress during exactly the incident that must never be dressed up
    mocks.messages = [{ role: "user" }];
    const { container } = render(<WorkingIndicator status={{ type: "incomplete" }} />);
    expect(container.querySelectorAll(".working-dot")).toHaveLength(0);
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(screen.getByText("The turn ended without a reply.")).toBeTruthy();
  });

  it("names the refusal a failed turn carried instead of a bare ending", () => {
    mocks.messages = [{ role: "user" }];
    // the shape the local runtime stores (toAssistantError): a plain object,
    // not the Error the adapter threw
    render(
      <WorkingIndicator
        status={{
          type: "incomplete",
          error: { code: "unknown", message: "The model session is in use. Wait for the current turn to finish." },
        }}
      />,
    );
    expect(screen.getByRole("status").textContent).toBe(
      "The model session is in use. Wait for the current turn to finish.",
    );
    expect(screen.queryByText("The turn ended without a reply.")).toBeNull();
  });

  it("says the wait is unusual after thirty seconds and points to Stop", () => {
    vi.useFakeTimers();
    try {
      mocks.messages = [{ role: "user" }];
      const { rerender } = render(<WorkingIndicator status={{ type: "running" }} />);
      // the region is in the DOM, empty, before the sentence lands in it: a
      // live region inserted together with its text is not announced
      const region = screen.getByRole("status");
      expect(region.textContent).toBe("");
      act(() => vi.advanceTimersByTime(LONG_WAIT_S * 1000));
      expect(screen.getByRole("status")).toBe(region);
      expect(region.textContent).toBe(
        "The model has not answered yet. Press Stop to send a new message.",
      );
      expect(screen.getByText("Thinking…")).toBeTruthy();
      // a refusal after the wait lands in the same region, so it is spoken
      rerender(
        <WorkingIndicator status={{ type: "incomplete", error: { code: "unknown", message: "Rate cap." } }} />,
      );
      expect(screen.getByRole("status")).toBe(region);
      expect(region.textContent).toBe("Rate cap.");
    } finally {
      vi.useRealTimers();
    }
  });

  it("still shows the dots while the turn is running", () => {
    mocks.messages = [{ role: "user" }];
    const { container } = render(<WorkingIndicator status={{ type: "running" }} />);
    expect(container.querySelectorAll(".working-dot")).toHaveLength(3);
  });

  it("announces itself to a screen reader", () => {
    // an empty message is silent to assistive tech, so the state has to be
    // spoken rather than only animated - into a region that was mounted
    // empty first, or the screen reader meets region and text together and
    // says nothing
    vi.useFakeTimers();
    try {
      mocks.messages = [{ role: "user" }];
      const { container } = render(<WorkingIndicator />);
      const region = container.querySelector("[aria-live='polite']");
      expect(region?.textContent).toBe("");
      act(() => vi.runOnlyPendingTimers());
      expect(container.querySelector("[aria-live='polite']")).toBe(region);
      expect(region?.textContent).toBe("Thinking…");
      // the dots are decoration over that sentence
      expect(container.querySelectorAll(".working-dot")).toHaveLength(3);
      expect(container.querySelector("[aria-hidden]")).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

it("announces only a newly completed reply, not tokens or loaded history", () => {
  vi.mocked(reportStatus).mockClear();
  mocks.isRunning = false;
  mocks.messages = [{ role: "assistant", status: { type: "complete" } }];
  const { rerender } = render(<Thread />);
  expect(reportStatus).not.toHaveBeenCalled();
  mocks.isRunning = true;
  mocks.messages = [{ role: "assistant", status: { type: "running" } }];
  rerender(<Thread />);
  rerender(<Thread />);
  expect(reportStatus).not.toHaveBeenCalled();
  mocks.isRunning = false;
  mocks.messages = [{ role: "assistant", status: { type: "complete" } }];
  rerender(<Thread />);
  expect(reportStatus).toHaveBeenCalledExactlyOnceWith("Reply complete.", "confirmation");
  rerender(<Thread />);
  expect(reportStatus).toHaveBeenCalledTimes(1);
});

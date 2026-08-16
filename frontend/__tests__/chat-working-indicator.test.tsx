import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The wait before an assistant's first word is real and used to be invisible:
 *  a thinking model streams empty text deltas for seconds (70 of them, 4.4s,
 *  measured on glm-5.2), and an attached image the chat model cannot read
 *  spends a whole extra model call on the vision sidecar first. The bubble sat
 *  blank through all of it, which reads as a hung app. */

const mocks = vi.hoisted(() => ({
  messages: [] as { role: string; attachments?: unknown[] }[],
}));

vi.mock("@assistant-ui/react", () => ({
  ThreadPrimitive: { Root: () => null, Viewport: () => null, Empty: () => null },
  MessagePrimitive: { Root: () => null, Parts: () => null, Attachments: () => null },
  AttachmentPrimitive: { Root: () => null, Name: () => null, Remove: () => null },
  ComposerPrimitive: { Root: () => null, Input: () => null, Send: () => null },
  useComposer: () => ({}),
  useComposerRuntime: () => ({}),
  useThread: (selector: (t: { messages: unknown[] }) => unknown) =>
    selector({ messages: mocks.messages }),
  unstable_useComposerInputHistory: () => ({}),
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

import { WorkingIndicator } from "@/components/thread";

describe("the working indicator", () => {
  it("says the turn is thinking when nothing was attached", () => {
    mocks.messages = [{ role: "user" }];
    render(<WorkingIndicator />);
    expect(screen.getByText("Thinking…")).toBeTruthy();
  });

  it("names the attachment wait, which is a whole extra model call", () => {
    mocks.messages = [{ role: "user", attachments: [{ id: "7" }] }];
    render(<WorkingIndicator />);
    expect(screen.getByText("Reading the attachment…")).toBeTruthy();
  });

  it("reads the LAST user message, not the first", () => {
    mocks.messages = [
      { role: "user", attachments: [{ id: "7" }] },
      { role: "assistant" },
      { role: "user" },
    ];
    render(<WorkingIndicator />);
    expect(screen.getByText("Thinking…")).toBeTruthy();
  });

  it("announces itself to a screen reader", () => {
    // an empty message is silent to assistive tech, so the state has to be
    // spoken rather than only animated
    mocks.messages = [{ role: "user" }];
    const { container } = render(<WorkingIndicator />);
    expect(container.querySelector("[aria-live='polite']")?.textContent).toBe("Thinking…");
    // the dots are decoration over that sentence
    expect(container.querySelectorAll(".working-dot")).toHaveLength(3);
    expect(container.querySelector("[aria-hidden]")).not.toBeNull();
  });
});

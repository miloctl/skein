import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** ArrowUp means two different things in the composer, and exactly one of
 *  them may fire per keypress. Input history comes from
 *  `unstable_useComposerInputHistory`, an assistant-ui API its own types mark
 *  "Under active development and might change without notice". Our slash
 *  popup holds it off by calling preventDefault first, and the hook yields to
 *  an already-prevented event.
 *
 *  That contract lives inside the library. tsc catches the export vanishing
 *  or its signature changing; it catches NOTHING about the guard — and
 *  neither do these tests: deleting the hook's defaultPrevented check leaves
 *  all three green, because the popup is only ever open on a non-empty draft
 *  and the hook refuses to recall from one. What these DO pin is `recalling`.
 *  Without it a recalled slash command reopens the popup, which then swallows
 *  the next arrow and strands the walk on one entry. */

// jsdom ships no ResizeObserver, and assistant-ui's composer attaches one to
// autosize the textarea. Without this the component throws on mount and both
// assertions below fail for a reason that has nothing to do with ArrowUp.
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", NoopResizeObserver);

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    // REJECT, do not resolve []: the composer replaces its command list with
    // whatever the fetch returns, so an empty array empties the popup and the
    // open-popup assertion below can never run. Rejecting keeps
    // FALLBACK_COMMANDS, which is thread.tsx's documented no-backend path.
    api: () => Promise.reject(new Error("no backend in this test")),
    getUser: () => "tester",
  };
});

import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";

import { Thread } from "@/components/thread";

const SENT = "ship the billing page";

function Harness() {
  const runtime = useLocalRuntime({
    async run() {
      return { content: [{ type: "text" as const, text: "noted" }] };
    },
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}

const composer = () => screen.getByRole("combobox") as HTMLTextAreaElement;

/** History must be NON-EMPTY before either assertion runs. With nothing to
 *  recall the hook returns early and ArrowUp is a no-op whatever the popup
 *  does, so all three tests would pass while proving nothing. */
async function send(text: string) {
  const box = composer();
  fireEvent.change(box, { target: { value: text } });
  await waitFor(() => expect(box.value).toBe(text));
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(box.value).toBe(""));
  await screen.findByText(text);
}

const sendOneMessage = () => send(SENT);

describe("ArrowUp in the composer", () => {
  it("recalls the last sent message while the popup is closed", async () => {
    render(<Harness />);
    await sendOneMessage();
    const box = composer();
    expect(box.value).toBe("");
    expect(screen.queryByRole("listbox")).toBeNull();

    fireEvent.keyDown(box, { key: "ArrowUp" });

    await waitFor(() => expect(box.value).toBe(SENT));
  });

  it("leaves the composer alone while the slash popup is open", async () => {
    render(<Harness />);
    await sendOneMessage();
    const box = composer();
    fireEvent.change(box, { target: { value: "/b" } });

    // the popup must really be open, or this asserts nothing
    const list = await screen.findByRole("listbox");
    expect(list).toBeTruthy();

    fireEvent.keyDown(box, { key: "ArrowUp" });

    // still the typed token: the popup consumed the key, and history — which
    // has SENT available and would otherwise overwrite this — did not run.
    // TWO mechanisms enforce this, the popup's preventDefault and the hook's
    // own "only recall from an empty draft" guard, so this passes if either
    // one holds. The test below needs neither: it pins `recalling`, which
    // keeps the popup shut so the walk can continue.
    await waitFor(() => expect(box.value).toBe("/b"));
    expect(box.value).not.toBe(SENT);
  });

  it("keeps walking history after recalling a bare slash command", async () => {
    // The reachable trap, and the only case where the popup and history can
    // both claim the key. Recall a message that IS a slash command and the
    // popup's open condition is met by the recalled text itself — it then
    // swallows the next arrow, stranding the person on one entry with no way
    // back to their draft. "/briefing" is a shipped empty-state suggestion,
    // so this is a path users take, not a contrived one.
    render(<Harness />);
    await sendOneMessage();
    await send("/briefing");
    const box = composer();

    fireEvent.keyDown(box, { key: "ArrowUp" });
    await waitFor(() => expect(box.value).toBe("/briefing"));
    expect(screen.queryByRole("listbox")).toBeNull();

    fireEvent.keyDown(box, { key: "ArrowUp" });
    await waitFor(() => expect(box.value).toBe(SENT));
  });
});

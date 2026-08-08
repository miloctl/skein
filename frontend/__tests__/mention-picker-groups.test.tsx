import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The @ picker offers two different actions behind one symbol: naming a
 *  PERSON files something they can open, naming a SPECIALIST answers you in
 *  this turn. A LEADING @slug is the deterministic handoff (routes/chat.py
 *  rewrites it into the /as form); a mid-sentence slug reaches the bench
 *  through the orchestrator's consult tool, so those rows depend on a real
 *  provider — this file runs with one. mention-picker-keyless.test.tsx pins
 *  the mock side, where mid-sentence rows must stay hidden. */

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
    api: (path: string) =>
      path === "/api/users"
        ? Promise.resolve([
            { name: "mira", kind: "human" },
            { name: "backend-architect", kind: "agent" },
            { name: "ada lovelace", kind: "human" },
            { name: "O'Brien", kind: "human" },
            { name: "José", kind: "human" },
          ])
        : path === "/api/personas"
          ? Promise.resolve([
              {
                slug: "growth-mentor",
                name: "Growth Mentor",
                description: "coaching",
                emoji: "🌱",
              },
            ])
          : path === "/api/agents/status"
            ? Promise.resolve({ provider: "ollama" })
            : Promise.resolve([]),
    getUser: () => "tester",
  };
});

import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";

import { Thread } from "@/components/thread";

function Harness() {
  const runtime = useLocalRuntime({
    async run() {
      return { content: [{ type: "text" as const, text: "ok" }] };
    },
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}

const composer = () => screen.getByRole("combobox") as HTMLTextAreaElement;

/** Accessible names of the options inside one labelled group. Hidden nodes
 *  are dropped, so the ↵ badge on the selected row does not land in the name
 *  a screen reader would read. */
const groupNames = (group: string) =>
  [
    ...screen
      .getByRole("group", { name: group })
      .querySelectorAll('[role="option"]'),
  ].map((o) => {
    const copy = o.cloneNode(true) as HTMLElement;
    copy.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove());
    return (copy.textContent ?? "").replace(/\s+/g, " ").trim();
  });

async function type(value: string) {
  const box = composer();
  fireEvent.change(box, { target: { value } });
  await waitFor(() => expect(box.value).toBe(value));
  return box;
}

describe("the @ picker", () => {
  it("offers people and specialists at the start of a message", async () => {
    render(<Harness />);
    await type("@");
    await screen.findByRole("listbox");
    // groups, not aria-label on each row: aria-label REPLACES the accessible
    // name, so labelling rows dropped the description that is the whole value
    // of a specialist row
    expect(groupNames("People")).toContain("@mira");
    expect(groupNames("Specialists")).toContain("@growth-mentor🌱 coaching");
  });

  it("offers specialists mid-sentence too, on a real provider", async () => {
    // the consult feature's own headline case is "ask @code-reviewer about
    // tomorrow's plan" — a picker that only helped at position zero made the
    // user type the slug from memory exactly where the feature lives
    render(<Harness />);
    await type("ask @");
    await screen.findByRole("listbox");
    expect(groupNames("People")).toContain("@mira");
    expect(groupNames("Specialists")).toContain("@growth-mentor🌱 coaching");
  });

  it("never offers a name the backend cannot match", async () => {
    render(<Harness />);
    await type("@");
    await screen.findByRole("listbox");
    const names = groupNames("People");
    // every one of these is a real roster name the backend's _MENTION cannot
    // tokenize: the space, the apostrophe and the accent all end the token, so
    // the mention matches nobody AND the turn guard never sees a miss either
    expect(names.some((n) => n.includes("ada lovelace"))).toBe(false);
    expect(names.some((n) => n.includes("Brien"))).toBe(false);
    expect(names.some((n) => n.includes("Jos"))).toBe(false);
    // the bench is kept out of People by kind, not by charset — the slug
    // tokenizes fine, and it has its own section
    expect(names).not.toContain("@backend-architect");
  });

  it("splices the name in place instead of replacing the message", async () => {
    render(<Harness />);
    const box = await type("can @mi");
    await screen.findByRole("listbox");
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(box.value).toBe("can @mira "));
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

const { bench, specialist, catalog } = vi.hoisted(() => ({
  bench: [
    { slug: "growth-mentor", name: "Growth Mentor", description: "coaching", emoji: "🌱" },
    {
      slug: "backend-architect",
      name: "Backend Architect",
      description: "Design consultations — schemas, APIs, tradeoffs — biased to boring technology and reversible choices",
      emoji: "🏛️",
    },
  ],
  specialist: {
    slug: "acme.workplace.delivery",
    name: "Acme Delivery Specialist",
    description: "Reviews delivery risk and Atlas synchronization.",
    emoji: "🧩",
  },
  catalog: { permitted: true, failed: false, requests: [] as (RequestInit | undefined)[] },
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (path === "/api/chat/specialists") {
        catalog.requests.push(init);
        return catalog.failed ? Promise.reject(new Error("Catalog unavailable"))
          : Promise.resolve(catalog.permitted ? [...bench, specialist] : bench);
      }
      if (path === "/api/personas") return Promise.resolve(bench);
      if (path === "/api/users") return Promise.resolve([
        { name: "mira", kind: "human" },
        { name: "backend-architect", kind: "agent" },
        { name: "ada lovelace", kind: "human" },
        { name: "O'Brien", kind: "human" },
        { name: "José", kind: "human" },
      ]);
      if (path === "/api/agents/status") return Promise.resolve({ provider: "ollama" });
      return Promise.resolve([]);
    },
    getUser: () => "tester",
  };
});

import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";

import { Thread } from "@/components/thread";

const run = vi.fn(async () => ({ content: [{ type: "text" as const, text: "ok" }] }));
beforeEach(() => {
  run.mockClear();
  catalog.permitted = true;
  catalog.failed = false;
  catalog.requests = [];
});

function Harness() {
  const runtime = useLocalRuntime({ run });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}

const composer = () => screen.getByRole("textbox", { name: /Message/ }) as HTMLTextAreaElement;

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
  it.each(["Enter", "Tab"])("completes a permitted workplace specialist with %s without sending", async (key) => {
    render(<Harness />);
    const prefix = "@acme.workplace.del";
    const box = await type(`${prefix} check delivery`);
    box.focus();
    fireEvent.select(box, { target: { selectionStart: prefix.length, selectionEnd: prefix.length } });
    await screen.findByRole("option", { name: /@acme.workplace.delivery/ });
    fireEvent.keyDown(box, { key });
    await waitFor(() => expect(box.value).toBe("@acme.workplace.delivery check delivery"));
    expect(box.selectionStart).toBe("@acme.workplace.delivery ".length);
    expect(document.activeElement).toBe(box);
    expect(run).not.toHaveBeenCalled();
  });

  it("offers permitted workplace specialists mid-sentence only in the mention roster", async () => {
    render(<Harness />);
    await type("ask @acme");
    await screen.findByRole("option", { name: /@acme.workplace.delivery/ });
    await type("/as ");
    await screen.findByRole("option", { name: /as backend-architect/ });
    expect(screen.queryByRole("option", { name: /acme.workplace.delivery/ })).toBeNull();
  });

  it("reads the permitted roster again after the composer remounts", async () => {
    const first = render(<Harness />);
    await type("@acme");
    await screen.findByRole("option", { name: /@acme.workplace.delivery/ });
    first.unmount();
    catalog.permitted = false;
    render(<Harness />);
    await type("@");
    await screen.findByRole("option", { name: /@backend-architect/ });
    expect(screen.queryByRole("option", { name: /acme.workplace.delivery/ })).toBeNull();
    expect(catalog.requests).toEqual([{ cache: "no-store" }, { cache: "no-store" }]);
  });

  it("still sends ordinary text when the specialist catalog is unavailable", async () => {
    catalog.failed = true;
    render(<Harness />);
    await waitFor(() => expect(catalog.requests).toHaveLength(1));
    const box = await type("hello");
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
  });

  it.each(["Enter", "Tab", "click"])("completes before existing text with %s without sending", async (key) => {
    render(<Harness />);
    const box = await type("@bac plan it");
    box.focus();
    fireEvent.select(box, { target: { selectionStart: 4, selectionEnd: 4 } });
    const option = await screen.findByRole("option", { name: /@backend-architect/ });
    if (key === "click") fireEvent.click(option);
    else fireEvent.keyDown(box, { key });
    await waitFor(() => expect(box.value).toBe("@backend-architect plan it"));
    expect(document.activeElement).toBe(box);
    await waitFor(() => expect(box.selectionStart).toBe("@backend-architect ".length));
    expect(box.selectionEnd).toBe(box.selectionStart);
    expect(run).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("moves the caret past an existing full mention when completion leaves the text unchanged", async () => {
    render(<Harness />);
    const box = await type("@backend-architect plan it");
    box.focus();
    fireEvent.select(box, { target: { selectionStart: 4, selectionEnd: 4 } });
    await screen.findByRole("option", { name: /@backend-architect/ });
    fireEvent.keyDown(box, { key: "Tab" });
    expect(box.value).toBe("@backend-architect plan it");
    await waitFor(() => expect(box.selectionStart).toBe("@backend-architect ".length));
    expect(box.selectionEnd).toBe(box.selectionStart);
    expect(document.activeElement).toBe(box);
    expect(run).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("replaces the whole mention when the caret is inside the token", async () => {
    render(<Harness />);
    const box = await type("ask @bac-old plan it");
    box.focus();
    fireEvent.select(box, { target: { selectionStart: 8, selectionEnd: 8 } });
    await screen.findByRole("option", { name: /@backend-architect/ });
    fireEvent.keyDown(box, { key: "Tab" });
    await waitFor(() => expect(box.value).toBe("ask @backend-architect plan it"));
    await waitFor(() => expect(box.selectionStart).toBe("ask @backend-architect ".length));
    expect(run).not.toHaveBeenCalled();
  });

  it("closes for a selection and follows cursor movement without changing the draft", async () => {
    render(<Harness />);
    const box = await type("@bac");
    await screen.findByRole("option", { name: /@backend-architect/ });
    box.focus();
    fireEvent.select(box, { target: { selectionStart: 1, selectionEnd: 4 } });
    expect(screen.queryByRole("listbox")).toBeNull();
    fireEvent.keyDown(box, { key: "Tab" });
    expect(box.value).toBe("@bac");
    fireEvent.select(box, { target: { selectionStart: 0, selectionEnd: 0 } });
    expect(screen.queryByRole("listbox")).toBeNull();
    fireEvent.select(box, { target: { selectionStart: 4, selectionEnd: 4 } });
    await screen.findByRole("option", { name: /@backend-architect/ });
  });

  it("offers people and specialists at the start of a message", async () => {
    render(<Harness />);
    const box = await type("@");
    await screen.findByRole("listbox");
    expect(box.getAttribute("aria-autocomplete")).toBe("list");
    expect(box.getAttribute("aria-controls")).toBe("cmd-list");
    expect(box.getAttribute("aria-activedescendant")).toBe("cmd-0");
    expect(
      screen.getAllByRole("status").some((status) =>
        /suggestions open/i.test(status.textContent ?? ""),
      ),
    ).toBe(true);
    // groups, not aria-label on each row: aria-label REPLACES the accessible
    // name, so labelling rows dropped the description that is the whole value
    // of a specialist row
    expect(groupNames("People")).toContain("@mira");
    expect(groupNames("Specialists")).toContain("@growth-mentorcoaching");
  });

  it("offers specialists mid-sentence too, on a real provider", async () => {
    // the consult feature's own headline case is "ask @code-reviewer about
    // tomorrow's plan" — a picker that only helped at position zero made the
    // user type the slug from memory exactly where the feature lives
    render(<Harness />);
    await type("ask @");
    await screen.findByRole("listbox");
    expect(groupNames("People")).toContain("@mira");
    expect(groupNames("Specialists")).toContain("@growth-mentorcoaching");
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

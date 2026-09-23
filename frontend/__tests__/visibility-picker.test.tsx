import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** The picker states who can read a row, and the badge repeats that statement
 *  in every list. A control that says one tier and sends another is worse
 *  than no control: the person believed they had scoped the row.
 *
 *  These are rendered assertions on purpose. The tier is assembled from the
 *  parent's state and the picker's own fetched crew list, so no source-text
 *  sweep can see the two disagree. */

const calls: { url: string; init?: RequestInit }[] = [];
let crews: { id: number; name: string }[] = [];
let mine: number[] = [];
let failCrews = false;

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/crews") {
        if (failCrews) throw new Error("backend unreachable");
        return crews;
      }
      if (url === "/api/crews/mine") return mine;
      return { kind: "task", id: 1 };
    },
  };
});

vi.mock("@/lib/auth", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/auth")>();
  return { ...real, sessionSnapshot: () => ({ ...real.sessionSnapshot(), strong: true }) };
});

import { CapturePalette } from "@/components/capture-palette";
import {
  VisibilityBadge,
  VisibilityPicker,
} from "@/components/visibility-picker";

beforeEach(() => {
  calls.length = 0;
  crews = [{ id: 1, name: "Platform" }];
  mine = [1];
  failCrews = false;
});

function Harness({
  initial,
}: {
  initial: { visibility: string; crew_id: number };
}) {
  const [tier, setTier] = useState(initial);
  return (
    <>
      <VisibilityPicker value={tier} onChange={setTier} label="task" />
      <output data-testid="sent">{JSON.stringify(tier)}</output>
    </>
  );
}

describe("the picker never describes a tier it is not sending", () => {
  it("offers only crews the caller belongs to", async () => {
    crews = [
      { id: 1, name: "Platform" },
      { id: 2, name: "Design" },
    ];
    mine = [1];
    render(<Harness initial={{ visibility: "workspace", crew_id: 0 }} />);
    await screen.findByText("Platform only");
    // the server refuses a write to any other crew, so offering Design is
    // offering a choice that always fails
    expect(screen.queryByText("Design only")).toBeNull();
  });

  it("narrows the parent to only you when the selected crew leaves the list", async () => {
    // the state after an identity change, or a remembered crew the person
    // left: the parent still holds crew 1, and this caller is in no crew.
    // The roster would be the one direction that costs a reader privacy.
    mine = [];
    render(<Harness initial={{ visibility: "crew", crew_id: 1 }} />);
    await waitFor(() =>
      expect(screen.getByTestId("sent").textContent).toBe(
        JSON.stringify({ visibility: "private", crew_id: 0 }),
      ),
    );
    // and the label agrees with what would now be sent
    const select = screen.getByLabelText(
      "Who can see this task",
    ) as HTMLSelectElement;
    expect(select.value).toBe("private");
    expect(select.name).toBe("task-visibility");
  });

  it("leaves a crew the caller really is in alone", async () => {
    render(<Harness initial={{ visibility: "crew", crew_id: 1 }} />);
    await screen.findByText("Platform only");
    expect(screen.getByTestId("sent").textContent).toBe(
      JSON.stringify({ visibility: "crew", crew_id: 1 }),
    );
  });
});

describe("a failed crew fetch never widens the audience", () => {
  it("keeps a chosen crew when /api/crews fails", async () => {
    // `[]` on failure made "we could not ask" identical to "you are in no
    // crew", and the reconciliation then reset the row to the whole roster.
    // Widening is the one direction that costs a reader their privacy.
    failCrews = true;
    render(<Harness initial={{ visibility: "crew", crew_id: 1 }} />);
    await screen.findByLabelText("Who can see this task");
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByTestId("sent").textContent).toBe(
      JSON.stringify({ visibility: "crew", crew_id: 1 }),
    );
    // and the select still shows a crew rather than falling back to the
    // roster option, so the label agrees with what would be submitted
    expect(
      (screen.getByLabelText("Who can see this task") as HTMLSelectElement)
        .value,
    ).toBe("crew:1");
  });

  it("still offers the roster and only-you when the fetch fails", async () => {
    failCrews = true;
    render(<Harness initial={{ visibility: "workspace", crew_id: 0 }} />);
    const sel = (await screen.findByLabelText(
      "Who can see this task",
    )) as HTMLSelectElement;
    expect([...sel.options].map((o) => o.value)).toEqual([
      "workspace",
      "private",
    ]);
  });
});

describe("quick capture starts at only you and remembers the choice", () => {
  it("files the first capture privately and the next at the tier last picked", async () => {
    localStorage.clear();
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(new Event("skein-capture-open"));
    });
    await screen.findByText("Platform only");

    const input = screen.getByLabelText("What to capture");
    fireEvent.change(input, { target: { value: "todo: first thought" } });
    fireEvent.click(screen.getByRole("button", { name: /capture/i }));
    await waitFor(() =>
      expect(bodyOf(calls.at(-1))).toMatchObject({
        visibility: "private",
        crew_id: 0,
      }),
    );

    fireEvent.change(screen.getByLabelText("Who can see this capture"), {
      target: { value: "crew:1" },
    });
    fireEvent.change(input, { target: { value: "todo: crew work" } });
    fireEvent.click(screen.getByRole("button", { name: /capture/i }));
    await waitFor(() =>
      expect(bodyOf(calls.at(-1))).toMatchObject({ crew_id: 1 }),
    );

    // the picker still shows the remembered crew, so the next capture's
    // audience is on screen before it is filed
    fireEvent.change(input, { target: { value: "todo: next thought" } });
    expect(
      (screen.getByLabelText("Who can see this capture") as HTMLSelectElement)
        .value,
    ).toBe("crew:1");
    fireEvent.click(screen.getByRole("button", { name: /capture/i }));
    await waitFor(() =>
      expect(bodyOf(calls.at(-1))).toMatchObject({
        visibility: "crew",
        crew_id: 1,
      }),
    );
  });
});

function bodyOf(call?: { init?: RequestInit }): Record<string, unknown> {
  return JSON.parse(String(call?.init?.body ?? "{}"));
}

describe("every control in quick capture is reachable by keyboard", () => {
  it("includes the visibility select in the focus trap", async () => {
    // The picker mounts BELOW the Capture button, and the trap's selector
    // listed only buttons, textareas and [tabindex] — so the last focusable
    // was Capture, Tab wrapped to the first chip, and the one control that
    // decides who can read the capture could not be reached at all.
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(new Event("skein-capture-open"));
    });
    await screen.findByText("Platform only");
    // Asserted through the component's OWN trap, never by re-running its
    // selector here — a test that queries with the fixed selector passes no
    // matter what the component uses, which is no test at all.
    //
    // jsdom does not move focus on Tab, so the observable is the wrap: the
    // trap calls preventDefault and focuses the FIRST element only when the
    // active one is last. With the select missing from the selector, Capture
    // was last and Tab from it jumped to the first chip.
    const capture = screen.getByRole("button", { name: /capture/i });
    capture.focus();
    fireEvent.keyDown(capture, { key: "Tab" });
    const firstChip = screen.getByRole("button", { name: "task" });
    expect(document.activeElement).not.toBe(firstChip);
  });
});

describe("the fb: path states the tier it actually uses", () => {
  it("replaces the picker when the capture is private feedback", async () => {
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(new Event("skein-capture-open"));
    });
    const input = await screen.findByLabelText("What to capture");
    await screen.findByText("Platform only");

    fireEvent.change(input, {
      target: { value: "fb: ada — clearer specs please" },
    });
    // services/capture.py routes this into private.db before it reads a tier,
    // so a picker offering "Platform only" here would name one that is dropped
    expect(screen.queryByLabelText("Who can see this capture")).toBeNull();
    expect(
      screen.getByText(/feedback is kept out of the shared record/),
    ).toBeTruthy();

    fireEvent.change(input, {
      target: { value: "todo: back to a normal capture" },
    });
    // awaited: the picker remounts and refetches, so it renders null for a tick
    expect(
      await screen.findByLabelText("Who can see this capture"),
    ).toBeTruthy();
  });
});

describe("the badge", () => {
  it("marks the workspace tier as shared with the roster", () => {
    render(<VisibilityBadge visibility="workspace" />);
    expect(screen.getByText("everyone on the roster")).toBeTruthy();
  });

  it("offers the one-way share on a private row only", async () => {
    const onShared = vi.fn();
    const { rerender } = render(
      <VisibilityBadge
        visibility="private"
        share={{ kind: "notes", id: 9, onShared }}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "share with the team" }),
    );
    await waitFor(() => expect(onShared).toHaveBeenCalled());
    expect(calls.at(-1)).toMatchObject({
      url: "/api/share/notes/9",
      init: { method: "POST" },
    });
    rerender(
      <VisibilityBadge
        visibility="crew"
        crewId={1}
        share={{ kind: "notes", id: 9 }}
      />,
    );
    expect(
      screen.queryByRole("button", { name: "share with the team" }),
    ).toBeNull();
  });

  it("names the crew, so it matches the picker that set it", async () => {
    render(<VisibilityBadge visibility="crew" crewId={1} />);
    // "Platform only" is the picker's own option text (docs/LEXICON.md)
    await screen.findByText("Platform only");
  });

  it("says only you for the private tier", () => {
    render(<VisibilityBadge visibility="private" />);
    expect(screen.getByText("only you")).toBeTruthy();
  });
});

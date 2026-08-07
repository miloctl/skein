import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

import { CapturePalette } from "@/components/capture-palette";
import { VisibilityBadge, VisibilityPicker } from "@/components/visibility-picker";

beforeEach(() => {
  calls.length = 0;
  crews = [{ id: 1, name: "Platform" }];
  mine = [1];
  failCrews = false;
});

function Harness({ initial }: { initial: { visibility: string; crew_id: number } }) {
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

  it("resets the parent when the selected crew leaves the list", async () => {
    // the state after an identity change: the parent still holds crew 1, and
    // this caller is in no crew at all
    mine = [];
    render(<Harness initial={{ visibility: "crew", crew_id: 1 }} />);
    await waitFor(() =>
      expect(screen.getByTestId("sent").textContent).toBe(
        JSON.stringify({ visibility: "workspace", crew_id: 0 }),
      ),
    );
    // and the label agrees with what would now be sent
    expect(
      (screen.getByLabelText("Who can see this task") as HTMLSelectElement).value,
    ).toBe("workspace");
  });

  it("leaves a crew the caller really is in alone", async () => {
    render(<Harness initial={{ visibility: "crew", crew_id: 1 }} />);
    await screen.findByText("Platform only");
    expect(screen.getByTestId("sent").textContent).toBe(
      JSON.stringify({ visibility: "crew", crew_id: 1 }),
    );
  });
});

describe("the tier does not survive a capture", () => {
  it("files the next unrelated thought at the workspace tier", async () => {
    render(<CapturePalette />);
    act(() => {
      window.dispatchEvent(new Event("skein-capture-open"));
    });
    await screen.findByText("Platform only");

    const input = screen.getByLabelText("What to capture");
    fireEvent.change(screen.getByLabelText("Who can see this capture"), {
      target: { value: "crew:1" },
    });
    fireEvent.change(input, { target: { value: "todo: crew work" } });
    fireEvent.click(screen.getByRole("button", { name: /capture/i }));

    await waitFor(() => expect(bodyOf(calls.at(-1))).toMatchObject({ crew_id: 1 }));

    fireEvent.change(input, { target: { value: "todo: unrelated thought" } });
    fireEvent.click(screen.getByRole("button", { name: /capture/i }));
    await waitFor(() =>
      expect(bodyOf(calls.at(-1))).toMatchObject({
        visibility: "workspace",
        crew_id: 0,
      }),
    );
  });
});

function bodyOf(call?: { init?: RequestInit }): Record<string, unknown> {
  return JSON.parse(String(call?.init?.body ?? "{}"));
}

describe("the badge", () => {
  it("renders nothing for the workspace tier", () => {
    const { container } = render(<VisibilityBadge visibility="workspace" />);
    expect(container.textContent).toBe("");
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

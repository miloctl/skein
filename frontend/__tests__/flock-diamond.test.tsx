import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { axe } from "vitest-axe";

import { FlockDiamond, type FlockTrace } from "@/components/flock-diamond";

/** The diamond is the only place a reader learns the members ran at the SAME
 *  time and which of them proposed a write — the chat transcript shows the
 *  sections one after another and cannot say either. It is also a picture, so
 *  everything it encodes with position or colour must be in words too. */

const member = (over: Partial<FlockTrace["members"][0]> = {}) => ({
  slug: "code-reviewer",
  name: "Code Reviewer",
  emoji: "👁️",
  status: "ok",
  ms: 120,
  receipts: 0,
  tokens_in: 0,
  tokens_out: 0,
  ...over,
});

const trace = (over: Partial<FlockTrace> = {}): FlockTrace => ({
  id: 1,
  thread_id: "t",
  user: "mira",
  flock: "engineering",
  members: [member(), member({ slug: "backend-architect", name: "Backend Architect", ms: 340 })],
  synthesis: null,
  created_at: "2026-08-05T10:00:00+00:00",
  ...over,
});

const label = () => screen.getByRole("img").getAttribute("aria-label") ?? "";

describe("FlockDiamond", () => {
  it("names every member and who asked", () => {
    render(<FlockDiamond trace={trace()} />);
    expect(screen.getByText(/Code Reviewer/)).toBeTruthy();
    expect(screen.getByText(/Backend Architect/)).toBeTruthy();
    expect(screen.getByText("mira")).toBeTruthy();
  });

  it("states each member's outcome in words, not colour alone", () => {
    render(
      <FlockDiamond
        trace={trace({
          members: [member(), member({ slug: "x", name: "Sprint Prioritizer", status: "failed" })],
        })}
      />,
    );
    // visible, for a sighted reader who cannot resolve the stroke colour
    expect(screen.getByText("answered")).toBeTruthy();
    expect(screen.getByText("did not answer")).toBeTruthy();
    expect(screen.getAllByText("120 ms · 0 proposal(s)").length).toBe(2);
    // and in the accessible name, for a reader who gets no picture at all
    expect(label()).toContain("Code Reviewer answered");
    expect(label()).toContain("Sprint Prioritizer did not answer");
  });

  it("reports an unknown status as itself, never as a guess", () => {
    render(<FlockDiamond trace={trace({ members: [member(), member({ slug: "y", status: "timeout" })] })} />);
    expect(label()).toContain("timeout");
    expect(label()).not.toContain("stopped");
  });

  it("carries the proposal count, which no other surface shows", () => {
    render(<FlockDiamond trace={trace({ members: [member({ receipts: 1 }), member({ slug: "b" })] })} />);
    expect(label()).toContain("proposed 1 write");
    expect(label()).toContain("proposed 0 writes");
  });

  it("states the slowest member, because concurrency is the point", () => {
    render(<FlockDiamond trace={trace()} />);
    expect(screen.getByText(/The slowest member took 340 ms/)).toBeTruthy();
  });

  /** Wall clock is the slowest member; spend is every member added together.
   *  A reader who takes the 340 ms caption as the whole cost of the turn
   *  under-reads a 3-member flock by roughly a factor of three. */
  it("draws each member's tokens and totals the turn's spend", () => {
    render(
      <FlockDiamond
        trace={trace({
          members: [
            member({ tokens_in: 1000, tokens_out: 200 }),
            member({ slug: "b", name: "Backend Architect", tokens_in: 300, tokens_out: 55 }),
          ],
          synthesis: { status: "ok", ms: 90, tokens_in: 40, tokens_out: 5 },
        })}
      />,
    );
    expect(screen.getByText("1,200 tokens")).toBeTruthy();
    expect(screen.getByText("355 tokens")).toBeTruthy();
    // 1200 + 355 + 45 — the merge call is spend the members do not carry
    expect(screen.getByText(/the turn used 1,600 tokens/i)).toBeTruthy();
    expect(label()).toContain("The turn used 1,600 tokens in total.");
  });

  it("says the answers are not merged when the flock does not synthesize", () => {
    render(<FlockDiamond trace={trace()} />);
    expect(screen.getByText("not merged")).toBeTruthy();
    expect(label()).toContain("The answers are not merged.");
  });

  it("never calls a failed merge 'merged'", () => {
    render(
      <FlockDiamond
        trace={trace({ synthesis: { status: "failed", ms: 12, tokens_in: 0, tokens_out: 0 } })}
      />,
    );
    expect(screen.getByText("merge failed")).toBeTruthy();
    expect(screen.queryByText("merged")).toBeNull();
    expect(label()).toContain("The merge did not run.");
  });

  it.each([2, 3, 4])("draws %i members without overlapping boxes", (n) => {
    const many = Array.from({ length: n }, (_, i) =>
      member({ slug: `m${i}`, name: `Minimal Change Engineer ${i}` }),
    );
    render(<FlockDiamond trace={trace({ members: many })} />);
    // geometry, not a constant: read the boxes back and prove they clear
    const boxes = [...screen.getByRole("img").querySelectorAll("rect")]
      .map((r) => ({
        left: Number(r.getAttribute("x")),
        right: Number(r.getAttribute("x")) + Number(r.getAttribute("width")),
        top: Number(r.getAttribute("y")),
      }))
      .filter((b, _, all) => b.top === all[1].top) // the member row only
      .sort((a, b) => a.left - b.left);
    for (let i = 1; i < boxes.length; i++) {
      expect(boxes[i].left).toBeGreaterThanOrEqual(boxes[i - 1].right);
    }
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<FlockDiamond trace={trace()} />);
    expect(await axe(container)).toHaveNoViolations();
  });
});

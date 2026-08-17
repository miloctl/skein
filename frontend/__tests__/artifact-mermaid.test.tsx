import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** A ```mermaid fence renders as a diagram; every other fence stays literal
 *  text. The parser half matters as much as the render half: a diagram's own
 *  `-->` and `#` lines are bullets and headings to the rest of the grammar,
 *  so a fence has to be consumed whole before any other rule sees it. */

const mermaid = vi.hoisted(() => ({
  initialize: vi.fn<(config: { securityLevel: string; htmlLabels: boolean }) => void>(),
  render: vi.fn<(id: string, code: string) => Promise<{ svg: string }>>(async () => ({
    svg: "<svg data-testid='drawn'></svg>",
  })),
}));

vi.mock("mermaid", () => ({ default: mermaid }));

import { ArtifactMarkdown } from "@/components/artifact-markdown";
import { MermaidDiagram } from "@/components/mermaid-diagram";

const DIAGRAM = ["```mermaid", "graph TD", "  A --> B", "```"].join("\n");

describe("a fence in an artifact body", () => {
  it("renders a mermaid fence as a diagram", async () => {
    const { container } = render(<ArtifactMarkdown markdown={DIAGRAM} />);
    await waitFor(() => expect(mermaid.render).toHaveBeenCalled());
    expect(mermaid.render.mock.calls[0][1]).toBe("graph TD\n  A --> B");
    await waitFor(() =>
      expect(container.querySelector("[data-testid='drawn']")).not.toBeNull(),
    );
  });

  it("never loosens the setting that sanitizes the drawn markup", async () => {
    render(<ArtifactMarkdown markdown={DIAGRAM} />);
    await waitFor(() => expect(mermaid.initialize).toHaveBeenCalled());
    const config = mermaid.initialize.mock.calls[0][0];
    // strict is what runs DOMPurify over mermaid's own output and refuses a
    // click directive; htmlLabels would put real markup in a foreignObject
    expect(config.securityLevel).toBe("strict");
    expect(config.htmlLabels).toBe(false);
  });

  it("keeps a diagram's own lines out of the rest of the grammar", async () => {
    const body = ["# Title", "", DIAGRAM, "", "- a bullet"].join("\n");
    const { container } = render(<ArtifactMarkdown markdown={body} />);
    await waitFor(() => expect(mermaid.render).toHaveBeenCalled());
    // one bullet, from the line outside the fence — not "A --> B" as well
    expect(container.querySelectorAll("li")).toHaveLength(1);
    expect(screen.getByText("a bullet")).toBeTruthy();
  });

  it("becomes a diagram after a half-written fence was rejected", async () => {
    // chat streams the fence token by token, so this component sees every
    // prefix and mermaid rejects almost all of them. A sticky `failed` pinned
    // the source fallback and the finished diagram never replaced it.
    mermaid.render.mockRejectedValueOnce(new Error("Parse error"));
    const { container, rerender } = render(<MermaidDiagram code="graph T" />);
    await waitFor(() => expect(container.querySelector("pre")).not.toBeNull());
    rerender(<MermaidDiagram code="graph TD\n  A --> B" />);
    await waitFor(() =>
      expect(container.querySelector("[data-testid='drawn']")).not.toBeNull(),
    );
    expect(container.querySelector("pre.sr-only")).not.toBeNull();
  });

  it("shows any other fence as literal text", () => {
    const body = ["```python", "print('hi')", "```"].join("\n");
    const { container } = render(<ArtifactMarkdown markdown={body} />);
    expect(container.querySelector("pre")?.textContent).toBe("print('hi')");
    expect(mermaid.render).not.toHaveBeenCalledWith(
      expect.anything(),
      "print('hi')",
    );
  });

  it("reads the diagram source to a screen reader", async () => {
    // the node text IS the content, and one generic "Diagram" label would
    // hide all of it
    render(<ArtifactMarkdown markdown={DIAGRAM} />);
    await waitFor(() => expect(mermaid.render).toHaveBeenCalled());
    expect(screen.getByText("graph TD A --> B")).toBeTruthy();
  });
});

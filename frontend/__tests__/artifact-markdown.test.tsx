import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ArtifactMarkdown } from "@/components/artifact-markdown";

/** The reports the scheduler files were being dumped into a <pre> or not shown
 *  at all. This renders the grammar our own generators emit — and only that.
 *
 *  The body is assembled from rows PEOPLE wrote (task titles, decision text,
 *  promise wording), so the one thing it must never do is treat that text as
 *  markup. */

describe("ArtifactMarkdown", () => {
  it("renders headings as real headings, one level below the page title", () => {
    render(<ArtifactMarkdown markdown={"# Week open\n\n## Your promises\n\n### Detail"} />);
    expect(screen.getByRole("heading", { level: 2, name: "Week open" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 3, name: "Your promises" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 4, name: "Detail" })).toBeTruthy();
  });

  it("groups consecutive bullets into one list", () => {
    render(<ArtifactMarkdown markdown={"- first\n- second\n\ntext\n\n- third"} />);
    const lists = screen.getAllByRole("list");
    expect(lists.length).toBe(2);
    expect(screen.getAllByRole("listitem").length).toBe(3);
  });

  /** readout.py indents each engagement's receipts two spaces under it.
   *  Flattened, three receipts read as three more engagements — and the exec
   *  readout is the generator with the most structure to lose. */
  it("nests an indented bullet under the one above it", () => {
    render(
      <ArtifactMarkdown
        markdown={"- 🟢 **Onboarding** (active)\n  - no overdue milestone\n  - no open blocker\n- 🔴 **Migration** (active)"}
      />,
    );
    const top = screen.getAllByRole("list")[0];
    const engagements = [...top.children];
    expect(engagements.length).toBe(2); // not 4
    expect(engagements[0].querySelectorAll("li").length).toBe(2);
    expect(engagements[1].querySelectorAll("li").length).toBe(0);
  });

  it("renders bold and inline code from the generators' own grammar", () => {
    render(<ArtifactMarkdown markdown={"- **Ship it** — run `skein week`"} />);
    expect(screen.getByText("Ship it").tagName).toBe("STRONG");
    expect(screen.getByText("skein week").tagName).toBe("CODE");
  });

  /** An artifact body quotes text people typed. Rendered as HTML, every
   *  generator becomes an injection sink — the reason nav-search parses FTS5's
   *  <b> into runs instead of setting innerHTML. */
  it("shows markup in the source as text, never as elements", () => {
    const { container } = render(
      <ArtifactMarkdown
        markdown={'- <img src=x onerror="alert(1)"> and <b>not bold</b>'}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(screen.getByRole("listitem").textContent).toContain("<b>not bold</b>");
  });

  it("leaves an unclosed marker as the literal text it is", () => {
    render(<ArtifactMarkdown markdown={"a **dangling marker"} />);
    expect(screen.getByText(/a \*\*dangling marker/)).toBeTruthy();
  });

  it("reads a body restored from a Windows-authored backup", () => {
    // a trailing \r turns every heading match into a paragraph
    render(<ArtifactMarkdown markdown={"# Digest\r\n\r\n- one\r\n"} />);
    expect(screen.getByRole("heading", { level: 2, name: "Digest" })).toBeTruthy();
    expect(screen.getByRole("listitem").textContent).toBe("one");
  });
});

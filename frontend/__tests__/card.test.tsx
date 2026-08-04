import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Card, EmptyState } from "@/components/card";

/** The one card every surface groups content with. The review found two
 *  portfolio sections rendering a <Card> INSIDE an identical <Card> while
 *  loading, so the title painted twice inside two nested bordered boxes on
 *  every page load. A card renders exactly one heading for its title. */

describe("Card", () => {
  it("renders its title as a single heading", () => {
    render(<Card title="Slip forecast">body</Card>);
    expect(screen.getAllByRole("heading", { name: "Slip forecast" })).toHaveLength(1);
  });

  it("renders no heading at all when it has no title", () => {
    render(<Card>body</Card>);
    expect(screen.queryByRole("heading")).toBeNull();
  });

  it("does not nest a second card inside itself for a loading state", () => {
    // the shape the bug had: <Card title=X>{loading ? <Card title=X>…
    const { container } = render(
      <Card title="Flow">
        <p>Loading…</p>
      </Card>,
    );
    expect(container.querySelectorAll("section")).toHaveLength(1);
    expect(screen.getAllByRole("heading", { name: "Flow" })).toHaveLength(1);
  });
});

describe("EmptyState", () => {
  it("renders the caller's wording rather than inventing its own", () => {
    render(<EmptyState>Nobody is over 100%.</EmptyState>);
    expect(screen.getByText("Nobody is over 100%.")).toBeDefined();
  });
});

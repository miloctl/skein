import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** The verdict is the one deterministic control on a model-authored write,
 *  and it binds the bytes, not the glyphs. A bidi override, a zero-width
 *  space or a Unicode tag character renders as nothing on the card, so the
 *  reviewer approved text they never saw. Each one shows as a code point. */

const TAGGED = "Freeze the deploy window\u{E0001}\u{E0041}";
const OVERRIDDEN = "‮yned‬ the rollback plan, and​ keep it filed";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    getUser: () => "mira",
    subscribeUser: () => () => {},
    api: (path: string) => {
      if (path.startsWith("/api/review?status=pending"))
        return Promise.resolve([
          {
            id: 1,
            entity: "decision",
            entity_id: null,
            action: "create",
            payload: { title: TAGGED, decision: OVERRIDDEN },
            summary: "record a decision",
            proposed_by: "scout",
            requested_by: "mira",
            origin: "agent",
            created_at: "2026-09-23T09:00:00+00:00",
            label: "record a decision",
            review_visibility: "workspace",
          },
        ]);
      return Promise.resolve([]);
    },
  };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/review" }));

import ReviewPage from "@/app/review/page";

describe("a proposal that carries invisible characters", () => {
  it("shows every format character as its code point", async () => {
    render(<ReviewPage />);
    await screen.findByText("record a decision");
    const cells = Array.from(document.querySelectorAll("td")).map((td) => td.textContent ?? "");
    const title = cells.find((text) => text.startsWith("Freeze the deploy window"));
    expect(title).toBe("Freeze the deploy window<U+E0001><U+E0041>");
    const decision = cells.find((text) => text.includes("rollback"));
    expect(decision).toBe("<U+202E>yned<U+202C> the rollback plan, and<U+200B> keep it filed");
    // the raw bytes never reach the DOM, where they would render as nothing
    expect(document.body.textContent).not.toContain("‮");
    expect(document.body.textContent).not.toContain("\u{E0041}");
  });
});

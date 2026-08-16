import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () =>
      Promise.resolve({
        origin: "agent_verified",
        created_by: "scout",
        created_at: "2026-08-16T10:00:00+00:00",
        proposal: {
          id: 7,
          proposed_by: "scout",
          requested_by: "ava",
          reviewed_by: "ava",
          reviewed_at: "2026-08-16T10:05:00+00:00",
          reviewed_strong: 0,
          reviewed_override: 0,
          review_note: "",
        },
        verdict_is_weak: true,
        history: [],
      }),
  };
});

import { Provenance } from "@/components/provenance";

describe("provenance identity wording", () => {
  it("describes a weak verdict as lacking strong identity", async () => {
    render(<Provenance entity="task" entityId={7} />);
    fireEvent.click(screen.getByRole("button", { name: "where did this come from?" }));

    expect(
      await screen.findByText(
        "This verdict did not use strong identity. The reviewer used a self-asserted name.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/Nobody used a personal API key/)).toBeNull();
  });
});

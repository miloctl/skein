import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: () => Promise.resolve({ id: 1 }) };
});

import { StandupComposer } from "@/components/standup-card";
import { StatusRegion } from "@/components/status-region";

describe("standup receipt", () => {
  it("announces a successful post through the polite status region", async () => {
    render(
      <>
        <StandupComposer />
        <StatusRegion />
      </>,
    );
    fireEvent.change(screen.getByLabelText(/what are you on today/), {
      target: { value: "Review the release" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Post" }));

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain("Standup posted."),
    );
    expect(screen.getByRole("alert").textContent).toBe("");
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: () =>
      Promise.resolve({
        suggestion: {
          id: "first_watch",
          feature: "First Watch",
          pitch: "Follow one task through Skein.",
          link: "/?tour=first-watch",
        },
      }),
  };
});

import { GuideHint } from "@/components/guide-hint";

describe("First Watch weekly suggestion", () => {
  it("starts the mounted shell instead of relying on a soft query navigation", async () => {
    const starts = vi.fn();
    window.addEventListener("skein-first-watch-start", starts);
    render(<GuideHint />);

    fireEvent.click(await screen.findByRole("button", { name: "Start First Watch" }));

    expect(starts).toHaveBeenCalledOnce();
    window.removeEventListener("skein-first-watch-start", starts);
  });
});

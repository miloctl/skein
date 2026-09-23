import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** The × unmounts the hint line with focus on it. Focus must land on the
 *  next control on the page, never on <body>, and the reader hears that the
 *  dismissal stuck. */

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path === "/api/field-guide/hint"
        ? Promise.resolve({
            suggestion: {
              id: "decision_log",
              feature: "Decision log",
              pitch: "Write down why, once.",
              link: "/charter",
            },
          })
        : Promise.resolve({}),
  };
});

import { GuideHint } from "@/components/guide-hint";
import { dismissStatus, getStatus } from "@/lib/status";

afterEach(() => act(() => dismissStatus()));

describe("dismissing the weekly suggestion", () => {
  it("moves focus to the next control and confirms", async () => {
    render(
      <main id="content" tabIndex={-1}>
        <GuideHint />
        <button>Team context</button>
      </main>,
    );
    const dismiss = await screen.findByRole("button", { name: "Never suggest Decision log again" });
    dismiss.focus();
    fireEvent.click(dismiss);

    expect(screen.queryByRole("button", { name: "Never suggest Decision log again" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Team context" }));
    await waitFor(() =>
      expect(getStatus()).toMatchObject({
        message: "Skein does not suggest Decision log again.",
        tone: "confirmation",
      }),
    );
  });
});

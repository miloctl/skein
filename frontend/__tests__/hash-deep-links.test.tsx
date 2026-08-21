import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useHashTarget } from "@/lib/hash-target";

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: mocks.api };
});
vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));

import Dashboard from "@/app/dashboard/page";

const QUESTIONS = [
  {
    id: 11,
    question: "Who signs off the cutover?",
    status: "open",
    visibility: "workspace",
    crew_id: 0,
  },
  {
    id: 12,
    question: "Does the vendor hold the date?",
    status: "open",
    visibility: "workspace",
    crew_id: 0,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.api.mockImplementation((path: string, opts?: { method?: string }) => {
    if (opts?.method) return Promise.resolve({});
    if (path === "/api/tasks/browse")
      return Promise.resolve({ open: [], done: [] });
    if (path === "/api/questions") return Promise.resolve(QUESTIONS);
    if (path === "/api/pulse") return Promise.resolve(null);
    return Promise.resolve([]);
  });
});

afterEach(() => {
  window.location.hash = "";
});

describe("a deep link that names one row", () => {
  it("focuses that row on the dashboard, not the top of the page", async () => {
    // set BEFORE the render, the way an arriving navigation leaves it. The
    // rows do not exist until the fetch settles, which is why the browser's
    // own fragment scroll never worked on this page.
    window.location.hash = "#question-12";
    render(<Dashboard />);

    await waitFor(() => expect(document.activeElement?.id).toBe("question-12"));
    expect(document.activeElement?.textContent).toContain(
      "Does the vendor hold the date?",
    );
  });

  it("answers a fragment that arrives while the page is already mounted", async () => {
    render(<Dashboard />);
    await screen.findByText("Who signs off the cutover?");

    // a next/link soft navigation fires neither hashchange nor popstate, so
    // an in-app link announces the target itself (components/nav-search.tsx)
    window.dispatchEvent(
      new CustomEvent("skein-hash", { detail: { anchor: "question-11" } }),
    );
    await waitFor(() => expect(document.activeElement?.id).toBe("question-11"));
  });
});

function Harness({ ready, rows }: { ready: number; rows: number[] }) {
  useHashTarget(ready);
  return (
    <div>
      {rows.map((id) => (
        <p key={id} id={`question-${id}`} tabIndex={-1}>
          question {id}
        </p>
      ))}
      <button type="button">elsewhere</button>
    </div>
  );
}

describe("useHashTarget", () => {
  it("waits for the row instead of spending its one try on the empty page", () => {
    window.location.hash = "#question-12";
    const { rerender } = render(<Harness ready={0} rows={[]} />);
    expect(document.activeElement?.id).not.toBe("question-12");

    rerender(<Harness ready={1} rows={[12]} />);
    expect(document.activeElement?.id).toBe("question-12");
  });

  it("leaves focus alone once it has landed", () => {
    window.location.hash = "#question-12";
    const { rerender } = render(<Harness ready={0} rows={[12]} />);
    expect(document.activeElement?.id).toBe("question-12");

    const elsewhere = screen.getByRole("button", { name: "elsewhere" });
    elsewhere.focus();
    // a background refresh delivers new rows and re-runs the effect. Unguarded,
    // this pulled the reader out of the control they had moved to.
    rerender(<Harness ready={2} rows={[12]} />);
    expect(document.activeElement).toBe(elsewhere);
  });

  it("announces the landing with the highlight pulse", () => {
    window.location.hash = "#question-12";
    render(<Harness ready={0} rows={[12]} />);
    const el = document.getElementById("question-12")!;
    expect(el.classList.contains("hash-landed")).toBe(true);
    // removed when the animation ends, so a later landing replays it
    el.dispatchEvent(new Event("animationend"));
    expect(el.classList.contains("hash-landed")).toBe(false);
  });

  it("pins the row against layout shift until the reader takes over", () => {
    // the dashboard's collections settle at different speeds — one that
    // lands AFTER the landing inserts content above the row and pushes it
    // out of view. Each later settle re-scrolls the row back; the reader's
    // first input ends the pinning so it never fights their scroll.
    const scrolls = vi
      .spyOn(Element.prototype, "scrollIntoView")
      .mockImplementation(() => {});
    try {
      window.location.hash = "#question-12";
      const { rerender } = render(<Harness ready={0} rows={[12]} />);
      expect(document.activeElement?.id).toBe("question-12");

      rerender(<Harness ready={1} rows={[12]} />); // a later collection settles
      expect(
        scrolls.mock.instances.some((el) => (el as Element).id === "question-12"),
      ).toBe(true);

      const before = scrolls.mock.calls.length;
      window.dispatchEvent(new Event("wheel")); // the reader takes over
      rerender(<Harness ready={2} rows={[12]} />);
      expect(scrolls.mock.calls.length).toBe(before);
    } finally {
      scrolls.mockRestore();
    }
  });
});

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  window.history.replaceState(null, "", "/dashboard");
});

// Captured from the disposable seeded mock API as ava.
const LESSON = { id: 1, engagement_id: null, project_class: "prototype", lesson: "Demo with realistic data — stakeholders don't extrapolate", recommendation: "Budget half a day for demo data", origin: "human", created_by: "ava", created_at: "2026-09-20T18:01:54+00:00", visibility: "workspace", crew_id: null };

const announce = (anchor: string) => act(() => window.dispatchEvent(new CustomEvent("skein-hash", { detail: { anchor } })));

describe("a deep link that names one row", () => {
  it("lands generic main fragments before delayed registers arrive", async () => {
    window.history.replaceState(null, "", "/dashboard?task=1#content");
    const original = mocks.api.getMockImplementation()!;
    let deliver!: (rows: typeof QUESTIONS) => void;
    mocks.api.mockImplementation((path: string) => path === "/api/questions" ? new Promise((resolve) => { deliver = resolve; }) : original(path));
    render(<Dashboard />);
    const main = screen.getByRole("main");
    expect(document.activeElement).toBe(main);
    await act(async () => deliver(QUESTIONS));
    expect(document.activeElement).toBe(main);
    expect((screen.getByRole("combobox", { name: "Browse register" }) as HTMLSelectElement).value).toBe("browse-tasks");
  });
  it("restores Tasks on Back to a hashless Browse URL without default autofocus", async () => {
    window.history.replaceState(null, "", "/dashboard");
    const { rerender } = render(<Dashboard />);
    const select = await screen.findByRole("combobox", { name: "Browse register" }) as HTMLSelectElement;
    expect(document.activeElement?.id).not.toBe("browse-tasks");
    // a fragment link (an engagement's return link, a search hit) pushes an
    // entry; the select rewrites the current one and is covered elsewhere
    act(() => {
      window.history.pushState(null, "", "/dashboard#browse-calendar");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    rerender(<Dashboard />);
    expect((screen.getByRole("combobox", { name: "Browse register" }) as HTMLSelectElement).value).toBe("browse-calendar");
    await waitFor(() => expect(document.activeElement?.id).toBe("browse-calendar"));
    act(() => window.history.back());
    await waitFor(() => expect(select.value).toBe("browse-tasks"));
    expect(window.location.hash).toBe("");
    expect(screen.getByRole("heading", { name: "Tasks" })).toBeTruthy();
    expect(document.activeElement?.id).not.toBe("browse-tasks");
    act(() => window.history.forward());
    await waitFor(() => expect(document.activeElement?.id).toBe("browse-calendar"));
  });
  it("reveals a delayed lesson once without taking focus back after a register switch", async () => {
    const original = mocks.api.getMockImplementation()!;
    let deliver!: (rows: typeof LESSON[]) => void;
    mocks.api.mockImplementation((path: string) => path === "/api/lessons" ? new Promise((resolve) => { deliver = resolve; }) : original(path));
    window.history.replaceState(null, "", "/dashboard");
    render(<Dashboard />);
    await screen.findByRole("combobox", { name: "Browse register" });
    announce("lesson-1");
    expect((screen.getByRole("combobox", { name: "Browse register" }) as HTMLSelectElement).value).toBe("browse-lessons");
    expect(document.activeElement?.id).not.toBe("lesson-1");
    await act(async () => deliver([LESSON]));
    expect(document.activeElement?.id).toBe("lesson-1");
    expect(document.activeElement?.closest("[hidden]")).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Browse register" }), { target: { value: "browse-tasks" } });
    expect(document.activeElement?.id).not.toBe("browse-tasks");
    expect(screen.queryByRole("heading", { name: /Lessons/ })).toBeNull();
    announce("lesson-1");
    expect(document.activeElement?.id).toBe("lesson-1");
  });

  it("retains a lesson request sent before the registers mount", async () => {
    const original = mocks.api.getMockImplementation()!;
    let deliver!: (rows: typeof QUESTIONS) => void;
    mocks.api.mockImplementation((path: string) => path === "/api/questions" ? new Promise((resolve) => { deliver = resolve; }) : path === "/api/lessons" ? Promise.resolve([LESSON]) : original(path));
    window.history.replaceState(null, "", "/dashboard");
    render(<Dashboard />);
    announce("lesson-1");
    await act(async () => deliver(QUESTIONS));
    await waitFor(() => expect(document.activeElement?.id).toBe("lesson-1"));
    expect(document.activeElement?.closest("[hidden]")).toBeNull();
  });

  it("clears the lesson filter for a requested row and focuses only once", async () => {
    const original = mocks.api.getMockImplementation()!;
    mocks.api.mockImplementation((path: string) => path === "/api/lessons" ? Promise.resolve([LESSON]) : path.startsWith("/api/lessons?") ? Promise.resolve([]) : original(path));
    window.history.replaceState(null, "", "/dashboard#browse-lessons");
    render(<Dashboard />);
    const filter = await screen.findByRole("combobox", { name: "Filter lessons by type of work" });
    fireEvent.change(filter, { target: { value: "prototype" } });
    await screen.findByText("No lesson recorded from prototype work yet.");
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    try {
      announce("lesson-1");
      await waitFor(() => expect(document.activeElement?.id).toBe("lesson-1"));
      expect((filter as HTMLSelectElement).value).toBe("");
      expect(focus.mock.instances.filter((el) => (el as HTMLElement).id === "lesson-1")).toHaveLength(1);
    } finally { focus.mockRestore(); }
  });

  it("keeps an uncontrolled answer while switching and handles Back/Forward fragments", async () => {
    window.history.replaceState(null, "", "/dashboard#question-11");
    render(<Dashboard />);
    await waitFor(() => expect(document.activeElement?.id).toBe("question-11"));
    fireEvent.click(screen.getByRole("button", { name: /answer… question #11/ }));
    const answer = screen.getByRole("textbox", { name: "Answer this question" });
    fireEvent.change(answer, { target: { value: "Keep this answer" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Browse register" }), { target: { value: "browse-tasks" } });
    window.history.replaceState(null, "", "/dashboard#question-11");
    act(() => window.dispatchEvent(new HashChangeEvent("hashchange")));
    await waitFor(() => expect(document.activeElement?.id).toBe("question-11"));
    expect(screen.getByRole("textbox", { name: "Answer this question" })).toBe(answer);
    expect((answer as HTMLInputElement).value).toBe("Keep this answer");
    announce("unknown-fragment");
    expect((screen.getByRole("combobox", { name: "Browse register" }) as HTMLSelectElement).value).toBe("browse-open-questions");
  });
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
  it("does not spend a landing on a hidden target", () => {
    window.location.hash = "#question-12";
    const { rerender } = render(<div hidden><Harness ready={0} rows={[12]} /></div>);
    expect(document.activeElement?.id).not.toBe("question-12");
    rerender(<div><Harness ready={1} rows={[12]} /></div>);
    expect(document.activeElement?.id).toBe("question-12");
  });

  it("retains a sent anchor until delayed rows arrive before the URL changes", () => {
    window.history.replaceState(null, "", "/dashboard");
    const { rerender } = render(<Harness ready={0} rows={[]} />);
    act(() => window.dispatchEvent(new CustomEvent("skein-hash", { detail: { anchor: "question-12" } })));
    rerender(<Harness ready={1} rows={[12]} />);
    expect(document.activeElement?.id).toBe("question-12");
  });
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

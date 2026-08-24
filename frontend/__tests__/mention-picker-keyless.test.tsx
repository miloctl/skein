/** The keyless half of the @ picker's specialist rows. On the mock provider
 *  a mid-sentence @slug reaches nothing — MockAgent has no tool loop, so no
 *  consult can happen — and a picker that offers one promises an answer the
 *  deployment cannot give. The leading position stays offered: /as is
 *  deterministic on every provider. mention-picker-groups.test.tsx pins the
 *  real-provider side. Its own module because the status fetch is cached at
 *  module scope — one provider per test file. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", NoopResizeObserver);

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string) =>
      path === "/api/users"
        ? Promise.resolve([{ name: "mira", kind: "human" }])
        : path === "/api/personas"
          ? Promise.resolve([
              {
                slug: "growth-mentor",
                name: "Growth Mentor",
                description: "coaching",
                emoji: "🌱",
              },
            ])
          : path === "/api/agents/status"
            ? Promise.resolve({ provider: "mock" })
            : Promise.resolve([]),
    getUser: () => "tester",
  };
});

import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";

import { Thread } from "@/components/thread";

function Harness() {
  const runtime = useLocalRuntime({
    async run() {
      return { content: [{ type: "text" as const, text: "ok" }] };
    },
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  );
}

const composer = () => screen.getByRole("textbox", { name: /Message/ }) as HTMLTextAreaElement;

async function type(value: string) {
  const box = composer();
  fireEvent.change(box, { target: { value } });
  await waitFor(() => expect(box.value).toBe(value));
  return box;
}

describe("the @ picker on the mock provider", () => {
  it("keeps specialists out of the mid-sentence rows", async () => {
    render(<Harness />);
    await type("ask @");
    await screen.findByRole("listbox");
    expect(screen.queryByRole("group", { name: "Specialists" })).toBeNull();
    // People stay: a mention files a row on every provider
    expect(screen.getByRole("group", { name: "People" })).toBeTruthy();
  });

  it("still offers specialists at the start, where /as answers keyless", async () => {
    render(<Harness />);
    await type("@");
    await screen.findByRole("listbox");
    expect(screen.getByRole("group", { name: "Specialists" })).toBeTruthy();
  });
});

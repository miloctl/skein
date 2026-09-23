import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

/** A chat's title is its first line. When the auth gate replaces the page
 *  after a sign-out, the tab and browser history kept it for the next
 *  person on the device. */

vi.mock("@/lib/chat-threads", () => ({
  chatThreads: () => Promise.resolve([{ id: "t1", title: "interview with Acme on Friday" }]),
}));

import { ThreadTitle } from "@/components/thread-title";

it("leaves no chat title in the tab once the chat is gone", async () => {
  const { unmount } = render(<ThreadTitle threadId="t1" />);
  await screen.findByText("interview with Acme on Friday");
  expect(document.title).toBe("interview with Acme on Friday — Skein");
  unmount();
  expect(document.title).toBe("Skein");
});

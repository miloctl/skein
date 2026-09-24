import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

/** In trusted-header mode a name is whatever a caller types, so a personal
 *  surface must say so instead of reading as private. */

const auth = vi.hoisted(() => ({ weak: true }));

vi.mock("@/lib/auth", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/auth")>();
  return { ...real, trustedHeaderIdentity: () => auth.weak };
});

import { AttachedFilesCard } from "@/components/attached-files-card";
import { WeakIdentityNotice } from "@/components/weak-identity-notice";

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return { ...real, api: () => Promise.resolve({ files: [], used: 0, quota: 1, max_file: 1 }) };
});

describe("the weak identity notice", () => {
  it("tells a trusted-header name that anyone can read this", () => {
    auth.weak = true;
    render(<AttachedFilesCard />);
    expect(
      screen.getByText(/Anyone who can reach this server can pick your name and read this\./),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sign in with a key for privacy." })).toBeTruthy();
  });

  it("says nothing to a signed-in or keyed caller", () => {
    auth.weak = false;
    const { container } = render(<WeakIdentityNotice />);
    expect(container.textContent).toBe("");
  });
});

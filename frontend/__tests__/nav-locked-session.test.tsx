import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** Keep the shell in app/layout.tsx order. With real auth and API modules,
 *  a protected request here can restart session recovery and remount the
 *  shell, repeating the same request on every mount. */

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@/lib/extensions/context", () => ({
  useFrontendExtensions: () => ({ navigation: [] }),
}));

const calls = { attention: 0, theme: 0, session: 0 };
const anonymous = {
  authenticated: false,
  user: "anonymous",
  strong: false,
  auth_method: null,
  csrf_token: "expired-csrf",
};

vi.stubGlobal(
  "fetch",
  vi.fn(async (input: string | URL) => {
    // a real round trip yields to the event loop. Without this the unfixed
    // loop lives entirely in microtasks and starves the test timeout.
    await new Promise((r) => setTimeout(r, 1));
    const url = String(input);
    if (url.endsWith("/api/auth/config"))
      return Response.json({
        mode: "oidc",
        error: "",
        client_id: "skein-web",
        scopes: "openid profile",
        authorize_url: "https://idp.example.com/authorize",
      });
    if (url.endsWith("/api/auth/session")) {
      calls.session += 1;
      return Response.json(anonymous);
    }
    if (url.includes("/api/attention")) calls.attention += 1;
    if (url.endsWith("/api/users/theme")) calls.theme += 1;
    return Response.json({ detail: "Sign in.", code: "SESSION_INVALID" }, { status: 401 });
  }),
);

import { AuthGate, SessionBoundary } from "@/components/auth-gate";
import { Nav } from "@/components/nav";
import { ThemeSync } from "@/components/theme-sync";

afterEach(() => vi.unstubAllGlobals());

describe("a stale session cookie", () => {
  it("keeps the sign-in gate stable without requesting attention or theme", async () => {
    render(
      <SessionBoundary>
        <ThemeSync />
        <Nav>
          <AuthGate>
            <div data-testid="page" />
          </AuthGate>
        </Nav>
      </SessionBoundary>,
    );
    const signIn = await screen.findByRole("button", { name: "Sign in" });
    // A delayed 401 must settle before checking for a recovery remount.
    await act(async () => {
      await vi.dynamicImportSettled();
      await new Promise((r) => setTimeout(r, 50));
    });
    await waitFor(() => expect(screen.queryByTestId("page")).toBeNull());
    expect(calls).toEqual({ attention: 0, theme: 0, session: 1 });
    expect(screen.getByRole("button", { name: "Sign in" })).toBe(signIn);
  });
});

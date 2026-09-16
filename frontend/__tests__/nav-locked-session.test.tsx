import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

/** A stale session cookie used to loop the app: Nav is AuthGate's sibling in
 *  app/layout.tsx and mounts first, so its attention poll fired before the
 *  gate's effect could publish `gated`. The 401 sent the session store back
 *  to "loading", SessionBoundary unmounted the shell, the re-read came back
 *  anonymous, the shell remounted, and Nav polled again — one request per
 *  round trip, forever, with the sign-in gate flickering behind it.
 *
 *  This mounts the two in layout order with the REAL api and auth modules,
 *  so the only thing between Nav and the request is the check under test. */

vi.mock("next/navigation", () => ({ usePathname: () => "/dashboard" }));
vi.mock("@/lib/extensions/context", () => ({
  useFrontendExtensions: () => ({ navigation: [] }),
}));

const calls = { attention: 0, session: 0 };
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
    return Response.json({ detail: "Sign in.", code: "SESSION_INVALID" }, { status: 401 });
  }),
);

import { AuthGate, SessionBoundary } from "@/components/auth-gate";
import { Nav } from "@/components/nav";

afterEach(() => vi.unstubAllGlobals());

describe("a stale session cookie", () => {
  it("reaches the sign-in gate once, without polling attention", async () => {
    render(
      <SessionBoundary>
        <Nav />
        <AuthGate>
          <div data-testid="page" />
        </AuthGate>
      </SessionBoundary>,
    );
    await screen.findByRole("button", { name: "Sign in" });
    // let any poll the mount started come back and re-bootstrap
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    await waitFor(() => expect(screen.queryByTestId("page")).toBeNull());
    expect(calls.attention).toBe(0);
    expect(calls.session).toBe(1);
  });
});

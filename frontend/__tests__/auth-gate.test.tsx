import { act, render, screen } from "@testing-library/react";
import { axe } from "vitest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** The identity gate for locked deployments (components/auth-gate.tsx). In
 *  oidc mode every request needs a credential — reads included — so without
 *  the gate a signed-out visitor lands on a page of dead panels, each
 *  printing the backend's 401 detail, with the remedy buried in the nav menu.
 *  These pin who is gated, who is never gated, and which wording each state
 *  gets. */

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

/** What GET /api/auth/config actually answers (backend/app/routes/auth.py):
 *  oidc mode always carries client_id and scopes, never only the mode. */
function mockConfig(mode: string) {
  const body =
    mode === "oidc"
      ? {
          mode,
          error: "",
          client_id: "skein-web",
          scopes: "openid profile",
          authorize_url: "https://idp.example.com/authorize",
        }
      : { mode, error: "" };
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => body })));
}

/** authConfig() caches per module instance, so each case gets a fresh import
 *  — otherwise the first test's mode answers every later one. */
async function renderGate(mode: string, path = "/") {
  pathname = path;
  mockConfig(mode);
  vi.resetModules();
  const { AuthGate } = await import("@/components/auth-gate");
  const view = render(
    <AuthGate>
      <div data-testid="page" />
    </AuthGate>,
  );
  // the gate decides after the config fetch resolves — flush that chain
  await act(async () => {
    await Promise.resolve();
  });
  return view;
}

function signIn(expiresInMs = 3_600_000) {
  window.localStorage.setItem(
    "skein-oidc",
    // `user` matters: isSignedIn() answers from the stored display name, so a
    // token stored without one reads as signed out and the gate stays up
    JSON.stringify({
      access_token: "tok",
      expires_at: Date.now() + expiresInMs,
      user: "casey",
    }),
  );
}

beforeEach(() => {
  pathname = "/";
});

afterEach(() => {
  // the stub outlives the case otherwise, and the next file's fetch is this
  // file's config answer
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("who is gated", () => {
  it("never gates a trusted-header deployment", async () => {
    await renderGate("trusted-header");
    expect(screen.getByTestId("page")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  });

  it("gates a signed-out visitor in oidc mode, replacing the page", async () => {
    await renderGate("oidc");
    expect(screen.queryByTestId("page")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByText("Sign in to open the workspace.")).toBeTruthy();
  });

  it("lets a signed-in session through", async () => {
    signIn();
    await renderGate("oidc");
    expect(screen.getByTestId("page")).toBeTruthy();
  });

  it("lets a personal-key holder through in every locked mode", async () => {
    // routes/deps.py resolves a personal key first in EVERY mode — the gate
    // treating a key holder as signed out would gate working automation users
    window.localStorage.setItem("skein-key", "sk-skein-personal");
    const first = await renderGate("oidc");
    expect(screen.getByTestId("page")).toBeTruthy();
    first.unmount();
    await renderGate("api-key");
    expect(screen.getByTestId("page")).toBeTruthy();
  });

  it("gates api-key mode without a key, naming the bootstrap remedy", async () => {
    await renderGate("api-key");
    expect(screen.queryByTestId("page")).toBeNull();
    expect(screen.getByText(/bootstrap_key/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Settings" })).toBeTruthy();
  });

  it("exempts Settings — the one page where a key can be pasted", async () => {
    await renderGate("api-key", "/settings");
    expect(screen.getByTestId("page")).toBeTruthy();
  });

  it("exempts the sign-in callback the gate itself starts", async () => {
    await renderGate("oidc", "/auth/callback");
    expect(screen.getByTestId("page")).toBeTruthy();
  });

  it("does not extend the /auth exemption to a same-prefix page", async () => {
    // startsWith("/auth") would hand a future /authority page a silent
    // exemption, and the gate is the only thing standing between that page
    // and a reader with no credential
    await renderGate("oidc", "/authority");
    expect(screen.queryByTestId("page")).toBeNull();
  });

  it("renders the page when the config could not be read", async () => {
    // a network fault proves nothing about the deployment's identity model,
    // so "unknown" must not lock anyone out of a trusted-header instance
    await renderGate("unknown");
    expect(screen.getByTestId("page")).toBeTruthy();
  });
});

describe("what each ended session reads", () => {
  it("tells an expired session the fix, without the landing warmth", async () => {
    window.sessionStorage.setItem("skein-oidc-ended", "expired");
    await renderGate("oidc");
    expect(
      screen.getByRole("heading", { name: "Your sign-in expired" }),
    ).toBeTruthy();
    expect(screen.getByText("Sign in again to continue.")).toBeTruthy();
    expect(screen.queryByText("many strands, one formation")).toBeNull();
  });

  it("closes a chosen sign-out with the signed-out line", async () => {
    window.sessionStorage.setItem("skein-oidc-ended", "signed-out");
    await renderGate("oidc");
    // the pool varies by day in the browser; here whimsy.ts is unhydrated and
    // returns its first line. The contract either way is the prefix, which
    // states the condition before the voice starts.
    expect(screen.getByText(/^Signed out\./)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
  });
});

describe("what the gate does to the surfaces around it", () => {
  it("publishes the gated state so the siblings can stand down", async () => {
    // the nav and the two overlays are siblings, not children — the gate
    // covers them rather than unmounting them, and both overlays out-rank it
    // on z-index. lib/gated.ts is how they learn.
    // imported AFTER the render: renderGate resets the module registry, so an
    // earlier import would read a different instance of the store than the
    // component writes to
    const view = await renderGate("oidc");
    const { isGated } = await import("@/lib/gated");
    expect(isGated()).toBe(true);
    view.unmount();
    expect(isGated()).toBe(false);
  });

  it("puts focus on the panel, not the button", async () => {
    // the button is LAST in the reading order: focusing it skips the sentence
    // that says why the workspace was replaced, which on an expired session
    // is the entire message
    window.sessionStorage.setItem("skein-oidc-ended", "expired");
    await renderGate("oidc");
    expect(document.activeElement).toBe(screen.getByRole("main"));
  });

  it("gives every gated state a heading to navigate by", async () => {
    // heading navigation is the primary screen-reader wayfinding method, and
    // the expired state is where a reader most needs to orient
    for (const ended of ["", "signed-out", "expired"]) {
      window.sessionStorage.setItem("skein-oidc-ended", ended);
      const view = await renderGate("oidc");
      expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
      view.unmount();
    }
  });
});

describe("how a session ends", () => {
  it("records 'expired' when a stored token dies with nothing to renew from", async () => {
    signIn(-1_000); // expired, no refresh_token
    const changed = vi.fn();
    window.addEventListener("skein-identity-change", changed, { once: true });
    const { bearer } = await import("@/lib/api");
    expect(await bearer()).toBe("");
    expect(window.sessionStorage.getItem("skein-oidc-ended")).toBe("expired");
    expect(changed).toHaveBeenCalledOnce();
  });

  it("records 'signed-out' on a chosen sign-out", async () => {
    signIn();
    const { signOut } = await import("@/lib/auth");
    await signOut();
    expect(window.sessionStorage.getItem("skein-oidc-ended")).toBe("signed-out");
    expect(window.localStorage.getItem("skein-oidc")).toBeNull();
  });

  it("signs out when the generation marker cannot be stored", async () => {
    signIn();
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "skein-oidc-generation") throw new DOMException("blocked");
      return originalSetItem.call(this, key, value);
    });
    const { signOut } = await import("@/lib/auth");

    await signOut();

    expect(window.localStorage.getItem("skein-oidc")).toBeNull();
    expect(window.sessionStorage.getItem("skein-oidc-ended")).toBe("signed-out");
  });

  it("sessionRejected touches only the session whose token was judged", async () => {
    const auth = await import("@/lib/auth");
    // a refresh may have replaced the token an in-flight request carried —
    // that request's 401 must not disturb the newer session
    signIn();
    await auth.sessionRejected("some-other-token");
    const before = window.localStorage.getItem("skein-oidc");
    expect(before).not.toBeNull();
    await auth.sessionRejected("tok");
    expect(window.localStorage.getItem("skein-oidc")).toBeNull();
    expect(window.sessionStorage.getItem("skein-oidc-ended")).toBe("expired");
  });

  it("ends a rejected session when its refresh demotion cannot be stored", async () => {
    window.localStorage.setItem(
      "skein-oidc",
      JSON.stringify({
        access_token: "tok",
        refresh_token: "renew-me",
        expires_at: Date.now() + 3_600_000,
        user: "casey",
      }),
    );
    const originalSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "skein-oidc") throw new DOMException("blocked");
      return originalSetItem.call(this, key, value);
    });
    const auth = await import("@/lib/auth");

    await auth.sessionRejected("tok");

    expect(window.localStorage.getItem("skein-oidc")).toBeNull();
    expect(window.sessionStorage.getItem("skein-oidc-ended")).toBe("expired");
  });

  it("renews rather than ends when a refresh token survives the 401", async () => {
    // an IdP that omits expires_in stores 0, which turns the proactive
    // refresh off entirely — so the first 401 is where EVERY such session
    // lands, and ending it there costs a full sign-in every token lifetime
    window.localStorage.setItem(
      "skein-oidc",
      JSON.stringify({
        access_token: "tok",
        refresh_token: "renew-me",
        expires_at: 0,
        user: "casey",
      }),
    );
    const auth = await import("@/lib/auth");
    await auth.sessionRejected("tok");
    const stored = JSON.parse(window.localStorage.getItem("skein-oidc") ?? "null");
    expect(stored?.refresh_token).toBe("renew-me");
    expect(window.sessionStorage.getItem("skein-oidc-ended")).toBeNull();
    // back-dated, so the next accessTokenResult() call renews instead of reusing it
    expect(stored.expires_at).toBeLessThan(Date.now());
  });

  it("keeps the session when a refresh returns a retryable provider outage", async () => {
    window.localStorage.setItem(
      "skein-oidc",
      JSON.stringify({
        access_token: "expired",
        refresh_token: "renew-me",
        expires_at: Date.now() - 1_000,
        user: "casey",
      }),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 503,
        json: async () => ({
          detail:
            "Skein cannot reach the identity provider. Wait one minute, then start the sign-in again.",
        }),
      })),
    );
    const auth = await import("@/lib/auth");

    expect(await auth.accessTokenResult()).toEqual({ token: "", canFallback: false });
    expect(window.localStorage.getItem("skein-oidc")).not.toBeNull();
    expect(window.sessionStorage.getItem("skein-oidc-ended")).toBeNull();
  });

  it("clears a stale reason when another tab signs back in", async () => {
    // the reason is per-tab and writeStored clears it only in the writing
    // tab: a tab that expired, watched a sign-in elsewhere, then watched a
    // sign-out elsewhere told its reader the sign-in had expired
    await import("@/lib/auth");
    window.sessionStorage.setItem("skein-oidc-ended", "expired");
    signIn(); // as another tab would leave localStorage
    window.dispatchEvent(new Event("storage"));
    expect(window.sessionStorage.getItem("skein-oidc-ended")).toBeNull();
  });
});

describe("structure", () => {
  it("has no structural accessibility violations on the landing", async () => {
    const { container } = await renderGate("oidc");
    expect(await axe(container)).toHaveNoViolations();
  });
});

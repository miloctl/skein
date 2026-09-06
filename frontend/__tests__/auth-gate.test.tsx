import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { axe } from "vitest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
const anonymous = { authenticated: false, user: "anonymous", strong: false, auth_method: "", csrf_token: "" };
let metadata = anonymous;
const person = (user = "casey", method = "oidc") => ({ authenticated: true, user, strong: true, auth_method: method, csrf_token: `csrf-${user}` });
function mockConfig(mode: string) {
  const config = mode === "oidc" ? { mode, error: "", client_id: "skein-web", scopes: "openid profile", authorize_url: "https://idp.example.com/authorize" } : { mode, error: "" };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method === "DELETE") { metadata = anonymous; return new Response(null, { status: 204 }); }
    return new Response(JSON.stringify(input.endsWith("/auth/config") ? config : metadata));
  }));
}
async function renderGate(mode: string, path = "/") {
  pathname = path; mockConfig(mode); vi.resetModules();
  const { AuthGate, SessionBoundary } = await import("@/components/auth-gate");
  const view = render(<SessionBoundary><AuthGate><div data-testid="page" /></AuthGate></SessionBoundary>);
  await act(async () => { await Promise.resolve(); });
  return view;
}
beforeEach(() => { pathname = "/"; metadata = anonymous; });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("who is gated", () => {
  it("never gates an anonymous trusted-header deployment", async () => {
    await renderGate("trusted-header");
    expect(screen.getByTestId("page")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  });
  it("gates a signed-out visitor in oidc mode instead of mounting private content", async () => {
    await renderGate("oidc");
    expect(screen.queryByTestId("page")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByText("Sign in to open the workspace.")).toBeTruthy();
  });
  it("lets a server-confirmed OIDC session through", async () => {
    metadata = person(); await renderGate("oidc"); expect(screen.getByTestId("page")).toBeTruthy();
  });
  it.each(["oidc", "api-key", "trusted-header"])("lets a key-backed browser session through in %s", async (mode) => {
    metadata = person("casey", "api-key"); await renderGate(mode); expect(screen.getByTestId("page")).toBeTruthy();
  });
  it("does not trust an old browser key", async () => {
    localStorage.setItem("skein-key", "sk-skein-personal"); await renderGate("api-key");
    expect(screen.queryByTestId("page")).toBeNull(); expect(localStorage.getItem("skein-key")).toBeNull();
  });
  it("gates api-key mode without a session and names the bootstrap remedy", async () => {
    await renderGate("api-key"); expect(screen.queryByTestId("page")).toBeNull();
    expect(screen.getByText(/bootstrap_key/)).toBeTruthy(); expect(screen.getByRole("link", { name: "Open Settings" })).toBeTruthy();
  });
  it.each(["/settings", "/auth/callback"])("keeps the sign-in remedy reachable at %s", async (path) => {
    await renderGate("oidc", path); expect(screen.getByTestId("page")).toBeTruthy();
  });
  it("does not extend the auth exemption to a same-prefix page", async () => {
    await renderGate("oidc", "/authority"); expect(screen.queryByTestId("page")).toBeNull();
  });
  it("shows unavailable state instead of assuming weak identity when config cannot be read", async () => {
    await renderGate("unknown"); expect(screen.queryByTestId("page")).toBeNull();
    expect(screen.getByRole("alert").textContent).toMatch(/configuration/);
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
  it("offers explicit recovery when a trusted-header browser lost its session cookie", async () => {
    sessionStorage.setItem("skein-oidc-ended", "expired");
    await renderGate("trusted-header");
    expect(screen.queryByTestId("page")).toBeNull();
    expect(screen.getByRole("link", { name: "Open Settings" })).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Sign out" })); });
    expect(screen.getByTestId("page")).toBeTruthy();
  });

  it("offers explicit logout for an expired cookie", async () => {
    metadata = { ...anonymous, csrf_token: "expired-csrf" };
    await renderGate("oidc");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Sign out" })); });
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
    expect(sessionStorage.getItem("skein-oidc-ended")).toBe("signed-out");
  });
});

describe("ended-session wording and accessibility", () => {
  it("tells an expired session the fix without landing warmth", async () => {
    sessionStorage.setItem("skein-oidc-ended", "expired"); await renderGate("oidc");
    expect(screen.getByRole("heading", { name: "Your sign-in expired" })).toBeTruthy();
    expect(screen.getByText("Sign in again to continue.")).toBeTruthy();
    expect(screen.queryByText("many strands, one formation")).toBeNull();
  });
  it("closes chosen sign-out with its closer", async () => {
    sessionStorage.setItem("skein-oidc-ended", "signed-out"); await renderGate("oidc");
    expect(screen.getByText(/^Signed out\./)).toBeTruthy();
  });
  it("publishes the gated state for overlays and nav", async () => {
    const view = await renderGate("oidc"); const { isGated } = await import("@/lib/gated");
    expect(isGated()).toBe(true); view.unmount(); expect(isGated()).toBe(false);
  });
  it("focuses the panel instead of skipping its explanation", async () => {
    sessionStorage.setItem("skein-oidc-ended", "expired"); await renderGate("oidc");
    expect(document.activeElement).toBe(screen.getByRole("main"));
  });
  it.each(["", "signed-out", "expired"])("provides an accessible heading for ended state %s", async (ended) => {
    sessionStorage.setItem("skein-oidc-ended", ended); await renderGate("oidc");
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
  });
  it("has no structural accessibility violations on the landing", async () => {
    const { container } = await renderGate("oidc"); expect(await axe(container)).toHaveNoViolations();
  });
  it("clears an expired reason after a fresh session bootstrap", async () => {
    sessionStorage.setItem("skein-oidc-ended", "expired"); metadata = person();
    await renderGate("oidc"); expect(sessionStorage.getItem("skein-oidc-ended")).toBeNull();
  });
});

it("unmounts all identity-scoped state, including exempt Settings siblings", async () => {
  pathname = "/settings"; metadata = person("ava"); mockConfig("oidc"); vi.resetModules();
  const { SessionBoundary, AuthGate } = await import("@/components/auth-gate");
  const auth = await import("@/lib/auth");
  function PrivateState({ name }: { name: string }) { const [draft, setDraft] = useState(""); return <input aria-label={name} value={draft} onChange={(e) => setDraft(e.target.value)} />; }
  render(<SessionBoundary><PrivateState name="nav" /><PrivateState name="peek" /><AuthGate><PrivateState name="settings" /></AuthGate></SessionBoundary>);
  await act(async () => { await Promise.resolve(); });
  for (const name of ["nav", "peek", "settings"]) fireEvent.change(screen.getByLabelText(name), { target: { value: "A private draft" } });
  metadata = person("marcus");
  await act(async () => { await auth.bootstrapSession(true); });
  for (const name of ["nav", "peek", "settings"]) expect((screen.getByLabelText(name) as HTMLInputElement).value).toBe("");
});

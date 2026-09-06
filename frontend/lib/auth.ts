import { API_URL, backendUnreachable } from "./config";

const errorMessage = (error: unknown) => error instanceof TypeError
  ? backendUnreachable(error)
  : error instanceof Error ? error.message : String(error);

const GENERATION_KEY = "skein-oidc-generation";
const FLOW_KEY = "skein-oidc-flow";
const SESSION_EVENT_KEY = "skein-session-change";
const ENDED_KEY = "skein-oidc-ended";
const CHANGED = "Your browser identity changed. Check your identity, then try again.";

export type AuthConfig = {
  mode: string;
  error: string;
  browser_session_error?: string;
  client_id?: string;
  scopes?: string;
  authorize_url?: string;
};
type BrowserSession = {
  authenticated: boolean;
  user: string;
  strong: boolean;
  auth_method: string;
  csrf_token: string;
};
type SessionState = BrowserSession & {
  status: "loading" | "ready" | "unavailable";
  error: string;
};
const ANONYMOUS: BrowserSession = {
  authenticated: false, user: "anonymous", strong: false, auth_method: "", csrf_token: "",
};
let session: SessionState = { ...ANONYMOUS, status: "loading", error: "" };
let mode = "";
let revision = 0;
let blockWeakFallback = sessionEnd() === "expired";
let bootstrap: Promise<SessionState> | null = null;
let configCache: Promise<AuthConfig> | null = null;

export function sessionGeneration(): string {
  try { return window.localStorage.getItem(GENERATION_KEY) ?? ""; } catch { return ""; }
}

function eraseLegacyCredentials() {
  for (const key of ["skein-key", "skein-oidc"]) {
    try { window.localStorage.removeItem(key); } catch {}
  }
}

function publish(next: SessionState) {
  const identityChanged = session.csrf_token !== next.csrf_token ||
    session.user !== next.user || session.authenticated !== next.authenticated ||
    session.strong !== next.strong;
  const changed = identityChanged || session.status !== next.status || session.error !== next.error;
  session = next;
  if (identityChanged) revision++;
  // Every focus re-reads the session. An unchanged result must not fire the
  // listeners that empty lib/api.ts's GET cache and refetch capabilities.
  if (changed) window.dispatchEvent(new Event("storage"));
  if (identityChanged) window.dispatchEvent(new Event("skein-identity-change"));
}

export const sessionSnapshot = () => session;
export const sessionRevision = () => {
  let weakName = "";
  if (trustedHeaderIdentity()) {
    try { weakName = window.localStorage.getItem("skein-user") ?? ""; } catch {}
  }
  return `${sessionGeneration()}:${revision}:${weakName}`;
};
export function checkSessionRevision(expected: string) {
  if (expected !== sessionRevision()) throw new Error(CHANGED);
}
export function subscribeSession(listener: () => void) {
  window.addEventListener("storage", listener);
  return () => window.removeEventListener("storage", listener);
}
export const signedInUser = () => session.authenticated ? session.user : "";
export const isSignedIn = () => session.authenticated;
export const trustedHeaderIdentity = () =>
  mode === "trusted-header" && session.status === "ready" &&
  !session.csrf_token && !session.authenticated && !blockWeakFallback;

export function sessionEnd(): string {
  try { return window.sessionStorage.getItem(ENDED_KEY) ?? ""; } catch { return ""; }
}
function markEnded(reason: "signed-out" | "expired" | "") {
  try {
    if (reason) window.sessionStorage.setItem(ENDED_KEY, reason);
    else window.sessionStorage.removeItem(ENDED_KEY);
  } catch {}
}

export function authConfig(): Promise<AuthConfig> {
  if (!configCache) {
    const attempt = fetch(`${API_URL}/api/auth/config`, { credentials: "include", cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const config = await r.json() as AuthConfig;
        mode = config.mode;
        return config;
      })
      .catch((error) => {
        if (configCache === attempt) configCache = null;
        return { mode: "unknown", error: String(error) };
      });
    configCache = attempt;
  }
  return configCache;
}

function metadata(value: unknown): BrowserSession {
  const v = value as Partial<BrowserSession> | null;
  if (!v || typeof v.authenticated !== "boolean" || typeof v.user !== "string" ||
    typeof v.strong !== "boolean" || (typeof v.auth_method !== "string" && (v.authenticated || v.auth_method !== null)) ||
    typeof v.csrf_token !== "string" || (v.authenticated && (!v.csrf_token || !v.user)))
    throw new Error("The server returned an invalid session response. Check the server log.");
  return { authenticated: v.authenticated, user: v.user, strong: v.strong,
    auth_method: v.auth_method ?? "", csrf_token: v.csrf_token };
}
async function responseError(res: Response): Promise<Error> {
  let detail = `Request failed (HTTP ${res.status}).`;
  try { const body = await res.json(); if (typeof body.detail === "string") detail = body.detail; } catch {}
  return new Error(detail);
}
async function readSession(): Promise<BrowserSession> {
  const res = await fetch(`${API_URL}/api/auth/session`, { credentials: "include", cache: "no-store" });
  if (!res.ok) throw await responseError(res);
  return metadata(await res.json());
}

export function bootstrapSession(force = false): Promise<SessionState> {
  eraseLegacyCredentials();
  if (bootstrap) return bootstrap;
  if (!force && session.status === "ready") return Promise.resolve(session);
  const started = sessionRevision();
  const attempt = Promise.all([authConfig(), navigator.locks ? withSessionLock(readSession) : readSession()]).then(([config, value]) => {
    if (sessionRevision() !== started) throw new Error(CHANGED);
    if (config.mode === "unknown") throw new Error("Cannot read the sign-in configuration. Check that the server is running, then try again.");
    if (session.authenticated && !value.authenticated) {
      markEnded("expired");
      blockWeakFallback = true;
    }
    if (value.authenticated) markEnded("");
    publish({ ...value, status: "ready", error: "" });
    return session;
  }).catch((error) => {
    if (sessionRevision() === started)
      publish({ ...session, status: "unavailable", error: errorMessage(error) });
    throw error;
  }).finally(() => { if (bootstrap === attempt) bootstrap = null; });
  bootstrap = attempt;
  return attempt;
}

export async function sessionRejected(response: Response, sentRevision: string) {
  if (sentRevision !== sessionRevision()) throw new Error(CHANGED);
  let code = "";
  try { code = (await response.clone().json()).code; } catch {}
  if (!["SESSION_INVALID", "SESSION_CHANGED"].includes(code)) return;
  if (sentRevision !== sessionRevision()) throw new Error(CHANGED);
  blockWeakFallback = true;
  markEnded("expired");
  publish({ ...session, authenticated: false, strong: false, status: "loading", error: "" });
  // A stale response cannot expire a newer cookie. Metadata reads never write cookies.
  bootstrap = null;
  await bootstrapSession(true).catch(() => {});
}

function randomString(bytes: number): string {
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return base64url(raw);
}
function base64url(bytes: Uint8Array): string {
  let text = "";
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function newGeneration(): string {
  const value = randomString(16);
  try { window.localStorage.setItem(GENERATION_KEY, value); }
  catch { throw new Error("This browser blocks sign-in coordination. Allow Skein to use browser storage, then try again."); }
  return value;
}
function checkGeneration(expected: string) {
  if (sessionGeneration() !== expected) throw new Error(CHANGED);
}
async function withSessionLock<T>(run: () => Promise<T>): Promise<T> {
  if (!navigator.locks)
    throw new Error("This browser cannot coordinate sign-in across tabs. Use a current browser, then try again.");
  // Set-Cookie is applied before JS receives the response. The lock must cover
  // the whole exchange AND cookie check, not only the metadata commit.
  return navigator.locks.request("skein-oidc-session", run);
}
function redirectUri(): string { return `${window.location.origin}/auth/callback`; }

export async function signIn(returnTo?: string): Promise<string> {
  try {
    const expected = newGeneration();
    const cfg = await authConfig();
    if (cfg.mode === "unknown") return "Cannot read the sign-in configuration. Check that the server is running, then start the sign-in again.";
    if (cfg.mode !== "oidc") return "This deployment does not use sign-in.";
    if (cfg.error || cfg.browser_session_error || !cfg.authorize_url || !cfg.client_id)
      return cfg.error || cfg.browser_session_error || "Sign-in is not configured. Ask whoever runs the server.";
    const verifier = randomString(32);
    const state = randomString(16);
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    await withSessionLock(async () => {
      checkGeneration(expected);
      window.sessionStorage.setItem(FLOW_KEY, JSON.stringify({ verifier, state, generation: expected,
        returnTo: returnTo || window.location.pathname + window.location.search + window.location.hash }));
    });
    const params = new URLSearchParams({ response_type: "code", client_id: cfg.client_id,
      redirect_uri: redirectUri(), scope: cfg.scopes || "openid profile", state,
      code_challenge: base64url(new Uint8Array(digest)), code_challenge_method: "S256" });
    checkGeneration(expected);
    window.location.assign(`${cfg.authorize_url}?${params}`);
    return "";
  } catch (error) { return errorMessage(error); }
}

function localPath(returnTo: string): string {
  try {
    const url = new URL(returnTo, window.location.origin);
    return url.origin === window.location.origin ? url.pathname + url.search + url.hash : "/";
  } catch { return "/"; }
}
async function establishSession(path: string, body: Record<string, string>, expected: string) {
  await withSessionLock(async () => {
    checkGeneration(expected);
    const res = await fetch(`${API_URL}${path}`, { method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) throw await responseError(res);
    const issued = metadata(await res.json());
    checkGeneration(expected);
    const accepted = await readSession();
    checkGeneration(expected);
    if (!accepted.authenticated || accepted.csrf_token !== issued.csrf_token) {
      const error = "The browser did not accept the sign-in cookie. Use HTTPS and keep the app and API on the same site, then sign in again.";
      blockWeakFallback = true;
      markEnded("expired");
      publish({ ...accepted, authenticated: false, strong: false, status: "unavailable", error });
      throw new Error(error);
    }
    blockWeakFallback = false;
    markEnded("");
    publish({ ...accepted, status: "ready", error: "" });
    window.localStorage.setItem(SESSION_EVENT_KEY, randomString(16));
  });
}

export async function signInWithKey(key: string): Promise<void> {
  const expected = newGeneration();
  await establishSession("/api/auth/session/key", { key: key.trim() }, expected);
}
let completing: { search: string; result: Promise<string> } | null = null;
export function completeSignIn(search: string): Promise<string> {
  if (completing?.search === search) return completing.result;
  const result = runCompleteSignIn(search);
  completing = { search, result };
  result.catch(() => { if (completing?.result === result) completing = null; });
  return result;
}
async function runCompleteSignIn(search: string): Promise<string> {
  const params = new URLSearchParams(search);
  if (params.get("error")) throw new Error("The identity provider refused the sign-in. Start the sign-in again.");
  let flow: { verifier: string; state: string; generation: string; returnTo: string } | null = null;
  try { flow = JSON.parse(window.sessionStorage.getItem(FLOW_KEY) || "null"); } catch {}
  window.sessionStorage.removeItem(FLOW_KEY);
  if (!params.get("code") || !flow?.generation)
    throw new Error("This sign-in link is no longer valid. Start the sign-in again.");
  if (!params.get("state") || params.get("state") !== flow.state)
    throw new Error("This sign-in did not start in this tab. Start the sign-in again.");
  await establishSession("/api/auth/token", { code: params.get("code")!, code_verifier: flow.verifier, redirect_uri: redirectUri() }, flow.generation);
  return localPath(flow.returnTo || "/");
}

export async function signOut(): Promise<void> {
  const expected = newGeneration();
  blockWeakFallback = true;
  // Hide private content immediately while a prior cookie-changing request
  // releases the lock. Logout then reads that request's actual cookie.
  publish({ ...session, authenticated: false, strong: false, status: "loading", error: "" });
  try {
    await withSessionLock(async () => {
      checkGeneration(expected);
      const current = await readSession();
      checkGeneration(expected);
      const res = await fetch(`${API_URL}/api/auth/session`, { method: "DELETE", credentials: "include",
        headers: current.csrf_token ? { "X-Skein-CSRF": current.csrf_token } : {} });
      if (!res.ok) throw await responseError(res);
      checkGeneration(expected);
      blockWeakFallback = false;
      markEnded("signed-out");
      publish({ ...ANONYMOUS, status: "ready", error: "" });
      window.localStorage.setItem(SESSION_EVENT_KEY, randomString(16));
    });
  } catch (error) {
    if (sessionGeneration() === expected)
      publish({ ...session, status: "unavailable", error: errorMessage(error) });
    throw error;
  }
}

if (typeof window !== "undefined") {
  eraseLegacyCredentials();
  window.addEventListener("storage", (event) => {
    if (!(event instanceof StorageEvent) || ![GENERATION_KEY, SESSION_EVENT_KEY].includes(event.key ?? "")) return;
    blockWeakFallback = event.key !== SESSION_EVENT_KEY;
    revision++;
    bootstrap = null;
    publish({ ...ANONYMOUS, status: "loading", error: "" });
    // Wait for the cookie-changing request, not merely the event announcing
    // its intent. Otherwise this tab bootstraps the cookie being replaced.
    void bootstrapSession(true).catch(() => {});
  });
  window.addEventListener("focus", () => { void bootstrapSession(true).catch(() => {}); });
}

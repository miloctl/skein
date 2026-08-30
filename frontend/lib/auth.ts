/** Browser sign-in for SKEIN_AUTH_MODE=oidc: OAuth 2.0 authorization code
 *  with PKCE (RFC 7636), the flow for a public client that holds no secret.
 *
 *  Shape of a sign-in:
 *    1. signIn()  makes a random verifier, sends its SHA-256 challenge to the
 *       identity provider, and keeps the verifier in sessionStorage.
 *    2. the provider sends the browser back to /auth/callback with a code.
 *    3. completeSignIn() posts code + verifier to the API, which relays them
 *       to the provider, validates what comes back, and answers with a token.
 *
 *  The verifier never leaves this browser tab until step 3, which is what
 *  makes an intercepted code useless on its own.
 *
 *  Tokens live in localStorage, beside the personal API key, and carry the
 *  same exposure: any script running on this origin can read them. They are
 *  the weaker of the two — a token expires, a key does not — and the app has
 *  no server session to hold them in instead.
 */

import { API_URL } from "./config";

const TOKEN_KEY = "skein-oidc";
const GENERATION_KEY = "skein-oidc-generation";
// the in-flight authorization: per tab, and gone when the tab closes. A second
// tab must not be able to complete a sign-in this one started.
const FLOW_KEY = "skein-oidc-flow";
// Why this tab has no session — "signed-out" | "expired" — for the auth
// gate's wording: a person who chose to leave gets a closer, a person whose
// token died mid-task gets the fix and their place back. Per tab on purpose:
// a fresh tab has no story to tell and gets the plain landing.
const ENDED_KEY = "skein-oidc-ended";

export type AuthConfig = {
  mode: string;
  error: string;
  client_id?: string;
  scopes?: string;
  authorize_url?: string;
};

type Stored = {
  access_token: string;
  refresh_token: string;
  expires_at: number; // epoch ms; 0 when the provider sent no lifetime
  user: string;
  generation?: string;
};

function currentGeneration(): string {
  try {
    return window.localStorage.getItem(GENERATION_KEY) ?? "";
  } catch {
    return "";
  }
}

function readStored(): Stored | null {
  try {
    const raw = window.localStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const t = JSON.parse(raw);
    if (
      typeof t?.access_token === "string" &&
      t.access_token &&
      (t.generation ?? "") === currentGeneration()
    )
      return t as Stored;
  } catch {}
  return null;
}

function writeStored(t: Stored | null, previousUser = readStored()?.user ?? "") {
  try {
    if (t) {
      window.localStorage.setItem(TOKEN_KEY, JSON.stringify(t));
      // a completed sign-in supersedes whatever ended the last session
      window.sessionStorage.removeItem(ENDED_KEY);
    } else window.localStorage.removeItem(TOKEN_KEY);
  } catch {}
  // same-tab subscribers (nav, settings) listen for this, as everywhere else
  window.dispatchEvent(new Event("storage"));
  if (previousUser !== (t?.user ?? ""))
    window.dispatchEvent(new Event("skein-identity-change"));
}

function setGeneration(generation: string): boolean {
  try {
    window.localStorage.setItem(GENERATION_KEY, generation);
    return true;
  } catch {
    return false;
  }
}

function commitStored(generation: string, stored: Stored): boolean {
  if (currentGeneration() !== generation) return false;
  const expected = { ...stored, generation };
  writeStored(expected);
  return sameSession(readStored(), expected);
}

function endSession(reason: "signed-out" | "expired") {
  const previousUser = readStored()?.user ?? "";
  markEnded(reason);
  // A marker failure must not leave the bearer token behind after sign-out.
  setGeneration(randomString(16));
  writeStored(null, previousUser);
}

/** Session commits and refresh single-flight use separate locks. If the IdP
 *  stalls, sign-out can still invalidate the stored generation. */
async function withSessionLock<T>(run: () => T | Promise<T>): Promise<T | null> {
  const locks = navigator.locks;
  if (!locks) return null;
  return locks.request("skein-oidc-session", run);
}

async function withRefreshLock<T>(run: () => T | Promise<T>): Promise<T | null> {
  const locks = navigator.locks;
  if (!locks) return null;
  return locks.request("skein-oidc-refresh", run);
}

if (typeof window !== "undefined") {
  // writeStored clears the reason only in the tab that writes the token, and
  // the reason is per-tab. A tab that expired, then watched a sign-in in
  // ANOTHER tab, kept its stale "expired" — so the next sign-out anywhere
  // told that tab's reader their sign-in had expired, which is not what
  // happened. Registered at import, so it runs before the component
  // subscribers that read the reason on the same event.
  window.addEventListener("storage", () => {
    if (!readStored()) return;
    try {
      window.sessionStorage.removeItem(ENDED_KEY);
    } catch {}
  });
}

/** "signed-out" | "expired" | "" — why this tab has no session. */
export function sessionEnd(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.sessionStorage.getItem(ENDED_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Set BEFORE the writeStored that clears the tokens: writeStored dispatches
 *  the storage event, and the gate reads the reason inside that same tick. */
function markEnded(reason: "signed-out" | "expired") {
  try {
    window.sessionStorage.setItem(ENDED_KEY, reason);
  } catch {}
}

/** A 401 that arrived ON the stored access token: the server refused THIS
 *  token, which the proactive refresh in accessTokenResult() cannot see — the token
 *  still looked fresh when the request left.
 *
 *  A refusal of the access token is not a verdict on the session while a
 *  refresh token survives, so this DEMOTES rather than ends: it back-dates the
 *  expiry, and the next accessTokenResult() call renews. Ending it here instead cost
 *  a full sign-in every token lifetime on any IdP that omits `expires_in` —
 *  routes/auth.py stores 0 for that, accessTokenResult() reads 0 as "no proactive
 *  refresh", and the first 401 is therefore where such a session ALWAYS lands.
 *  The session ends only when the renewal itself is refused (refreshOnce). */
export async function sessionRejected(token: string) {
  await withSessionLock(() => {
    const t = readStored();
    // several in-flight requests can 401 together, and a refresh may have
    // already replaced the token they carried — only the CURRENT token counts
    if (!t || t.access_token !== token) return;
    if (t.refresh_token) {
      // any past instant works; Date.now() keeps it one comparison away from
      // the EXPIRY_MARGIN_MS test that reads it
      if (!commitStored(t.generation ?? "", { ...t, expires_at: Date.now() - 1 }))
        endSession("expired");
      return;
    }
    endSession("expired");
  });
}

/** Who is signed in, or "" — for display only. What the API trusts is the
 *  token's own claims, checked server-side on every request. */
export function signedInUser(): string {
  if (typeof window === "undefined") return "";
  return readStored()?.user ?? "";
}

export function isSignedIn(): boolean {
  return Boolean(signedInUser());
}

let configCache: Promise<AuthConfig> | null = null;

/** The deployment's auth mode and public sign-in parameters. Cached: it is
 *  read on nav mount and cannot change without a server restart.
 *
 *  A FAILED read is not cached. The mode cannot change without a restart, but
 *  a fetch that never reached the server proves nothing about it — and holding
 *  "unknown" for the life of the page hides the Sign in button entirely, which
 *  in oidc mode leaves no way to establish identity until a full reload. */
export function authConfig(): Promise<AuthConfig> {
  if (!configCache) {
    const attempt = fetch(`${API_URL}/api/auth/config`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .catch((e) => {
        if (configCache === attempt) configCache = null; // let the next mount retry
        return { mode: "unknown", error: String(e) };
      });
    configCache = attempt;
  }
  return configCache;
}

function randomString(bytes: number): string {
  const raw = new Uint8Array(bytes);
  crypto.getRandomValues(raw);
  return base64url(raw);
}

function base64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function challenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

export function redirectUri(): string {
  return `${window.location.origin}/auth/callback`;
}

/** Start a sign-in. Returns only if it could not start; otherwise the browser
 *  leaves for the identity provider. */
export async function signIn(returnTo?: string): Promise<string> {
  const cfg = await authConfig();
  if (cfg.mode === "unknown") {
    // a failed CONFIG READ proves nothing about the deployment. "Does not
    // use sign-in" here turned a network fault into a configuration verdict
    // — served to a user mid-flow, on the callback page's retry button,
    // where the cache is empty and the API being down is the likely cause.
    return "Cannot read the sign-in configuration. Check that the server is running, then start the sign-in again.";
  }
  if (cfg.mode !== "oidc") return "This deployment does not use sign-in.";
  if (cfg.error || !cfg.authorize_url || !cfg.client_id) {
    return cfg.error || "Sign-in is not configured. Ask whoever runs the server.";
  }
  if (!navigator.locks)
    return "This browser cannot coordinate sign-in across tabs. Use a current browser. Then start the sign-in again.";
  // S256 only. The "plain" method sends the verifier itself, which gives an
  // interceptor exactly what PKCE exists to withhold.
  const verifier = randomString(32);
  const state = randomString(16);
  const generation = randomString(16);
  const codeChallenge = await challenge(verifier);
  try {
    window.sessionStorage.setItem(
      FLOW_KEY,
      JSON.stringify({
        verifier,
        state,
        generation,
        returnTo: returnTo || window.location.pathname,
      }),
    );
  } catch {
    return "This browser blocks the storage that sign-in needs. Allow Skein to use browser storage. Then start the sign-in again.";
  }
  const generationSet = await withSessionLock(() => setGeneration(generation));
  if (!generationSet) {
    window.sessionStorage.removeItem(FLOW_KEY);
    return "This browser cannot coordinate sign-in across tabs. Use a current browser. Then start the sign-in again.";
  }
  // No nonce. A nonce binds an ID TOKEN to this request, and this flow never
  // consumes one — the API validates the access token and answers with a name.
  // Sending one nothing checks reads as a guarantee that is not in force. If an
  // ID token is ever consumed, the nonce comes back WITH the check that reads it.
  const params = new URLSearchParams({
    response_type: "code",
    client_id: cfg.client_id,
    redirect_uri: redirectUri(),
    scope: cfg.scopes || "openid profile",
    state,
    code_challenge: codeChallenge,
    code_challenge_method: "S256",
  });
  window.location.assign(`${cfg.authorize_url}?${params}`);
  return "";
}

/** A path inside this app, or "/".
 *
 *  Anything handed to location.replace() after a sign-in sits in the classic
 *  open-redirect position, so this asks the SAME parser the navigation will
 *  use and compares origins. Inspecting the string instead is what fails:
 *  "//evil.com" is a path to a prefix check and an origin to the browser, and
 *  the URL parser strips tab, newline and carriage return BEFORE resolving —
 *  so "/<tab>/evil.com" passes any check that runs first and still lands on
 *  evil.com. One parser, one answer, no gap to slip through. */
function localPath(returnTo: string): string {
  try {
    const here = window.location.origin;
    const url = new URL(returnTo, here);
    if (url.origin !== here) return "/";
    return url.pathname + url.search + url.hash;
  } catch {
    return "/";
  }
}

// One exchange per callback URL. React StrictMode runs an effect twice in
// development, and completeSignIn takes the flow state on its first line —
// the second run would find it gone and report a failed sign-in that in fact
// succeeded. Joining the first attempt makes both runs see one outcome.
let completing: { search: string; result: Promise<string> } | null = null;

/** Finish a sign-in from the callback URL. Returns where to go next, or
 *  throws with a message worth showing. */
export function completeSignIn(search: string): Promise<string> {
  if (completing && completing.search === search) return completing.result;
  const result = runCompleteSignIn(search);
  completing = { search, result };
  // a failure is not cached: Try again re-runs it, and the flow state it
  // needed is already gone, so it fails the same way rather than hanging
  result.catch(() => {
    if (completing?.result === result) completing = null;
  });
  return result;
}

async function runCompleteSignIn(search: string): Promise<string> {
  const params = new URLSearchParams(search);
  const idpError = params.get("error");
  if (idpError) {
    // The query value comes from the provider redirect and is never safe to
    // reflect into the browser-visible refusal.
    throw new Error(
      "The identity provider refused the sign-in. Start the sign-in again.",
    );
  }
  const code = params.get("code");
  const state = params.get("state");
  let flow: {
    verifier: string;
    state: string;
    generation: string;
    returnTo: string;
  } | null = null;
  try {
    flow = JSON.parse(window.sessionStorage.getItem(FLOW_KEY) || "null");
  } catch {}
  window.sessionStorage.removeItem(FLOW_KEY); // one code, one attempt
  if (!code || !flow?.generation) {
    throw new Error("This sign-in link is no longer valid. Start the sign-in again.");
  }
  // The state check is what makes a link someone else crafted useless: it can
  // carry a code, but not the random value this tab generated.
  if (!state || state !== flow.state) {
    throw new Error("This sign-in did not start in this tab. Start the sign-in again.");
  }
  const stored = await exchange({
    code,
    code_verifier: flow.verifier,
    redirect_uri: redirectUri(),
  });
  const committed = await withSessionLock(() =>
    commitStored(flow.generation, stored),
  );
  if (!committed)
    throw new Error("Another identity change replaced this sign-in. Start the sign-in again.");
  return localPath(flow.returnTo || "/");
}

/** A sign-in failure the session cannot survive: the provider judged the
 *  credential and said no. Anything else — the network, a 5xx, a server that
 *  is restarting — leaves the stored session alone, because throwing it away
 *  turns a moment of bad Wi-Fi into a full re-authentication. */
class Rejected extends Error {}

async function exchange(body: Record<string, string>): Promise<Stored> {
  const res = await fetch(`${API_URL}/api/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let payload: {
    access_token?: string;
    refresh_token?: string;
    expires_in?: number;
    user?: string;
    detail?: string;
  } = {};
  try {
    payload = await res.json();
  } catch {}
  if (!res.ok || !payload.access_token) {
    const message = payload.detail || `Sign-in failed (HTTP ${res.status}).`;
    // A 4xx response except 429 is the provider's verdict on this credential.
    // After Retry-After, the same request can succeed without changes.
    const rejected = res.status >= 400 && res.status < 500 && res.status !== 429;
    throw rejected ? new Rejected(message) : new Error(message);
  }
  return {
    access_token: payload.access_token,
    refresh_token: payload.refresh_token || "",
    expires_at: payload.expires_in ? Date.now() + payload.expires_in * 1000 : 0,
    user: payload.user || "",
  };
}

type AccessTokenResult = { token: string; canFallback: boolean };

const BLOCK_FALLBACK: AccessTokenResult = { token: "", canFallback: false };
const ALLOW_FALLBACK: AccessTokenResult = { token: "", canFallback: true };
const tokenResult = (token: string): AccessTokenResult => ({ token, canFallback: false });

// one refresh at a time: a page that fires six requests at once must not send
// six refreshes, which some providers answer by revoking the whole chain
let refreshing: Promise<AccessTokenResult> | null = null;
const EXPIRY_MARGIN_MS = 60_000;

/** This function resolves the OIDC rung and keeps each request bound to one
 *  identity. Fallback is permitted only when no OIDC session exists or the
 *  provider rejects that session. A temporary fault or identity change blocks
 *  fallback. */
export async function accessTokenResult(): Promise<AccessTokenResult> {
  if (typeof window === "undefined") return ALLOW_FALLBACK;
  const t = readStored();
  if (!t) return ALLOW_FALLBACK;
  if (!t.expires_at || t.expires_at - Date.now() > EXPIRY_MARGIN_MS)
    return tokenResult(t.access_token);
  if (!t.refresh_token) {
    const ended = await withSessionLock(() => {
      if (!sameSession(readStored(), t)) return BLOCK_FALLBACK;
      endSession("expired");
      return ALLOW_FALLBACK;
    });
    return ended ?? BLOCK_FALLBACK;
  }
  if (!refreshing) {
    refreshing = withRefreshLock(() => refreshOnce(t))
      .then((result) => result ?? BLOCK_FALLBACK)
      .catch(() => BLOCK_FALLBACK)
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

function sameSession(left: Stored | null, right: Stored): boolean {
  return (
    left?.access_token === right.access_token &&
    left.refresh_token === right.refresh_token &&
    left.expires_at === right.expires_at &&
    left.user === right.user &&
    (left.generation ?? "") === (right.generation ?? "")
  );
}

async function refreshOnce(started: Stored): Promise<AccessTokenResult> {
  // re-read INSIDE the lock: while this tab waited, the other one may have
  // finished and stored a token that is good for another hour
  const now = readStored();
  if (!now || now.user !== started.user) return BLOCK_FALLBACK;
  const renewed = now.expires_at && now.expires_at - Date.now() > EXPIRY_MARGIN_MS;
  if (now.refresh_token !== started.refresh_token || renewed)
    return tokenResult(now.access_token);
  try {
    const fresh = await exchange({ refresh_token: now.refresh_token });
    const committed = await withSessionLock(() => {
      if (!sameSession(readStored(), now)) return BLOCK_FALLBACK;
      // RFC 6749 §6 permits a provider to omit refresh_token when it does not
      // rotate tokens. Several providers do this. If the response omits it,
      // this code keeps the current refresh token.
      const stored = {
        ...fresh,
        refresh_token: fresh.refresh_token || now.refresh_token,
      };
      return commitStored(now.generation ?? "", stored)
        ? tokenResult(fresh.access_token)
        : BLOCK_FALLBACK;
    });
    return committed ?? BLOCK_FALLBACK;
  } catch (err) {
    // Only a verdict ends the same session. A temporary fault or another tab's
    // identity change blocks fallback and keeps the current session unchanged.
    if (err instanceof Rejected) {
      const rejected = await withSessionLock(() => {
        if (!sameSession(readStored(), now)) return BLOCK_FALLBACK;
        endSession("expired");
        return ALLOW_FALLBACK;
      });
      return rejected ?? BLOCK_FALLBACK;
    }
    return BLOCK_FALLBACK;
  }
}

/** The stored token without refreshing it. For the one caller that cannot
 *  await — the pagehide save in lib/theme.ts, where the tab is closing and an
 *  async refresh would never land. */
export function accessTokenSync(): string {
  if (typeof window === "undefined") return "";
  return readStored()?.access_token ?? "";
}

export async function signOut() {
  const previousUser = readStored()?.user ?? "";
  markEnded("signed-out");
  // Invalidate the generation first. Then an in-progress refresh fails its
  // post-write check before this short lock clears the stored session.
  setGeneration(randomString(16));
  const ended = await withSessionLock(() => {
    // A refresh commit can clear the reason while sign-out waits for this lock.
    markEnded("signed-out");
    writeStored(null, previousUser);
    return true;
  });
  if (!ended) writeStored(null, previousUser);
}

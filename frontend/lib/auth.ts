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
// the in-flight authorization: per tab, and gone when the tab closes. A second
// tab must not be able to complete a sign-in this one started.
const FLOW_KEY = "skein-oidc-flow";

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
};

function readStored(): Stored | null {
  try {
    const raw = window.localStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const t = JSON.parse(raw);
    if (typeof t?.access_token === "string" && t.access_token) return t as Stored;
  } catch {}
  return null;
}

function writeStored(t: Stored | null) {
  try {
    if (t) window.localStorage.setItem(TOKEN_KEY, JSON.stringify(t));
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {}
  // same-tab subscribers (nav, settings) listen for this, as everywhere else
  window.dispatchEvent(new Event("storage"));
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
 *  read on nav mount and cannot change without a server restart. */
export function authConfig(): Promise<AuthConfig> {
  if (!configCache) {
    configCache = fetch(`${API_URL}/api/auth/config`)
      .then((r) => (r.ok ? r.json() : { mode: "unknown", error: `HTTP ${r.status}` }))
      .catch((e) => ({ mode: "unknown", error: String(e) }));
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
  if (cfg.mode !== "oidc") return "This deployment does not use sign-in.";
  if (cfg.error || !cfg.authorize_url || !cfg.client_id) {
    return cfg.error || "Sign-in is not configured. Ask whoever runs the server.";
  }
  // S256 only. The "plain" method sends the verifier itself, which gives an
  // interceptor exactly what PKCE exists to withhold.
  const verifier = randomString(32);
  const state = randomString(16);
  const nonce = randomString(16);
  try {
    window.sessionStorage.setItem(
      FLOW_KEY,
      JSON.stringify({ verifier, state, nonce, returnTo: returnTo || window.location.pathname }),
    );
  } catch {
    return "This browser blocks session storage, which sign-in needs.";
  }
  const params = new URLSearchParams({
    response_type: "code",
    client_id: cfg.client_id,
    redirect_uri: redirectUri(),
    scope: cfg.scopes || "openid profile",
    state,
    nonce,
    code_challenge: await challenge(verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(`${cfg.authorize_url}?${params}`);
  return "";
}

/** Finish a sign-in from the callback URL. Returns where to go next, or
 *  throws with a message worth showing. */
export async function completeSignIn(search: string): Promise<string> {
  const params = new URLSearchParams(search);
  const idpError = params.get("error");
  if (idpError) {
    // the provider's own refusal, e.g. access_denied
    throw new Error(`The identity provider refused the sign-in (${idpError}).`);
  }
  const code = params.get("code");
  const state = params.get("state");
  let flow: { verifier: string; state: string; returnTo: string } | null = null;
  try {
    flow = JSON.parse(window.sessionStorage.getItem(FLOW_KEY) || "null");
  } catch {}
  window.sessionStorage.removeItem(FLOW_KEY); // one code, one attempt
  if (!code || !flow) {
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
  writeStored(stored);
  return flow.returnTo || "/";
}

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
    throw new Error(payload.detail || `Sign-in failed (HTTP ${res.status}).`);
  }
  return {
    access_token: payload.access_token,
    refresh_token: payload.refresh_token || "",
    expires_at: payload.expires_in ? Date.now() + payload.expires_in * 1000 : 0,
    user: payload.user || "",
  };
}

// one refresh at a time: a page that fires six requests at once must not send
// six refreshes, which some providers answer by revoking the whole chain
let refreshing: Promise<string> | null = null;
const EXPIRY_MARGIN_MS = 60_000;

/** A usable access token, refreshed if it is about to expire. "" when nobody
 *  is signed in or the session cannot be renewed. */
export async function accessToken(): Promise<string> {
  if (typeof window === "undefined") return "";
  const t = readStored();
  if (!t) return "";
  if (!t.expires_at || t.expires_at - Date.now() > EXPIRY_MARGIN_MS) return t.access_token;
  if (!t.refresh_token) {
    // expired with nothing to renew from: the session is over, and holding a
    // dead token would show a signed-in UI that 401s on every request
    writeStored(null);
    return "";
  }
  if (!refreshing) {
    refreshing = exchange({ refresh_token: t.refresh_token })
      .then((fresh) => {
        writeStored(fresh);
        return fresh.access_token;
      })
      .catch(() => {
        writeStored(null);
        return "";
      })
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

/** The stored token without refreshing it. For the one caller that cannot
 *  await — the pagehide save in lib/theme.ts, where the tab is closing and an
 *  async refresh would never land. */
export function accessTokenSync(): string {
  if (typeof window === "undefined") return "";
  return readStored()?.access_token ?? "";
}

export function signOut() {
  writeStored(null);
}

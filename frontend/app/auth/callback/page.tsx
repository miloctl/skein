"use client";

import { useEffect, useState } from "react";

import { completeSignIn, signIn } from "@/lib/auth";
import { setUser } from "@/lib/api";
import { signedInUser } from "@/lib/auth";

/** Where the identity provider sends the browser back to.
 *
 *  Reads window.location directly rather than useSearchParams: the whole page
 *  is client-only and one-shot, and useSearchParams would put the route behind
 *  a Suspense boundary for a value that is never prerendered anyway.
 */
export default function AuthCallback() {
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    completeSignIn(window.location.search)
      .then((returnTo) => {
        if (cancelled) return;
        // the rest of the app reads identity from here, so a signed-in person
        // sees their own name everywhere without a second source of truth
        setUser(signedInUser() || "anonymous");
        // replace(): the callback URL carries a spent code, and it must not
        // come back on the Back button
        window.location.replace(returnTo || "/");
      })
      .catch((e) => !cancelled && setError(String(e.message || e)));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main id="content" className="mx-auto w-full max-w-md px-6 py-24">
      {error ? (
        <div className="rounded-xl border border-line bg-card p-6">
          <h1 className="mb-2 font-display text-lg font-semibold text-ink">
            Sign-in did not finish
          </h1>
          <p className="mb-4 text-sm text-ink-2">{error}</p>
          <button
            onClick={() => signIn("/").then((m) => m && setError(m))}
            className="rounded-lg bg-thread-solid px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
          >
            Try again
          </button>
        </div>
      ) : (
        <p role="status" className="text-sm text-ink-3">
          Signing you in…
        </p>
      )}
    </main>
  );
}

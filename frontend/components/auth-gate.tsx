"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { CSSProperties } from "react";

import { getApiKey, subscribeUser } from "@/lib/api";
import { authConfig, isSignedIn, sessionEnd, signIn } from "@/lib/auth";
import { setGated } from "@/lib/gated";
import { signedOutLine } from "@/lib/whimsy";

/** Segment endpoints along the centerline of SkeinMark's band, on the same
 *  32-unit grid — the formation the strands settle into is the mark the nav
 *  shows once inside (components/mark.tsx).
 *
 *  STROKES at fractional coordinates, which mark.tsx refuses for itself: a
 *  stroke that lands off-grid rasterizes as a half-alpha smear, and the mark
 *  renders at 16px where that is visible. This draws at 96px, where a
 *  half-unit is three-tenths of a pixel. Shrink Formation toward nav size and
 *  the strands must become the filled path instead.
 *
 *  `from` is each strand's scatter position, consumed by the strand-settle
 *  keyframe in globals.css; CSS px on an SVG child are user units, so these
 *  distances scale with the render size. */
const STRANDS: Array<{ line: [number, number, number, number]; from: string }> = [
  { line: [5, 12, 8.67, 14.67], from: "translate(-14px, -9px) rotate(-40deg)" },
  { line: [8.67, 14.67, 12.33, 17.33], from: "translate(-9px, 12px) rotate(24deg)" },
  { line: [12.33, 17.33, 16, 20], from: "translate(-3px, -14px) rotate(52deg)" },
  { line: [16, 20, 19.67, 17.33], from: "translate(4px, 13px) rotate(-28deg)" },
  { line: [19.67, 17.33, 23.33, 14.67], from: "translate(10px, -11px) rotate(34deg)" },
  { line: [23.33, 14.67, 27, 12], from: "translate(15px, 8px) rotate(-46deg)" },
];

function Formation({ animate }: { animate: boolean }) {
  return (
    // overflow-visible: the scatter positions sit outside the viewBox, and a
    // clipped strand would pop in at the edge instead of flying in
    <svg
      width={96}
      height={96}
      viewBox="0 0 32 32"
      aria-hidden
      focusable="false"
      className="shrink-0 overflow-visible text-thread"
    >
      {STRANDS.map((s, i) => (
        <line
          key={i}
          x1={s.line[0]}
          y1={s.line[1]}
          x2={s.line[2]}
          y2={s.line[3]}
          stroke="currentColor"
          strokeWidth={6.4}
          strokeLinecap="round"
          className={animate ? "gate-strand" : undefined}
          style={
            animate
              ? ({ "--from": s.from, animationDelay: `${i * 70}ms` } as CSSProperties)
              : undefined
          }
        />
      ))}
    </svg>
  );
}

/** The identity gate for locked deployments. In oidc mode every request needs
 *  a credential — reads included — so a signed-out visitor otherwise lands on
 *  a page of dead panels, each printing the backend's 401 detail, with the
 *  remedy buried in the nav menu. This renders the one remedy instead.
 *
 *  It renders in PLACE of the page rather than over it, so the page cannot
 *  keep fanning out fetches that can only 401. The one exception is the first
 *  paint: the deployment's mode is not known until GET /api/auth/config
 *  answers, and holding every load hostage to that fetch would tax the
 *  default trusted-header deployment for a mode it is not in. A signed-out
 *  oidc visitor therefore sees one skeleton paint, then the gate.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  // "" until the config answers. "unknown" (an unreadable config) renders the
  // page too — a network fault proves nothing about the deployment, which is
  // the same verdict lib/auth.ts::signIn makes for the same reason.
  const [mode, setMode] = useState("");
  useEffect(() => {
    authConfig().then((c) => setMode(c.mode));
  }, []);
  const signedIn = useSyncExternalStore(subscribeUser, isSignedIn, () => false);
  const hasKey = useSyncExternalStore(
    subscribeUser,
    () => Boolean(getApiKey()),
    () => false,
  );
  const ended = useSyncExternalStore(subscribeUser, sessionEnd, () => "");
  const pathname = usePathname();
  const [fault, setFault] = useState("");
  const panel = useRef<HTMLElement>(null);

  // /auth/callback completes the sign-in this gate starts, and Settings is
  // where a personal key gets pasted — gating either locks the door shut.
  // The trailing slash keeps a future /authority page out of the exemption.
  const exempt = pathname.startsWith("/auth/") || pathname.startsWith("/settings");
  // A personal key satisfies every mode (routes/deps.py resolves it first),
  // so a key holder is never gated.
  const locked =
    (mode === "oidc" && !signedIn && !hasKey) || (mode === "api-key" && !hasKey);
  const gating = locked && !exempt;

  // The nav and the two overlays are siblings of this component, not children,
  // so they keep rendering while the gate stands. lib/gated.ts is how they
  // learn to stand down — see the reasoning there.
  useEffect(() => {
    setGated(gating);
    return () => setGated(false);
  }, [gating]);

  // Focus the panel, never the button: the button is LAST in the reading
  // order, so focusing it skips the one sentence that says why the workspace
  // was replaced — which on an expired session is the whole message. Landing
  // on the panel makes a screen reader read the heading and the explanation,
  // then reach the control. This is also the only announcement the swap gets:
  // the gate is client state inside one component, so Next's route announcer
  // (which fires on a router tree change) never sees it.
  useEffect(() => {
    if (gating) panel.current?.focus();
  }, [gating]);

  if (!gating) return <>{children}</>;

  // signIn resolves to a message only when it could not start — an
  // unconfigured deployment, or a config it could not read. Never a success
  // path: on success the browser has already left for the identity provider.
  const start = () => {
    // cleared first: an identical second fault leaves the alert node
    // untouched, so nothing is announced and the click reads as ignored
    setFault("");
    signIn(pathname).then((m) => m && setFault(m));
  };
  // the assembly plays for a first arrival only: a person who signed out or
  // was expired has seen the formation, and replaying it upstages the message
  const animate = ended === "";
  const rise = animate ? "gate-rise " : "";

  return (
    // overflow-y-auto with the centering on an INNER wrapper: justify-center
    // on a scroll container pushes overflow out of both ends, and the top of
    // it cannot be reached by scrolling. At 400% zoom (WCAG 1.4.10 reflow,
    // 320x256) the sign-in fault was the part that fell off the bottom.
    <main
      id="content"
      ref={panel}
      tabIndex={-1}
      className="fixed inset-0 z-30 flex overflow-y-auto bg-page px-6 py-8"
      style={{ backgroundImage: "var(--fabric-texture)" }}
    >
      {/* m-auto on a FLEX child, not justify-center on the container: auto
          margins keep the top edge reachable once the content overflows,
          which is what centering alone loses. */}
      <div className="m-auto flex flex-col items-center text-center">
        {mode === "api-key" ? (
          <>
            <Formation animate={false} />
            <h1 className="mt-5 font-display text-3xl font-semibold tracking-tight text-ink">
              Skein
            </h1>
            {/* the sentences are routes/deps.py::NEED_KEY, less the env-var
                prefix and the Authorization clause a browser cannot use.
                __tests__/one-wording.test.ts reads that constant and fails
                here if the two drift. */}
            <p className="mt-4 max-w-sm text-sm text-ink-2">
              Every request needs a personal API key. Get your first one from
              whoever runs the server (
              <code className="font-mono text-[0.85em]">
                python -m app.bootstrap_key &lt;you&gt;
              </code>
              ). Then paste it in Settings, step 2.
            </p>
            <Link
              href="/settings"
              className="mt-6 rounded-lg bg-thread-solid px-5 py-2 text-sm font-medium text-white hover:opacity-90"
            >
              Open Settings
            </Link>
          </>
        ) : ended === "expired" ? (
          <>
            <Formation animate={false} />
            <h1 className="mt-5 font-display text-2xl font-semibold tracking-tight text-ink">
              Your sign-in expired
            </h1>
            <p className="mt-2 text-sm text-ink-2">Sign in again to continue.</p>
            <button
              onClick={start}
              className="mt-6 rounded-lg bg-thread-solid px-5 py-2 text-sm font-medium text-white hover:opacity-90"
            >
              Sign in
            </button>
          </>
        ) : (
          <>
            <Formation animate={animate} />
            <h1
              className={`${rise}mt-6 font-display text-4xl font-semibold tracking-tight text-ink`}
            >
              Skein
            </h1>
            <p className={`${rise}mt-2 text-sm text-ink-3`}>
              many strands, one formation
            </p>
            <button
              onClick={start}
              className={`${rise}mt-8 rounded-lg bg-thread-solid px-5 py-2 text-sm font-medium text-white hover:opacity-90`}
            >
              Sign in
            </button>
            <p className={`${rise}mt-3 text-xs text-ink-3`}>
              {ended === "signed-out"
                ? signedOutLine()
                : "Sign in to open the workspace."}
            </p>
          </>
        )}
        {fault && (
          <p role="alert" className="mt-6 max-w-sm text-sm text-danger">
            {fault}
          </p>
        )}
      </div>
    </main>
  );
}

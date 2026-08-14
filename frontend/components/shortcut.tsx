import { Fragment } from "react";

/** The search shortcut, spelled for the reader's keyboard.
 *
 *  `⌘K` is the token this product writes everywhere it names the shortcut —
 *  in JSX, in `fieldguide/knots.yaml`, and in the onboarding hints the
 *  backend sends. It is not what every reader presses. The binding is
 *  `metaKey || ctrlKey` (nav-search.tsx), which is ⌘K on an Apple keyboard
 *  and Ctrl+K on the rest, and a Windows reader who read ⌘ as the Windows
 *  key reported the feature as unusable — Win+K opens the Cast panel there,
 *  so the one hint meant to teach the shortcut named a key that does
 *  something else.
 *
 *  The key opens SEARCH. It opened quick capture until 2026-08-14: beside a
 *  search box, ⌘K reads as the command-palette convention it now is, and
 *  capture WRITES a row. Capture is reached by its own button.
 *
 *  Both spellings render and globals.css drops the wrong one, so this is
 *  safe in server-rendered markup.
 *
 *  Text that reaches CHAT must NOT carry the token. Chat renders through
 *  MarkdownTextPrimitive (thread.tsx), which never sees this component, so a
 *  token there ships raw to the reader. The backend strings on that path name
 *  the action instead of the key.
 */
export function Shortcut() {
  return (
    <>
      <span className="os-mac">⌘K</span>
      <span className="os-other">Ctrl+K</span>
    </>
  );
}

/** The token as written on the wire and in YAML. */
const SHORTCUT_TOKEN = "⌘K";

/** Server prose that renders as a plain string — the fieldguide `pitch:` and
 *  `how:` lines and the onboarding hints — with the token swapped for the
 *  reader's keyboard. Text with no token passes through untouched. */
export function ShortcutText({ text }: { text: string }) {
  const parts = String(text ?? "").split(SHORTCUT_TOKEN);
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 ? <Shortcut /> : null}
          {part}
        </Fragment>
      ))}
    </>
  );
}

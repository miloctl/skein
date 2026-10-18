import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useHashTarget } from "@/lib/hash-target";

/** A comment anchor belongs to the thread that renders it
 *  (components/comment-thread.tsx), and a page's own hook must not claim it.
 *  If both claim it, a late collection pulls focus back to the comment after
 *  the reader has moved on. */

function Page({ ready }: { ready: number }) {
  useHashTarget(ready);
  return <li id="comment-9" tabIndex={-1}>a comment</li>;
}

describe("a page's own hash target", () => {
  it("leaves a comment anchor to the thread", () => {
    window.history.replaceState({}, "", "/dashboard?task=12#comment-9");
    const view = render(<Page ready={0} />);
    view.rerender(<Page ready={1} />);
    expect(document.activeElement?.id).not.toBe("comment-9");
  });
});

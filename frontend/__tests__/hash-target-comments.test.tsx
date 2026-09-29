import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useHashTarget } from "@/lib/hash-target";

/** A comment anchor belongs to the thread that renders it
 *  (components/comment-thread.tsx). A page's own hook claimed it too, and a
 *  late collection pulled focus back to the comment after the reader had
 *  moved on. */

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

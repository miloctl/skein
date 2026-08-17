"use client";

import { useEffect, useId, useRef, useState } from "react";

/** Renders a ```mermaid fence as a diagram.
 *
 *  THE ONE dangerouslySetInnerHTML IN THE APP, and why it is allowed here and
 *  nowhere else: mermaid produces an SVG STRING, so there is no way to mount
 *  it as React elements, and `securityLevel: "strict"` is what makes the
 *  string safe — mermaid runs DOMPurify over its own output in that mode,
 *  encodes HTML inside node text, and refuses `click` directives. NEVER
 *  loosen that setting, and never widen this component to render markup from
 *  anywhere else: components/artifact-markdown.tsx builds elements by hand
 *  precisely because an artifact body quotes rows people wrote.
 *
 *  Diagram source is authored text, so a diagram that will not parse shows
 *  its source rather than vanishing — the author has to see it to fix it.
 *
 *  mermaid is about 2 MB, so it loads only when a fence actually appears. */
export function MermaidDiagram({ code }: { code: string }) {
  const [svg, setSvg] = useState("");
  const [failed, setFailed] = useState(false);
  // useId carries colons, which are not legal in the DOM id mermaid assigns
  const id = useId().replace(/:/g, "");
  // The wrapper is rendered on every path, so this ref is attached before the
  // effect runs, and the palette is read off the element rather than off
  // document.documentElement — so a diagram picks up the theme pack and
  // colorway in force WHEN IT RENDERS. It does not repaint on a later theme
  // change: the effect keys on the code, and an already-drawn diagram keeps
  // its colors until the thread remounts. Deliberate — a theme subscription
  // for a redraw nobody waits on is not worth the wiring.
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const styles = host.current ? getComputedStyle(host.current) : null;
    const read = (name: string, fallback: string) =>
      styles?.getPropertyValue(name).trim() || fallback;

    import("mermaid")
      .then(async ({ default: mermaid }) => {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          // an HTML label is a foreignObject holding real markup, which is the
          // one thing the sanitizer above should never have to judge
          htmlLabels: false,
          theme: "base",
          themeVariables: {
            background: read("--surface-card", "#ffffff"),
            primaryColor: read("--surface-raised", "#f3f1ec"),
            primaryTextColor: read("--text-1", "#1d1a16"),
            primaryBorderColor: read("--border-strong", "#d6d0c4"),
            lineColor: read("--text-3", "#736c5e"),
            secondaryColor: read("--surface-page", "#faf9f6"),
            tertiaryColor: read("--surface-card", "#ffffff"),
          },
        });
        const out = await mermaid.render(`m${id}`, code);
        if (!cancelled) {
          setSvg(out.svg);
          // CLEARED, not just set: in chat this component sees every
          // half-written prefix of a streaming fence, and mermaid rejects
          // almost all of them. Left sticky, the first rejected prefix pinned
          // the fallback and the finished diagram never replaced it — every
          // streamed diagram stayed source forever.
          setFailed(false);
        }
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [code, id]);

  return (
    <div ref={host} className="my-3">
      {failed ? (
        <pre className="overflow-x-auto rounded-lg bg-raised p-3 text-xs">
          <code>{code}</code>
        </pre>
      ) : (
        <>
          <div
            className="overflow-x-auto"
            aria-hidden="true"
            // The comment at the top of this file is the reason this is here.
            // Read it before copying this line anywhere else.
            dangerouslySetInnerHTML={{ __html: svg }}
          />
          {/* The source, not a label. A rendered diagram's node text is the
              content, and role="img" with one generic label would hide all of
              it — the source at least reads as the shape it draws. */}
          <pre className="sr-only">{code}</pre>
        </>
      )}
    </div>
  );
}

/** Renders the markdown OUR OWN generators write — the daily digest, the week
 *  rituals, the exec readout, handoff packages, context packs.
 *
 *  Deliberately not a markdown library. The chat's `MarkdownTextPrimitive`
 *  reads its text from assistant-ui's message-part context and takes no text
 *  prop, so it cannot render a string; pulling in a parser instead would add a
 *  dependency to render a handful of constructs. Those are the whole grammar
 *  the generators emit — `#`/`##`/`###` headings, `- ` bullets nested one
 *  level, blank-line paragraphs, and inline `**bold**` and `code` — against
 *  services/{digest,readout,rituals,handoff,context_pack}.py. Anything else in
 *  the file renders as its own literal text rather than vanishing, so a
 *  generator that grows a table shows a reader raw pipes instead of nothing.
 *
 *  It builds React elements and never touches dangerouslySetInnerHTML: an
 *  artifact body is assembled from rows people wrote (task titles, decision
 *  text, promise wording), so treating it as HTML would make every generator
 *  an injection sink. Same reason nav-search parses FTS5's <b> into runs. */

/** Splits on `**bold**` and `` `code` `` in one pass. A capturing split
 *  alternates plain, marked, plain… so the run's KIND is its index parity —
 *  no second scan that could disagree with the first. An unclosed marker
 *  matches nothing and stays literal, which is what a reader should see. */
function inline(text: string, key: string) {
  const parts = text.split(/\*\*([\s\S]+?)\*\*|`([^`]+?)`/g);
  return parts.map((part, i) => {
    if (part === undefined || part === "") return null;
    // split with two capture groups emits [plain, bold, code, plain, …]
    const kind = i % 3;
    if (kind === 1)
      return (
        <strong key={`${key}-${i}`} className="font-semibold text-ink">
          {part}
        </strong>
      );
    if (kind === 2)
      return (
        <code
          key={`${key}-${i}`}
          className="rounded bg-raised px-1 py-0.5 font-mono text-[0.9em]"
        >
          {part}
        </code>
      );
    return <span key={`${key}-${i}`}>{part}</span>;
  });
}

type Item = { text: string; sub: string[] };

type Block =
  | { kind: "h"; level: 1 | 2 | 3; text: string }
  | { kind: "ul"; items: Item[] }
  | { kind: "p"; text: string };

function parse(markdown: string): Block[] {
  const blocks: Block[] = [];
  // \r\n as well as \n: an artifact can be restored from a Windows-authored
  // backup, and a trailing \r turns every heading match into a paragraph.
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) blocks.push({ kind: "p", text: para.join(" ") });
    para = [];
  };

  for (const line of lines) {
    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    // the indent is captured, not skipped: readout.py indents an engagement's
    // receipts two spaces under their engagement, and flattening those made
    // three receipts read as three more engagements
    const bullet = /^(\s*)[-*]\s+(.*)$/.exec(line);
    if (heading) {
      flushPara();
      blocks.push({
        kind: "h",
        level: heading[1].length as 1 | 2 | 3,
        text: heading[2],
      });
    } else if (bullet) {
      flushPara();
      const indented = bullet[1].length > 0;
      const last = blocks[blocks.length - 1];
      if (last?.kind === "ul") {
        const parent = last.items[last.items.length - 1];
        // one level only. Our generators nest exactly one deep, and a deeper
        // line lands beside its nearest parent rather than disappearing.
        if (indented && parent) parent.sub.push(bullet[2]);
        else last.items.push({ text: bullet[2], sub: [] });
      } else {
        blocks.push({ kind: "ul", items: [{ text: bullet[2], sub: [] }] });
      }
    } else if (line.trim() === "") {
      flushPara();
    } else {
      para.push(line.trim());
    }
  }
  flushPara();
  return blocks;
}

const HEADING_TAG = { 1: "h2", 2: "h3", 3: "h4" } as const;

const HEADING = {
  1: "mt-0 mb-2 font-display text-[20px]/[1.2] font-semibold text-ink",
  2: "mt-5 mb-1.5 font-display text-[16px]/[1.25] font-semibold text-ink",
  3: "mt-4 mb-1 text-[13px] font-semibold uppercase tracking-wide text-ink-2",
} as const;

export function ArtifactMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="text-sm text-ink-2">
      {parse(markdown).map((b, i) => {
        if (b.kind === "h") {
          // one level down from the tag the source asks for: the page's own
          // <h1> names the report, and a second <h1> inside it would give a
          // screen reader two document titles for one page
          const H = HEADING_TAG[b.level];
          return (
            <H key={i} className={HEADING[b.level]}>
              {inline(b.text, `h${i}`)}
            </H>
          );
        }
        if (b.kind === "ul")
          return (
            <ul key={i} className="my-1.5 ml-5 list-disc space-y-0.5">
              {b.items.map((item, j) => (
                <li key={j}>
                  {inline(item.text, `l${i}-${j}`)}
                  {item.sub.length > 0 ? (
                    <ul className="ml-5 list-[circle] text-ink-3">
                      {item.sub.map((s, k) => (
                        <li key={k}>{inline(s, `l${i}-${j}-${k}`)}</li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ul>
          );
        return (
          <p key={i} className="my-2">
            {inline(b.text, `p${i}`)}
          </p>
        );
      })}
    </div>
  );
}

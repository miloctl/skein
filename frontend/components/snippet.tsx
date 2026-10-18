/** A search snippet wraps each match in <b>: ts_headline for the nav search
 *  (services/search.py) and chat_threads._snippet for chat search. Rendered as
 *  HTML that would be an injection sink for every row anyone wrote, so the
 *  tags are parsed into text runs and the emphasis is applied by React.
 *  Anything that is not a tag stays literal. */
export function Snippet({ text }: { text: string }) {
  // A capturing split alternates plain, marked, plain, marked… so the
  // emphasis is derived from the index rather than carried in a flag the
  // render mutates. An unclosed <b> matches nothing and stays literal text,
  // which is the safe direction to fail. [\s\S] rather than the /s flag:
  // the tsconfig target predates it, and a snippet can carry a newline.
  const parts = String(text ?? "").split(/<b>([\s\S]*?)<\/b>/);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 ? (
          <mark key={i} className="bg-thread/20 font-semibold text-ink">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

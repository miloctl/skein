/** A unified diff from the server (services/documents.py::unified). The `+`
 *  and `-` stay at the start of every line, so color is never the only
 *  signal of what changed. */
export function UnifiedDiff({ diff, label }: { diff: string; label: string }) {
  if (!diff) return null;
  const tone = (line: string) =>
    line.startsWith("@@")
      ? "text-ink-3"
      : line.startsWith("+") && !line.startsWith("+++")
        ? "text-ok"
        : line.startsWith("-") && !line.startsWith("---")
          ? "text-danger"
          : "text-ink-2";
  return (
    <figure className="min-w-0">
      <figcaption className="sr-only">{label}</figcaption>
      <pre className="overflow-x-auto rounded-lg border border-line bg-raised/50 p-3 font-mono text-xs leading-5">
        {diff.split("\n").map((line, index) => (
          <span key={index} className={`block ${tone(line)}`}>
            {line || " "}
          </span>
        ))}
      </pre>
    </figure>
  );
}

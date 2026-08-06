/** The trace of one flock turn, drawn as the diamond it actually ran as:
 *  the asker at the top, the members side by side in the middle, the merge (or
 *  the plain join) at the bottom.
 *
 *  This is a TRACE view, not an org chart. It shows what the chat transcript
 *  cannot: that the members ran at the same time, how long each one took
 *  against the others, and which of them proposed a write. Read the sections
 *  in /chat for what they said.
 *
 *  Hand-rolled SVG on purpose. The layout is fixed (2-4 members, one row), so
 *  a force-directed library would add a dependency and jitter to a picture
 *  that must land in the same place every render. */

type FlockMember = {
  slug: string;
  name: string;
  emoji: string;
  status: string;
  ms: number;
  receipts: number;
  tokens_in: number;
  tokens_out: number;
};

export type FlockTrace = {
  id: number;
  thread_id: string;
  user: string;
  flock: string;
  members: FlockMember[];
  synthesis: { status: string; ms: number; tokens_in: number; tokens_out: number } | null;
  created_at: string;
};

const H = 240;
// wide enough for the longest bench name at 11px ("🪡 Minimal Change Engineer"
// measures ~137) and for the two sub-lines below it. Sized by the CONTENT: at
// 132 the status word pushed every member's sub-label past its own box and
// into the next member's.
const NODE_W = 152;
const NODE_H = 60;
/** Every node keeps NODE_W with a 12-unit gap, so 4 members do not overlap.
 *  MAX_MEMBERS is 4 (backend/app/services/flocks.py), and at the old fixed
 *  560 the four boxes crossed each other by 20 units. */
const width = (members: number) => Math.max(560, (members + 1) * (NODE_W + 12));

/** Status is spelled out next to every node. Colour alone is not a signal a
 *  reader can rely on: two colorways render `--thread-solid` and `--danger`
 *  close enough to be indistinguishable on a hairline stroke, and the theme
 *  gate (scripts/check_theme_contrast.py) proves `--thread-solid` only as a
 *  solid fill under white text, never as a stroke on a raised surface. */
const statusWord = (s: string) =>
  s === "ok" ? "answered" : s === "failed" ? "did not answer" : s === "cancelled" ? "stopped" : s;

/** var() names, NOT Tailwind utility names. `--line-strong` and `--ink-3` are
 *  the utility spellings (`border-line-strong`, `text-ink-3`); the custom
 *  properties are these. globals.css uses `@theme inline`, so the utility
 *  spellings are never emitted into :root — an unresolvable stroke computes to
 *  `none` and the whole diagram loses its lines with no error anywhere. */
const STROKE_EDGE = "var(--border-strong)";
const STATUS_STROKE: Record<string, string> = {
  ok: "var(--thread-solid)",
  failed: "var(--danger)",
  cancelled: "var(--text-3)",
};

function Node({
  x,
  y,
  w,
  label,
  sub,
  stroke,
}: {
  x: number;
  y: number;
  w: number;
  label: string;
  /** one <text> per entry: a single line of "answered · 12406 ms · 0
   *  proposal(s)" is half again wider than the node it sits in */
  sub: string[];
  stroke: string;
}) {
  return (
    <g>
      <rect
        x={x - w / 2}
        y={y - NODE_H / 2}
        width={w}
        height={NODE_H}
        rx="8"
        fill="var(--surface-raised)"
        stroke={stroke}
        strokeWidth="1.5"
      />
      <text x={x} y={y - 10} textAnchor="middle" className="fill-ink text-[11px] font-medium">
        {label}
      </text>
      {sub.map((line, i) => (
        <text
          key={line}
          x={x}
          y={y + 5 + i * 12}
          textAnchor="middle"
          className="fill-ink-3 text-[10px]"
        >
          {line}
        </text>
      ))}
    </g>
  );
}

export function FlockDiamond({ trace }: { trace: FlockTrace }) {
  const members = trace.members;
  const W = width(members.length);
  const topY = 34;
  const midY = H / 2;
  const botY = H - 34;
  const xs = members.map((_, i) => ((i + 1) * W) / (members.length + 1));
  const slowest = Math.max(0, ...members.map((m) => m.ms));
  const answered = members.filter((m) => m.status === "ok").length;

  const synth = trace.synthesis;
  const mergeLabel = !synth
    ? "not merged"
    : synth.status === "ok"
      ? "merged"
      : synth.status === "failed"
        ? "merge failed"
        : "merge stopped";
  const mergeSentence = !synth
    ? "The answers are not merged."
    : synth.status === "ok"
      ? `The flock merged the answers in ${synth.ms} ms.`
      : synth.status === "failed"
        ? "The merge did not run."
        : "The merge did not finish.";

  // everything a reader gets from position or colour, said once in words
  const label =
    `${members.length} members of flock ${trace.flock} ran at the same time. ` +
    `${answered} of ${members.length} answered. ` +
    members
      .map(
        (m) =>
          `${m.name} ${statusWord(m.status)} in ${m.ms} ms` +
          ` and proposed ${m.receipts} write${m.receipts === 1 ? "" : "s"}.`,
      )
      .join(" ") +
    ` ${mergeSentence}`;

  return (
    <figure className="m-0">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        // min-w: without a floor the SVG shrinks to its container instead of
        // scrolling, and on a 360px phone the 11px labels render at under 6px
        className="h-auto w-full min-w-[420px]"
        role="img"
        aria-label={label}
      >
        {/* Chrome does not prune an SVG subtree under role="img", so without
            this every node's text is announced a second time after the label,
            emoji included ("eye", "sheep"). Every other emoji in the app is
            hidden the same way. */}
        <g aria-hidden="true">
          {xs.map((x, i) => (
            <g key={`edge-${members[i].slug}`}>
              <line x1={W / 2} y1={topY + NODE_H / 2} x2={x} y2={midY - NODE_H / 2} stroke={STROKE_EDGE} />
              <line x1={x} y1={midY + NODE_H / 2} x2={W / 2} y2={botY - NODE_H / 2} stroke={STROKE_EDGE} />
            </g>
          ))}
          <Node
            x={W / 2}
            y={topY}
            w={NODE_W}
            label={trace.user}
            sub={["asked"]}
            stroke={STROKE_EDGE}
          />
          {members.map((m, i) => (
            <Node
              key={m.slug}
              x={xs[i]}
              y={midY}
              w={NODE_W}
              label={`${m.emoji} ${m.name}`}
              sub={[statusWord(m.status), `${m.ms} ms · ${m.receipts} proposal(s)`]}
              stroke={STATUS_STROKE[m.status] ?? "var(--text-3)"}
            />
          ))}
          <Node
            x={W / 2}
            y={botY}
            w={NODE_W}
            label={mergeLabel}
            sub={synth ? [`${synth.ms} ms`] : [`${answered} of ${members.length} answered`]}
            stroke={synth && synth.status !== "ok" ? "var(--danger)" : STROKE_EDGE}
          />
        </g>
      </svg>
      <figcaption className="mt-1 text-xs text-ink-3">
        The slowest member took {slowest} ms. The members ran at the same time. The turn took
        approximately {slowest} ms, not the total of all the members.
      </figcaption>
    </figure>
  );
}

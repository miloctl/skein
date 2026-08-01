// The Skein mark: one strand laid as an open V, arms staggered so the lead
// runs longer — the formation, not a bird and not a knot (knots are the field
// guide's vocabulary and are only ever drawn correctly, so the mark leaves
// them alone).
//
// Authored, not traced. Filled path on a 32-unit grid with integer
// coordinates: browsers rasterize SVG strokes without hinting, so a stroke
// landing off-grid at 16px becomes a half-alpha smear. Limb thickness is ~6.3
// units (3.2px at a 16px render) and the included angle is 106° — wide enough
// that it never reads as a checkmark next to /review's approve control.
//
// Paints with currentColor so it inherits --thread through the normal cascade.
// An <img src> or background-image could not: --thread is set as an inline
// style on <html>, and custom properties do not cross into a separate SVG
// document. Keep it inline, keep it free of <defs> and ids — the mark renders
// more than once per page and duplicate ids resolve to the first instance.
export function SkeinMark({ size = 16, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="currentColor"
      aria-hidden
      focusable="false"
      className={className}
    >
      <path d="M5 7 L16 15 L27 7 L27 17 L16 25 L5 17 Z" />
    </svg>
  );
}

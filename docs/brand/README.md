# Skein brand

"Many strands. One formation."

A skein is a coil of yarn and a flock of geese in V-formation. The mark takes
the **strand / V / formation** half. The **yarn / knot** half is already spoken
for: the field guide is knot-named end to end (`backend/fieldguide/knots.yaml`)
and uses 🧶. Keep the split — the mark is not a knot, and knots stay correct.

## The mark

`frontend/components/mark.tsx` — one open V, filled path, 32-unit grid,
integer coordinates:

```
M5 7 L16 15 L27 7 L27 17 L16 25 L5 17 Z
```

| Property | Value | Why |
|---|---|---|
| Limb thickness | 8.1 units (4px at 16px) | floor is 4 units; below that it smears |
| Included angle | 107.9° | ≥100° so it never reads as a checkmark beside /review's approve |
| Margin | 5 units all round | plate needs to read as a plate at 16px |

**Authored, not generated.** Three earlier candidates were rejected by
rendering them at an actual 16px and looking: a sharper apex antialiased to a
rounded bowl, and thinner limbs closed the notch. Test changes the same way —
render at 16 and blow it up nearest-neighbour. Zooming out in a design tool is
how illegible favicons ship.

## Why the mark must be inline SVG

`--thread` is set as an **inline style on `<html>`** (`app/layout.tsx`
pre-paint script, `lib/theme.ts` `customCss`). Custom properties only cascade
within one document, so:

| Technique | Inherits `--thread`? |
|---|---|
| Inline JSX `<svg>` | **yes** |
| `<img src>` / `next/image` | no — separate SVG document |
| `background-image: url()` | no |

So the mark paints `currentColor` and takes its colour from `text-thread`.
`currentColor` also survives `forced-colors: active`, where a background-image
would be stripped (`globals.css` already nulls those).

Two rules that follow:

- **No `<defs>`, no `id`s.** The mark renders more than once per page; duplicate
  ids resolve to the first instance and later copies render wrong,
  intermittently.
- **Never paint it `--thread-solid`.** That token stays dark in dark mode so
  light text can sit on it — painted *on* the page it falls to 2.52:1 under
  graphite. `--thread` holds ≥5.5:1 across every named theme on every pack
  surface, and the 360-hue custom sweep bottoms at 5.56:1 — both enforced by
  `scripts/check_theme_contrast.py` in `lint.sh`, which prints the current
  sweep floor on every run.

## Why the icon is a plate

A favicon sits on browser chrome we do not control, and no single ink clears
4.5:1 both ways — `--thread` light is 7.0:1 on a white tab strip but 1.7:1 on
`#35363a`; the dark value inverts the problem. An opaque plate moves the
contrast inside the icon, where it is fixed at 8.9:1.

`prefers-color-scheme` inside an SVG favicon is not a substitute: Firefox
honours it, Chrome rasterizes in a cached theme-blind context and never
re-rasterizes on a mid-session theme flip, and every surface that discards
alpha (iOS home screen, Android maskable, Windows taskbar) ignores it anyway.

## The wordmark stays live text

`nav.tsx` renders "Skein" as text in `.font-display`, which `globals.css`
re-cuts per pack — Bricolage under loom/contrast, Fraunces `SOFT 0 WONK 0`
under ledger, `SOFT 80 WONK 1` under atelier, Geist Mono uppercase with a glow
under phosphor, Geist tight-tracked under claw, Pixelify Sans uppercase under
hermes. A frozen SVG wordmark would be the one non-parametric piece of type on
screen and would look broken in five of seven packs. The **mark** carries the
fixed identity; the wordmark wears the fabric.

## Restraint is the brand

Three of ~45 empty states carry a joke (`lib/whimsy.ts` is wired to `review`,
`blockers`, `allclear` — the three that mean *you are done*). Of ~25 emoji, all
but three are functional. `app/page.tsx` bans peppiness in code: *"a 👎 must
never trigger anything peppy, and no modal ever"*. `dashboard/page.tsx`:
*"pulse is decorative — its failure must not blank the page"*.

So: the mark ships with **no expressive variant by default**, and no brand
asset may become load-bearing. Nothing waits on an image to render.

## Files

| File | What |
|---|---|
| `frontend/components/mark.tsx` | the mark, inline SVG, `currentColor` |
| `frontend/app/icon.svg` | plate + glyph, browser tab |
| `frontend/app/favicon.ico` | 16/32/48/256, plate |
| `frontend/app/apple-icon.png` | 180×180 opaque, full-bleed (iOS masks it) |
| `frontend/public/icon-{192,512}.png` | manifest |
| `frontend/public/icon-maskable-512.png` | Android adaptive, glyph at 72% |
| `frontend/app/manifest.ts` | install metadata |
| `docs/brand/image-prompts.md` | gpt-image-2 prompts for atmosphere art |
| `docs/brand/gen_banner.py` | authors the README banner (SVG + PNG) |
| `docs/site/img/banner-{light,dark}.{svg,png}` | README banner |
| `docs/site/img/hero-{light,dark}.png` | landing hero (generated) |

Icons are rasterized from the same polygon as `icon.svg`. If the path changes,
re-render all of them — there is no build step tying them together.

Metadata uses **Next file conventions only**. Do not add an `icons` field to
`metadata`: Next already emits the links, and declaring them twice produces
duplicate conflicting `<link rel="icon">` tags.

## The banner is authored, not generated

`docs/brand/gen_banner.py` — twelve strands, one lead, four crossings where
an adjacent pair swaps places.

Three rounds of prompting could not produce an over-under. The model drew
flat X's every time: two lines meeting and continuing, no break in the one
passing behind. That break is the entire point — it is the only thing
separating *strand* from *line*, and without it the image reads as a wiring
diagram. Diffusion models do not reason about topology.

Everything else about the banner is exact and trivially specifiable: strand
count, spacing, band thickness, colour, crop safety. That combination —
exact geometry plus required topology — is the same call as the mark.
Author it.

The depth falloff is not decoration: the site's `.vee` already sets the
house style for a formation (gold lead at 4px, trailing bars at opacity
0.75 and 0.5). The banner continues that over twelve. Opacity is tied to
the strand rather than its slot so it does not flicker across a swap.

Re-run after any edit; SVG is the source of truth and the PNG is what the
README references, since git hosts are inconsistent about rendering SVG
through `<img>`.

## Still open

- `opengraph-image.png` (1200×630) — needs the generated art. Ship it static;
  a dynamic `opengraph-image.tsx` would drag satori into the standalone bundle
  and its font path does not exist in the container (`frontend/Dockerfile`
  copies only `.next/standalone`, `.next/static`, `public`).
  Note the app is LAN-only, so external crawlers cannot fetch it regardless.
- Landing hero — see `image-prompts.md`. Generated, and correctly so:
  atmospheric, organic, thousands of subtle lines, nothing that has to be
  *correct*. That is what the medium is good at.
- Field-guide set glyphs. `app/guide/page.tsx` defines five sets (loops,
  hitches, bends, stoppers, manager) with taglines and no glyphs, and the page
  is already built to receive them. Five correctly-drawn knots, hand-verified
  against real diagrams — the highest-value asset left, and more on-brand than
  a hero image.

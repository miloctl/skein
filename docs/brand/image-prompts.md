# gpt-image-2 prompts — Skein brand atmosphere

Generate **atmosphere only**. The logo mark and every icon are authored as SVG
(`frontend/components/mark.tsx`, `frontend/app/icon.svg`) and composited in
afterward — never generated. See `docs/brand/README.md` for why.

## Size rules

gpt-image-2 accepts arbitrary `WIDTHxHEIGHT` where both dimensions are
divisible by 16, aspect ratio ≤ 3:1, and total pixels are between 655,360 and
8,294,400. Target sizes that violate this are generated larger and cropped.

| Asset | Generate at | Then |
|---|---|---|
| Landing hero | `1600x896` | use as-is |
| README banner | `1920x640` | scale to 1280w, crop to 1280x400 |
| OG card | `1200x640` | crop to 1200x630 |

## Palette — app tokens, not the site's

The docs site historically used a different indigo/gold. Always use these:

| Token | Light | Dark |
|---|---|---|
| paper (`--surface-page`) | `#faf9f6` | `#141311` |
| ink (`--text-1`) | `#1d1a16` | `#ede9e2` |
| thread (`--thread`) | `#3b4dbf` | `#a7b5f2` |
| weld (`--weld`) | `#935a1c` | `#d9a15e` |

## The ban list

Fold into every prompt. The first block is the standard slop; the second is
specific to this product.

```
Do not include: any letters, numbers, words, wordmark, logo, caption,
watermark, or signature; birds, geese, feathers, wings, or any animal; yarn
balls, spools, knitting needles, crochet hooks, or fabric photography; swooshes
or ribbon curves; people or hands; UI panels, screenshots, browser windows,
dashboards, cards, or charts; a border or frame; a mockup or presentation
slide.

Do not include: any knot, tangle, spaghetti loop, or impossible over-under
topology; no decorative "knot-like" form; no infinity loop.
```

The knot ban is not stylistic. `backend/fieldguide/knots.yaml` is a correct
26-knot marlinspike taxonomy, and image models cannot tie knots — they produce
plausible tangles with incoherent topology. A fake knot next to that file is a
fake receipt in a product whose thesis is receipts. Knots belong to the field
guide; the product's visual vocabulary is **strand, V, formation**.

## Craft notes

- Say **"flat printed ink"**, **"screen print"**, **"die-cut"**, or
  **"letterpress"**. Never say "vector" — it summons microstock gradient meshes.
- **Occupy the slot instead of negating it.** "No background" summons a
  background; "one flat even field of `#faf9f6`" does not.
- **Depth from line weight and spacing, never from blur.** State the mechanism
  positively or the exclusion list loses about half the time.
- Generate `n=4` per prompt before rewriting. Sample variance exceeds prompt
  variance.
- Iterate with the **edit endpoint**, not a fresh prompt — re-prompting loses
  the composition you liked.

---

# 1. Landing hero — 1600x896

For `docs/site/index.html`. The site has real light and dark themes; an
atmospheric hero cannot split the difference, so generate both.

## 1-LIGHT

```
A wide, quiet illustration in a restrained technical print style — flat printed
ink on paper, precise and hand-ruled, in the manner of a fine screen print.

Background: one flat, completely even field of warm off-white paper (#faf9f6)
filling the entire frame. No vignette, no gradient, no lighter or darker areas
anywhere, no texture overlay.

Across this field travels a single long formation built entirely from thin
straight lines, all at a consistent shallow 24-degree upward angle. Roughly
thirty lines enter low at the lower left, widely spaced and very thin, and rise
across the frame toward the upper right, where they draw closer together and
grow slightly heavier. In three or four places along the sweep, two or three
adjacent lines converge and run together as a single heavier line for a short
distance, then separate again and continue — strands plying and releasing. The
lines never cross each other; they only converge and part.

Colour: every line is deep indigo blue (#3b4dbf), except exactly three lines
that are burnt amber (#935a1c) — one at the leading tip of the formation and
two more deep inside the pack.

Depth comes from line weight and spacing alone, never from blur: lines lowest
in the frame are the thinnest and most widely spaced; lines highest and
furthest right are the heaviest and most tightly grouped. Line weights run from
hairline to three pixels. Wide areas of completely empty paper surround the
formation, especially in the lower left quarter and along the top edge.

Flat printed ink. No gradients, no glow, no bloom, no lens flare, no blur, no
depth of field, no drop shadow, no 3D, no film grain, no vignette. No horizon,
no landscape, no sky, no clouds, no sun.

Do not include: any letters, numbers, words, wordmark, logo, caption,
watermark, or signature; birds, geese, feathers, wings, or any animal; yarn
balls, spools, knitting needles, crochet hooks, or fabric photography; swooshes
or ribbon curves; people or hands; UI panels, screenshots, dashboards, or
charts; a border or frame; any knot, tangle, spaghetti loop, or impossible
over-under topology.
```

**Going for:** strands travelling and briefly plying — the formation implied,
never drawn.
**Failure mode:** "atmospheric" summons bloom and bokeh, which will read as
AI-generated next to the site's hairline-ruled CSS. Discard rather than edit
any sample with glow. Second failure: it invents a horizon and you get a
landscape with birds.

## 1-DARK

Same prompt with three substitutions:

- Background → `one flat, completely even field of near-black warm charcoal (#141311)`
- Line colour → `soft periwinkle (#a7b5f2)`
- Accent lines → `warm sand (#d9a15e)`

---

# 2. README banner — SUPERSEDED, the banner is now authored

> Kept as a record of why. v2 fixed what v1 broke — the band came back a
> constant 175px thick with even 23-26px gaps and no fraying — but three
> rounds could not produce a single over-under crossing. The model drew
> flat X's: two strands meeting and carrying on, no break in the one
> behind. See `docs/brand/gen_banner.py` and the rationale in
> `docs/brand/README.md`.

# 2-OLD. README banner — v2

**Why v1 was replaced.** The first version asked for ~30 hairlines "at a
consistent 24-degree angle" that "converge and run together." The model read
the whole description as one-point perspective and drew a vanishing-point fan
— technically clean, but it says *telecom*, not *formation*. The strands never
plied, and at high density the lines frayed and wandered (and the dark variant
produced solid filled wedges where lines should be).

Three fixes below: perspective is banned by name, the strand count drops to
~12 so each one is individually followable, and the crossings are specified as
**over-under position swaps** rather than "convergence" — which is the word
that caused the problem.

Generate `1920x640`, scale to 1280 wide, crop to `1280x400`.

## 2-LIGHT-v2

```
A very wide banner illustration in a restrained technical print style — flat
printed ink on paper, drawn with a pen and a ruler, mechanically precise.

Background: one flat, completely even field of warm off-white paper (#faf9f6)
edge to edge. No vignette, no gradient, no texture, no soft or faded edges.

The image is a FLAT DIAGRAM seen straight on. There is no perspective, no
depth, no recession, and no vanishing point. The lines do not radiate from a
point and do not fan out. They run as a parallel band across the frame.

Draw exactly twelve long straight strands. All twelve run left to right across
the full width of the frame, rising very gently at about 12 degrees. They are
evenly spaced, roughly equal in weight, and stay parallel to one another for
most of their length — the band keeps a constant thickness from left edge to
right edge. It does not widen, narrow, or bunch.

At four evenly spaced points along the band, one adjacent PAIR of strands
crosses over each other and swaps places — the upper strand passes down and
under, the lower strand passes up and over, they trade positions, and then both
continue on parallel as before. Each crossing is a small, tidy, deliberate X
about as wide as the gap between two strands. At every crossing the strand
passing behind has a clean visible break in it, so you can always tell which is
in front. Only one pair crosses at a time; the other ten strands run straight
through, undisturbed.

Every strand is deep indigo blue (#3b4dbf), of even hairline-to-two-pixel
weight, perfectly straight between crossings, with no wobble, no waver, no
fraying, and no bunching. Except the topmost strand, which is dark burnt umber
(#935a1c) — a deep muted brown-ochre, not orange, not gold, not yellow — and
which runs slightly ahead of and above the other eleven.

All twelve strands sit inside the middle 55 percent of the frame height. The
top band and the bottom band are completely empty flat paper, because this
image will be cropped to a wide letterbox strip.

Flat printed ink. No gradients, no glow, no blur, no drop shadow, no 3D, no
noise, no depth of field.

Do not include: a vanishing point, perspective, radiating rays, a starburst, a
fan, or any suggestion of depth or distance; any letters, numbers, words,
wordmark, logo, caption, watermark, or signature; birds, geese, feathers,
wings, or any animal; yarn balls, spools, knitting needles, or fabric;
swooshes or curves; people or hands; UI panels, screenshots, dashboards, or
charts; a border or frame; any knot, tangle, spaghetti loop, or impossible
over-under topology.
```

**Going for:** a flat band of strands that stays a band, with four legible
places where two strands trade position. That swap is the whole brand idea —
the lead rotates, and the formation holds.

**Failure mode:** it reintroduces perspective anyway. Check first that the band
is the same thickness at both edges; if it tapers, discard. Second check: count
the crossings and confirm the behind-strand actually breaks. A crossing drawn
as a merge or a shaded overlap is a reject — that is the v1 failure returning.

**If the crossings will not come:** generate the band clean with no crossings
at all, then use the **edit endpoint** with a mask over one crossing site and
the instruction "make these two adjacent lines cross over each other and swap
places, with a clean break in the line that passes behind." Masked edits hold
this far better than a fresh prompt.

## 2-DARK-v2

Same prompt, three substitutions. Note the background is a **warm** near-black
— v1 came back at a cooler, darker `#0e0e0e`, which would not sit flush with
the site's dark paper:

- Background → `one flat, completely even field of warm near-black (#141311) — a soft warm charcoal, not pure black and not a cool grey`
- Strand colour → `soft periwinkle (#a7b5f2)`
- Lead strand → `warm sand (#d9a15e)`

---

# 2-OLD. README banner — v1 (superseded)

Scale to 1280 wide, crop to `1280x400`. Gitea renders README images against
both light and dark page backgrounds, so give the banner an **opaque ground**
and ship both variants behind a `<picture>` element.

The crop takes ~37% off the height, so the prompt hard-reserves the top and
bottom bands.

## 2-LIGHT

```
A very wide banner illustration, minimal and precise, in a restrained technical
print style — flat printed ink on paper.

Background: one flat, completely even field of warm off-white paper (#faf9f6)
filling the entire frame edge to edge. No vignette, no gradient, no soft or
faded edges, no texture.

Everything drawn sits inside a horizontal band across the exact vertical middle
of the frame, occupying only the middle 55 percent of the image height. The top
band and the bottom band are completely empty flat paper, because this image
will be cropped to a wide letterbox strip.

Inside that middle band: a long horizontal formation of thin straight lines in
deep indigo blue (#3b4dbf), all at the same shallow 24-degree upward angle. At
the left edge the lines are few, long, and widely spaced. Travelling right they
multiply and draw progressively closer together, and in the right third several
adjacent lines converge and run as single heavier lines before parting again.
The formation is densest at the right edge. The lines never cross one another.
One single line — the leading one at the top of the formation — is burnt amber
(#935a1c).

Line weights run hairline to three pixels, crisp and mechanically even, never
touching except where they deliberately converge.

Flat printed ink. No gradients, no glow, no blur, no drop shadow, no 3D, no
texture, no noise, no depth of field, no vignette, no soft or faded edges
anywhere.

Do not include: any letters, numbers, words, wordmark, logo, caption,
watermark, or signature; birds, geese, feathers, wings, or any animal; yarn
balls, spools, knitting needles, or fabric; swooshes or ribbon curves; people or
hands; UI panels, screenshots, dashboards, cards, or charts; a border or frame;
any knot, tangle, spaghetti loop, or impossible over-under topology.
```

**Going for:** sparse → dense left to right. Reads as one continuous strip.
**Failure mode:** it treats the reserved top and bottom bands as usable space
and the crop decapitates the composition. Check the crop before committing.
Second failure: a subtle vignette that becomes an obvious dirty band at
1280x400 against a flat page.

## 2-DARK

Same substitutions as 1-DARK: `#141311` field, `#a7b5f2` lines, `#d9a15e` lead.

---

# 3. OG / social card — generate 1200x640

Crop to `1200x630`. Lower priority: the app is served on a trusted LAN at
`http://${SKEIN_HOST}:3000`, so external crawlers cannot reach it and the card
will rarely unfurl. Worth having for screenshots and completeness.

The wordmark, tagline, and mark are composited afterward in HTML/SVG — the
generated art must leave room and contain no text.

```
A wide flat editorial illustration on warm off-white paper (#faf9f6), in a
restrained Swiss print style — precise, quiet, built from thin ruled lines.

Composition: the left 55 percent of the frame is almost entirely empty paper,
deliberately reserved as clean negative space. Nothing crosses into it except
the last few tapering line-ends. All artwork lives in the right 45 percent and
is densest at the right edge.

The artwork: a formation of about twenty long thin straight lines, all at the
same 24-degree upward angle, opening toward the upper right and converging
toward the left. Near the right edge the lines are many, evenly spaced and
parallel. Travelling left they thin out, draw together, and the last several
converge into a single line that tapers to nothing before reaching the middle
of the frame. Exactly three lines are burnt amber (#935a1c) — one at the
leading tip and two trailing; every other line is deep indigo blue (#3b4dbf).
Hairline to two-pixel weights, crisp and mechanically even. The lines never
cross one another.

All meaningful artwork sits within the middle horizontal band: the top 15
percent and bottom 15 percent of the frame are empty paper.

Flat printed ink on paper. No gradients, no glow, no drop shadow, no 3D, no
texture, no vignette, no photographic depth of field.

Do not include: any letters, numbers, words, wordmark, logo, caption,
watermark, or signature; birds, geese, feathers, wings, or any animal; yarn
balls, spools, knitting needles, or fabric; swooshes or ribbon curves; people or
hands; UI panels, screenshots, browser windows, dashboards, or charts; a border
or frame; any knot, tangle, spaghetti loop, or impossible over-under topology.
```

**Going for:** a reserved text column on the left, the formation anchored
right.
**Failure mode:** gpt-image-2 has strong horror vacui on wide canvases and will
fill the reserved left side. Expect to discard half. If four samples all
encroach, take the best and run an **edit** with a mask over the left 55% and
the instruction "erase all content in the masked region, leave flat off-white
paper." The mask route is far more reliable than re-prompting.

---

## Crop commands

Python + Pillow (no ImageMagick needed):

```python
from PIL import Image
Image.open("banner.png").resize((1280, 427)).crop((0, 13, 1280, 413)).save("banner-1280x400.png")
Image.open("og.png").crop((0, 5, 1200, 635)).save("og-1200x630.png")
```

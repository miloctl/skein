"""Author the README banner: twelve strands, one lead, four real over-under
crossings. Emits SVG (source of truth) and PNG (what the README references,
since git hosts are inconsistent about rendering SVG through <img>).

Generated rather than prompted because the whole thing is exact geometry with
topology that has to be *correct* — a diffusion model drew flat X's with no
over-under three attempts running, and an over-under is the only thing
separating "strand" from "line".

    python docs/brand/gen_banner.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

W, H = 1280, 400
COUNT = 12
SPACING = 18
RISE = 36  # total climb, left edge to right. Gentle: a 3:1 band steepens fast.
TOP = (H - SPACING * (COUNT - 1) - RISE) / 2 + RISE
STEP = 4  # polyline sampling
BLEND = 110  # horizontal distance a swap takes

# (centre x, upper slot) — the pair is (slot, slot+1). Slot 0 is the lead and
# never crosses: it has to stay legible as the strand out front.
CROSSINGS = [(250, 3), (530, 7), (820, 1), (1075, 5)]

GAP = 11  # break in the strand passing behind
LEAD_W, STRAND_W = 2.6, 1.9
# The site's .vee already sets the house style for a formation: gold lead at
# 4px, then trailing bars at opacity 0.75 and 0.5. Same idea continued over
# twelve — the formation reads as having depth rather than being a fence.
# Tied to the strand, not the slot, so it does not flicker across a swap.
FADE_MIN = 0.34


def opacity(strand: int) -> float:
    if strand == 0:
        return 1.0
    t = (strand - 1) / (COUNT - 2)
    return round(1.0 - (1.0 - FADE_MIN) * t**1.25, 3)

THEMES = {
    "light": {"bg": "#faf9f6", "ink": "#3b4dbf", "lead": "#935a1c"},
    "dark": {"bg": "#141311", "ink": "#a7b5f2", "lead": "#d9a15e"},
}


def slot_at(strand: int, x: float) -> float:
    """Where this strand sits vertically, in slot units, at a given x."""
    slot = float(strand)
    for cx, upper in CROSSINGS:
        if strand not in (upper, upper + 1):
            continue
        target = upper + 1 if strand == upper else upper
        lo, hi = cx - BLEND / 2, cx + BLEND / 2
        if x >= hi:
            slot = float(target)
        elif x > lo:
            t = (x - lo) / BLEND
            t = t * t * (3 - 2 * t)  # smoothstep, so the swap eases in and out
            slot = slot + (target - slot) * t
    return slot


def y_at(strand: int, x: float) -> float:
    return TOP + slot_at(strand, x) * SPACING - RISE * (x / W)


def points(strand: int) -> list[tuple[float, float]]:
    """Sample densely only inside the blend zones this strand takes part in.
    Everywhere else the path is a straight run and two endpoints describe it —
    the difference between a 3KB file and a 51KB one."""
    xs = {0.0, float(W)}
    for cx, upper in CROSSINGS:
        if strand not in (upper, upper + 1):
            continue
        lo, hi = cx - BLEND / 2, cx + BLEND / 2
        xs |= {lo, hi}
        xs |= {lo + i * STEP for i in range(1, int(BLEND / STEP) + 1) if lo + i * STEP < hi}
    return [(x, y_at(strand, x)) for x in sorted(xs)]


def behind_gaps(strand: int) -> list[tuple[float, float]]:
    """Ranges to cut out of this strand. The strand travelling UP passes
    behind — one consistent rule everywhere, so the weave looks deliberate
    rather than arbitrary."""
    out = []
    for cx, upper in CROSSINGS:
        rising = strand == upper + 1  # moving to a smaller slot = upward
        if rising:
            out.append((cx - GAP / 2, cx + GAP / 2))
    return out


def segments(strand: int) -> list[list[tuple[float, float]]]:
    gaps = behind_gaps(strand)
    if not gaps:
        return [points(strand)]
    segs, cur = [], []
    for x, y in points(strand):
        if any(a <= x <= b for a, b in gaps):
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        else:
            cur.append((x, y))
    if len(cur) > 1:
        segs.append(cur)
    return segs


def svg(theme: dict) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">',
        f'  <rect width="{W}" height="{H}" fill="{theme["bg"]}"/>',
        '  <g fill="none" stroke-linecap="round" stroke-linejoin="round">',
    ]
    for strand in range(COUNT):
        lead = strand == 0
        colour = theme["lead"] if lead else theme["ink"]
        width = LEAD_W if lead else STRAND_W
        for seg in segments(strand):
            d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in seg)
            parts.append(
                f'    <path d="{d}" stroke="{colour}" stroke-width="{width}"'
                f' stroke-opacity="{opacity(strand)}"/>'
            )
    parts += ["  </g>", "</svg>", ""]
    return "\n".join(parts)


def _mix(fg: str, bg: str, a: float) -> tuple[int, int, int]:
    f = tuple(int(fg[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(bg[i : i + 2], 16) for i in (1, 3, 5))
    return tuple(round(x * a + y * (1 - a)) for x, y in zip(f, b, strict=True))


def png(theme: dict, scale: int = 3) -> Image.Image:
    img = Image.new("RGB", (W * scale, H * scale), theme["bg"])
    d = ImageDraw.Draw(img)
    for strand in range(COUNT):
        lead = strand == 0
        colour = _mix(theme["lead"] if lead else theme["ink"], theme["bg"], opacity(strand))
        width = max(1, round((LEAD_W if lead else STRAND_W) * scale))
        for seg in segments(strand):
            d.line([(x * scale, y * scale) for x, y in seg], fill=colour, width=width, joint="curve")
    return img.resize((W, H), Image.LANCZOS)


def main() -> None:
    out = Path(__file__).resolve().parents[2] / "docs" / "site" / "img"
    out.mkdir(parents=True, exist_ok=True)
    for name, theme in THEMES.items():
        (out / f"banner-{name}.svg").write_text(svg(theme))
        png(theme).save(out / f"banner-{name}.png", optimize=True)
    print(f"wrote banner-{{light,dark}}.{{svg,png}} to {out}")


if __name__ == "__main__":
    main()

"""Verify every theme pack and colorway in globals.css against the Loom baseline.

Replaces the two hand-written pack checks that lived in docs/reviews/ and were
never run by anything. Those files listed their pack tokens as Python literals,
so a pack that shipped after the file was written was never checked. Atelier
shipped that way and fell outside the surface bounds unnoticed, and no colorway
older than 2026-08-01 was ever held to the 5.5:1 thread rule. This reads the
packs out of globals.css, so a new pack is covered on the day it ships.

Three hard invariants and one advisory note:

(a) Text and status tokens hold >= 4.5:1 on every surface combination that the
    Loom baseline itself passes. Combinations Loom already fails are excluded,
    so one weak pair in the baseline does not block a new pack.

(b) A colorway thread holds >= 5.5:1 and a weld >= 4.5:1 on EVERY pack surface,
    not only on the Loom bound surfaces. docs/brand/README.md states the 5.5
    figure for --thread across every named theme, so this checks that claim
    where it is made rather than inferring it.

(c) The custom colorway holds WCAG AA (4.5:1) at every hue. theme.ts pins
    lightness and chroma and exposes only the hue dial (thread
    oklch(0.44 0.13 h) light / oklch(0.8 0.09 h) dark, weld
    oklch(0.47 0.09 h) light / oklch(0.78 0.09 h) dark). This sweeps all 360
    integer hues of both formulas against every pack surface in both modes.
    The formulas here MUST match theme.ts customCss and the layout.tsx
    bootstrap inline script - update all three together.

(d) Surface brightness. A light surface is not dimmer than Loom's dimmest, and
    a dark surface is not lighter than Loom's brightest. This one is a proxy:
    it is what let a single sweep against Loom stand for every pack. Invariants
    (b) and (c) now verify each pack directly, so a surface outside the bounds
    is survivable — but only by an entry in SURFACE_BOUND_EXCEPTIONS that says
    which surface and why. An unlisted surface outside the bounds fails, and a
    listed surface back inside them fails as a stale entry.

Exit status is 0 when every pack passes and 1 when one fails.
"""

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "frontend" / "app" / "globals.css"

# Loom is the baseline: :root carries it, and [data-pack="loom"] aliases it.
BASE_PACK = "loom"
SURFACES = ("surface-page", "surface-card", "surface-raised")
TEXTS = ("text-1", "text-2", "text-3")
STATUSES = ("ok", "warn", "danger")
THREAD_MIN = 5.5
WELD_MIN = 4.5
TEXT_MIN = 4.5
CUSTOM_MIN = 4.5

# Surfaces knowingly outside the Loom brightness bounds: (pack, mode, surface)
# -> why it is accepted. Keyed to the exact surface the exception was granted
# for, so a repaint stops matching and the pack fails until someone re-grants
# it. A permanent unactionable NOTE trains readers to skip NOTE lines, which
# is exactly when the next real one gets missed.
SURFACE_BOUND_EXCEPTIONS = {
    ("atelier", "light", "surface-raised"): (
        "paper warmth #f4eee2 (luminance 0.8587 vs the Loom floor 0.8802); accents on it are "
        "proved directly by (b) and (c) — the custom-hue floor of 5.56:1 sits on this surface"
    ),
}

# Mirror of theme.ts customCss (and the layout.tsx bootstrap script):
# token -> (light (L, C), dark (L, C)); the hue is the swept variable.
CUSTOM_FORMULAS = {
    "thread": ((0.44, 0.13), (0.8, 0.09)),
    "weld": ((0.47, 0.09), (0.78, 0.09)),
}

_BLOCK = re.compile(r"([^{}]*)\{([^{}]*)\}", re.S)
_LIGHT_DARK = re.compile(
    r"--([a-z0-9-]+):\s*light-dark\(\s*(#[0-9a-fA-F]{6})\s*,\s*(#[0-9a-fA-F]{6})\s*\)"
)


def _luminance(hex_color: str) -> float:
    raw = hex_color.lstrip("#")
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _oklch_luminance(lightness: float, chroma: float, hue_deg: float) -> float:
    """WCAG relative luminance of an oklch color (Bjorn Ottosson's OKLab
    matrices, sRGB clipped to gamut the way browsers render these values)."""
    import math

    h = math.radians(hue_deg)
    a, b = chroma * math.cos(h), chroma * math.sin(h)
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = l_**3, m_**3, s_**3
    linear = (
        +4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    )
    r, g, bl = (min(1.0, max(0.0, c)) for c in linear)
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl


def _ratio(lum_a: float, lum_b: float) -> float:
    hi, lo = max(lum_a, lum_b), min(lum_a, lum_b)
    return (hi + 0.05) / (lo + 0.05)


def parse(css: str) -> tuple[dict, dict]:
    """Return ({pack: {mode: {token: hex}}}, {colorway: {mode: {token: hex}}}).

    A pack inherits every token it does not set from Loom, which is how the
    cascade resolves it in the browser.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    packs: dict[str, dict[str, dict[str, str]]] = {}
    ways: dict[str, dict[str, dict[str, str]]] = {}
    for selector, body in _BLOCK.findall(css):
        tokens = {m[0]: (m[1], m[2]) for m in _LIGHT_DARK.findall(body)}
        if not tokens:
            continue
        pack_names = re.findall(r'\[data-pack="([a-z]+)"\]', selector)
        way_names = re.findall(r'\[data-theme="([a-z]+)"\]', selector)
        # ":root," with no [data-pack] is Loom itself.
        if re.search(r"(^|,)\s*:root\s*(,|$)", selector) and not way_names:
            pack_names.append(BASE_PACK)
        for name in pack_names:
            target = packs.setdefault(name, {"light": {}, "dark": {}})
            for token, (light, dark) in tokens.items():
                target["light"][token] = light
                target["dark"][token] = dark
        for name in way_names:
            target = ways.setdefault(name, {"light": {}, "dark": {}})
            for token, (light, dark) in tokens.items():
                target["light"][token] = light
                target["dark"][token] = dark
    base = packs[BASE_PACK]
    for name, pack in packs.items():
        if name == BASE_PACK:
            continue
        for mode in ("light", "dark"):
            pack[mode] = {**base[mode], **pack[mode]}
    return packs, ways


def check(packs: dict, ways: dict) -> tuple[list[str], list[str], list[str]]:
    """Return (failures, notes, floors)."""
    base = packs[BASE_PACK]
    failures: list[str] = []
    notes: list[str] = []
    floors: list[str] = []

    # (a) needs the set of combinations the baseline itself passes.
    passes_baseline = {
        (mode, token, surface): _contrast(base[mode][token], base[mode][surface]) >= TEXT_MIN
        for mode in ("light", "dark")
        for token in TEXTS + STATUSES
        for surface in SURFACES
    }
    for name in sorted(packs):
        if name == BASE_PACK:
            continue
        pack = packs[name]
        for mode in ("light", "dark"):
            for token in TEXTS + STATUSES:
                for surface in SURFACES:
                    if not passes_baseline[(mode, token, surface)]:
                        continue
                    ratio = _contrast(pack[mode][token], pack[mode][surface])
                    if ratio < TEXT_MIN:
                        failures.append(
                            f"{name} {mode} --{token} on --{surface}: "
                            f"{ratio:.2f}:1 is below {TEXT_MIN}:1"
                        )

    # (b) every colorway accent, on every pack surface, in both modes.
    # The default colorway lives on Loom itself, so it is checked alongside.
    accents = {name: ways[name] for name in ways}
    accents["(default)"] = base
    for cname in sorted(accents):
        for pname in sorted(packs):
            for mode in ("light", "dark"):
                for token, need in (("thread", THREAD_MIN), ("weld", WELD_MIN)):
                    color = accents[cname][mode].get(token)
                    if color is None:
                        continue
                    for surface in SURFACES:
                        ratio = _contrast(color, packs[pname][mode][surface])
                        if ratio < need:
                            failures.append(
                                f"colorway {cname} on pack {pname}: {mode} --{token} "
                                f"{color} on --{surface} {packs[pname][mode][surface]}: "
                                f"{ratio:.2f}:1 is below {need}:1"
                            )

    # (c) the custom colorway: sweep all 360 integer hues of both accent
    # formulas against every pack surface in both modes. Report the floor so
    # the numbers documented in theme.ts and docs/brand stay auditable.
    floor_ratio, floor_at = 999.0, ""
    for token, (light_lc, dark_lc) in CUSTOM_FORMULAS.items():
        for mode, (lightness, chroma) in (("light", light_lc), ("dark", dark_lc)):
            for hue in range(360):
                accent_lum = _oklch_luminance(lightness, chroma, hue)
                for pname in sorted(packs):
                    for surface in SURFACES:
                        surf_lum = _luminance(packs[pname][mode][surface])
                        ratio = _ratio(accent_lum, surf_lum)
                        if ratio < floor_ratio:
                            floor_ratio = ratio
                            floor_at = f"--{token} {mode} hue {hue} on {pname} --{surface}"
                        if ratio < CUSTOM_MIN:
                            failures.append(
                                f"custom --{token} {mode} hue {hue} on pack {pname} "
                                f"--{surface} {packs[pname][mode][surface]}: "
                                f"{ratio:.2f}:1 is below {CUSTOM_MIN}:1"
                            )
    floors.append(f"custom-hue sweep floor: {floor_ratio:.2f}:1 at {floor_at}")

    # (d) surfaces outside the Loom bounds: a hard gate with a named-exception
    # list, so a NEW violation never hides behind an accepted one.
    floor = _luminance(base["light"]["surface-raised"])
    ceiling = _luminance(base["dark"]["surface-raised"])
    triggered = set()
    for name in sorted(packs):
        if name == BASE_PACK:
            continue
        for surface in SURFACES:
            for mode, value, bound, word in (
                ("light", _luminance(packs[name]["light"][surface]), floor, "below the Loom floor"),
                (
                    "dark",
                    _luminance(packs[name]["dark"][surface]),
                    ceiling,
                    "above the Loom ceiling",
                ),
            ):
                out_of_bounds = value < bound - 1e-9 if mode == "light" else value > bound + 1e-9
                if not out_of_bounds:
                    continue
                key = (name, mode, surface)
                triggered.add(key)
                shown = packs[name][mode][surface]
                if key in SURFACE_BOUND_EXCEPTIONS:
                    notes.append(
                        f"accepted exception: {name} {mode} --{surface} — "
                        f"{SURFACE_BOUND_EXCEPTIONS[key]}"
                    )
                else:
                    failures.append(
                        f"{name} {mode} --{surface} {shown}: luminance {value:.4f} is "
                        f"{word} {bound:.4f}. Repaint it, or add it to "
                        f"SURFACE_BOUND_EXCEPTIONS with the reason."
                    )
    for key in sorted(set(SURFACE_BOUND_EXCEPTIONS) - triggered):
        failures.append(
            f"stale exception: {key[0]} {key[1]} --{key[2]} is back inside the Loom "
            "bounds. Delete it from SURFACE_BOUND_EXCEPTIONS."
        )
    return failures, notes, floors


def main() -> int:
    packs, ways = parse(CSS.read_text(encoding="utf-8"))
    failures, notes, floors = check(packs, ways)
    checked = sorted(n for n in packs if n != BASE_PACK)
    print(f"packs: {', '.join(checked)} (baseline {BASE_PACK})")
    print(f"colorways: {', '.join(sorted(ways))} (+ the default on {BASE_PACK})")
    for line in floors:  # a measurement, not a violation — never shares NOTE
        print(f"FLOOR {line}")
    for line in notes:
        print(f"NOTE {line}")
    for line in failures:
        print(f"FAIL {line}")
    if failures:
        print(f"{len(failures)} contrast failure(s)")
        return 1
    print("all packs and colorways pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())

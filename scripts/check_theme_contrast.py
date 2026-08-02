"""Verify every theme pack and colorway in globals.css against the Loom baseline.

Replaces the two hand-written pack checks that lived in docs/reviews/ and were
never run by anything. Those files listed their pack tokens as Python literals,
so a pack that shipped after the file was written was never checked. Atelier
shipped that way and fell outside the surface bounds unnoticed, and no colorway
older than 2026-08-01 was ever held to the 5.5:1 thread rule. This reads the
packs out of globals.css, so a new pack is covered on the day it ships.

Two hard invariants and one advisory note:

(a) Text and status tokens hold >= 4.5:1 on every surface combination that the
    Loom baseline itself passes. Combinations Loom already fails are excluded,
    so one weak pair in the baseline does not block a new pack.

(b) A colorway thread holds >= 5.5:1 and a weld >= 4.5:1 on EVERY pack surface,
    not only on the Loom bound surfaces. docs/brand/README.md states the 5.5
    figure for --thread across every named theme, so this checks that claim
    where it is made rather than inferring it.

(note) Surface brightness. The originals made this a hard gate: a light surface
    must not be dimmer than Loom's dimmest, and a dark surface not lighter than
    Loom's brightest. That rule is a proxy. It lets an accent verified once
    against Loom be assumed safe on every other pack. Invariant (b) now checks
    every pack directly, so a pack outside the bounds is reported and does not
    fail the run. A real contrast gap fails on (a) or (b) instead.

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


def parse(css: str) -> tuple[dict, dict]:
    """Return ({pack: {mode: {token: hex}}}, {colorway: {mode: {token: hex}}}).

    A pack inherits every token it does not set from Loom, which is how the
    cascade resolves it in the browser.
    """
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


def check(packs: dict, ways: dict) -> tuple[list[str], list[str]]:
    """Return (failures, notes)."""
    base = packs[BASE_PACK]
    failures: list[str] = []
    notes: list[str] = []

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

    # (note) surfaces outside the Loom bounds: accent safety is no longer
    # inherited there, so (b) above is what proves it.
    floor = _luminance(base["light"]["surface-raised"])
    ceiling = _luminance(base["dark"]["surface-raised"])
    for name in sorted(packs):
        if name == BASE_PACK:
            continue
        for surface in SURFACES:
            light = _luminance(packs[name]["light"][surface])
            dark = _luminance(packs[name]["dark"][surface])
            if light < floor - 1e-9:
                notes.append(
                    f"{name} light --{surface} {packs[name]['light'][surface]} "
                    f"luminance {light:.4f} is below the Loom floor {floor:.4f}"
                )
            if dark > ceiling + 1e-9:
                notes.append(
                    f"{name} dark --{surface} {packs[name]['dark'][surface]} "
                    f"luminance {dark:.4f} is above the Loom ceiling {ceiling:.4f}"
                )
    return failures, notes


def main() -> int:
    packs, ways = parse(CSS.read_text(encoding="utf-8"))
    failures, notes = check(packs, ways)
    checked = sorted(n for n in packs if n != BASE_PACK)
    print(f"packs: {', '.join(checked)} (baseline {BASE_PACK})")
    print(f"colorways: {', '.join(sorted(ways))} (+ the default on {BASE_PACK})")
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

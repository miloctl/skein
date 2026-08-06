"""Verify every theme pack and colorway in globals.css against the Loom baseline.

Replaces the two hand-written pack checks that lived in docs/reviews/ and were
never run by anything. Those files listed their pack tokens as Python literals,
so a pack that shipped after the file was written was never checked. Atelier
shipped that way and fell outside the surface bounds unnoticed, and no colorway
older than 2026-08-01 was ever held to the 5.5:1 thread rule. This reads the
packs out of globals.css, so a new pack is covered on the day it ships.

Four hard invariants and one advisory note:

(a) Text and status tokens hold >= 4.5:1 on every surface combination that the
    Loom baseline itself passes. Combinations Loom already fails are excluded,
    so one weak pair in the baseline does not block a new pack.

(b) A colorway thread holds >= 5.5:1 and a weld >= 4.5:1 on EVERY pack surface,
    not only on the Loom bound surfaces. docs/brand/README.md states the 5.5
    figure for --thread across every named theme, so this checks that claim
    where it is made rather than inferring it.

(c) The custom colorway holds WCAG AA (4.5:1) at every hue. theme.ts pins
    lightness and chroma and exposes only the hue dial. The formulas are READ
    from frontend/lib/theme.ts (CUSTOM_LC) rather than copied here, and each
    row declares what it must stay legible against: an ink token is swept
    across every pack surface in both modes, a solid fill is swept against the
    white text it carries. All 360 integer hues, every row. A row added in
    theme.ts is swept without touching this file; a shape this cannot read is
    a hard error, never a silently empty sweep.

(d) Surface brightness. A light surface is not dimmer than Loom's dimmest, and
    a dark surface is not lighter than Loom's brightest. This one is a proxy:
    it is what let a single sweep against Loom stand for every pack. Invariants
    (b) and (c) now verify each pack directly, so a surface outside the bounds
    is survivable — but only by an entry in SURFACE_BOUND_EXCEPTIONS that says
    which surface and why. An unlisted surface outside the bounds fails, and a
    listed surface back inside them fails as a stale entry.

(e) frontend/lib/theme.ts and globals.css name the same packs and colorways.
    A pack styled in CSS but missing from theme.ts is unreachable in the
    picker and unstamped by the pre-paint script; one listed in theme.ts but
    unstyled renders as Loom while the UI claims otherwise. theme.ts is the
    single source for the ids, the storage keys and the custom formulas -
    layout.tsx GENERATES its pre-paint script from them (lib/theme-boot.ts)
    and this file parses them, so nothing is kept in sync by hand.

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
# Tokens this file sweeps as INK on surfaces. Any of them under white text is
# the misuse the mirror scan at the bottom catches — they are tuned to be
# legible ON a surface, which is the opposite requirement. `weld` and `thread`
# ride along because their fill halves are the -solid pair.
INK_FILL_CANDIDATES = (*STATUSES, "weld", "thread")
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

THEME_TS = Path(__file__).resolve().parents[1] / "frontend" / "lib" / "theme.ts"

# White, for the solid fills that carry white text (bg-thread-solid).
WHITE_LUM = 1.0

_CUSTOM_BLOCK = re.compile(r"export const CUSTOM_LC = \{(.*?)\n\} as const;", re.S)
_CUSTOM_ROW = re.compile(
    r'"(--[a-z0-9-]+)":\s*\{\s*hue:\s*"\w+",\s*on:\s*"(\w+)",\s*'
    r"light:\s*\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\],\s*"
    r"dark:\s*\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]"
)
_LIST_BLOCK = "export const {} = \\[(.*?)\\n\\] as const;"
# [a-z0-9-]+, matching the CSS selectors below and the token regex above. A
# narrower class here is not a stricter check — an id it cannot match drops out
# of BOTH `declared` and `styled`, so the pack ships unswept and invariant (e)
# reports nothing. Widen these four together or not at all.
_ID = re.compile(r'\{\s*id:\s*"([a-z0-9-]+)"')


def parse_custom(ts: str) -> dict[str, dict]:
    """The custom-colorway formulas, READ from lib/theme.ts.

    This used to be a hand-copied literal, one of three copies of the same
    numbers (theme.ts, the layout.tsx pre-paint script, here). Parsing means a
    retuned formula is swept on the next lint instead of when someone
    remembers. A shape this cannot read is a hard error, never an empty sweep
    that reports success.
    """
    block = _CUSTOM_BLOCK.search(ts)
    if not block:
        raise SystemExit(
            f"cannot find `export const CUSTOM_LC = {{...}} as const;` in {THEME_TS}."
            " The contrast sweep reads its formulas there. Restore the export,"
            " or update this parser with it."
        )
    rows = {}
    for token, on, light_l, light_c, dark_l, dark_c in _CUSTOM_ROW.findall(block.group(1)):
        rows[token] = {
            "on": on,
            "light": (float(light_l), float(light_c)),
            "dark": (float(dark_l), float(dark_c)),
        }
    declared = block.group(1).count('": {')
    if not rows or len(rows) != declared:
        raise SystemExit(
            f"read {len(rows)} of {declared} CUSTOM_LC rows in {THEME_TS}."
            " Keep one token per line in the documented shape, or update this parser."
        )
    return rows


def parse_default(ts: str, name: str) -> str:
    """DEFAULT_PACK / DEFAULT_COLORWAY: the id globals.css carries on :root, so
    it has no [data-*] selector of its own and cannot be found by parsing CSS."""
    found = re.search(rf'export const DEFAULT_{name} = "([a-z0-9-]+)";', ts)
    if not found:
        raise SystemExit(f"cannot find `export const DEFAULT_{name}` in {THEME_TS}.")
    return found.group(1)


def parse_ids(ts: str, name: str) -> set[str]:
    block = re.search(_LIST_BLOCK.format(name), ts, re.S)
    if not block:
        raise SystemExit(f"cannot find `export const {name} = [...]` in {THEME_TS}.")
    ids = set(_ID.findall(block.group(1)))
    if not ids:
        raise SystemExit(f"no ids parsed out of {name} in {THEME_TS}.")
    return ids


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
        pack_names = re.findall(r'\[data-pack="([a-z0-9-]+)"\]', selector)
        way_names = re.findall(r'\[data-theme="([a-z0-9-]+)"\]', selector)
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


def check(
    packs: dict,
    ways: dict,
    custom: dict,
    theme_packs: set[str],
    theme_ways: set[str],
    default_colorway: str,
) -> tuple[list[str], list[str], list[str]]:
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
    # The baseline is NOT grandfathered. A pair Loom itself fails would
    # otherwise vanish from the sweep entirely — the skip below excuses every
    # pack on exactly the pairs the baseline breaks, and Loom never appears
    # as a pack. text-3 on raised shipped at 4.16:1 in dark mode with every
    # gate green.
    for (mode, token, surface), ok in sorted(passes_baseline.items()):
        if not ok:
            ratio = _contrast(base[mode][token], base[mode][surface])
            failures.append(
                f"{BASE_PACK} {mode} --{token} on --{surface}: {ratio:.2f}:1 is below {TEXT_MIN}:1"
            )
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

    # (c) the custom colorway: sweep all 360 integer hues of every formula in
    # theme.ts against what that token has to stay legible on. Report the floor
    # so the numbers documented in theme.ts and docs/brand stay auditable.
    floor_ratio, floor_at = 999.0, ""
    solid_floor, solid_at = 999.0, ""
    for token, row in sorted(custom.items()):
        for mode in ("light", "dark"):
            lightness, chroma = row[mode]
            for hue in range(360):
                accent_lum = _oklch_luminance(lightness, chroma, hue)
                if row["on"] == "white":
                    # a solid fill under white text: the pack surfaces behind it
                    # are irrelevant, the label on top is what must be readable
                    ratio = _ratio(accent_lum, WHITE_LUM)
                    if ratio < solid_floor:
                        solid_floor = ratio
                        solid_at = f"{token} {mode} hue {hue}"
                    if ratio < CUSTOM_MIN:
                        failures.append(
                            f"custom {token} {mode} hue {hue} under white text: "
                            f"{ratio:.2f}:1 is below {CUSTOM_MIN}:1"
                        )
                    continue
                for pname in sorted(packs):
                    for surface in SURFACES:
                        surf_lum = _luminance(packs[pname][mode][surface])
                        ratio = _ratio(accent_lum, surf_lum)
                        if ratio < floor_ratio:
                            floor_ratio = ratio
                            floor_at = f"{token} {mode} hue {hue} on {pname} --{surface}"
                        if ratio < CUSTOM_MIN:
                            failures.append(
                                f"custom {token} {mode} hue {hue} on pack {pname} "
                                f"--{surface} {packs[pname][mode][surface]}: "
                                f"{ratio:.2f}:1 is below {CUSTOM_MIN}:1"
                            )
    floors.append(f"custom-hue sweep floor: {floor_ratio:.2f}:1 at {floor_at}")
    if solid_at:
        floors.append(
            f"custom solid-fill floor under white text: {solid_floor:.2f}:1 at {solid_at}"
        )

    # (e) the ids in theme.ts and the selectors in globals.css describe the same
    # set. A pack styled in CSS but absent from theme.ts is unreachable in the
    # picker AND unstamped by the pre-paint script; one listed in theme.ts but
    # unstyled renders as Loom while the UI claims otherwise. Either way the two
    # files disagree about what ships, which is how the theme bugs started.
    for label, declared, styled in (
        ("pack", theme_packs, set(packs)),
        ("colorway", theme_ways, set(ways) | {default_colorway}),
    ):
        for missing in sorted(declared - styled):
            failures.append(f"{label} {missing!r} is in theme.ts but has no rule in globals.css")
        for extra in sorted(styled - declared):
            failures.append(f"{label} {extra!r} is styled in globals.css but missing from theme.ts")

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
    ts = THEME_TS.read_text(encoding="utf-8")
    custom = parse_custom(ts)
    base = parse_default(ts, "PACK")
    if base != BASE_PACK:
        raise SystemExit(
            f"theme.ts DEFAULT_PACK is {base!r} but this checker baselines on {BASE_PACK!r}."
            f" Set BASE_PACK to {base!r}, or restore the default in theme.ts."
        )
    failures, notes, floors = check(
        packs,
        ways,
        custom,
        parse_ids(ts, "PACKS"),
        parse_ids(ts, "COLORWAYS"),
        parse_default(ts, "COLORWAY"),
    )
    # A token declared on:"white" is a FILL — its formula is tuned so white
    # text on top is legible, which says nothing about the token as ink. The
    # sweep below never looks at it that way, so `text-thread-solid` measured
    # 2.39:1 in dark on graphite with every gate green. Catch the usage
    # instead, from the same single source that declares the intent.
    fill_only = [t.removeprefix("--") for t, row in custom.items() if row["on"] == "white"]
    # *.ts* and the stylesheet, not *.tsx alone: a class assembled in a .ts
    # module or applied in CSS is the same misuse, and scanning one extension
    # would fix this instance rather than the rule.
    src = THEME_TS.parents[1]
    scanned = [
        f
        for d in ("app", "components", "lib")
        for f in (src / d).rglob("*.ts*")
        if "node_modules" not in f.parts
    ]
    for path in [*sorted(scanned), CSS]:
        text = path.read_text()
        for token in fill_only:
            if f"text-{token}" in text:
                failures.append(
                    f"{path.name} uses text-{token} as ink."
                    f" --{token} is a solid fill (on: white in theme.ts) and is not"
                    f" swept against pack surfaces. Use text-{token.replace('-solid', '')}."
                )

    # The mirror image of the scan above, and the one that was missing: an INK
    # token carrying white text. The sweep proves every ink against surfaces,
    # never under white, so `bg-ok text-white` sat at 1.87:1 in dark on the
    # Approve button with every gate green. Same single source of intent — a
    # token is safe under white only if it declares on:"white" — and no
    # literal list of call sites to maintain.
    # (?![-\w/]) matters twice: `-` stops bg-thread-solid matching bg-thread
    # (a word boundary sits before a hyphen), and `/` stops the alpha tints —
    # bg-danger/10 is a wash carrying its own ink, not a fill under white.
    ink_class = re.compile(r"\bbg-(" + "|".join(sorted(INK_FILL_CANDIDATES)) + r")(?![-\w/])")
    for path in [*sorted(scanned), CSS]:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if "text-white" not in line:
                continue
            for m in ink_class.finditer(line):
                token = m.group(1)
                if token in fill_only:
                    continue
                failures.append(
                    f"{path.name}:{n} puts text-white on bg-{token}."
                    f" --{token} is an ink tuned to sit ON a surface, and is not"
                    f" swept under white. Use bg-{token}-solid, adding it to"
                    f" globals.css and theme.ts (on: white) if it does not exist."
                )

    checked = sorted(n for n in packs if n != BASE_PACK)
    print(f"custom formulas read from theme.ts: {', '.join(sorted(custom))}")
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

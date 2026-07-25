"""Verify theme-pack token sets: (a) the surface-brightness invariant that
keeps every accent hue AA-valid, (b) text and status tokens vs every surface
they can appear on, at the same combos the loom baseline passes."""


def hex_rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i : i + 2], 16) / 255 for i in (0, 2, 4))


def lum(rgb):
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = lum(hex_rgb(a)), lum(hex_rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


STATUS = {
    "ok": ("#22754a", "#85cd9e"),
    "warn": ("#86590e", "#dfb362"),
    "danger": ("#b23425", "#f0998b"),
}

PACKS = {
    "loom": {
        "light": {
            "page": "#faf9f6", "card": "#ffffff", "raised": "#f3f1ec",
            "t1": "#1d1a16", "t2": "#5b5549", "t3": "#756e60",
        },
        "dark": {
            "page": "#141311", "card": "#1c1a18", "raised": "#262320",
            "t1": "#ede9e2", "t2": "#ada79b", "t3": "#8a8375",
        },
    },
    "ledger": {
        "light": {
            "page": "#f5f6f8", "card": "#ffffff", "raised": "#f0f2f5",
            "t1": "#181a1f", "t2": "#525763", "t3": "#6b7180",
        },
        "dark": {
            "page": "#13151a", "card": "#1a1d24", "raised": "#20232c",
            "t1": "#e9ebef", "t2": "#a6abb8", "t3": "#848b9b",
        },
    },
    "phosphor": {
        "light": {
            "page": "#f2f5ef", "card": "#fbfdf8", "raised": "#eef3e9",
            "t1": "#171d16", "t2": "#4c584a", "t3": "#657263",
        },
        "dark": {
            "page": "#0c100c", "card": "#131813", "raised": "#1b231b",
            "t1": "#dcead9", "t2": "#9cb399", "t3": "#7d947a",
        },
    },
    "contrast": {
        "light": {
            "page": "#ffffff", "card": "#ffffff", "raised": "#f5f5f5",
            "t1": "#000000", "t2": "#2e2e2e", "t3": "#494949",
        },
        "dark": {
            "page": "#000000", "card": "#0a0a0a", "raised": "#181818",
            "t1": "#ffffff", "t2": "#d4d4d4", "t3": "#b0b0b0",
        },
    },
}

SURFS = ("page", "card", "raised")

# baseline: which (token, surface) combos does loom itself pass at 4.5?
baseline = {}
for mode in ("light", "dark"):
    p = PACKS["loom"][mode]
    for tok in ("t1", "t2", "t3"):
        for s in SURFS:
            baseline[(mode, tok, s)] = contrast(p[tok], p[s]) >= 4.5
    for name, pair in STATUS.items():
        col = pair[0] if mode == "light" else pair[1]
        for s in SURFS:
            baseline[(mode, name, s)] = contrast(col, p[s]) >= 4.5

floor_light = lum(hex_rgb("#f3f1ec"))  # dimmest loom light surface
ceil_dark = lum(hex_rgb("#262320"))  # brightest loom dark surface

ok = True
for pname, pack in PACKS.items():
    # invariant: keeps every verified accent hue valid on pack surfaces
    for s in SURFS:
        ll = lum(hex_rgb(pack["light"][s]))
        dl = lum(hex_rgb(pack["dark"][s]))
        if ll < floor_light - 1e-9:
            ok = False
            print(f"FAIL {pname} light {s}: lum {ll:.3f} < loom floor {floor_light:.3f}")
        if dl > ceil_dark + 1e-9:
            ok = False
            print(f"FAIL {pname} dark {s}: lum {dl:.3f} > loom ceiling {ceil_dark:.3f}")
    # text + status vs surfaces, wherever loom passes
    for mode in ("light", "dark"):
        p = pack[mode]
        for tok in ("t1", "t2", "t3"):
            for s in SURFS:
                if not baseline[(mode, tok, s)]:
                    continue
                c = contrast(p[tok], p[s])
                if c < 4.5:
                    ok = False
                    print(f"FAIL {pname} {mode} {tok} on {s}: {c:.2f}")
        for name, pair in STATUS.items():
            col = pair[0] if mode == "light" else pair[1]
            for s in SURFS:
                if not baseline[(mode, name, s)]:
                    continue
                c = contrast(col, p[s])
                if c < 4.5:
                    ok = False
                    print(f"FAIL {pname} {mode} {name} on {s}: {c:.2f}")

skipped = [k for k, v in baseline.items() if not v]
print("loom-baseline combos excluded (loom itself <4.5):", skipped or "none")
print("ALL PASS" if ok else "TUNE NEEDED")

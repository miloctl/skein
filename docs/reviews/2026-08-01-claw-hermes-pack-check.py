"""Verify the claw + hermes packs and coral + bone colorways added 2026-08-01,
against the same invariants as 2026-07-25-pack-contrast-check.py:
(a) surface-brightness bounds (light >= loom's dimmest light surface,
    dark <= loom's brightest dark surface) so every verified accent stays AA,
(b) text and status tokens vs every surface combo the loom baseline passes,
(c) new colorway threads >= 5.5:1 and welds >= 4.5:1 on the bound surfaces
    (docs/brand/README.md claims >= 5.5 for --thread across named themes)."""


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

LOOM = {
    "light": {
        "page": "#faf9f6", "card": "#ffffff", "raised": "#f3f1ec",
        "t1": "#1d1a16", "t2": "#5b5549", "t3": "#756e60",
    },
    "dark": {
        "page": "#141311", "card": "#1c1a18", "raised": "#262320",
        "t1": "#ede9e2", "t2": "#ada79b", "t3": "#8a8375",
    },
}

PACKS = {
    "claw": {
        "light": {
            "page": "#faf9f7", "card": "#ffffff", "raised": "#f4f1ec",
            "t1": "#211e1a", "t2": "#56524a", "t3": "#6e6960",
        },
        "dark": {
            "page": "#0e1015", "card": "#161920", "raised": "#191c24",
            "t1": "#f4f4f5", "t2": "#d4d4d8", "t3": "#8b8b94",
        },
    },
    "hermes": {
        "light": {
            "page": "#f0f6fd", "card": "#ffffff", "raised": "#ecf3fc",
            "t1": "#170d02", "t2": "#46566b", "t3": "#57687f",
        },
        "dark": {
            "page": "#041c1c", "card": "#0e2423", "raised": "#112625",
            "t1": "#ffe6cb", "t2": "#cdbea8", "t3": "#a79f8e",
        },
    },
}

# light thread, dark thread, light weld, dark weld
COLORWAYS = {
    "coral": ("#a83c29", "#ff7070", "#0f766e", "#14b8a6"),
    "bone": ("#74581f", "#ffe6cb", "#047857", "#34d399"),
}

SURFS = ("page", "card", "raised")

baseline = {}
for mode in ("light", "dark"):
    p = LOOM[mode]
    for tok in ("t1", "t2", "t3"):
        for s in SURFS:
            baseline[(mode, tok, s)] = contrast(p[tok], p[s]) >= 4.5
    for name, pair in STATUS.items():
        col = pair[0] if mode == "light" else pair[1]
        for s in SURFS:
            baseline[(mode, name, s)] = contrast(col, p[s]) >= 4.5

floor_light = lum(hex_rgb("#f3f1ec"))
ceil_dark = lum(hex_rgb("#262320"))

ok = True
for pname, pack in PACKS.items():
    for s in SURFS:
        ll = lum(hex_rgb(pack["light"][s]))
        dl = lum(hex_rgb(pack["dark"][s]))
        if ll < floor_light - 1e-9:
            ok = False
            print(f"FAIL {pname} light {s}: lum {ll:.3f} < loom floor {floor_light:.3f}")
        if dl > ceil_dark + 1e-9:
            ok = False
            print(f"FAIL {pname} dark {s}: lum {dl:.3f} > loom ceiling {ceil_dark:.3f}")
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

# the bound surfaces are the worst case for accents on every conforming pack
for cname, (tl, td, wl, wd) in COLORWAYS.items():
    for label, col, surf, need in [
        ("thread light", tl, "#f3f1ec", 5.5),
        ("thread dark", td, "#262320", 5.5),
        ("weld light", wl, "#f3f1ec", 4.5),
        ("weld dark", wd, "#262320", 4.5),
    ]:
        c = contrast(col, surf)
        if c < need:
            ok = False
            print(f"FAIL {cname} {label} {col}: {c:.2f} < {need}")

print("ALL PASS" if ok else "TUNE NEEDED")

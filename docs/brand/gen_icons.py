"""Rasterize the Skein mark into the icon set. Mirrors frontend/app/icon.svg —
if that path changes, change GLYPH here too."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[2] / "frontend"
PLATE = (59, 77, 191)  # #3b4dbf --thread light
PAPER = (250, 249, 246)  # #faf9f6 --surface-page light
GLYPH = [(5, 7), (16, 15), (27, 7), (27, 17), (16, 25), (5, 17)]  # on a 32 grid
GRID = 32
SS = 8  # supersample factor; Pillow has no AA on polygon/rounded_rectangle


def render(px: int, *, radius_frac: float, glyph_scale: float = 1.0) -> Image.Image:
    n = px * SS
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if radius_frac:
        d.rounded_rectangle([0, 0, n - 1, n - 1], radius=n * radius_frac, fill=PLATE)
    else:
        d.rectangle([0, 0, n - 1, n - 1], fill=PLATE)
    k = n / GRID
    c = GRID / 2
    pts = [((c + (x - c) * glyph_scale) * k, (c + (y - c) * glyph_scale) * k) for x, y in GLYPH]
    d.polygon(pts, fill=PAPER)
    return img.resize((px, px), Image.LANCZOS)


def main() -> None:
    # iOS discards alpha and composites onto black — full bleed, no rounding,
    # the home-screen mask supplies the corners.
    apple = render(180, radius_frac=0).convert("RGB")
    apple.save(OUT / "app/apple-icon.png")
    # the same image at the well-known path. Next serves app/apple-icon.png
    # under a hashed URL and links it, but a client that does not read the
    # link tag probes /apple-touch-icon.png and logs a 404 against us.
    apple.save(OUT / "public/apple-touch-icon.png")

    for px in (192, 512):
        render(px, radius_frac=0.175).save(OUT / f"public/icon-{px}.png")

    # Android adaptive icons crop to a circle of ~40% radius; keep the glyph
    # inside that and let the plate bleed.
    render(512, radius_frac=0, glyph_scale=0.72).save(OUT / "public/icon-maskable-512.png")

    ico = render(256, radius_frac=0.175)
    ico.save(OUT / "app/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])

    print(
        "wrote apple-icon.png, apple-touch-icon.png, icon-192/512,"
        " icon-maskable-512, favicon.ico"
    )


if __name__ == "__main__":
    main()

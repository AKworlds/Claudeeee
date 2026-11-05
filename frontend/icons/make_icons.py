"""Generate PWA icons (run with a Python that has Pillow): python3 frontend/icons/make_icons.py
Same motif as the favicon: blue rounded square, white rising line."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).parent
BLUE = (37, 99, 235, 255)
WHITE = (255, 255, 255, 255)
PTS = [(7, 22), (13, 15), (18, 19), (25, 10)]  # from the 32x32 favicon


def draw(size, inner=1.0, rounded=True):
    s = 4  # supersample
    big = size * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if rounded:
        d.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 7 / 32), fill=BLUE)
    else:
        d.rectangle([0, 0, big, big], fill=BLUE)
    # map the 32-unit motif into the centered `inner` fraction of the canvas
    scale = big * inner / 32
    off = big * (1 - inner) / 2
    pts = [(off + x * scale, off + y * scale) for x, y in PTS]
    w = int(3 * scale)
    d.line(pts, fill=WHITE, width=w, joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


draw(192).save(OUT / "icon-192.png")
draw(512).save(OUT / "icon-512.png")
draw(512, inner=0.62, rounded=False).save(OUT / "icon-maskable-512.png")  # full-bleed, motif inside safe zone
# iOS applies its own mask; give it an opaque full-bleed square
draw(180, rounded=False).convert("RGB").save(OUT / "apple-touch-icon.png")

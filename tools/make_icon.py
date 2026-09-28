"""Draws assets/icon.ico (a magnifying glass over sound bars).

    python tools/make_icon.py
"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "assets")
S = 1024  # draw large, downscale for smooth edges


def main():
    os.makedirs(OUT, exist_ok=True)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Diagonal gradient background in a rounded square
    grad = Image.new("RGBA", (S, S))
    top, bottom = (124, 92, 255), (47, 179, 255)
    px = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S - 2)
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=220, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx, cy, r = 430, 430, 250
    # handle
    d.line((cx + 170, cy + 170, 830, 830), fill=white, width=110)
    d.ellipse((830 - 55, 830 - 55, 830 + 55, 830 + 55), fill=white)
    # lens
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=white, width=70)
    # sound bars inside the lens
    bars = [90, 190, 280, 170, 110]
    w, gap = 44, 22
    x0 = cx - (len(bars) * w + (len(bars) - 1) * gap) // 2
    for i, h in enumerate(bars):
        x = x0 + i * (w + gap)
        d.rounded_rectangle((x, cy - h // 2, x + w, cy + h // 2), radius=w // 2, fill=white)

    img = img.resize((256, 256), Image.LANCZOS)
    img.save(os.path.join(OUT, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                                   (64, 64), (128, 128), (256, 256)])
    img.save(os.path.join(OUT, "icon.png"))
    print("saved", os.path.abspath(OUT))


if __name__ == "__main__":
    main()

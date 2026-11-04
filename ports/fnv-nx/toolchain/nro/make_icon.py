#!/usr/bin/env python3
"""make_icon.py <out.jpg>: the fnv-nx hbmenu icon, drawn from scratch (our own artwork, no game assets):
256x256 baseline JPEG, a dark field with a desert-orange horizon band and the project name. Needs Pillow; the
generated icon.jpg is committed so builds do not need it."""
import sys

from PIL import Image, ImageDraw, ImageFont

W = 256
im = Image.new("RGB", (W, W), (24, 22, 30))
d = ImageDraw.Draw(im)
for y in range(150, 196):   # horizon band, orange to dark
    t = (y - 150) / 46
    d.line([(0, y), (W, y)], fill=(int(224 - 170 * t), int(128 - 100 * t), int(48 - 20 * t)))
d.ellipse([96, 104, 160, 168], fill=(240, 196, 96))    # low sun behind the band
for y in range(150, 196):
    t = (y - 150) / 46
    d.line([(0, y), (W, y)], fill=(int(224 - 170 * t), int(128 - 100 * t), int(48 - 20 * t)))
try:
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 44)
except OSError:
    font = ImageFont.load_default()
d.text((W // 2, 60), "fnv-nx", font=font, fill=(236, 232, 220), anchor="mm")
d.rectangle([0, 0, W - 1, W - 1], outline=(224, 128, 48), width=4)
im.save(sys.argv[1], "JPEG", quality=90, progressive=False, optimize=False, subsampling=2)

import os
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
os.makedirs("video/ovl", exist_ok=True)

for i in range(1, 7):
    lines = open(f"video/sub/sub{i}.txt", encoding="utf-8").read().rstrip("\n").split("\n")
    font = ImageFont.truetype(FONT, 56)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ls = 14  # extra line spacing
    widths = [d.textlength(l, font=font) for l in lines]
    tw = max(widths)
    asc, desc = font.getmetrics()
    lh = asc + desc + ls
    th = lh * len(lines) - ls
    x0 = (W - tw) / 2
    y0 = H - 430 - th / 2
    pad = 24
    d.rounded_rectangle(
        [x0 - pad, y0 - pad, x0 + tw + pad, y0 + th + pad],
        radius=18, fill=(0, 0, 0, 95),
    )
    for j, l in enumerate(lines):
        w = d.textlength(l, font=font)
        y = y0 + j * lh
        d.text(((W - w) / 2, y), l, font=font, fill=(255, 255, 255, 255),
               stroke_width=4, stroke_fill=(0, 0, 0, 230))
    img.save(f"video/ovl/ovl{i}.png")
    print(f"ovl{i}.png ok")

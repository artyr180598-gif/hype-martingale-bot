import math
import os
import random
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FF = "/usr/local/lib/python3.11/dist-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"
W, H = 1080, 1920
FPS = 30
SRC_W, SRC_H = 2484, 4416  # ~2.3x of 1080x1920 with pan margin
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56)
ACCENTS = [(255, 60, 90), (120, 220, 255), (255, 190, 60),
           (140, 255, 160), (190, 140, 255), (255, 120, 60)]


def dur_of(path):
    err = subprocess.run([FF, "-i", path], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ease_out_cubic(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


def ease_out_back(x):
    x = max(0.0, min(1.0, x))
    c1 = 1.70158
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def make_vignette():
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([-W * 0.35, -H * 0.28, W * 1.35, H * 1.28], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(180))
    alpha = mask.point(lambda a: int((255 - a) * 0.5))
    v = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    v.putalpha(alpha)
    return v


def make_sub_overlay(i):
    lines = open(f"video/sub/sub{i}.txt", encoding="utf-8").read().rstrip("\n").split("\n")
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ls = 14
    widths = [d.textlength(l, font=FONT) for l in lines]
    tw = max(widths)
    asc, desc = FONT.getmetrics()
    lh = asc + desc + ls
    th = lh * len(lines) - ls
    x0 = (W - tw) / 2
    y0 = H - 430 - th / 2
    pad = 24
    d.rounded_rectangle([x0 - pad, y0 - pad, x0 + tw + pad, y0 + th + pad],
                        radius=18, fill=(0, 0, 0, 110))
    for j, l in enumerate(lines):
        w = d.textlength(l, font=FONT)
        d.text(((W - w) / 2, y0 + j * lh), l, font=FONT,
               fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0, 230))
    return img


def build_bg(i):
    im = Image.open(f"video/img/scene{i}.png").convert("RGB")
    sc = max(SRC_W / im.width, SRC_H / im.height)
    im = im.resize((round(im.width * sc), round(im.height * sc)), Image.LANCZOS)
    x = (im.width - SRC_W) // 2
    y = (im.height - SRC_H) // 2
    return im.crop((x, y, x + SRC_W, y + SRC_H))


def make_bokeh_sprite(r, color):
    pad = 10
    s = Image.new("RGBA", (2 * r + 2 * pad, 2 * r + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(s)
    d.ellipse([pad, pad, pad + 2 * r, pad + 2 * r], fill=color + (160,))
    return s.filter(ImageFilter.GaussianBlur(r * 0.55))


def heart_pts(cx, cy, s):
    pts = []
    for k in range(36):
        t = 2 * math.pi * k / 36
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * s, cy - y * s))
    return pts


def star_pts(cx, cy, s, rot):
    pts = []
    for k in range(8):
        ang = rot + math.pi * k / 4
        rad = s if k % 2 == 0 else s * 0.28
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    return pts


def render_scene(i, t_start, total, vin):
    acc = ACCENTS[(i - 1) % len(ACCENTS)]
    rng = random.Random(i * 777)
    audio = f"video/audio/seg{i}.mp3"
    dur = dur_of(audio)
    pad = dur + 0.6
    nfr = int(round(pad * FPS))
    bg = build_bg(i)
    sub = make_sub_overlay(i)
    dirn = 1 if i % 2 == 1 else -1

    bokeh = []
    for _ in range(20):
        r = rng.uniform(13, 40)
        bokeh.append({
            "spr": make_bokeh_sprite(int(r), acc if rng.random() < 0.6 else (255, 255, 255)),
            "r": r,
            "x0": rng.uniform(0, W),
            "y0": rng.uniform(0, H),
            "speed": rng.uniform(28, 85),
            "amp": rng.uniform(10, 34),
            "f": rng.uniform(0.25, 0.7),
            "ph": rng.uniform(0, 6.28),
            "a0": rng.uniform(0.45, 1.0),
        })
    sparkles = [{
        "x": rng.uniform(60, W - 60), "y": rng.uniform(80, H - 500),
        "s": rng.uniform(13, 30), "ph": rng.uniform(0, 4),
        "cyc": rng.uniform(1.0, 1.9), "rot": rng.choice([0, 0.78]),
        "col": rng.choice([(255, 255, 255), (255, 215, 120), acc]),
    } for _ in range(9)]
    hearts = [{
        "x0": rng.uniform(0.05, 0.95) * W,
        "s": rng.uniform(0.7, 1.6),
        "speed": rng.uniform(55, 140),
        "amp": rng.uniform(14, 40), "f": rng.uniform(0.3, 0.8),
        "ph": rng.uniform(0, 6.28), "a": rng.uniform(120, 220),
    } for _ in range(14)] if i == 6 else []

    out = f"video/out/scene_v2_{i}.mp4"
    cmd = [FF, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", audio,
           "-filter_complex", "[1:a]afade=t=in:st=0:d=0.12,apad[a]",
           "-map", "0:v", "-map", "[a]", "-t", f"{pad:.3f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-ar", "44100", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    for f in range(nfr):
        t = f / FPS
        zp = 0.12 * (1 - ease_out_cubic(min(1.0, t / 0.35)))
        zm = 0.07 * smooth(t / pad)
        z = 1 + zp + zm
        # base window (z=1) = SRC/1.15, zoom divides window size by z
        cw = (SRC_W / 1.15) / z
        ch = cw * H / W
        if ch > SRC_H:
            ch = SRC_H
            cw = ch * W / H
        panx = dirn * 70 * smooth(t / pad)
        pany = -16 * smooth(t / pad)
        sxx = cw / W
        cx = SRC_W / 2 + panx * sxx
        cy = SRC_H / 2 + pany * sxx
        x0 = min(max(cx - cw / 2, 0), SRC_W - cw)
        y0 = min(max(cy - ch / 2, 0), SRC_H - ch)

        frame = bg.transform((W, H), Image.AFFINE,
                             (sxx, 0, x0, 0, sxx, y0),
                             resample=Image.BICUBIC).convert("RGBA")
        frame = Image.alpha_composite(frame, vin)

        # soft bokeh particles
        for b in bokeh:
            y = (b["y0"] - b["speed"] * t) % (H + 160) - 80
            x = b["x0"] + b["amp"] * math.sin(t * b["f"] * 2 * math.pi + b["ph"])
            a = b["a0"] * (0.65 + 0.35 * math.sin(t * 2.0 + b["ph"]))
            if a < 0.05:
                continue
            spr = b["spr"]
            if a < 0.98:
                spr = spr.copy()
                alpha = spr.getchannel("A").point(lambda v, aa=a: int(v * aa))
                spr.putalpha(alpha)
            frame.alpha_composite(spr, (int(x - spr.width / 2), int(y - spr.height / 2)))

        # sharp layer: sparkles, hearts, progress bar
        sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for sp in sparkles:
            k = ((t + sp["ph"]) % sp["cyc"]) / sp["cyc"]
            sc = math.sin(math.pi * k)
            if sc < 0.06:
                continue
            ss = sp["s"] * (0.6 + 0.4 * sc)
            sd.polygon(star_pts(sp["x"], sp["y"], ss, sp["rot"]),
                       fill=sp["col"] + (int(230 * sc),))
        for hrt in hearts:
            y = (H + 100 - ((hrt["speed"] * (t + hrt["ph"])) % (H + 260)))
            x = hrt["x0"] + hrt["amp"] * math.sin(t * hrt["f"] * 2 * math.pi + hrt["ph"])
            a = int(hrt["a"] * min(1.0, (y + 100) / 300.0, (H - y) / 220.0))
            if a > 5:
                sd.polygon(heart_pts(x, y, hrt["s"]), fill=(255, 70, 110, max(0, a)))
        prog = (t_start + t) / total
        sd.rounded_rectangle([80, 52, W - 80, 64], radius=6, fill=(0, 0, 0, 80))
        sd.rounded_rectangle([80, 52, 80 + (W - 160) * prog, 64], radius=6,
                             fill=acc + (235,))
        frame = Image.alpha_composite(frame, sl)

        # subtitle pop-in
        k = min(1.0, t / 0.38)
        if k >= 1.0:
            frame = Image.alpha_composite(frame, sub)
        else:
            scale = 0.82 + 0.18 * ease_out_back(k)
            sw, sh = int(W * scale), int(H * scale)
            ssub = sub.resize((sw, sh), Image.BILINEAR)
            alpha = ssub.getchannel("A").point(lambda v: int(v * min(1.0, k * 1.6)))
            ssub.putalpha(alpha)
            frame.alpha_composite(ssub, ((W - sw) // 2, (H - sh) // 2 + int(26 * (1 - ease_out_cubic(k)))))

        # white flash at scene start
        if t < 0.26:
            fa = int(150 * (1 - t / 0.26) ** 2)
            frame = Image.alpha_composite(frame, Image.new("RGBA", (W, H), (255, 255, 255, fa)))

        proc.stdin.write(frame.convert("RGB").tobytes())
    proc.stdin.close()
    proc.wait()
    print(f"scene {i}: {nfr} frames, {pad:.2f}s -> {out}", flush=True)
    return out, pad


def main():
    os.makedirs("video/out", exist_ok=True)
    vin = make_vignette()
    durs = [dur_of(f"video/audio/seg{i}.mp3") for i in range(1, 7)]
    pads = [d + 0.6 for d in durs]
    total = sum(pads)
    t = 0.0
    for i in range(1, 7):
        _, pad = render_scene(i, t, total, vin)
        t += pad
    print("ALL SCENES DONE")


if __name__ == "__main__":
    main()

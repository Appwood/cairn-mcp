"""Cairn README banner — horizontal lockup (icon + wordmark), 'Luminous Sediment'."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

HERE = Path(__file__).resolve().parent
# Fonts are not bundled. Set CAIRN_FONTS_DIR to a directory containing
# GeistMono-Bold.ttf and JetBrainsMono-Regular.ttf, or drop them in ./fonts.
FONTS = os.environ.get("CAIRN_FONTS_DIR", str(HERE / "fonts"))

SS = 2
W, H = 1780 * SS, 660 * SS

BG_TOP = (12, 16, 24); BG_BOT = (6, 8, 13)
STONE_TOP = (70, 86, 111); STONE_BOT = (33, 42, 64)
EMBER_TOP = (255, 205, 138); EMBER_BOT = (231, 144, 52)
GLOW = (255, 176, 92); INK = (233, 238, 247); MUTE = (120, 132, 152); FAINT = (78, 90, 110)
WIDTHS = [3.45, 2.82, 2.16, 1.30]
DOTS = [(108, 196, 178), (168, 150, 232), (255, 196, 120), (228, 142, 150)]


def font(name, size): return ImageFont.truetype(f"{FONTS}/{name}", int(size))
def lerp(a, b, t): return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def vgrad(w, h, c0, c1):
    img = Image.new("RGB", (w, h), c0); d = ImageDraw.Draw(img)
    for y in range(h): d.line([(0, y), (w, y)], fill=lerp(c0, c1, y / max(1, h - 1)))
    return img


def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255); return m


def screen(a, b): return ImageChops.screen(a, b)


def paint_stack(base, cx, bottom_y, unit):
    sh = unit * 0.92; gap = unit * 0.30; centers = []; y = bottom_y
    for wm in WIDTHS:
        w = unit * wm; centers.append((cx, y - sh / 2, w, sh)); y -= (sh + gap)
    top = centers[-1]
    ecx, ecy = top[0], top[1] - top[3] * 0.5 - unit * 0.36
    gl = Image.new("RGB", base.size, (0, 0, 0)); gd = ImageDraw.Draw(gl)
    hr = unit * 2.2
    gd.ellipse([top[0] - hr, top[1] - hr * 1.12, top[0] + hr, top[1] + hr * 0.82], fill=lerp((0, 0, 0), GLOW, 0.40))
    tw, th = top[2], top[3]
    gd.rounded_rectangle([top[0] - tw / 2, top[1] - th / 2, top[0] + tw / 2, top[1] + th / 2], radius=th * 0.4, fill=lerp((0, 0, 0), GLOW, 0.82))
    er = unit * 0.46
    gd.ellipse([ecx - er, ecy - er, ecx + er, ecy + er], fill=(255, 206, 140))
    bw = unit * 0.30
    gd.rectangle([ecx - bw, ecy - unit * 3.0, ecx + bw, ecy], fill=lerp((0, 0, 0), GLOW, 0.38))
    bloom = Image.new("RGB", base.size, (0, 0, 0))
    for rad, inten in [(unit * 0.16, 0.90), (unit * 0.55, 0.78), (unit * 1.3, 0.58), (unit * 2.4, 0.40)]:
        b = gl.filter(ImageFilter.GaussianBlur(rad))
        if inten != 1.0: b = b.point(lambda v: int(v * inten))
        bloom = screen(bloom, b)
    base.paste(screen(base.copy(), bloom))
    d = ImageDraw.Draw(base, "RGBA")
    for i, (scx, scy, w, h) in enumerate(centers):
        x0, y0 = scx - w / 2, scy - h / 2; is_top = (i == len(centers) - 1)
        ctop, cbot = (EMBER_TOP, EMBER_BOT) if is_top else (STONE_TOP, STONE_BOT); r = h * 0.4
        base.paste(vgrad(int(w), int(h), ctop, cbot), (int(x0), int(y0)), rounded_mask(int(w), int(h), int(r)))
        d.line([(x0 + r * 0.7, y0 + h * 0.16), (x0 + w - r * 0.7, y0 + h * 0.16)], fill=(255, 255, 255, 70 if is_top else 38), width=max(1, int(unit * 0.02)))
        d.line([(x0 + r * 0.7, y0 + h - h * 0.14), (x0 + w - r * 0.7, y0 + h - h * 0.14)], fill=(0, 0, 0, 55), width=max(1, int(unit * 0.02)))
        dot_r = unit * 0.12; dcx, dcy = x0 + h * 0.5, scy
        d.ellipse([dcx - dot_r, dcy - dot_r, dcx + dot_r, dcy + dot_r], fill=DOTS[i % 4] + (235,))
        lx0 = dcx + dot_r * 1.8; lx1 = x0 + w - h * 0.4
        if lx1 > lx0:
            d.rounded_rectangle([lx0, dcy - unit * 0.045, min(lx1, lx0 + w * 0.5), dcy + unit * 0.045], radius=unit * 0.045, fill=lerp(ctop, (255, 255, 255), 0.18) + (60,))
    return ecx, ecy


# ---- compose banner ----
sheet = vgrad(W, H, BG_TOP, BG_BOT)
lift = Image.new("RGB", (W, H), (0, 0, 0))
ImageDraw.Draw(lift).ellipse([W * 0.02, -H * 0.4, W * 0.42, H * 1.4], fill=(13, 17, 26))
sheet = screen(sheet, lift.filter(ImageFilter.GaussianBlur(W * 0.07)))

# icon (left)
unit = 92 * SS
icx = 420 * SS
paint_stack(sheet, icx, 598 * SS, unit)

d = ImageDraw.Draw(sheet, "RGBA")
# wordmark
tx = 760 * SS
f_word = font("GeistMono-Bold.ttf", 188 * SS)
word_y = 286 * SS
x = tx; centers = []
for c in "cairn":
    w = d.textlength(c, font=f_word)
    d.text((x, word_y), c, font=f_word, fill=INK, anchor="lm")
    centers.append((x + w / 2, w)); x += w + 12 * SS
i_cx = centers[2][0]
# amber tittle spark
tit = Image.new("RGB", sheet.size, (0, 0, 0))
tr = 18 * SS; tit_y = word_y - 99 * SS
ImageDraw.Draw(tit).ellipse([i_cx - tr, tit_y - tr, i_cx + tr, tit_y + tr], fill=GLOW)
sheet = screen(sheet, tit.filter(ImageFilter.GaussianBlur(21 * SS)))
d = ImageDraw.Draw(sheet, "RGBA")
d.ellipse([i_cx - tr, tit_y - tr, i_cx + tr, tit_y + tr], fill=(255, 208, 144, 255))

# tagline + subtitle (letter-spaced)
def spaced(xy, text, fnt, fill, tracking):
    x = xy[0]
    for c in text:
        d.text((x, xy[1]), c, font=fnt, fill=fill, anchor="lm")
        x += d.textlength(c, font=fnt) + tracking

spaced((tx + 6 * SS, 410 * SS), "evidence, not instructions", font("JetBrainsMono-Regular.ttf", 33 * SS), (150, 160, 178), 6 * SS)
spaced((tx + 6 * SS, 466 * SS), "structured log adapter · mcp", font("JetBrainsMono-Regular.ttf", 25 * SS), FAINT, 5 * SS)

final = sheet.resize((W // SS, H // SS), Image.LANCZOS)
final.save(HERE / "cairn-banner.png")
print("banner", final.size)

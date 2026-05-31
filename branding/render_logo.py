"""Cairn logo — 'Luminous Sediment'. Pillow-only, supersampled, glow via blur."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

HERE = Path(__file__).resolve().parent
# Fonts are not bundled. Set CAIRN_FONTS_DIR to a directory containing
# GeistMono-Bold.ttf and JetBrainsMono-Regular.ttf, or drop them in ./fonts.
FONTS = os.environ.get("CAIRN_FONTS_DIR", str(HERE / "fonts"))

SS = 2                      # supersample factor
W, H = 1600 * SS, 2000 * SS

# palette
BG_TOP = (12, 16, 24)
BG_BOT = (6, 8, 13)
STONE_TOP = (70, 86, 111)
STONE_BOT = (33, 42, 64)
EMBER_TOP = (255, 205, 138)
EMBER_BOT = (231, 144, 52)
GLOW = (255, 176, 92)
INK = (233, 238, 247)
MUTE = (120, 132, 152)
FAINT = (78, 90, 110)
AMBER = (255, 184, 92)

WIDTHS = [3.45, 2.82, 2.16, 1.30]   # stone width multipliers, bottom -> top
DOTS = [(108, 196, 178), (168, 150, 232), (255, 196, 120), (228, 142, 150)]


def font(name, size):
    return ImageFont.truetype(f"{FONTS}/{name}", int(size))


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def vgrad(w, h, c0, c1):
    img = Image.new("RGB", (w, h), c0)
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=lerp(c0, c1, y / max(1, h - 1)))
    return img


def rounded_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return m


def screen(base, layer):
    return ImageChops.screen(base, layer)


def paint_stack(base, cx, bottom_y, unit, ember=True):
    """Draw the cairn (stones that read as log lines) + warm glow onto base RGB.

    Returns (ember_cx, ember_cy). All geometry derives from `unit`.
    """
    sh = unit * 0.92            # stone height
    gap = unit * 0.30           # vertical gap
    centers = []                # (cx, cy, w, h) per stone, bottom -> top
    y = bottom_y
    for i, wm in enumerate(WIDTHS):
        w = unit * wm
        cy = y - sh / 2
        centers.append((cx, cy, w, sh))
        y -= (sh + gap)
    top = centers[-1]
    ember_cx, ember_cy = top[0], top[1] - top[3] * 0.5 - unit * 0.36

    # ---- glow accumulator (amber light on black, screened onto base) ----
    gl = Image.new("RGB", base.size, (0, 0, 0))
    gd = ImageDraw.Draw(gl)
    # broad halo around the summit
    hr = unit * 2.2
    gd.ellipse([top[0] - hr, top[1] - hr * 1.12, top[0] + hr, top[1] + hr * 0.82],
               fill=lerp((0, 0, 0), GLOW, 0.40))
    # the top stone glows — deep amber so it reads gold, not white-hot
    tw, th = top[2], top[3]
    gd.rounded_rectangle([top[0] - tw / 2, top[1] - th / 2, top[0] + tw / 2, top[1] + th / 2],
                         radius=th * 0.4, fill=lerp((0, 0, 0), GLOW, 0.82))
    if ember:
        er = unit * 0.46
        gd.ellipse([ember_cx - er, ember_cy - er, ember_cx + er, ember_cy + er], fill=(255, 206, 140))
        # upward beam
        bw = unit * 0.30
        gd.rectangle([ember_cx - bw, ember_cy - unit * 3.0, ember_cx + bw, ember_cy], fill=lerp((0, 0, 0), GLOW, 0.38))
    # multi-radius bloom
    bloom = Image.new("RGB", base.size, (0, 0, 0))
    for rad, inten in [(unit * 0.16, 0.90), (unit * 0.55, 0.78), (unit * 1.3, 0.58), (unit * 2.4, 0.40)]:
        b = gl.filter(ImageFilter.GaussianBlur(rad))
        if inten != 1.0:
            b = b.point(lambda v: int(v * inten))
        bloom = screen(bloom, b)
    base_img = screen(base.copy(), bloom)
    base.paste(base_img)

    # ---- stones (drawn crisp on top of glow) ----
    d = ImageDraw.Draw(base, "RGBA")
    for i, (scx, scy, w, h) in enumerate(centers):
        x0, y0 = scx - w / 2, scy - h / 2
        is_top = (i == len(centers) - 1)
        ctop, cbot = (EMBER_TOP, EMBER_BOT) if is_top else (STONE_TOP, STONE_BOT)
        r = h * 0.4
        tile = vgrad(int(w), int(h), ctop, cbot)
        base.paste(tile, (int(x0), int(y0)), rounded_mask(int(w), int(h), int(r)))
        # top specular highlight
        d.line([(x0 + r * 0.7, y0 + h * 0.16), (x0 + w - r * 0.7, y0 + h * 0.16)],
               fill=(255, 255, 255, 38 if not is_top else 70), width=max(1, int(unit * 0.02)))
        # faint base shadow line
        d.line([(x0 + r * 0.7, y0 + h - h * 0.14), (x0 + w - r * 0.7, y0 + h - h * 0.14)],
               fill=(0, 0, 0, 55), width=max(1, int(unit * 0.02)))
        # --- the "log line" tell: a severity dot + a redacted line ---
        dot_r = unit * 0.12
        dcx, dcy = x0 + h * 0.5, scy
        dcol = DOTS[i % len(DOTS)]
        d.ellipse([dcx - dot_r, dcy - dot_r, dcx + dot_r, dcy + dot_r], fill=dcol + (235,))
        # short message bar to the right of the dot
        ln_x0 = dcx + dot_r * 1.8
        ln_x1 = x0 + w - h * 0.4
        if ln_x1 > ln_x0:
            lc = lerp(ctop, (255, 255, 255), 0.18)
            d.rounded_rectangle([ln_x0, dcy - unit * 0.045, min(ln_x1, ln_x0 + w * 0.5), dcy + unit * 0.045],
                                radius=unit * 0.045, fill=lc + (60,))
    return ember_cx, ember_cy


def make_icon(px, bg=BG_BOT):
    s = px * 4
    img = Image.new("RGB", (s, s), bg)
    unit = s * 0.165
    # center the stack within the tile
    stack_h = 4 * (unit * 0.92) + 3 * (unit * 0.30) + unit * 0.5
    bottom_y = s * 0.5 + stack_h * 0.5 - unit * 0.2
    paint_stack(img, s * 0.5, bottom_y, unit)
    return img.resize((px, px), Image.LANCZOS)


def draw_spaced(d, xy, text, fnt, fill, tracking, anchor_mm=True):
    widths = [d.textlength(c, font=fnt) for c in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = xy[0] - total / 2 if anchor_mm else xy[0]
    y = xy[1]
    centers = []
    for c, w in zip(text, widths):
        d.text((x, y), c, font=fnt, fill=fill, anchor="lm")
        centers.append((x + w / 2, w))
        x += w + tracking
    return total, centers


# ============================ compose the sheet ============================
sheet = vgrad(W, H, BG_TOP, BG_BOT)
# subtle central lift (kept dark so the single warm light dominates)
lift = Image.new("RGB", (W, H), (0, 0, 0))
ImageDraw.Draw(lift).ellipse([W * 0.18, H * 0.04, W * 0.82, H * 0.55], fill=(12, 16, 25))
sheet = screen(sheet, lift.filter(ImageFilter.GaussianBlur(W * 0.18)))

d = ImageDraw.Draw(sheet, "RGBA")
M = 130 * SS

# corner ticks
tick = 26 * SS
for (cxp, cyp, dx, dy) in [(M, M, 1, 1), (W - M, M, -1, 1), (M, H - M, 1, -1), (W - M, H - M, -1, -1)]:
    d.line([(cxp, cyp), (cxp + dx * tick, cyp)], fill=FAINT + (180,), width=SS)
    d.line([(cxp, cyp), (cxp, cyp + dy * tick)], fill=FAINT + (180,), width=SS)

# top micro labels
f_micro = font("JetBrainsMono-Regular.ttf", 19 * SS)
d.text((M, M - 4 * SS), "CAIRN", font=font("JetBrainsMono-Bold.ttf", 19 * SS), fill=MUTE, anchor="lm")
d.text((M, M + 24 * SS), "structured log adapter · mcp", font=f_micro, fill=FAINT, anchor="lm")
d.text((W - M, M - 4 * SS), "fig.01", font=f_micro, fill=FAINT, anchor="rm")
d.text((W - M, M + 24 * SS), "luminous sediment", font=f_micro, fill=FAINT, anchor="rm")

# hero mark
unit = 150 * SS
ecx, ecy = paint_stack(sheet, W * 0.5, 980 * SS, unit)

# wordmark "cairn" with an amber ember as the tittle of the i
d = ImageDraw.Draw(sheet, "RGBA")
f_word = font("GeistMono-Bold.ttf", 168 * SS)
total, centers = draw_spaced(d, (W * 0.5, 1235 * SS), "cairn", f_word, INK, 10 * SS)
i_cx = centers[2][0]
# amber tittle (glow + core) over the i
tit = Image.new("RGB", sheet.size, (0, 0, 0))
td = ImageDraw.Draw(tit)
tr = 17 * SS
tit_y = 1140 * SS
td.ellipse([i_cx - tr, tit_y - tr, i_cx + tr, tit_y + tr], fill=GLOW)
sheet = screen(sheet, tit.filter(ImageFilter.GaussianBlur(19 * SS)))
d = ImageDraw.Draw(sheet, "RGBA")
d.ellipse([i_cx - tr, tit_y - tr, i_cx + tr, tit_y + tr], fill=(255, 208, 144, 255))

# tagline
f_tag = font("JetBrainsMono-Regular.ttf", 28 * SS)
draw_spaced(d, (W * 0.5, 1338 * SS), "evidence, not instructions", f_tag, (150, 160, 178), 7 * SS)

# hairline
hy = 1492 * SS
d.line([(M, hy), (W - M, hy)], fill=FAINT + (120,), width=SS)
d.text((M, hy - 22 * SS), "the mark, applied", font=f_micro, fill=FAINT, anchor="lm")
d.text((W - M, hy - 22 * SS), "favicon · 96 56 32 px", font=f_micro, fill=FAINT, anchor="rm")

# favicon trio (chips)
sizes = [150 * SS, 96 * SS, 60 * SS]
labels = ["96", "56", "32"]
gapc = 70 * SS
totw = sum(sizes) + gapc * (len(sizes) - 1)
x = W * 0.5 - totw / 2
chip_cy = 1660 * SS
for s, lab in zip(sizes, labels):
    ic = make_icon(int(s))
    # rounded chip with hairline border
    pad = int(s * 0.16)
    cw = int(s) + pad * 2
    chip = Image.new("RGB", (cw, cw), BG_BOT)
    chip.paste(make_icon(int(s)), (pad, pad))
    cm = rounded_mask(cw, cw, int(cw * 0.18))
    cx0 = int(x - pad)
    cy0 = int(chip_cy - cw / 2)
    sheet.paste(chip, (cx0, cy0), cm)
    bd = ImageDraw.Draw(sheet, "RGBA")
    bd.rounded_rectangle([cx0, cy0, cx0 + cw - 1, cy0 + cw - 1], radius=int(cw * 0.18),
                         outline=FAINT + (150,), width=SS)
    bd.text((x + s / 2, chip_cy + cw / 2 + 6 * SS), lab + " px", font=f_micro, fill=FAINT, anchor="ma")
    x += s + gapc

# hex swatches (specimen nod), bottom
d = ImageDraw.Draw(sheet, "RGBA")
swatches = [("#0A0D14", BG_BOT), ("#46566F", STONE_TOP), ("#FFB05C", GLOW)]
sw = 30 * SS
sx = M
syc = H - M - 6 * SS
for hexv, col in swatches:
    d.rounded_rectangle([sx, syc - sw, sx + sw, syc], radius=6 * SS, fill=col)
    d.text((sx + sw + 12 * SS, syc - sw / 2), hexv, font=f_micro, fill=MUTE, anchor="lm")
    sx += sw + 12 * SS + 118 * SS
d.text((W - M, syc - sw / 2), "appwood — open source", font=f_micro, fill=FAINT, anchor="rm")

# ---- downsample & save ----
final = sheet.resize((W // SS, H // SS), Image.LANCZOS)
final.save(HERE / "cairn-logo.png")

# standalone assets
make_icon(512).save(HERE / "cairn-icon-512.png")
make_icon(64).save(HERE / "cairn-favicon-64.png")
print("saved sheet + icons; sheet size", final.size)

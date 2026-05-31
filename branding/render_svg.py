"""Cairn — vector (SVG) icon + lockup. Text is converted to real paths via
fontTools, so the mark is font-independent and infinitely scalable. Glow is
built from radialGradients (no filters) for maximum renderer compatibility."""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

HERE = Path(__file__).resolve().parent
# Fonts are not bundled. Set CAIRN_FONTS_DIR to a directory containing
# GeistMono-Bold.ttf and JetBrainsMono-Regular.ttf, or drop them in ./fonts.
FONTS = os.environ.get("CAIRN_FONTS_DIR", str(HERE / "fonts"))
OUT = HERE

WIDTHS = [3.45, 2.82, 2.16, 1.30]
DOTS = ["#6CC4B2", "#A896E8", "#FFC478", "#E48E96"]
STONE_TOP, STONE_BOT = "#46566F", "#212C40"
EMBER_TOP, EMBER_BOT = "#FFCD8A", "#E79034"
GLOW = "#FFB05C"
INK, MUTE, FAINT = "#E9EEF7", "#8893A6", "#56627A"


class SVG:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.defs, self.body, self.n = [], [], 0

    def uid(self, p="g"):
        self.n += 1
        return f"{p}{self.n}"

    def lin_v(self, c0, c1):
        i = self.uid("lg")
        self.defs.append(
            f'<linearGradient id="{i}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{c0}"/><stop offset="1" stop-color="{c1}"/></linearGradient>')
        return i

    def radial(self, stops):
        i = self.uid("rg")
        s = "".join(f'<stop offset="{o}" stop-color="{c}" stop-opacity="{a}"/>' for o, c, a in stops)
        self.defs.append(f'<radialGradient id="{i}" cx="0.5" cy="0.5" r="0.5">{s}</radialGradient>')
        return i

    def stack(self, cx, bottom_y, unit):
        sh, gap = unit * 0.92, unit * 0.30
        cells, y = [], bottom_y
        for wm in WIDTHS:
            w = unit * wm
            cells.append((cx, y - sh / 2, w, sh))
            y -= (sh + gap)
        top = cells[-1]
        tx, ty, tw, th = top
        ecx, ecy = tx, ty - th * 0.5 - unit * 0.36
        # glow: broad halo + ember, as radial-filled ellipses
        halo = self.radial([(0, GLOW, "0.50"), (0.42, GLOW, "0.16"), (1, GLOW, "0")])
        hr = unit * 2.3
        self.body.append(f'<ellipse cx="{tx:.1f}" cy="{ty:.1f}" rx="{hr:.1f}" ry="{hr:.1f}" fill="url(#{halo})"/>')
        emb = self.radial([(0, "#FFE0B0", "0.95"), (0.5, GLOW, "0.45"), (1, GLOW, "0")])
        er = unit * 1.2
        self.body.append(f'<ellipse cx="{ecx:.1f}" cy="{ecy:.1f}" rx="{er:.1f}" ry="{er:.1f}" fill="url(#{emb})"/>')
        # stones
        for i, (scx, scy, w, h) in enumerate(cells):
            is_top = i == len(cells) - 1
            grad = self.lin_v(*( (EMBER_TOP, EMBER_BOT) if is_top else (STONE_TOP, STONE_BOT)))
            x0, y0 = scx - w / 2, scy - h / 2
            r = h * 0.4
            self.body.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r:.1f}" fill="url(#{grad})"/>')
            # top specular + base shadow
            self.body.append(f'<rect x="{x0+r*0.7:.1f}" y="{y0+h*0.14:.1f}" width="{w-r*1.4:.1f}" height="{max(1,unit*0.02):.1f}" rx="{unit*0.02:.1f}" fill="#FFFFFF" opacity="{0.28 if is_top else 0.16}"/>')
            # severity dot
            dr = unit * 0.12
            dcx, dcy = x0 + h * 0.5, scy
            self.body.append(f'<circle cx="{dcx:.1f}" cy="{dcy:.1f}" r="{dr:.1f}" fill="{DOTS[i%4]}"/>')
            # message line
            lx0 = dcx + dr * 1.8
            lw = min(x0 + w - h * 0.4, lx0 + w * 0.5) - lx0
            if lw > 0:
                self.body.append(f'<rect x="{lx0:.1f}" y="{dcy-unit*0.05:.1f}" width="{lw:.1f}" height="{unit*0.1:.1f}" rx="{unit*0.05:.1f}" fill="#FFFFFF" opacity="0.12"/>')
        return ecx, ecy

    def text(self, font_path, s, size, x, y, tracking, color, opacity=1.0):
        f = TTFont(font_path)
        upm = f["head"].unitsPerEm
        scale = size / upm
        gs, cmap, hmtx = f.getGlyphSet(), f.getBestCmap(), f["hmtx"]
        cx = x
        centers = []
        for ch in s:
            gn = cmap.get(ord(ch))
            if gn is None:
                cx += size * 0.6 + tracking
                continue
            pen = SVGPathPen(gs)
            gs[gn].draw(pen)
            d = pen.getCommands()
            adv = hmtx[gn][0] * scale
            if d:
                self.body.append(
                    f'<path d="{d}" fill="{color}" opacity="{opacity}" '
                    f'transform="translate({cx:.2f} {y:.2f}) scale({scale:.5f} {-scale:.5f})"/>')
            centers.append((cx + adv / 2, adv))
            cx += adv + tracking
        return centers

    def tittle(self, cx, cy, r):
        g = self.radial([(0, "#FFE0B0", "0.9"), (0.5, GLOW, "0.4"), (1, GLOW, "0")])
        self.body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r*3.2:.1f}" fill="url(#{g})"/>')
        self.body.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="#FFCF90"/>')

    def render(self, bg_rect=True, rx=0):
        bg = ""
        if bg_rect:
            grad = self.lin_v("#0C1018", "#070A10")
            bg = f'<rect x="0" y="0" width="{self.w}" height="{self.h}" rx="{rx}" fill="url(#{grad})"/>'
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}" role="img" aria-label="cairn">'
                f'<defs>{"".join(self.defs)}</defs>{bg}{"".join(self.body)}</svg>')


# ---------- icon ----------
ic = SVG(512, 512)
u = 512 * 0.165
sh, gap = u * 0.92, u * 0.30
stack_h = 4 * sh + 3 * gap + u * 0.5
bottom_y = 512 * 0.5 + stack_h * 0.5 - u * 0.2
ic.stack(512 * 0.5, bottom_y, u)
icon_svg = ic.render(bg_rect=True, rx=512 * 0.18)

# ---------- lockup / banner ----------
W, H = 1780, 660
lk = SVG(W, H)
lk.stack(420, 598, 92)
# wordmark
gm = f"{FONTS}/GeistMono-Bold.ttf"
jb = f"{FONTS}/JetBrainsMono-Regular.ttf"
centers = lk.text(gm, "cairn", 188, 760, 286, 12, INK)
icx = centers[2][0]
lk.tittle(icx, 286 - 99, 18)
lk.text(jb, "evidence, not instructions", 33, 766, 410, 6, "#96A0B2")
lk.text(jb, "structured log adapter · mcp", 25, 766, 466, 5, FAINT)
lockup_svg = lk.render(bg_rect=True, rx=0)

for name, svg in [("cairn-icon.svg", icon_svg), ("cairn-lockup.svg", lockup_svg)]:
    ET.fromstring(svg)  # validate well-formedness
    with open(OUT / name, "w") as fh:
        fh.write(svg)
    print(f"wrote {name}  ({len(svg)} bytes, well-formed)")

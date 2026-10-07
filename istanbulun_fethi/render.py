# -*- coding: utf-8 -*-
"""
İstanbul'un Fethi: 1453 — Matriks Tarih
2D animasyonlu video üretici.

Kullanım:
    python3 render.py            # tam video
    python3 render.py --onizleme # her sahneden birer kare (build/onizleme/)

Değiştirilebilir dosyalar (bu klasöre koyun, tekrar çalıştırın):
    logo.png              -> kanal logosu (şeffaf PNG önerilir)
    ses/01.mp3 ... 18.mp3 -> DubVoice seslendirmeleri (sahne sırasına göre; .wav/.m4a da olur)
    muzik.mp3             -> Flow Music fon müziği (yoksa sentetik ambiyans üretilir)
Ses dosyası olmayan sahneler için geçici espeak-ng Türkçe sesi kullanılır.
Sahne süreleri ses uzunluğuna göre otomatik ayarlanır.
"""
import glob
import math
import os
import random
import re
import subprocess
import sys
import wave
from functools import lru_cache
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from senaryo import SAHNELER

W, H, FPS = 1280, 720, 24
BASE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(BASE, "build")
CIKTI = os.path.join(BASE, "cikti")
OUT = os.path.join(CIKTI, "Istanbulun_Fethi_1453_MatriksTarih.mp4")
LEAD, TAIL = 0.6, 1.0
ESPEAK_HIZ = "172"

GOLD = (212, 175, 55)
RED = (178, 34, 34)
OTT_RED = (200, 16, 46)
CREAM = (240, 228, 200)

FONT_SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"


# ---------------------------------------------------------------- yardımcılar
@lru_cache(None)
def fnt(size, serif=False):
    return ImageFont.truetype(FONT_SERIF if serif else FONT_SANS, size)


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def seg(p, a, b):
    """p'nin [a,b] aralığındaki ilerlemesi (0..1, yumuşatılmış)."""
    return ease((p - a) / (b - a)) if b > a else float(p >= a)


def mix(c1, c2, k):
    return tuple(int(a + (b - a) * k) for a, b in zip(c1, c2))


@lru_cache(64)
def grad(top, bot):
    a = np.linspace(0, 1, H)[:, None, None]
    arr = (np.array(top)[None, None, :] * (1 - a) + np.array(bot)[None, None, :] * a)
    arr = np.repeat(arr, W, axis=1).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def bg(img, top, bot):
    img.paste(grad(tuple(top), tuple(bot)))


def text(d, xy, s, size, fill=CREAM, anchor="mm", serif=False, stroke=2, sfill=(0, 0, 0)):
    d.text(xy, s, font=fnt(size, serif), fill=fill, anchor=anchor,
           stroke_width=stroke, stroke_fill=sfill)


def wrap(s, font, width):
    words, lines, cur = s.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if font.getlength(t) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def label(d, x, y, s, a=1.0, size=24, fill=CREAM):
    if a <= 0:
        return
    f = fnt(size)
    w = f.getlength(s)
    d.rounded_rectangle([x - w / 2 - 12, y - size * 0.75, x + w / 2 + 12, y + size * 0.75],
                        8, fill=(10, 10, 20, int(170 * a)), outline=GOLD + (int(255 * a),), width=2)
    d.text((x, y), s, font=f, fill=fill + (int(255 * a),), anchor="mm")


# ---------------------------------------------------------------- logo
_LOGO = None


def logo_img():
    global _LOGO
    if _LOGO is None:
        p = os.path.join(BASE, "logo.png")
        _LOGO = Image.open(p).convert("RGBA") if os.path.exists(p) else False
    return _LOGO


def draw_logo(img, cx, cy, h, a=1.0, with_text=True):
    if a <= 0:
        return
    L = logo_img()
    if L:
        w = int(L.width * h / L.height)
        im = L.resize((w, int(h)), Image.LANCZOS)
        if a < 1:
            im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
        img.paste(im, (int(cx - w / 2), int(cy - h / 2)), im)
        return
    # geçici logo: altıgen monogram + yazı
    d = ImageDraw.Draw(img, "RGBA")
    r = h / 2
    tw = fnt(int(h * 0.42), True).getlength("MATRİKS TARİH") if with_text else 0
    ox = cx - (tw + h * 0.3) / 2 if with_text else cx
    pts = [(ox + r * math.cos(math.radians(60 * k + 30)), cy + r * math.sin(math.radians(60 * k + 30))) for k in range(6)]
    d.polygon(pts, fill=(15, 15, 25, int(230 * a)), outline=GOLD + (int(255 * a),), width=max(2, int(h / 20)))
    d.text((ox, cy), "MT", font=fnt(int(h * 0.42), True), fill=GOLD + (int(255 * a),), anchor="mm")
    if with_text:
        d.text((ox + r + h * 0.25, cy), "MATRİKS TARİH", font=fnt(int(h * 0.42), True),
               fill=CREAM + (int(255 * a),), anchor="lm", stroke_width=2, stroke_fill=(0, 0, 0, int(200 * a)))


# ---------------------------------------------------------------- çizim öğeleri
def stars(d, seed, n, t, a=1.0, ymax=0.6):
    r = random.Random(seed)
    for _ in range(n):
        x, y = r.random() * W, r.random() * H * ymax
        b = 0.6 + 0.4 * math.sin(t * 2 + r.random() * 6.28)
        s = r.choice([1, 1, 1.5, 2])
        d.ellipse([x - s, y - s, x + s, y + s], fill=(255, 255, 230, int(255 * a * b)))


def sea(d, y0, col, t, line=(255, 255, 255, 50), speed=20):
    d.rectangle([0, y0, W, H], fill=col)
    for k in range(10):
        y = y0 + 8 + k * (k + 6) * 1.6
        if y > H:
            break
        off = (t * speed * (1 + k * 0.15) + k * 37) % 60
        for x in range(-60, W + 60, 60):
            xx = x + off
            d.arc([xx, y - 4, xx + 30 + k * 2, y + 4], 200, 340, fill=line, width=2)


def walls(d, x0, x1, gy, h, col=(150, 135, 110), dark=(110, 95, 75), tower_every=170,
          tw=56, tex=50, damage=0.0, seed=3, breach=None):
    d.rectangle([x0, gy - h, x1, gy], fill=col)
    for y in range(int(gy - h + 18), int(gy), 22):  # taş sıraları
        d.line([x0, y, x1, y], fill=dark + (90,), width=1)
    for x in range(int(x0), int(x1), 22):
        d.rectangle([x, gy - h - 13, x + 12, gy - h], fill=col)
    for x in range(int(x0 + tower_every / 2), int(x1), tower_every):
        d.rectangle([x - tw / 2, gy - h - tex, x + tw / 2, gy], fill=dark)
        for cx in range(int(x - tw / 2), int(x + tw / 2), 14):
            d.rectangle([cx, gy - h - tex - 12, cx + 8, gy - h - tex], fill=dark)
        d.rectangle([x - 3, gy - h - tex + 18, x + 3, gy - h - tex + 34], fill=(30, 25, 20))
    r = random.Random(seed)
    for _ in range(int(damage * 14)):
        x = r.uniform(x0, x1)
        w = r.uniform(18, 45)
        dep = r.uniform(15, h * 0.55)
        d.polygon([(x - w, gy - h - 14), (x - w * 0.4, gy - h + dep), (x + w * 0.3, gy - h + dep * 0.7),
                   (x + w, gy - h - 14)], fill=mix(dark, (0, 0, 0), 0.45))
        d.line([(x, gy - h + dep * 0.6), (x + r.uniform(-20, 20), gy - h + dep + 25)], fill=(60, 50, 40), width=2)
    if breach:
        bx, bw = breach
        d.polygon([(bx - bw, gy - h - 60), (bx - bw * 0.5, gy - 40), (bx + bw * 0.6, gy - 30),
                   (bx + bw, gy - h - 60)], fill=(25, 15, 20))
        for k in range(8):  # moloz
            rx = bx + r.uniform(-bw, bw)
            d.ellipse([rx - 12, gy - 18, rx + 12, gy + 4], fill=dark)


def dome(d, cx, by, r, col):
    d.pieslice([cx - r, by - r, cx + r, by + r], 180, 360, fill=col)


def minaret(d, x, gy, h, col, w=12):
    if h <= 2:
        return
    d.rectangle([x - w / 2, gy - h, x + w / 2, gy], fill=col)
    d.rectangle([x - w / 2 - 4, gy - h * 0.72, x + w / 2 + 4, gy - h * 0.72 + 5], fill=col)
    d.polygon([(x - w / 2, gy - h), (x + w / 2, gy - h), (x, gy - h - w * 2.6)], fill=col)


def hagia(d, cx, gy, s, col, mins=0.0, roof=None, minaret_n=1):
    roof = roof or col
    d.rectangle([cx - 190 * s, gy - 110 * s, cx + 190 * s, gy], fill=col)
    d.rectangle([cx - 230 * s, gy - 60 * s, cx + 230 * s, gy], fill=col)
    for bx in (-150, -95, 95, 150):  # payandalar
        d.rectangle([cx + bx * s - 14 * s, gy - 150 * s, cx + bx * s + 14 * s, gy], fill=col)
    dome(d, cx - 95 * s, gy - 110 * s, 70 * s, roof)
    dome(d, cx + 95 * s, gy - 110 * s, 70 * s, roof)
    d.rectangle([cx - 85 * s, gy - 175 * s, cx + 85 * s, gy - 110 * s], fill=col)
    dome(d, cx, gy - 172 * s, 92 * s, roof)
    for k in range(-5, 6):  # kubbe kasnağı pencereleri
        x = cx + k * 14 * s
        d.rectangle([x - 3 * s, gy - 168 * s, x + 3 * s, gy - 150 * s], fill=(255, 230, 160, 160))
    d.line([cx, gy - 264 * s, cx, gy - 290 * s], fill=roof, width=max(2, int(3 * s)))
    if mins > 0:
        xs = [cx + 250 * s, cx - 250 * s, cx + 290 * s, cx - 290 * s][:minaret_n]
        for x in xs:
            minaret(d, x, gy, 300 * s * mins, col, 14 * s)


def skyline(d, gy, col, seed, x0=0, x1=W, hmin=30, hmax=90, domes=True):
    r = random.Random(seed)
    x = x0
    while x < x1:
        w = r.uniform(30, 70)
        h = r.uniform(hmin, hmax)
        d.rectangle([x, gy - h, x + w, gy], fill=col)
        if domes and r.random() < 0.25:
            dome(d, x + w / 2, gy - h, w / 2, col)
        elif r.random() < 0.5:
            d.polygon([(x - 3, gy - h), (x + w + 3, gy - h), (x + w / 2, gy - h - 18)], fill=col)
        x += w + r.uniform(-8, 6)


def ottoman_flag(d, x, y, s, t, pole=True, col=OTT_RED):
    if pole:
        d.line([x, y, x, y + 120 * s], fill=(70, 50, 30), width=max(2, int(4 * s)))
    pts_top, pts_bot = [], []
    for k in range(9):
        u = k / 8
        wv = math.sin(t * 5 + u * 5) * 6 * s * u
        pts_top.append((x + u * 90 * s, y + wv))
        pts_bot.append((x + u * 90 * s, y + 55 * s + wv))
    d.polygon(pts_top + pts_bot[::-1], fill=col)
    mx, my = x + 42 * s, y + 28 * s + math.sin(t * 5 + 2.3) * 3 * s
    d.ellipse([mx - 16 * s, my - 16 * s, mx + 16 * s, my + 16 * s], fill=(255, 255, 255))
    d.ellipse([mx - 9 * s, my - 13 * s, mx + 17 * s, my + 13 * s], fill=col)
    sx, sy = mx + 22 * s, my
    star = [(sx + (7 if k % 2 == 0 else 3) * s * math.cos(math.radians(-90 + 36 * k)),
             sy + (7 if k % 2 == 0 else 3) * s * math.sin(math.radians(-90 + 36 * k))) for k in range(10)]
    d.polygon(star, fill=(255, 255, 255))


def small_flag(d, x, y, s, t, col, cross=None):
    d.line([x, y, x, y + 26 * s], fill=(60, 40, 20), width=2)
    wv = math.sin(t * 6) * 3 * s
    d.polygon([(x, y), (x + 26 * s, y + wv), (x + 26 * s, y + 16 * s + wv), (x, y + 16 * s)], fill=col)
    if cross:
        d.line([x + 13 * s, y + wv / 2, x + 13 * s, y + 16 * s + wv / 2], fill=cross, width=max(1, int(3 * s)))
        d.line([x, y + 8 * s + wv / 2, x + 26 * s, y + 8 * s + wv / 2], fill=cross, width=max(1, int(3 * s)))


def carrack(d, x, y, s, t, hull=(95, 60, 35), sail=(235, 225, 200), flag=None, cross=None, tilt=0.0):
    bob = math.sin(t * 1.6 + x * 0.01) * 3 * s
    y += bob
    d.polygon([(x - 80 * s, y - 20 * s), (x + 85 * s, y - 25 * s), (x + 60 * s, y + 20 * s), (x - 60 * s, y + 20 * s)], fill=hull)
    d.rectangle([x - 80 * s, y - 40 * s, x - 45 * s, y - 20 * s], fill=hull)
    d.rectangle([x + 50 * s, y - 38 * s, x + 85 * s, y - 25 * s], fill=hull)
    d.line([x - 60 * s, y + 5 * s, x + 60 * s, y + 3 * s], fill=(60, 40, 20), width=max(1, int(3 * s)))
    for mx, mh in ((-20, 150), (35, 120)):
        d.line([x + mx * s, y - 20 * s, x + mx * s, y - mh * s], fill=(60, 40, 20), width=max(2, int(4 * s)))
        bulge = 8 * s
        d.polygon([(x + (mx - 34) * s, y - (mh - 15) * s), (x + (mx + 34) * s, y - (mh - 15) * s),
                   (x + (mx + 34) * s + bulge, y - (mh * 0.62) * s), (x + (mx - 34) * s + bulge, y - (mh * 0.62) * s)], fill=sail)
        d.polygon([(x + (mx - 40) * s, y - (mh * 0.58) * s), (x + (mx + 40) * s, y - (mh * 0.58) * s),
                   (x + (mx + 40) * s + bulge, y - 30 * s), (x + (mx - 40) * s + bulge, y - 30 * s)], fill=sail)
        if cross:
            cx = x + mx * s + bulge / 2
            cy = y - (mh * 0.4) * s
            d.line([cx, cy - 22 * s, cx, cy + 22 * s], fill=cross, width=max(2, int(6 * s)))
            d.line([cx - 26 * s, cy - 4 * s, cx + 26 * s, cy - 4 * s], fill=cross, width=max(2, int(6 * s)))
    if flag:
        small_flag(d, x - 20 * s, y - 175 * s, s, t, flag, cross)


def galley(d, x, y, s, t, hull=(120, 30, 30), sail=(230, 220, 200), flag=OTT_RED, oars=True, dirn=1):
    y += math.sin(t * 2 + x * 0.03) * 2 * s
    d.polygon([(x - 70 * s * dirn, y - 6 * s), (x + 75 * s * dirn, y - 12 * s), (x + 55 * s * dirn, y + 10 * s), (x - 55 * s * dirn, y + 10 * s)], fill=hull)
    if oars:
        for k in range(-4, 5):
            ox = x + k * 12 * s
            a = math.sin(t * 6 + k * 0.4) * 6 * s
            d.line([ox, y + 4 * s, ox - 8 * s + a, y + 22 * s], fill=(70, 45, 25), width=max(1, int(2 * s)))
    d.line([x, y - 8 * s, x, y - 75 * s], fill=(60, 40, 20), width=max(2, int(3 * s)))
    d.polygon([(x - 35 * s * dirn, y - 12 * s), (x + 30 * s * dirn, y - 78 * s), (x + 30 * s * dirn, y - 18 * s)], fill=sail)
    if flag:
        d.polygon([(x, y - 75 * s), (x + 18 * s * dirn, y - 70 * s), (x, y - 64 * s)], fill=flag)


def soldier(d, x, y, s, t, phase, body=(150, 30, 30), head=(220, 180, 140), hat=(240, 240, 240),
            spear=True, walk=True, shield=None, dirn=1):
    sw = math.sin(t * 9 + phase) if walk else 0
    by = y - abs(sw) * 2 * s
    d.line([x, by - 14 * s, x + sw * 6 * s, y], fill=(40, 30, 25), width=max(2, int(3 * s)))
    d.line([x, by - 14 * s, x - sw * 6 * s, y], fill=(40, 30, 25), width=max(2, int(3 * s)))
    d.rounded_rectangle([x - 7 * s, by - 38 * s, x + 7 * s, by - 12 * s], int(3 * s), fill=body)
    d.ellipse([x - 6 * s, by - 50 * s, x + 6 * s, by - 38 * s], fill=head)
    if hat:
        d.ellipse([x - 7 * s, by - 56 * s, x + 7 * s, by - 44 * s], fill=hat)
    if spear:
        d.line([x + 8 * s * dirn, by - 70 * s, x + 8 * s * dirn, by - 10 * s], fill=(90, 70, 40), width=max(1, int(2 * s)))
        d.polygon([(x + 8 * s * dirn, by - 80 * s), (x + 5 * s * dirn, by - 70 * s), (x + 11 * s * dirn, by - 70 * s)], fill=(200, 200, 210))
    if shield:
        d.ellipse([x + 2 * s * dirn - 8 * s, by - 34 * s, x + 2 * s * dirn + 8 * s, by - 16 * s], fill=shield)


def ox(d, x, y, s, t, phase=0, col=(110, 80, 55)):
    sw = math.sin(t * 6 + phase)
    for lx, ph in ((-22, 1), (-12, -1), (14, -1), (24, 1)):
        d.line([x + lx * s, y - 12 * s, x + lx * s + sw * ph * 4 * s, y + 6 * s], fill=(60, 40, 25), width=max(2, int(4 * s)))
    d.ellipse([x - 32 * s, y - 32 * s, x + 32 * s, y - 6 * s], fill=col)
    d.ellipse([x + 26 * s, y - 34 * s, x + 46 * s, y - 16 * s], fill=col)
    d.line([x + 32 * s, y - 32 * s, x + 28 * s, y - 42 * s], fill=(230, 220, 200), width=max(1, int(2 * s)))
    d.line([x + 40 * s, y - 32 * s, x + 44 * s, y - 42 * s], fill=(230, 220, 200), width=max(1, int(2 * s)))


def cannon(d, x, y, s, ang=-12, col=(70, 65, 60), length=120, bore=26):
    a = math.radians(ang)
    ca, sa = math.cos(a), math.sin(a)
    L, r0, r1 = length * s, bore * s, bore * 0.8 * s
    pts = [(x - sa * r0, y + ca * r0 * -1), (x + ca * L - sa * r1, y + sa * L - ca * r1),
           (x + ca * L + sa * r1, y + sa * L + ca * r1), (x + sa * r0, y + ca * r0)]
    d.polygon(pts, fill=col)
    for k in (0.25, 0.55, 0.85):
        px, py = x + ca * L * k, y + sa * L * k
        d.line([px - sa * r0 * 1.05, py + ca * r0 * -1.05, px + sa * r0 * 1.05, py + ca * r0 * 1.05], fill=(45, 40, 38), width=max(2, int(4 * s)))
    d.rectangle([x - 40 * s, y + 4 * s, x + 50 * s, y + 22 * s], fill=(90, 60, 35))
    for wx in (-25, 35):
        d.ellipse([x + wx * s - 15 * s, y + 8 * s, x + wx * s + 15 * s, y + 38 * s], fill=(70, 45, 25), outline=(40, 25, 15), width=2)
    return (x + ca * L, y + sa * L)


def smoke(d, x, y, age, s=1.0, col=(200, 200, 200)):
    if age < 0 or age > 3:
        return
    a = 1 - age / 3
    for k in range(5):
        r = (12 + age * 30 + k * 6) * s
        ox, oy = x + k * 9 * s * (1 + age) - 10 * s, y - age * 25 * s - k * 4 * s
        d.ellipse([ox - r, oy - r, ox + r, oy + r], fill=col + (int(110 * a),))


def flash(d, x, y, r, a):
    if a <= 0:
        return
    for k, c in enumerate([(255, 240, 180), (255, 180, 60), (255, 90, 20)]):
        rr = r * (1 - k * 0.25)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=c + (int(200 * a / (k + 1)),))


def torch(d, x, y, t, seed, s=1.0):
    f = 0.75 + 0.25 * math.sin(t * 13 + seed * 1.7) * math.sin(t * 7 + seed)
    d.ellipse([x - 22 * s * f, y - 22 * s * f, x + 22 * s * f, y + 22 * s * f], fill=(255, 140, 40, 45))
    d.ellipse([x - 5 * s, y - 9 * s * f, x + 5 * s, y + 4 * s], fill=(255, 190, 70))
    d.ellipse([x - 2.5 * s, y - 5 * s * f, x + 2.5 * s, y + 2 * s], fill=(255, 250, 200))


def tent(d, x, gy, w, h, col=(235, 225, 205), top=OTT_RED):
    d.polygon([(x - w / 2, gy), (x, gy - h), (x + w / 2, gy)], fill=col)
    d.polygon([(x - w * 0.18, gy - h * 0.64), (x, gy - h), (x + w * 0.18, gy - h * 0.64)], fill=top)
    d.line([x, gy - h, x, gy - h - 14], fill=(70, 50, 30), width=2)


def fire_particles(d, seed, t, n, x0, x1, ybase, rise=200):
    r = random.Random(seed)
    for _ in range(n):
        px = r.uniform(x0, x1)
        sp = r.uniform(30, 90)
        ph = r.random() * 10
        age = ((t + ph) * sp / rise) % 1
        y = ybase - age * rise
        x = px + math.sin((t + ph) * 3) * 10
        rr = (1 - age) * 4 + 1
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(255, int(120 + 100 * (1 - age)), 40, int(230 * (1 - age))))


def fade_black(d, a):
    if a > 0:
        d.rectangle([0, 0, W, H], fill=(0, 0, 0, int(255 * clamp(a))))


def bust(d, cx, cy, s, kind):
    """Stilize portre. kind: 'mehmed' veya 'konstantin'."""
    skin = (225, 185, 145)
    if kind == "mehmed":
        robe, trim = (150, 25, 35), GOLD
    else:
        robe, trim = (95, 40, 120), GOLD
    d.pieslice([cx - 170 * s, cy + 60 * s, cx + 170 * s, cy + 400 * s], 180, 360, fill=robe)
    d.polygon([(cx - 40 * s, cy + 70 * s), (cx + 40 * s, cy + 70 * s), (cx, cy + 200 * s)], fill=trim)
    d.polygon([(cx - 28 * s, cy + 70 * s), (cx + 28 * s, cy + 70 * s), (cx, cy + 170 * s)], fill=robe if kind == "konstantin" else (240, 230, 210))
    d.rectangle([cx - 22 * s, cy + 30 * s, cx + 22 * s, cy + 80 * s], fill=skin)
    d.ellipse([cx - 55 * s, cy - 70 * s, cx + 55 * s, cy + 60 * s], fill=skin)
    beard = (70, 45, 30) if kind == "mehmed" else (90, 85, 80)
    d.chord([cx - 55 * s, cy - 40 * s, cx + 55 * s, cy + 95 * s], 0, 180, fill=beard)
    d.ellipse([cx - 20 * s, cy + 22 * s, cx + 20 * s, cy + 34 * s], fill=(150, 80, 70))
    for ex in (-20, 20):
        d.ellipse([cx + ex * s - 7 * s, cy - 12 * s, cx + ex * s + 7 * s, cy - 2 * s], fill=(40, 30, 25))
        d.line([cx + ex * s - 12 * s, cy - 22 * s, cx + ex * s + 12 * s, cy - 24 * s], fill=beard, width=max(2, int(4 * s)))
    d.polygon([(cx, cy - 8 * s), (cx - 9 * s, cy + 14 * s), (cx + 9 * s, cy + 14 * s)], fill=(205, 160, 120))
    if kind == "mehmed":
        d.ellipse([cx - 85 * s, cy - 150 * s, cx + 85 * s, cy - 30 * s], fill=(245, 245, 240))
        for k in range(4):
            d.arc([cx - 85 * s, cy - 150 * s + k * 18 * s, cx + 85 * s, cy - 30 * s + k * 6 * s], 200, 340, fill=(205, 205, 200), width=max(1, int(3 * s)))
        d.ellipse([cx - 32 * s, cy - 185 * s, cx + 32 * s, cy - 115 * s], fill=OTT_RED)
        d.ellipse([cx - 8 * s, cy - 120 * s, cx + 8 * s, cy - 104 * s], fill=GOLD)
        d.polygon([(cx, cy - 115 * s), (cx + 18 * s, cy - 175 * s), (cx + 26 * s, cy - 170 * s)], fill=(250, 250, 250))
    else:
        d.chord([cx - 58 * s, cy - 95 * s, cx + 58 * s, cy - 10 * s], 180, 360, fill=(110, 90, 70))
        d.rectangle([cx - 62 * s, cy - 80 * s, cx + 62 * s, cy - 45 * s], fill=GOLD)
        for k in range(-2, 3):
            d.ellipse([cx + k * 24 * s - 6 * s, cy - 70 * s, cx + k * 24 * s + 6 * s, cy - 58 * s], fill=(160, 20, 40) if k % 2 else (40, 90, 160))
        d.chord([cx - 50 * s, cy - 125 * s, cx + 50 * s, cy - 35 * s], 180, 360, fill=GOLD)
        d.line([cx, cy - 125 * s, cx, cy - 150 * s], fill=GOLD, width=max(2, int(5 * s)))
        d.line([cx - 11 * s, cy - 140 * s, cx + 11 * s, cy - 140 * s], fill=GOLD, width=max(2, int(5 * s)))
        for k in range(-1, 2):  # prependulia
            d.line([cx + k * 52 * s, cy - 45 * s, cx + k * 54 * s, cy + 10 * s], fill=(240, 230, 210), width=max(1, int(3 * s)))


# ---------------------------------------------------------------- sahneler
def sc_intro(img, d, t, dur, p):
    bg(img, (8, 10, 22), (30, 20, 30))
    r = random.Random(7)
    chars = "1453MATRİKSTARİH"
    for col in range(32):
        x = col * 40 + 20
        sp = r.uniform(40, 120)
        off = r.uniform(0, H)
        for k in range(12):
            y = (off + t * sp + k * 32) % (H + 60) - 30
            a = int(50 * (k / 12))
            d.text((x, y), chars[(col + k) % len(chars)], font=fnt(20), fill=GOLD + (a,), anchor="mm")
    a = seg(t, 0.2, 1.6)
    ring = 120 + 40 * (1 - a)
    d.ellipse([W / 2 - ring, 250 - ring, W / 2 + ring, 250 + ring], outline=GOLD + (int(200 * a),), width=3)
    for k in range(24):
        ang = math.radians(k * 15 + t * 20)
        x1, y1 = W / 2 + math.cos(ang) * (ring + 8), 250 + math.sin(ang) * (ring + 8)
        x2, y2 = W / 2 + math.cos(ang) * (ring + 20), 250 + math.sin(ang) * (ring + 20)
        d.line([x1, y1, x2, y2], fill=GOLD + (int(160 * a),), width=2)
    draw_logo(img, W / 2, 250, 150 * (0.6 + 0.4 * a), a, with_text=False)
    d = ImageDraw.Draw(img, "RGBA")
    b = seg(t, 2.2, 3.8)
    if b > 0:
        d.text((W / 2, 455), "İSTANBUL'UN FETHİ", font=fnt(78, True), fill=GOLD + (int(255 * b),), anchor="mm",
               stroke_width=3, stroke_fill=(0, 0, 0, int(255 * b)))
        c = seg(t, 3.2, 4.6)
        d.text((W / 2, 545), "1 4 5 3", font=fnt(60, True), fill=(220, 50, 50, int(255 * c)), anchor="mm",
               stroke_width=3, stroke_fill=(0, 0, 0, int(255 * c)))
        d.line([W / 2 - 300 * c, 500, W / 2 + 300 * c, 500], fill=GOLD + (int(255 * c),), width=2)


def sc_dawn_city(img, d, t, dur, p):
    night = grad((8, 10, 30), (40, 30, 60))
    dawn = grad((40, 50, 100), (240, 140, 70))
    img.paste(Image.blend(night, dawn, ease(p * 1.1)))
    d = ImageDraw.Draw(img, "RGBA")
    stars(d, 11, 120, t, 1 - ease(p * 1.3))
    sun_y = 520 - 80 * p
    d.ellipse([900 - 60, sun_y - 60, 900 + 60, sun_y + 60], fill=(255, 200, 120, int(120 * p)))
    off = -p * 40
    skyline(d, 470, (35, 30, 50), 4, -50 + off, W + 50, 40, 100)
    hagia(d, 640 + off, 470, 0.9, (30, 25, 45))
    walls(d, -20, W + 20, 560, 70, (70, 60, 70), (55, 45, 58), tower_every=150)
    d.rectangle([0, 560, W, H], fill=(25, 18, 22))
    r = random.Random(5)
    for k in range(14):
        tent(d, r.uniform(0, W), 700 + r.uniform(-30, 10), r.uniform(70, 120), r.uniform(50, 80), (90, 70, 70), (110, 30, 30))
    for k in range(90):
        x = r.uniform(0, W)
        y = r.uniform(585, 715)
        torch(d, x, y, t, k, 0.6 + (y - 585) / 200)


def sc_map(img, d, t, dur, p):
    bg(img, (28, 70, 112), (20, 55, 95))
    d = ImageDraw.Draw(img, "RGBA")
    sea(d, 0, (28, 70, 112, 0), t, (255, 255, 255, 25), 8)
    land = (196, 178, 128)
    europe = [(0, 0), (640, 0), (668, 150), (698, 300), (712, 330), (722, 420), (692, 520), (600, 548),
              (420, 560), (200, 585), (0, 600)]
    asia = [(760, 0), (1280, 0), (1280, 620), (1000, 600), (820, 560), (765, 470), (772, 330), (742, 150)]
    d.polygon(europe, fill=land, outline=(120, 100, 70))
    d.polygon(asia, fill=(186, 170, 122), outline=(120, 100, 70))
    horn = [(712, 318), (600, 298), (480, 288), (380, 300), (382, 316), (480, 310), (600, 322), (704, 344)]
    d.polygon(horn, fill=(28, 70, 112))
    city = [(704, 346), (600, 326), (470, 316), (452, 320), (440, 546), (600, 546), (690, 518), (720, 420)]
    a = seg(p, 0.05, 0.15)
    pulse = 0.5 + 0.5 * math.sin(t * 3)
    d.polygon(city, fill=(230, 190, 80, int((90 + 50 * pulse) * a)), outline=GOLD + (int(255 * a),))
    label(d, 585, 435, "Konstantinopolis", a, 24)
    label(d, 250, 170, "AVRUPA", seg(p, 0.12, 0.2), 30)
    label(d, 1040, 430, "ASYA", seg(p, 0.12, 0.2), 30)
    label(d, 470, 272, "Haliç", seg(p, 0.22, 0.3), 22)
    label(d, 745, 90, "Boğaziçi", seg(p, 0.27, 0.35), 22)
    label(d, 640, 640, "Marmara Denizi", seg(p, 0.32, 0.4), 26)
    label(d, 650, 250, "Galata", seg(p, 0.36, 0.44), 18)
    w = seg(p, 0.45, 0.68)
    if w > 0:
        x0, y0, x1, y1 = 455, 318, 440, 548
        xe, ye = x0 + (x1 - x0) * w, y0 + (y1 - y0) * w
        d.line([x0, y0, xe, ye], fill=(90, 40, 30), width=9)
        d.line([x0, y0, xe, ye], fill=(220, 60, 40), width=5)
        for k in range(int(12 * w)):
            u = k / 12
            yy = y0 + (y1 - y0) * u
            xx = x0 + (x1 - x0) * u
            d.rectangle([xx - 8, yy - 4, xx + 2, yy + 4], fill=(90, 40, 30))
        label(d, 300, 430, "Theodosius Surları", seg(p, 0.55, 0.65), 22)
    k = seg(p, 0.72, 0.82)
    if k > 0:  # sur kesiti
        oy = -360
        d.rounded_rectangle([760, 470 + oy, 1260, 705 + oy], 12, fill=(20, 25, 35, int(225 * k)), outline=GOLD + (int(255 * k),), width=2)
        d.text((1010, 492 + oy), "Kara Surları — Kesit", font=fnt(20), fill=CREAM + (int(255 * k),), anchor="mm")
        g = (150, 135, 110, int(255 * k))
        d.rectangle([780, 640 + oy, 1240, 690 + oy], fill=(120, 100, 70, int(255 * k)))
        d.polygon([(790, 640 + oy), (850, 640 + oy), (840, 680 + oy), (800, 680 + oy)], fill=(40, 90, 140, int(255 * k)))
        d.rectangle([880, 600 + oy, 900, 640 + oy], fill=g)
        d.rectangle([960, 570 + oy, 990, 640 + oy], fill=g)
        d.rectangle([1080, 520 + oy, 1125, 640 + oy], fill=g)
        d.rectangle([1072, 505 + oy, 1133, 525 + oy], fill=g)
        for xx, s in ((820, "Hendek"), (890, "Siper"), (975, "Dış sur"), (1102, "İç sur")):
            d.text((xx, 700 - 4 + oy), s, font=fnt(14), fill=CREAM + (int(255 * k),), anchor="ms")


def sc_timeline(img, d, t, dur, p):
    bg(img, (52, 40, 26), (28, 20, 14))
    d = ImageDraw.Draw(img, "RGBA")
    for k in range(40):  # parşömen lekesi
        r = random.Random(k)
        x, y, rr = r.uniform(0, W), r.uniform(0, H), r.uniform(40, 160)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(90, 70, 40, 18))
    ev = [("626", "Avarlar"), ("674–678", "Emeviler"), ("717–718", "Emeviler"), ("813", "Bulgarlar"),
          ("1394–1402", "Yıldırım Bayezid"), ("1422", "II. Murad"), ("1453", "II. Mehmed")]
    lp = seg(p, 0.0, 0.12)
    y = 380
    d.line([90, y, 90 + 1100 * lp, y], fill=GOLD, width=4)
    for i, (yr, nm) in enumerate(ev):
        x = 130 + i * 170
        a = seg(p, 0.08 + i * 0.105, 0.13 + i * 0.105)
        if a <= 0:
            continue
        last = i == len(ev) - 1
        rr = (22 if last else 14) * a
        if last:
            glow = 40 + 10 * math.sin(t * 4)
            d.ellipse([x - glow, y - glow, x + glow, y + glow], fill=(255, 210, 90, 70))
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=GOLD if last else CREAM, outline=(60, 40, 20), width=3)
        up = i % 2 == 0
        ty = y - 70 if up else y + 70
        d.line([x, y + (-rr if up else rr), x, ty + (25 if up else -25)], fill=GOLD + (int(255 * a),), width=2)
        d.text((x, ty - (12 if up else -12)), yr, font=fnt(28 if last else 24, True), fill=(GOLD if last else CREAM) + (int(255 * a),), anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))
        d.text((x, ty + (22 if up else 46) - (12 if up else -12) * 0), nm, font=fnt(18), fill=CREAM + (int(255 * a),), anchor="mm")
        if not last:
            xa = seg(p, 0.12 + i * 0.105, 0.16 + i * 0.105)
            if xa > 0:
                L = 16 * xa
                d.line([x - L, y - L, x + L, y + L], fill=(220, 40, 40), width=6)
                d.line([x - L, y + L, x + L, y - L], fill=(220, 40, 40), width=6)
    a = seg(p, 0.86, 0.95)
    text(d, (W / 2, 545), "Fetih, nesiller boyu süren bir hayaldi.", 34, GOLD + (int(255 * a),), serif=True)


def sc_portraits(img, d, t, dur, p):
    bg(img, (60, 10, 18), (20, 5, 10))
    d = ImageDraw.Draw(img, "RGBA")
    k = seg(p, 0.55, 0.65)
    if k > 0:
        d.polygon([(W / 2 + 40, 0), (W, 0), (W, H), (W / 2 - 40, H)], fill=(40, 15, 60, int(255 * k)))
        d.line([W / 2 + 40, 0, W / 2 - 40, H], fill=GOLD + (int(255 * k),), width=4)
    mx = 640 - 300 * k
    a = seg(p, 0.0, 0.08)
    bust(d, mx, 260 + 30 * (1 - a), 0.95, "mehmed")
    text(d, (mx, 520), "II. MEHMED", 40, GOLD, serif=True)
    text(d, (mx, 562), "1432 – 1481  •  Tahta: 1451 (19 yaşında)", 20, CREAM)
    if k > 0:
        cx = 960 + 200 * (1 - k)
        bust(d, cx, 260, 0.95, "konstantin")
        text(d, (cx, 520), "XI. KONSTANTİN", 40, GOLD + (int(255 * k),), serif=True)
        text(d, (cx, 562), "1404 – 1453  •  Son Bizans İmparatoru", 20, CREAM + (int(255 * k),))
    b = seg(p, 0.25, 0.32) * (1 - seg(p, 0.5, 0.55))
    if b > 0:
        for i, s in enumerate(["Birkaç dil", "Matematik", "Tarih", "Askerlik"]):
            label(d, 1000, 220 + i * 70, s, seg(p, 0.25 + i * 0.03, 0.29 + i * 0.03) * b, 26)


def sc_fortress(img, d, t, dur, p):
    bg(img, (110, 165, 215), (200, 220, 235))
    d = ImageDraw.Draw(img, "RGBA")
    for k in range(5):  # bulutlar
        x = (k * 300 + t * 12) % (W + 300) - 150
        y = 80 + (k % 3) * 40
        for j in range(4):
            d.ellipse([x + j * 35 - 40, y - 22 - (j % 2) * 10, x + j * 35 + 40, y + 22], fill=(255, 255, 255, 200))
    d.polygon([(760, 470), (900, 360), (1080, 330), (1280, 380), (1280, 490)], fill=(95, 120, 80))
    # Anadolu Hisarı (karşı kıyı, tamam)
    d.rectangle([980, 300, 1030, 360], fill=(150, 140, 120))
    d.rectangle([960, 330, 1060, 360], fill=(150, 140, 120))
    for x in range(960, 1060, 12):
        d.rectangle([x, 323, x + 7, 330], fill=(150, 140, 120))
    label(d, 1010, 270, "Anadolu Hisarı", 1, 18)
    sea(d, 470, (40, 100, 150), t, (255, 255, 255, 60), 14)
    d.polygon([(0, 720), (0, 300), (180, 250), (380, 300), (560, 430), (700, 520), (720, 720)], fill=(105, 125, 70))
    b = seg(p, 0.08, 0.62)
    stone = (190, 180, 160)
    for (x, gy, w, h) in ((140, 300, 90, 230), (380, 345, 80, 190), (560, 470, 70, 150)):
        hh = h * b
        d.rectangle([x - w / 2, gy - hh, x + w / 2, gy + 40], fill=stone)
        if b < 1:
            for yy in range(int(gy - h), int(gy), 30):
                d.line([x - w / 2 - 10, yy, x + w / 2 + 10, yy], fill=(120, 90, 50), width=2)
            d.line([x - w / 2 - 10, gy - h, x - w / 2 - 10, gy + 20], fill=(120, 90, 50), width=3)
            d.line([x + w / 2 + 10, gy - h, x + w / 2 + 10, gy + 20], fill=(120, 90, 50), width=3)
        else:
            d.polygon([(x - w / 2 - 6, gy - hh), (x + w / 2 + 6, gy - hh), (x, gy - hh - w * 0.8)], fill=(110, 70, 50))
    wh = 80 * b
    d.polygon([(140, 340 - wh), (380, 380 - wh), (560, 500 - wh), (560, 500), (380, 380), (140, 340)], fill=(175, 165, 145))
    r = random.Random(9)
    for k in range(int(30 * (1 - seg(p, 0.6, 0.7)))):
        x0 = r.uniform(60, 640)
        x = x0 + math.sin(t * r.uniform(0.5, 1.5) + k) * 25
        gy = 300 + (x - 140) * 0.35 if x > 140 else 300
        gy = min(gy + 100, 690)
        soldier(d, x, gy, 0.55, t, k, (120, 90, 60), hat=(230, 230, 220), spear=False)
    ottoman_flag(d, 120, 300 - 230 * b - 140, 0.8, t)
    a = seg(p, 0.55, 0.62)
    label(d, 330, 120, "Rumeli Hisarı — 1452", a, 26)
    label(d, 330, 170, "≈ 4,5 ayda tamamlandı", seg(p, 0.6, 0.66), 20)
    c = seg(p, 0.72, 0.95)
    if c > 0:
        galley(d, 1350 - 600 * c, 560, 0.9, t, hull=(90, 60, 40), flag=(120, 40, 140), dirn=-1)
        if p > 0.82:
            smoke(d, 600, 360, (p - 0.82) * dur, 1.0)
            flash(d, 600, 360, 30, 1 - (p - 0.82) * dur * 2)
        label(d, 900, 650, "BOĞAZKESEN", seg(p, 0.82, 0.88), 34, GOLD)


def sc_cannon_forge(img, d, t, dur, p):
    if p < 0.48:
        bg(img, (40, 22, 14), (15, 8, 5))
        d = ImageDraw.Draw(img, "RGBA")
        f = 0.8 + 0.2 * math.sin(t * 9) * math.sin(t * 4.3)
        for k, c in enumerate([(255, 120, 30, 50), (255, 160, 50, 90), (255, 220, 120, 200)]):
            r = (160 - k * 45) * f
            d.ellipse([180 - r, 420 - r, 180 + r, 420 + r], fill=c)
        d.rectangle([90, 450, 270, 720], fill=(60, 40, 30))
        d.arc([110, 360, 250, 480], 180, 360, fill=(80, 55, 40), width=20)
        q = seg(p, 0.05, 0.4)
        d.line([250, 440, 380, 460, 420, 560], fill=(255, 170, 40), width=8, joint="curve")
        x0, x1, yy = 420, 1150, 560
        d.rounded_rectangle([x0, yy - 70, x1, yy + 70], 30, fill=(50, 40, 35), outline=(100, 80, 60), width=3)
        d.rounded_rectangle([x0 + 10, yy - 60, x0 + 10 + (x1 - x0 - 20) * q, yy + 60], 25, fill=(255, 140, 30))
        fire_particles(d, 3, t, 60, 300, 1100, 520, 260)
        label(d, 780, 160, "Usta Urban — Edirne, 1452", seg(p, 0.03, 0.1), 28)
        for i, s in enumerate(["Dev bronz toplar", "Yüzlerce kiloluk taş gülleler"]):
            label(d, 780, 230 + i * 55, s, seg(p, 0.2 + i * 0.08, 0.26 + i * 0.08), 22)
        fade_black(d, seg(p, 0.42, 0.48))
    else:
        bg(img, (230, 160, 100), (250, 210, 160))
        d = ImageDraw.Draw(img, "RGBA")
        d.ellipse([1000, 120, 1100, 220], fill=(255, 230, 170))
        d.polygon([(0, 470), (300, 420), (700, 450), (1000, 410), (1280, 440), (1280, 720), (0, 720)], fill=(150, 120, 80))
        d.rectangle([0, 540, W, 620], fill=(170, 140, 95))
        q = (p - 0.48) / 0.52
        sx = -500 + q * 1500
        for row in range(5):
            for j in range(2):
                ox(d, sx + 420 + row * 85, 580 + j * 26, 0.9, t, row + j)
        d.rectangle([sx - 60, 560, sx + 330, 585], fill=(100, 70, 40))
        for wx in range(int(sx - 40), int(sx + 330), 70):
            d.ellipse([wx - 20, 570, wx + 20, 610], fill=(80, 55, 30), outline=(50, 35, 20), width=3)
        d.rounded_rectangle([sx - 80, 495, sx + 340, 565], 28, fill=(90, 80, 60))
        d.ellipse([sx + 310, 500, sx + 360, 560], fill=(30, 25, 20))
        for k in range(10):
            soldier(d, sx - 150 + k * 60, 650, 0.8, t, k, (150, 30, 30))
        label(d, W / 2, 110, "Edirne  →  Konstantinopolis", seg(p, 0.52, 0.58), 30)
        label(d, W / 2, 165, "Onlarca çift öküz, yüzlerce asker", seg(p, 0.6, 0.66), 22)
        fade_black(d, 1 - seg(p, 0.48, 0.53))


def sc_chain(img, d, t, dur, p):
    bg(img, (30, 80, 120), (25, 70, 110))
    d = ImageDraw.Draw(img, "RGBA")
    sea(d, 0, (0, 0, 0, 0), t, (255, 255, 255, 25), 10)
    d.polygon([(0, 0), (W, 0), (W, 120), (1000, 180), (700, 200), (300, 210), (0, 220)], fill=(180, 160, 115))
    d.polygon([(0, 470), (300, 460), (700, 470), (980, 500), (1080, 560), (1100, 720), (0, 720)], fill=(185, 165, 120))
    walls(d, 0, 980, 500, 26, (150, 135, 110), (120, 105, 80), 120, 30, 16)
    skyline(d, 210, (150, 120, 90), 21, 300, 950, 15, 40)
    d.rectangle([880, 105, 920, 190], fill=(140, 120, 100))
    d.polygon([(870, 105), (930, 105), (900, 60)], fill=(110, 70, 50))
    text(d, (900, 40), "Galata", 20)
    text(d, (450, 600), "Konstantinopolis", 28)
    text(d, (380, 340), "HALİÇ", 34, (200, 230, 255))
    q = seg(p, 0.04, 0.3)
    x0, y0, x1, y1 = 1010, 190, 1010, 500
    n = 26
    for k in range(int(n * q)):
        u = (k + 0.5) / n
        y = y0 + (y1 - y0) * u
        x = x0 + math.sin(u * math.pi) * 30 + math.sin(t * 2 + k) * 2
        if k % 2:
            d.ellipse([x - 5, y - 9, x + 5, y + 9], outline=(60, 60, 65), width=4)
        else:
            d.ellipse([x - 9, y - 5, x + 9, y + 5], outline=(70, 70, 75), width=4)
        if k % 5 == 2:
            d.ellipse([x - 12, y - 7, x + 12, y + 7], fill=(120, 80, 40))
    if q > 0:
        label(d, 1130, 330, "Demir Zincir", seg(p, 0.1, 0.18), 22)
    for k in range(5):
        galley(d, 1150 + (k % 2) * 70, 230 + k * 60, 0.45, t + k, flag=OTT_RED, dirn=-1)
    g = seg(p, 0.33, 0.58)
    if g > 0:
        for k in range(3):
            carrack(d, 800 - 520 * g + k * 120, 300 + k * 60, 0.45, t, flag=(255, 255, 255), cross=(200, 20, 30))
        label(d, 420, 130 + 0, "Giustiniani + 700 Cenevizli asker", seg(p, 0.42, 0.5), 22)
    b = seg(p, 0.62, 0.7)
    if b > 0:
        d.rounded_rectangle([140, 230, 1140, 560], 18, fill=(10, 12, 22, int(230 * b)), outline=GOLD + (int(255 * b),), width=3)
        text(d, (640, 275), "Güçler Dengesi (tahmini)", 30, GOLD + (int(255 * b),), serif=True)
        g1 = seg(p, 0.7, 0.8)
        g2 = seg(p, 0.75, 0.9)
        d.rectangle([380, 340, 380 + 75 * g1, 400], fill=(130, 60, 160))
        d.rectangle([380, 440, 380 + 700 * g2, 500], fill=OTT_RED)
        d.text((360, 370), "Bizans", font=fnt(24), fill=CREAM + (int(255 * b),), anchor="rm")
        d.text((360, 470), "Osmanlı", font=fnt(24), fill=CREAM + (int(255 * b),), anchor="rm")
        if g1 > 0.5:
            d.text((470, 370), "≈ 7.000 – 8.000", font=fnt(22), fill=CREAM, anchor="lm")
        if g2 > 0.5:
            d.text((1060, 470), "≈ 80.000 – 100.000", font=fnt(22), fill=CREAM, anchor="rm")


def _ballistic(d, x0, y0, x1, y1, u):
    x = x0 + (x1 - x0) * u
    y = y0 + (y1 - y0) * u - math.sin(u * math.pi) * 120
    d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=(40, 40, 40))


def sc_siege_start(img, d, t, dur, p):
    bg(img, (120, 110, 130), (230, 170, 120))
    d = ImageDraw.Draw(img, "RGBA")
    d.polygon([(0, 560), (0, 450), (300, 430), (600, 470), (700, 560)], fill=(140, 120, 90))
    for k, x in enumerate(range(20, 600, 70)):
        tent(d, x, 470 + (k % 2) * 15, 60, 45)
    tent(d, 300, 450, 150, 110, (240, 230, 210), OTT_RED)
    ottoman_flag(d, 300, 300, 0.6, t)
    label(d, 300, 255, "Sultan'ın otağı", seg(p, 0.05, 0.12), 18)
    walls(d, 700, 1300, 600, 150, damage=p, seed=8)
    d.rectangle([0, 600, W, H], fill=(110, 90, 60))
    d.rectangle([680, 600, 720, 640], fill=(60, 80, 110))
    period = 4.0
    for i, (cx, ofs) in enumerate(((120, 0.0), (300, 1.3), (480, 2.6))):
        tip = cannon(d, cx, 610, 1.0, -14)
        lt = (t + ofs) % period
        fired = t + ofs >= period * 0.5
        if fired:
            flash(d, tip[0], tip[1], 45, 1 - lt * 3)
            smoke(d, tip[0], tip[1], lt, 1.0)
            u = lt / 0.9
            tx = 780 + ((int((t + ofs) / period) * 97 + i * 53) % 400)
            if u < 1:
                _ballistic(d, tip[0], tip[1], tx, 500, u)
            else:
                smoke(d, tx, 500, (lt - 0.9) * 1.5, 1.2, (170, 150, 120))
                flash(d, tx, 500, 50, 1 - (lt - 0.9) * 4)
    label(d, 1000, 380, "Topkapı (Aziz Romanos Kapısı)", seg(p, 0.1, 0.18), 18)
    k = seg(p, 0.7, 0.8)
    if k > 0:  # gece onarımı
        d.rectangle([0, 0, W, H], fill=(10, 15, 40, int(150 * k)))
        for j in range(6):
            x = 760 + j * 85
            torch(d, x, 430, t, j, 1.2)
            d.rectangle([x - 18, 440, x + 18, 470], fill=(120, 85, 50, int(255 * k)))
        label(d, 1000, 120, "Geceleri yıkılan surlar onarılıyordu", k, 22)


def sc_naval(img, d, t, dur, p):
    bg(img, (120, 170, 220), (210, 225, 240))
    d = ImageDraw.Draw(img, "RGBA")
    sea(d, 380, (35, 95, 150), t, (255, 255, 255, 70), 30)
    d.polygon([(0, 380), (200, 360), (420, 372), (520, 380)], fill=(120, 130, 100))
    q = p
    for k in range(4):
        byz = k == 3
        x = -250 + q * 1250 + k * 140 - (k % 2) * 40
        y = 470 + (k % 2) * 60 + k * 10
        carrack(d, x, y, 0.9 - k * 0.05, t + k, flag=(120, 30, 140) if byz else (255, 255, 255),
                cross=None if byz else (200, 20, 30), sail=(235, 225, 200) if not byz else (230, 210, 160))
    r = random.Random(4)
    for k in range(12):
        bx = r.uniform(-100, 1300)
        x = bx + math.sin(t * 0.5 + k) * 60
        y = r.uniform(420, 700)
        dirn = 1 if x < (-250 + q * 1250 + 200) else -1
        galley(d, x, y, 0.5 + (y - 420) / 500, t + k, dirn=dirn)
        pt = (t + k * 0.7) % 5
        if pt < 2.5 and k % 3 == 0:
            smoke(d, x + 40 * dirn, y - 20, pt, 0.6)
    for k in range(20):  # oklar
        u = ((t * 0.8 + k * 0.13) % 1)
        x = 100 + k * 55 + u * 120
        y = 520 - math.sin(u * math.pi) * 120 + (k % 4) * 20
        d.line([x, y, x + 14, y - 4 + u * 8], fill=(40, 30, 20), width=2)
    a = seg(p, 0.45, 0.5) * (1 - seg(p, 0.72, 0.76))
    if a > 0:
        d.polygon([(0, 720), (0, 560), (280, 600), (360, 720)], fill=(170, 150, 110))
        hx, hy = 200, 620
        d.ellipse([hx - 55, hy - 50, hx + 45, hy - 15], fill=(240, 240, 235))
        d.ellipse([hx + 30, hy - 75, hx + 70, hy - 40], fill=(240, 240, 235))
        bust_s = 0.25
        d.rectangle([hx - 12, hy - 95, hx + 12, hy - 50], fill=(150, 25, 35))
        d.ellipse([hx - 18, hy - 125, hx + 18, hy - 92], fill=(245, 245, 240))
        label(d, 230, 480, "Sultan Mehmed denize atını sürdü", a, 20)
    label(d, 640, 70, "Ceneviz ve Bizans gemileri", seg(p, 0.06, 0.14) * (1 - seg(p, 0.4, 0.45)), 24)
    label(d, 640, 70, "Baltaoğlu Süleyman Bey görevden alındı", seg(p, 0.82, 0.9), 24)


def _hill(x):
    return 470 - 230 * math.exp(-((x - 600) / 300) ** 2)


def sc_ships_over_land(img, d, t, dur, p):
    night = grad((6, 10, 30), (30, 30, 60))
    dawn = grad((60, 70, 120), (230, 150, 100))
    img.paste(Image.blend(night, dawn, seg(p, 0.75, 1.0)))
    d = ImageDraw.Draw(img, "RGBA")
    stars(d, 2, 140, t, 1 - seg(p, 0.75, 0.95))
    d.ellipse([1040, 70, 1110, 140], fill=(250, 245, 220, int(255 * (1 - seg(p, 0.8, 1)))))
    sea(d, 560, (20, 40, 70), t, (255, 255, 255, 40), 10)
    pts = [(x, _hill(x)) for x in range(0, W + 20, 20)]
    d.polygon([(0, 720), (0, 560)] + [(x, min(y, 560) if x < 1050 else 560 + (x - 1050) * 0.6) for x, y in pts] + [(W, 720)], fill=(25, 30, 25))
    for x in range(0, 1060, 24):  # kızaklar
        y = _hill(x)
        d.line([x, y, x + 14, _hill(x + 14)], fill=(120, 90, 50), width=4)
    for k in range(12):
        x = 40 + k * 90
        torch(d, x, _hill(x) - 30, t, k, 0.9)
    q = p * 1.25
    for k in range(6):
        u = q - k * 0.17
        if u < 0 or u > 1.1:
            continue
        x = -100 + u * 1250
        y = _hill(x) if x < 1050 else 560 + (x - 1050) * 0.35
        slope = (_hill(x + 5) - _hill(x - 5)) / 10 if x < 1050 else 0.35
        ang = math.atan(slope)
        s = 0.6
        cx, cy = x, y - 22
        hull = [(-75, -15), (80, -20), (58, 18), (-58, 18)]
        rot = [(cx + a * math.cos(ang) - b * math.sin(ang), cy + a * math.sin(ang) + b * math.cos(ang)) for a, b in [(hx * s, hy * s) for hx, hy in hull]]
        d.polygon(rot, fill=(110, 40, 35))
        mtop = (cx + 60 * s * math.sin(ang), cy - 60 * s * math.cos(ang))
        d.line([cx, cy, mtop[0] - 0, mtop[1] - 40], fill=(70, 50, 30), width=3)
        d.polygon([(mtop[0] - 4, mtop[1] - 40), (mtop[0] + 14, mtop[1] - 34), (mtop[0] - 4, mtop[1] - 28)], fill=OTT_RED)
        if x < 1000:
            ox(d, x + 80, _hill(x + 80) + 4, 0.55, t, k)
            ox(d, x + 115, _hill(x + 115) + 4, 0.55, t, k + 1)
    label(d, 330, 640, "Galata sırtları", seg(p, 0.05, 0.12), 20)
    label(d, 1130, 660, "Haliç", seg(p, 0.1, 0.18), 22)
    label(d, 640, 100, "21–22 Nisan 1453 gecesi", seg(p, 0.02, 0.1) * (1 - seg(p, 0.65, 0.7)), 26)
    label(d, 640, 100, "Sabah: ≈ 70 Osmanlı gemisi Haliç'te!", seg(p, 0.75, 0.82), 28, GOLD)


def sc_tunnels(img, d, t, dur, p):
    bg(img, (90, 80, 110), (200, 150, 120))
    d = ImageDraw.Draw(img, "RGBA")
    g = 300
    d.rectangle([0, g, W, H], fill=(105, 75, 50))
    for k, c in enumerate([(95, 68, 45), (85, 60, 40), (75, 52, 35)]):
        d.rectangle([0, g + 60 + k * 120, W, g + 120 + k * 120], fill=c)
    r = random.Random(6)
    for _ in range(80):
        x, y, s = r.uniform(0, W), r.uniform(g + 10, H), r.uniform(3, 9)
        d.ellipse([x - s, y - s * 0.7, x + s, y + s * 0.7], fill=(130, 110, 90))
    walls(d, 800, 1020, g, 140, tower_every=110, tw=50, tex=30)
    q = seg(p, 0.03, 0.55)
    path = [(150, g), (150, 470)] + [(150 + 700 * min(1, (q * 1.2 - 0.2) / 1) * 1, 470)]
    if q > 0:
        depth = 470 * 0 + g + (470 - g) * min(1, q * 5)
        d.line([150, g, 150, depth], fill=(35, 25, 20), width=26)
        if q > 0.2:
            hx = 150 + 700 * ((q - 0.2) / 0.8)
            d.line([150, 470, hx, 470], fill=(35, 25, 20), width=26)
            for x in range(170, int(hx), 50):
                d.line([x, 457, x, 483], fill=(140, 100, 60), width=4)
            soldier(d, hx - 15, 482, 0.5, t, 0, (150, 30, 30), spear=False)
            sw = math.sin(t * 8)
            d.line([hx - 10, 458, hx + 6 + sw * 4, 452 + sw * 8], fill=(80, 60, 40), width=3)
        label(d, 260, g + 60, "Osmanlı lağımı", seg(p, 0.05, 0.12), 20)
    c = seg(p, 0.3, 0.6)
    if c > 0:
        d.line([1150, g, 1150, g + 170 * min(1, c * 3)], fill=(30, 22, 18), width=22)
        if c > 0.33:
            cx = 1150 - 290 * ((c - 0.33) / 0.67)
            d.line([1150, 470, cx, 470], fill=(30, 22, 18), width=22)
        label(d, 1150, g + 220, "Karşı lağım", seg(p, 0.32, 0.4), 20)
    e = (p - 0.6) * dur
    if 0 <= e < 3:
        flash(d, 860, 470, 70, 1 - e / 1.2)
        smoke(d, 860, 460, e, 1.3, (150, 120, 90))
    tq = seg(p, 0.6, 0.95)
    tx = 120 + 560 * tq
    d.rectangle([tx - 40, g - 220, tx + 40, g - 10], fill=(130, 90, 50), outline=(80, 55, 30), width=3)
    for y in range(g - 210, g - 10, 25):
        d.line([tx - 40, y, tx + 40, y], fill=(90, 60, 35), width=2)
    d.rectangle([tx - 46, g - 232, tx + 46, g - 214], fill=(100, 70, 40))
    for wx in (-28, 28):
        d.ellipse([tx + wx - 14, g - 22, tx + wx + 14, g + 6], fill=(70, 45, 25))
    label(d, tx, g - 260, "Kuşatma kulesi", seg(p, 0.62, 0.7), 18)
    label(d, 640, 60, "Mayıs 1453: Yer altında savaş", seg(p, 0.0, 0.06) * (1 - seg(p, 0.85, 0.9)), 26)


def sc_eclipse(img, d, t, dur, p):
    bg(img, (5, 8, 25), (25, 20, 45))
    d = ImageDraw.Draw(img, "RGBA")
    stars(d, 13, 160, t, 1.0)
    mx, my, mr = 920, 190, 90
    e = seg(p, 0.03, 0.35)
    moon = Image.new("RGB", (mr * 2 + 4, mr * 2 + 4), (0, 0, 0))
    md = ImageDraw.Draw(moon)
    base = mix((245, 240, 215), (170, 60, 40), seg(p, 0.25, 0.4))
    md.ellipse([2, 2, mr * 2 + 2, mr * 2 + 2], fill=base)
    for cx, cy, cr in ((60, 70, 18), (120, 110, 24), (90, 140, 12)):
        md.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=mix(base, (0, 0, 0), 0.12))
    sx = mr + 2 + (1 - e) * mr * 2.3
    md.ellipse([sx - mr * 1.05, mr + 2 - mr * 1.05, sx + mr * 1.05, mr + 2 + mr * 1.05], fill=mix(base, (20, 5, 5), 0.6 * e))
    mask = Image.new("L", moon.size, 0)
    ImageDraw.Draw(mask).ellipse([2, 2, mr * 2 + 2, mr * 2 + 2], fill=255)
    d.ellipse([mx - mr - 30, my - mr - 30, mx + mr + 30, my + mr + 30], fill=base + (40,))
    img.paste(moon, (mx - mr - 2, my - mr - 2), mask)
    d = ImageDraw.Draw(img, "RGBA")
    skyline(d, 600, (12, 12, 22), 30, -20, W + 20, 40, 110)
    hagia(d, 420, 600, 0.75, (12, 12, 22))
    walls(d, -20, W + 20, 660, 60, (25, 22, 32), (20, 18, 26))
    d.rectangle([0, 660, W, H], fill=(8, 8, 14))
    label(d, 330, 120, "22 Mayıs 1453 — Ay tutulması", seg(p, 0.03, 0.1) * (1 - seg(p, 0.55, 0.6)), 24)
    fg = seg(p, 0.3, 0.5) * (1 - seg(p, 0.6, 0.7) * 0.6)
    if fg > 0:
        for k in range(14):
            x = (k * 140 + t * (15 + k % 3 * 8)) % (W + 400) - 200
            y = 470 + (k % 4) * 60
            d.ellipse([x - 220, y - 50, x + 220, y + 50], fill=(200, 200, 215, int(60 * fg)))
    s = seg(p, 0.6, 0.68)
    if s > 0:
        d.rectangle([0, 0, W, H], fill=(0, 0, 0, int(120 * s)))
        x0, x1 = 290, 990
        y0, y1 = 360 - 220 * s, 360 + 220 * s
        d.rectangle([x0, y0, x1, y1], fill=(232, 214, 170))
        for yy in (y0, y1):
            d.rounded_rectangle([x0 - 20, yy - 16, x1 + 20, yy + 16], 16, fill=(205, 180, 130), outline=(140, 110, 70), width=2)
        if s > 0.9:
            d.text((640, 240), "Sultan Mehmed'den İmparator'a", font=fnt(26, True), fill=(90, 50, 20), anchor="mm")
            for i, ln in enumerate(["Şehri teslim et;", "canın ve halkın", "bağışlansın."]):
                d.text((640, 320 + i * 50), ln, font=fnt(34, True), fill=(60, 30, 10), anchor="mm")
        st = seg(p, 0.82, 0.86)
        if st > 0:
            stamp = Image.new("RGBA", (360, 160), (0, 0, 0, 0))
            sd = ImageDraw.Draw(stamp)
            sd.rounded_rectangle([8, 8, 352, 152], 18, outline=(190, 20, 20, 230), width=10)
            sd.text((180, 80), "REDDEDİLDİ", font=fnt(46, True), fill=(190, 20, 20, 230), anchor="mm")
            sc_ = 1.6 - 0.6 * st
            stamp = stamp.resize((int(360 * sc_), int(160 * sc_))).rotate(-14, expand=True)
            img.paste(stamp, (int(760 - stamp.width / 2), int(470 - stamp.height / 2)), stamp)


def sc_assault(img, d, t, dur, p):
    bg(img, (15, 5, 10), (120, 35, 15))
    d = ImageDraw.Draw(img, "RGBA")
    stars(d, 3, 50, t, 0.5, 0.3)
    for k in range(5):
        x = 200 + k * 260
        r = 160 + 30 * math.sin(t * 3 + k)
        d.ellipse([x - r, 540 - r * 0.5, x + r, 540 + r * 0.5], fill=(255, 100, 20, 35))
    gy = 500
    walls(d, 650, 1300, gy, 170, (90, 75, 70), (70, 58, 55), 160, damage=0.8, seed=12, breach=(880, 55))
    d.rectangle([0, gy, W, H], fill=(40, 25, 20))
    fire_particles(d, 8, t, 90, 600, 1280, 500, 300)
    waves = [(0.0, 0.33, (130, 100, 70), (120, 100, 80)), (0.33, 0.55, (40, 110, 60), (200, 30, 30)),
             (0.55, 1.0, (60, 70, 140), (245, 245, 240))]
    for wi, (a0, a1, body, hat) in enumerate(waves):
        if not (a0 - 0.02 <= p <= a1 + 0.02):
            continue
        local = (p - a0) / (a1 - a0)
        r = random.Random(wi)
        for k in range(46):
            row = k % 4
            base = r.uniform(-500, 300)
            x = base + local * 900 + (t * 25) % 30
            if wi < 2 and local > 0.75:
                x -= (local - 0.75) * 1500  # geri püskürtülüyor
            if x > 830 - row * 10:
                x = 830 - row * 10 + math.sin(t * 6 + k) * 8
            y = 530 + row * 30 + r.uniform(-6, 6)
            soldier(d, x, y, 0.75 + row * 0.1, t, k, body, hat=hat, shield=(150, 120, 60) if wi < 2 else None)
        if wi == 2 and local > 0.15:
            ottoman_flag(d, 520 + local * 280, 400, 0.5, t)
    n_def = int(12 * (1 - seg(p, 0.62, 0.8)))
    for k in range(n_def):
        x = 700 + k * 50
        if 820 < x < 940:
            continue
        soldier(d, x, gy - 170, 0.6, t, k, (100, 60, 130), hat=(160, 160, 170), walk=False, dirn=-1)
    for k in range(25):
        u = (t * 1.3 + k * 0.17) % 1
        x0, y0 = 760 + (k * 37) % 500, gy - 190
        x = x0 - u * 600
        y = y0 + u * 250 - math.sin(u * math.pi) * 80
        d.line([x, y, x - 16, y + 5], fill=(30, 20, 15), width=2)
    hp = seg(p, 0.72, 0.86)
    if hp > 0:
        tx = 1050
        ty = gy - 170 - 50
        hy = gy - 20 - (gy - 20 - ty) * hp
        soldier(d, tx - 20, hy + 40, 0.9, t, 0, (60, 70, 140), hat=(245, 245, 240), spear=False, walk=hp < 1)
        ottoman_flag(d, tx - 12, hy - 80, 0.75, t)
        if hp >= 1:
            glow = 90 + 20 * math.sin(t * 4)
            d.ellipse([tx + 25 - glow, hy - 60 - glow, tx + 25 + glow, hy - 60 + glow], fill=(255, 220, 120, 45))
        label(d, tx, 120, "Ulubatlı Hasan", seg(p, 0.76, 0.82), 28, GOLD)
    wl = ["1. dalga: Başıbozuklar", "2. dalga: Anadolu askerleri", "3. dalga: Yeniçeriler"]
    for i, (a0, a1, _, _) in enumerate(waves):
        label(d, 330, 120, wl[i], seg(p, a0 + 0.01, a0 + 0.04) * (1 - seg(p, a1 - 0.03, a1)), 24)
    label(d, 1000, 590, "Giustiniani yaralandı", seg(p, 0.6, 0.64) * (1 - seg(p, 0.7, 0.73)), 22)


def sc_hagia_sophia(img, d, t, dur, p):
    bg(img, (90, 150, 210), (235, 215, 180))
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([1080, 60, 1180, 160], fill=(255, 245, 200))
    for k in range(7):
        u = (t * 0.05 + k * 0.14) % 1
        x, y = u * (W + 200) - 100, 120 + (k % 3) * 40 + math.sin(t + k) * 10
        wv = math.sin(t * 8 + k) * 6
        d.line([x - 12, y - wv, x, y, x + 12, y - wv], fill=(40, 40, 50), width=2)
    hagia(d, 700, 560, 1.25, (205, 140, 110), mins=seg(p, 0.5, 0.65), roof=(150, 150, 165), minaret_n=1)
    d.rectangle([0, 560, W, H], fill=(190, 170, 130))
    q = seg(p, 0.05, 0.5)
    hx = -100 + 520 * q
    hy = 610
    st = math.sin(t * 8) if q < 1 else 0
    for lx in (-30, -15, 20, 35):
        d.line([hx + lx, hy - 25, hx + lx + st * 6 * (1 if lx % 2 else -1), hy + 15], fill=(230, 230, 225), width=6)
    d.ellipse([hx - 45, hy - 55, hx + 45, hy - 15], fill=(245, 245, 240))
    d.polygon([(hx + 30, hy - 45), (hx + 55, hy - 85), (hx + 72, hy - 70), (hx + 45, hy - 30)], fill=(245, 245, 240))
    d.ellipse([hx + 52, hy - 92, hx + 82, hy - 66], fill=(245, 245, 240))
    d.rounded_rectangle([hx - 14, hy - 105, hx + 14, hy - 50], 6, fill=(150, 25, 35))
    d.ellipse([hx - 20, hy - 140, hx + 20, hy - 102], fill=(250, 250, 245))
    d.ellipse([hx - 8, hy - 156, hx + 8, hy - 136], fill=OTT_RED)
    r = random.Random(1)
    for k in range(18):
        soldier(d, r.uniform(-200, 380) + 520 * q, 625 + r.uniform(-10, 15), 0.7, t, k, (60, 70, 140),
                hat=(245, 245, 240), walk=q < 1)
    label(d, 330, 120, "Fatih, Ayasofya'da", seg(p, 0.3, 0.36) * (1 - seg(p, 0.58, 0.62)), 26)
    label(d, 640, 80, "1 Haziran 1453 — İlk Cuma Namazı", seg(p, 0.6, 0.66), 26)
    a = seg(p, 0.82, 0.9)
    if a > 0:
        text(d, (W / 2, 150), "FATİH SULTAN MEHMED", 56, GOLD + (int(255 * a),), serif=True, stroke=3)


def sc_aftermath(img, d, t, dur, p):
    bg(img, (120, 175, 225), (240, 225, 195))
    d = ImageDraw.Draw(img, "RGBA")
    sea(d, 610, (50, 110, 160), t, (255, 255, 255, 60), 12)
    d.rectangle([0, 520, W, 610], fill=(200, 180, 140))
    r = random.Random(42)
    items = []
    x = 20
    while x < W - 20:
        kind = r.choices(["ev", "cami", "carsi"], [6, 2, 1])[0]
        w = {"ev": r.uniform(40, 70), "cami": 150, "carsi": 140}[kind]
        items.append((x, w, kind, r.random()))
        x += w + r.uniform(5, 20)
    order = sorted(range(len(items)), key=lambda i: items[i][3])
    gy = 540
    for rank, i in enumerate(order):
        x, w, kind, _ = items[i]
        a = seg(p, 0.05 + rank / len(items) * 0.6, 0.1 + rank / len(items) * 0.6)
        if a <= 0:
            continue
        rise = (1 - a) * 40
        if kind == "ev":
            h = 40 + (i * 13 % 40)
            col = [(220, 200, 170), (200, 120, 90), (230, 220, 200)][i % 3]
            d.rectangle([x, gy - h + rise, x + w, gy], fill=col)
            d.polygon([(x - 4, gy - h + rise), (x + w + 4, gy - h + rise), (x + w / 2, gy - h - 22 + rise)], fill=(170, 70, 50))
            d.rectangle([x + w / 2 - 5, gy - 18, x + w / 2 + 5, gy], fill=(90, 60, 40))
        elif kind == "cami":
            d.rectangle([x, gy - 60 + rise, x + w, gy], fill=(225, 215, 195))
            dome(d, x + w / 2, gy - 60 + rise, 50, (140, 150, 165))
            minaret(d, x + 8, gy, 170 * a, (225, 215, 195), 10)
            minaret(d, x + w - 8, gy, 170 * a, (225, 215, 195), 10)
        else:
            d.rectangle([x, gy - 50 + rise, x + w, gy], fill=(210, 180, 140))
            for k in range(5):
                dome(d, x + 14 + k * 28, gy - 50 + rise, 13, (150, 150, 160))
                d.chord([x + 4 + k * 28, gy - 36, x + 24 + k * 28, gy + 4], 180, 360, fill=(110, 80, 50))
    for k in range(30):
        x = (k * 47 + t * (20 + k % 5 * 6) * (1 if k % 2 else -1)) % (W + 40) - 20
        soldier(d, x, 580, 0.45, t, k, [(150, 30, 30), (40, 90, 140), (60, 120, 70), (200, 170, 90)][k % 4],
                hat=(240, 240, 235), spear=False)
    ottoman_flag(d, 1150, 250, 0.9, t)
    labels = [("Anadolu'dan ve Balkanlar'dan yeni halk", 0.08), ("Patrik Gennadios", 0.28),
              ("Camiler • Çarşılar • Medreseler", 0.5)]
    for i, (s, a0) in enumerate(labels):
        label(d, 520, 100, s, seg(p, a0, a0 + 0.04) * (1 - seg(p, a0 + 0.17, a0 + 0.2)), 26)
    a = seg(p, 0.76, 0.84)
    if a > 0:
        text(d, (W / 2, 140), "İSTANBUL", 70, GOLD + (int(255 * a),), serif=True, stroke=3)
        text(d, (W / 2, 210), "Osmanlı başkenti: 1453 – 1923", 30, CREAM + (int(255 * a),))


def sc_legacy(img, d, t, dur, p):
    bg(img, (14, 16, 28), (30, 26, 40))
    d = ImageDraw.Draw(img, "RGBA")
    a = seg(p, 0.0, 0.08) * (1 - seg(p, 0.26, 0.3))
    if a > 0:
        d.rectangle([0, 0, W / 2, H], fill=(60, 60, 70, int(120 * a)))
        d.rectangle([W / 2, 0, W, H], fill=(120, 95, 30, int(100 * a)))
        text(d, (W / 4, 360), "ORTA ÇAĞ", 52, (190, 190, 200, int(255 * a)), serif=True)
        text(d, (W * 3 / 4, 360), "YENİ ÇAĞ", 52, GOLD + (int(255 * a),), serif=True)
        g = 60 + 10 * math.sin(t * 4)
        d.ellipse([W / 2 - g, 360 - g, W / 2 + g, 360 + g], fill=(255, 210, 90, int(160 * a)))
        text(d, (W / 2, 360), "1453", 40, (40, 20, 10, int(255 * a)), stroke=0)
    b = seg(p, 0.28, 0.32) * (1 - seg(p, 0.5, 0.54))
    if b > 0:
        walls(d, 750, 1150, 560, 160, damage=0.9, seed=4)
        cannon(d, 260, 520, 1.6, -8)
        lt = t % 2.5
        flash(d, 450, 495, 60 * b, 1 - lt * 3)
        smoke(d, 450, 495, lt, 1.4)
        d.rectangle([0, 560, W, H], fill=(20, 18, 24, int(255 * b)))
        label(d, W / 2, 120, "Barut çağı: Surların devri kapandı", b, 30)
    c = seg(p, 0.52, 0.56) * (1 - seg(p, 0.74, 0.78))
    if c > 0:
        land = (150, 140, 100, int(255 * c))
        d.rectangle([0, 0, W, H], fill=(25, 60, 100, int(255 * c)))
        d.polygon([(560, 120), (760, 100), (820, 200), (760, 260), (600, 280), (540, 220)], fill=land)
        d.polygon([(560, 300), (780, 290), (840, 400), (760, 560), (690, 640), (640, 520), (560, 400)], fill=land)
        d.polygon([(100, 100), (330, 110), (300, 300), (240, 360), (300, 470), (280, 650), (200, 640), (180, 420), (120, 300)], fill=land)
        d.polygon([(820, 140), (1250, 100), (1260, 380), (1000, 360), (880, 260)], fill=land)
        d.ellipse([772, 238, 788, 254], fill=(255, 60, 60, int(255 * c)))
        q = seg(p, 0.56, 0.72)
        route1 = [(560, 250), (500, 360), (520, 560), (680, 680), (860, 620), (960, 480), (1080, 420)]
        route2 = [(560, 230), (420, 260), (330, 300)]
        for route in (route1, route2):
            nseg = len(route) - 1
            for i in range(nseg):
                u = clamp(q * nseg - i)
                if u <= 0:
                    break
                (x0, y0), (x1, y1) = route[i], route[i + 1]
                xe, ye = x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
                for k in range(0, 10, 2):
                    a0, a1 = k / 10, (k + 1) / 10
                    if a0 > u:
                        break
                    d.line([x0 + (x1 - x0) * a0, y0 + (y1 - y0) * a0, x0 + (x1 - x0) * min(a1, u), y0 + (y1 - y0) * min(a1, u)], fill=GOLD, width=4)
        label(d, W / 2, 60, "Avrupa yeni deniz yolları aramaya başladı", c, 26)
    e = seg(p, 0.76, 0.8)
    if e > 0:
        for k in range(3):
            bx = 300 + k * 260
            fl = math.sin(t * 2 + k) * 6
            d.polygon([(bx, 300 + fl), (bx + 110, 280 + fl), (bx + 110, 430 + fl), (bx, 450 + fl)], fill=(230, 215, 180, int(255 * e)))
            d.polygon([(bx + 110, 280 + fl), (bx + 220, 300 + fl), (bx + 220, 450 + fl), (bx + 110, 430 + fl)], fill=(215, 200, 165, int(255 * e)))
            for j in range(5):
                d.line([bx + 15, 320 + j * 22 + fl, bx + 95, 316 + j * 22 + fl], fill=(120, 100, 70, int(255 * e)), width=2)
                d.line([bx + 125, 316 + j * 22 + fl, bx + 205, 320 + j * 22 + fl], fill=(120, 100, 70, int(255 * e)), width=2)
        label(d, W / 2, 140, "Bizanslı âlimler İtalya'ya: Rönesans'a katkı", e, 26)
        f = seg(p, 0.9, 0.96)
        text(d, (W / 2, 590), "Bir genç sultanın hayali, dünya tarihinin yönünü değiştirdi.", 30, GOLD + (int(255 * f),), serif=True)


def sc_outro(img, d, t, dur, p):
    bg(img, (10, 10, 22), (35, 20, 30))
    d = ImageDraw.Draw(img, "RGBA")
    r = random.Random(3)
    for k in range(80):
        x = r.uniform(0, W)
        sp = r.uniform(10, 40)
        y = (r.uniform(0, H) - t * sp) % H
        s = r.uniform(1, 3)
        d.ellipse([x - s, y - s, x + s, y + s], fill=GOLD + (int(r.uniform(60, 180)),))
    a = seg(t, 0.2, 1.2)
    draw_logo(img, W / 2, 230, 140, a, with_text=True)
    d = ImageDraw.Draw(img, "RGBA")
    text(d, (W / 2, 370), "İzlediğiniz için teşekkürler!", 44, CREAM + (int(255 * a),), serif=True)
    b = seg(t, 1.5, 2.3)
    if b > 0:
        pulse = 1 + 0.05 * math.sin(t * 5)
        bw, bh = 300 * pulse, 70 * pulse
        d.rounded_rectangle([W / 2 - bw / 2, 470 - bh / 2, W / 2 + bw / 2, 470 + bh / 2], 14, fill=(220, 20, 30, int(255 * b)))
        d.text((W / 2 - 20, 470), "ABONE OL", font=fnt(34), fill=(255, 255, 255, int(255 * b)), anchor="mm")
        bx, by = W / 2 + 110, 470
        sw = math.sin(t * 10) * 0.25 if (t % 3) < 1 else 0
        d.pieslice([bx - 16, by - 20, bx + 16, by + 14], 180 + sw * 40, 360 + sw * 40, fill=(255, 255, 255, int(255 * b)))
        d.rectangle([bx - 16, by - 4, bx + 16, by + 8], fill=(255, 255, 255, int(255 * b)))
        d.ellipse([bx - 4, by + 8, bx + 4, by + 16], fill=(255, 255, 255, int(255 * b)))
        text(d, (W / 2, 545), "Yorumlarda hangi tarihî olayı merak ettiğinizi yazın!", 26, CREAM + (int(255 * b),))
    fade_black(d, seg(t, dur - 1.2, dur))


SCENES = {
    "intro": sc_intro, "dawn_city": sc_dawn_city, "map": sc_map, "timeline": sc_timeline,
    "portraits": sc_portraits, "fortress": sc_fortress, "cannon_forge": sc_cannon_forge, "chain": sc_chain,
    "siege_start": sc_siege_start, "naval": sc_naval, "ships_over_land": sc_ships_over_land,
    "tunnels": sc_tunnels, "eclipse": sc_eclipse, "assault": sc_assault, "hagia_sophia": sc_hagia_sophia,
    "aftermath": sc_aftermath, "legacy": sc_legacy, "outro": sc_outro,
}


# ---------------------------------------------------------------- katmanlar
@lru_cache(1)
def vignette():
    y, x = np.mgrid[0:H, 0:W]
    dist = np.sqrt(((x - W / 2) / (W / 2)) ** 2 + ((y - H / 2) / (H / 2)) ** 2)
    a = (np.clip(dist - 0.65, 0, 1) * 200).astype(np.uint8)
    arr = np.zeros((H, W, 4), np.uint8)
    arr[..., 3] = a
    return Image.fromarray(arr, "RGBA")


def subtitle_chunks(textstr, adur):
    sents = [s.strip() for s in re.split(r"(?<=[.!?:])\s+", textstr) if s.strip()]
    chunks = []
    f = fnt(30)
    for s in sents:
        lines = wrap(s, f, 1000)
        for i in range(0, len(lines), 2):
            chunks.append(" ".join(lines[i:i + 2]))
    total = sum(len(c) + 12 for c in chunks)
    out, cur = [], LEAD
    for c in chunks:
        dd = adur * (len(c) + 12) / total
        out.append((cur, cur + dd, c))
        cur += dd
    return out


def draw_subtitle(img, chunks, t):
    for a, b, s in chunks:
        if a <= t < b:
            d = ImageDraw.Draw(img, "RGBA")
            f = fnt(30)
            lines = wrap(s, f, 1080)
            lh = 40
            top = H - 30 - lh * len(lines) - 16
            wmax = max(f.getlength(l) for l in lines)
            d.rounded_rectangle([W / 2 - wmax / 2 - 20, top, W / 2 + wmax / 2 + 20, H - 30], 10, fill=(0, 0, 0, 150))
            for i, l in enumerate(lines):
                d.text((W / 2, top + 8 + lh * i + lh / 2), l, font=f, fill=(255, 255, 255), anchor="mm",
                       stroke_width=2, stroke_fill=(0, 0, 0))
            return


def draw_title_banner(img, title, t):
    a = seg(t, 0.3, 0.9) * (1 - seg(t, 5.0, 5.8))
    if a <= 0:
        return
    d = ImageDraw.Draw(img, "RGBA")
    f = fnt(30, True)
    w = f.getlength(title) + 60
    x = -w + (w + 40) * ease(seg(t, 0.3, 1.0))
    d.rectangle([x, 30, x + w, 84], fill=(10, 10, 20, int(200 * a)))
    d.rectangle([x + w - 6, 30, x + w, 84], fill=GOLD + (int(255 * a),))
    d.text((x + 24, 57), title, font=f, fill=GOLD + (int(255 * a),), anchor="lm", stroke_width=2, stroke_fill=(0, 0, 0, int(255 * a)))


# ---------------------------------------------------------------- ses
def audio_for(i, txt):
    n = f"{i + 1:02d}"
    for ext in ("mp3", "wav", "m4a", "ogg", "flac"):
        pth = os.path.join(BASE, "ses", f"{n}.{ext}")
        if os.path.exists(pth):
            return pth
    os.makedirs(os.path.join(BUILD, "tts"), exist_ok=True)
    out = os.path.join(BUILD, "tts", f"{n}.wav")
    if not os.path.exists(out):
        subprocess.run(["espeak-ng", "-v", "tr", "-s", ESPEAK_HIZ, "-p", "38", "-w", out, txt], check=True)
    return out


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def make_music(path, seconds):
    sr = 44100
    n = int(sr * seconds)
    tt = np.arange(n) / sr
    chords = [[146.83, 174.61, 220.0], [116.54, 146.83, 174.61], [130.81, 164.81, 196.0], [110.0, 138.59, 164.81]]
    seg_len = 8.0
    out = np.zeros(n)
    for ci in range(int(seconds / seg_len) + 1):
        ch = chords[ci % 4]
        s0, s1 = int(ci * seg_len * sr), min(n, int((ci + 1) * seg_len * sr + sr))
        if s0 >= n:
            break
        lt = tt[s0:s1] - ci * seg_len
        env = np.clip(lt / 2.0, 0, 1) * np.clip((seg_len + 1 - lt) / 2.0, 0, 1)
        sig = np.zeros_like(lt)
        for f in ch:
            for h, amp in ((1, 1.0), (2, 0.35), (3, 0.15)):
                sig += amp * np.sin(2 * np.pi * f * h * lt + 0.3 * np.sin(2 * np.pi * 0.2 * lt))
            sig += 0.6 * np.sin(2 * np.pi * f / 2 * lt)
        out[s0:s1] += sig * env
    beat = (tt % 2.0)  # yumuşak davul
    out += 1.8 * np.sin(2 * np.pi * 55 * tt) * np.exp(-beat * 9)
    out /= np.max(np.abs(out)) + 1e-9
    data = (out * 0.8 * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


# ---------------------------------------------------------------- render
def plan():
    out = []
    for i, (kind, title, txt) in enumerate(SAHNELER):
        ap = audio_for(i, txt)
        ad = duration(ap)
        out.append(dict(i=i, kind=kind, title=title, text=txt, audio=ap, adur=ad, dur=LEAD + ad + TAIL))
    return out


def render_frame(sc, t):
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img, "RGBA")
    SCENES[sc["kind"]](img, d, t, sc["dur"], t / sc["dur"])
    img = img.convert("RGBA")
    img.alpha_composite(vignette())
    if sc["kind"] not in ("intro", "outro"):
        draw_title_banner(img, sc["title"], t)
        draw_logo(img, W - 140, 50, 44, 0.9)
    draw_subtitle(img, sc["chunks"], t)
    fade = min(1, t / 0.4, (sc["dur"] - t) / 0.4)
    img = img.convert("RGB")
    if fade < 1:
        img = Image.blend(Image.new("RGB", (W, H)), img, max(0, fade))
    return img


def render_scene(sc):
    out = os.path.join(BUILD, f"sahne_{sc['i'] + 1:02d}.mp4")
    nframes = int(round(sc["dur"] * FPS))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", sc["audio"],
           "-filter_complex", f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={int(LEAD * 1000)}:all=1,apad[a]",
           "-map", "0:v", "-map", "[a]", "-t", f"{nframes / FPS:.3f}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", out]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in range(nframes):
        pr.stdin.write(render_frame(sc, f / FPS).tobytes())
    pr.stdin.close()
    pr.wait()
    print(f"  sahne {sc['i'] + 1:02d} tamam ({sc['dur']:.1f} sn)", flush=True)
    return out


def srt_time(x):
    h, m = int(x // 3600), int(x % 3600 // 60)
    s = x % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def main():
    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(CIKTI, exist_ok=True)
    scenes = plan()
    for sc in scenes:
        sc["chunks"] = subtitle_chunks(sc["text"], sc["adur"])
    total = sum(s["dur"] for s in scenes)
    print(f"Toplam süre: {total / 60:.0f} dk {total % 60:.0f} sn")

    if "--onizleme" in sys.argv:
        od = os.path.join(BUILD, "onizleme")
        os.makedirs(od, exist_ok=True)
        for sc in scenes:
            for frac in (0.3, 0.75):
                render_frame(sc, sc["dur"] * frac).save(os.path.join(od, f"{sc['i'] + 1:02d}_{sc['kind']}_{int(frac * 100)}.jpg"), quality=85)
        print("Önizleme:", od)
        return

    # altyazı dosyası
    with open(os.path.join(CIKTI, "Istanbulun_Fethi_1453.srt"), "w", encoding="utf-8") as f:
        off, n = 0.0, 1
        for sc in scenes:
            for a, b, s in sc["chunks"]:
                f.write(f"{n}\n{srt_time(off + a)} --> {srt_time(off + b)}\n{s}\n\n")
                n += 1
            off += sc["dur"]

    with Pool(max(1, os.cpu_count() or 1)) as pool:
        parts = pool.map(render_scene, sorted(scenes, key=lambda s: -s["dur"]))
    parts = [os.path.join(BUILD, f"sahne_{s['i'] + 1:02d}.mp4") for s in scenes]
    lst = os.path.join(BUILD, "liste.txt")
    with open(lst, "w") as f:
        for pth in parts:
            f.write(f"file '{pth}'\n")
    joined = os.path.join(BUILD, "birlesik.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined], check=True)

    user_music = None
    for ext in ("mp3", "wav", "m4a", "ogg"):
        pth = os.path.join(BASE, f"muzik.{ext}")
        if os.path.exists(pth):
            user_music = pth
    if user_music:
        music, vol = user_music, 0.18
    else:
        music, vol = os.path.join(BUILD, "ambiyans.wav"), 0.10
        make_music(music, 64)
    fil = (f"[1:a]volume={vol},aformat=sample_rates=48000:channel_layouts=stereo,"
           f"afade=t=in:d=2,afade=t=out:st={total - 3:.2f}:d=3[m];"
           f"[0:a][m]amix=inputs=2:duration=first:normalize=0[a]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", joined, "-stream_loop", "-1", "-i", music,
                    "-filter_complex", fil, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart", OUT], check=True)
    print("Video hazır:", OUT)


if __name__ == "__main__":
    main()

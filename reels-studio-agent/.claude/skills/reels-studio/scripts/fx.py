#!/usr/bin/env python3
"""Анимированные 3D-стеклянные элементы поверх видео (glassmorphism).

Стиль: матовое тёмное стекло с размытием видео под ним, серебристая кромка,
пробегающий блик, мягкая тень, лёгкий 3D-наклон и «парение»; серебряные
иконки с фаской. Появление — разворот в пространстве с пружинкой, уход — растворение.

Элементы описываются в .<имя>_montage/fx.json (время — шкала готового ролика,
как в captions.json; вместо "t" можно дать "cap" — номер титра):

  [{"type": "card", "cap": 6, "title": "Удержание", "value": "+120%",
    "icon": "arrow_up", "accent": true, "pos": "right"},
   {"type": "pill", "t": 9.4, "text": "Без музыки", "icon": "check"},
   {"type": "list", "cap": 14, "title": "Чек-лист", "items": ["Паузы", "Титры"]},
   {"type": "icon", "t": 3.0, "icon": "cursor", "size": 200}]

Общие поля: dur (сек), pos (left/right/top-left/top-right/center/low-left/low-right/auto)
или x, y (центр, px из 1080×1920), scale. Иконки: cursor, check, arrow_up, star, bolt,
heart, play, plus, chat, code («</>»), braces («{…}»), percent, или "text:АБВ".

Проверка без рендера ролика:
  fx.py still video.mp4 --t 7.5 --from out.mp4 [-o still.png]
  — кадр собранного ролика (без эффектов) с наложенными эффектами в момент t
"""
import argparse
import json
import math
import re
import sys
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).resolve().parent.parent / "fonts"
W, H, FPS = 1080, 1920, 30
SS = 2  # элементы рисуются с двойным разрешением и уменьшаются — ровные края
WHITE = np.array([255, 255, 255], np.float32)
# Цвета и форма элементов — из профиля стиля (brand.json → fx), см. configure()
ST = {}
LIME = TEXT = MUTED = SILVER = ICON = FILL = WHITE


def rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def configure(style):
    """Применяет стиль анимаций пользователя (выведенный из его референсов)."""
    global ST, LIME, TEXT, MUTED, SILVER, ICON, FILL
    sys.path.insert(0, str(Path(__file__).parent))
    import brand
    base = brand.DEFAULT["fx"]
    ST = brand.merge(base, style or {})
    LIME = rgb(ST.get("accent") or brand.load()["subtitles"]["accent"] or "#C8FF2E")
    TEXT, MUTED = rgb(ST["text_color"]), rgb(ST["muted_color"])
    SILVER, ICON, FILL = rgb(ST["rim_color"]), rgb(ST["icon_color"]), rgb(ST["fill"])
    font.cache_clear()

# Пресеты рассчитаны на «говорящую голову»: лицо в верхней трети по центру,
# титры на 1250 — плашки встают по бокам на уровне груди. top-* — если лицо ниже.
POS = {"left": (290, 900), "right": (790, 900), "top-left": (290, 380),
       "top-right": (790, 380), "center": (540, 960), "low-left": (290, 1040),
       "low-right": (790, 1040)}
IN_DUR, OUT_DUR = 0.55, 0.35


# ---------- утилиты ----------

def clip01(x):
    return min(max(x, 0.0), 1.0)


def ease_out_cubic(p):
    p = clip01(p)
    return 1 - (1 - p) ** 3


def ease_out_back(p, s=1.5):
    p = clip01(p)
    return 1 + (s + 1) * (p - 1) ** 3 + s * (p - 1) ** 2


@lru_cache(maxsize=None)
def font(weight, size):
    key = ST.get("ui_font", "inter")
    if key == "inter":
        return ImageFont.truetype(str(FONTS / f"Inter-{weight}.otf"), int(size))
    import brand
    num = {"Medium": 500, "SemiBold": 600, "Bold": 700}[weight]
    path, _ = brand.install_font(key, num, "ui")
    return ImageFont.truetype(str(path), int(size))


def text_w(text, f):
    l, _, r, _ = f.getbbox(text)
    return r - l


def blank(w, h):
    return np.zeros((h, w, 4), np.float32)  # RGBA, premultiplied, 0..255 / 0..1


def over(dst, src, x=0, y=0):
    """Кладёт premultiplied src на dst в точку (x, y)."""
    h, w = src.shape[:2]
    reg = dst[y:y + h, x:x + w]
    a = src[..., 3:4]
    reg[...] = src + reg * (1 - a)


def solid(mask, color, alpha=1.0):
    """Маска → premultiplied слой одного цвета."""
    a = (mask * alpha)[..., None]
    return np.concatenate([a * color, a], axis=2).astype(np.float32)


def text_mask(text, f, pad=4):
    l, t, r, b = f.getbbox(text)
    img = Image.new("L", (r - l + 2 * pad, b - t + 2 * pad))
    ImageDraw.Draw(img).text((pad - l, pad - t), text, font=f, fill=255)
    return np.asarray(img, np.float32) / 255


def rounded_mask(w, h, r, inset=0):
    img = Image.new("L", (w, h))
    ImageDraw.Draw(img).rounded_rectangle(
        (inset, inset, w - 1 - inset, h - 1 - inset), radius=max(1, r - inset), fill=255)
    return np.asarray(img, np.float32) / 255


def downsample(layer):
    h, w = layer.shape[:2]
    return cv2.resize(layer, (max(1, w // SS), max(1, h // SS)), interpolation=cv2.INTER_AREA)


# ---------- иконки ----------

def icon_mask(name, s):
    img = Image.new("L", (s, s))
    d = ImageDraw.Draw(img)
    P = lambda pts: [(x * s, y * s) for x, y in pts]  # noqa: E731
    if name == "cursor":
        d.polygon(P([(.18, .06), (.18, .84), (.38, .66), (.52, .95), (.66, .88),
                     (.52, .6), (.8, .6)]), fill=255)
    elif name == "check":
        d.line(P([(.14, .54), (.4, .78), (.88, .24)]), fill=255, width=int(s * .15), joint="curve")
        for x, y in [(.14, .54), (.88, .24)]:
            r = s * .075
            d.ellipse((x * s - r, y * s - r, x * s + r, y * s + r), fill=255)
    elif name == "arrow_up":
        d.line(P([(.08, .82), (.38, .52), (.56, .66), (.8, .36)]), fill=255,
               width=int(s * .12), joint="curve")
        d.polygon(P([(.64, .24), (.94, .14), (.88, .46)]), fill=255)
    elif name == "star":
        pts = []
        for i in range(10):
            r = .47 if i % 2 == 0 else .2
            a = -math.pi / 2 + i * math.pi / 5
            pts.append((.5 + r * math.cos(a), .53 + r * math.sin(a)))
        d.polygon(P(pts), fill=255)
    elif name == "bolt":
        d.polygon(P([(.58, .02), (.16, .58), (.46, .58), (.36, .98), (.84, .4),
                     (.54, .4), (.66, .02)]), fill=255)
    elif name == "heart":
        d.ellipse(P([(.06, .14), (.52, .6)]), fill=255)
        d.ellipse(P([(.48, .14), (.94, .6)]), fill=255)
        d.polygon(P([(.09, .48), (.91, .48), (.5, .92)]), fill=255)
    elif name == "play":
        d.polygon(P([(.24, .12), (.24, .88), (.88, .5)]), fill=255)
    elif name == "plus":
        d.rounded_rectangle(P([(.4, .1), (.6, .9)]), radius=s * .06, fill=255)
        d.rounded_rectangle(P([(.1, .4), (.9, .6)]), radius=s * .06, fill=255)
    elif name == "chat":
        d.rounded_rectangle(P([(.06, .1), (.94, .72)]), radius=s * .16, fill=255)
        d.polygon(P([(.22, .66), (.22, .92), (.46, .7)]), fill=255)
    else:
        glyph = {"code": "</>", "braces": "{…}", "percent": "%"}.get(name)
        if glyph is None:
            glyph = name[5:] if name.startswith("text:") else "?"
        m = text_mask(glyph, font("Bold", s * .62), pad=0)
        sc = min(s * .92 / m.shape[1], s * .8 / m.shape[0], 1.0)
        m = cv2.resize(m, (max(1, int(m.shape[1] * sc)), max(1, int(m.shape[0] * sc))))
        out = np.zeros((s, s), np.float32)
        y0, x0 = (s - m.shape[0]) // 2, (s - m.shape[1]) // 2
        out[y0:y0 + m.shape[0], x0:x0 + m.shape[1]] = m
        return out
    return np.asarray(img, np.float32) / 255


def icon_layer(mask, tint=None):
    """Иконка в выбранной отделке: chrome — металл с фаской, flat — заливка, outline — контур."""
    tint = ICON if tint is None else tint
    finish = ST.get("icon_finish", "chrome")
    if finish == "flat":
        return solid(mask, tint)
    if finish == "outline":
        t = max(2, mask.shape[0] // 18)
        ring = np.clip(mask - cv2.erode(mask, np.ones((t, t), np.uint8)), 0, 1)
        return solid(ring, tint)
    return chrome(mask, tint)


def chrome(mask, tint=None, strength=1.0):
    """Серебряный объём: градиент сверху вниз, фаска (свет слева сверху), полоса отражения."""
    h, w = mask.shape
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    light = 1.0 - 0.42 * (0.75 * yy + 0.25 * xx)
    light = light + 0.14 * np.exp(-((yy - 0.32 - 0.25 * xx) / 0.07) ** 2)
    sig = max(1.0, min(h, w) / 45)
    b = cv2.GaussianBlur(mask, (0, 0), sig)
    d = max(1, int(round(sig)))
    emb = np.roll(np.roll(b, -d, 0), -d, 1) - np.roll(np.roll(b, d, 0), d, 1)
    light = np.clip(light + 1.6 * strength * emb, 0.25, 1.12)
    tint = SILVER if tint is None else tint
    rgb = np.clip(light[..., None] * tint, 0, 255)
    return np.concatenate([rgb * mask[..., None], mask[..., None]], axis=2).astype(np.float32)


def glass_body(w, h, r):
    """Слои стеклянной плашки: силуэт (под размытие) и тонировка + серебристая кромка."""
    surface = ST.get("surface", "glass-dark")
    rw = max(1, int(ST.get("rim_width", 3) * (2 if surface == "outline" else 1)))
    mask = rounded_mask(w, h, r)
    inner = rounded_mask(w, h, r, inset=rw * SS)
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    diag = 0.6 * yy + 0.4 * xx
    fo = float(ST.get("fill_opacity", 0.11))
    if surface == "solid":
        layer = solid(inner, FILL, min(1.0, fo))
    elif surface == "outline":
        layer = blank(w, h)
    else:
        layer = solid(inner, FILL, 1.0) * (fo * (1.2 - 0.8 * diag))[..., None]
    ring = np.clip(mask - inner, 0, 1)
    ro = float(ST.get("rim_opacity", 0.95))
    over(layer, solid(ring * (ro - 0.7 * ro * diag) if surface != "outline" else ring * ro, SILVER))
    # тонкий внутренний блик по верхней кромке
    top = np.clip(inner - rounded_mask(w, h, r, inset=5 * SS), 0, 1) * np.exp(-((yy - 0.0) / 0.12) ** 2)
    over(layer, solid(top, WHITE, 0.35))
    return mask, layer


# ---------- элементы ----------

class Element:
    sheen_from = 0.2

    def __init__(self, spec, t0, dur):
        self.spec, self.t0, self.dur = spec, t0, dur
        self.scale = float(spec.get("scale", 1.0))

    def local(self, tr):
        """(premultiplied RGBA, маска стекла или None) в момент tr от начала."""
        return self.base, self.mask

    def sheen(self, layer, sil, tr):
        p = (tr - self.sheen_from) / 0.8
        if not ST.get("sheen", True) or not 0 < p < 1:
            return layer
        h, w = sil.shape
        u = (np.arange(w, dtype=np.float32)[None, :] / w * 0.8
             + np.arange(h, dtype=np.float32)[:, None] / h * 0.2)
        band = np.exp(-((u - (-0.3 + 1.6 * p)) / 0.07) ** 2) * sil * 0.26
        out = layer.copy()
        over(out, solid(band, WHITE))
        return out


class Card(Element):
    """Карточка-уведомление: подпись, крупное значение (со счётчиком), текст, иконка."""

    def __init__(self, spec, t0, dur):
        super().__init__(spec, t0, dur)
        k = SS * self.scale
        self.pad, self.r = int(44 * k), int(ST.get("radius", 40) * k)
        self.f_title, self.f_value = font("Medium", 32 * k), font("Bold", 92 * k)
        self.f_text = font("SemiBold", 40 * k)
        self.title, self.value = spec.get("title", ""), spec.get("value", "")
        self.text = spec.get("text", "")
        self.icon = spec.get("icon")
        self.color = LIME if spec.get("accent") else TEXT
        isz = int(64 * k) if self.icon else 0
        widths = [text_w(self.title, self.f_title) + (isz + int(24 * k) if isz else 0),
                  text_w(self.value, self.f_value), text_w(self.text, self.f_text)]
        w = int(min(max(max(widths) + 2 * self.pad, 380 * k), 780 * k))
        rows = []
        y = self.pad
        if self.title or isz:
            rows.append(("title", y))
            y += max(int(40 * k), isz)
        if self.value:
            y += int(10 * k)
            rows.append(("value", y))
            y += int(100 * k)
        if self.text:
            y += int(8 * k)
            rows.append(("text", y))
            y += int(50 * k)
        h = y + self.pad
        self.w, self.h, self.rows, self.isz = w, h, rows, isz
        self.mask_ss, body = glass_body(w, h, self.r)
        if self.title:
            over(body, solid(text_mask(self.title, self.f_title), MUTED), self.pad, self.pad)
        if isz:
            ic = icon_layer(icon_mask(self.icon, isz), LIME if spec.get("accent") else None)
            over(body, ic, w - self.pad - isz, self.pad - int(8 * k))
        if self.text:
            ty = dict(rows)["text"]
            over(body, solid(text_mask(self.text, self.f_text), TEXT, 0.92), self.pad, ty)
        self.body_ss = body
        self.mask = downsample(self.mask_ss[..., None].repeat(4, 2))[..., 0]
        m = re.match(r"^([+\-−]?)(\d+(?:[.,]\d+)?)(.*)$", self.value)
        self.count = m if (m and spec.get("countup", True)) else None
        self.base = downsample(self._with_value(self.value))
        self.cache = {}

    def _with_value(self, value):
        layer = self.body_ss.copy()
        if self.value:
            over(layer, solid(text_mask(value, self.f_value), self.color), self.pad,
                 dict(self.rows)["value"])
        return layer

    def local(self, tr):
        layer = self.base
        if self.count:
            p = ease_out_cubic((tr - 0.15) / 0.9)
            sign, num, tail = self.count.groups()
            dec = len(num.split(",")[-1].split(".")[-1]) if re.search(r"[.,]", num) else 0
            val = float(num.replace(",", ".")) * p
            s = f"{sign}{val:.{dec}f}{tail}".replace(".", "," if "," in num else ".")
            if s not in self.cache:
                self.cache[s] = downsample(self._with_value(s))
            layer = self.cache[s]
        return self.sheen(layer, self.mask, tr), self.mask


class Pill(Element):
    def __init__(self, spec, t0, dur):
        super().__init__(spec, t0, dur)
        k = SS * self.scale
        h = int(108 * k)
        isz = int(58 * k) if spec.get("icon") else 0
        f = font("SemiBold", 44 * k)
        text = spec.get("text", "")
        pad = int(36 * k)
        w = pad + isz + (int(20 * k) if isz else 0) + text_w(text, f) + pad + int(8 * k)
        mask, body = glass_body(w, h, h // 2)
        x = pad
        if isz:
            tint = LIME if spec.get("accent") else None
            over(body, icon_layer(icon_mask(spec["icon"], isz), tint), x, (h - isz) // 2)
            x += isz + int(20 * k)
        tm = text_mask(text, f)
        over(body, solid(tm, TEXT), x, (h - tm.shape[0]) // 2)
        self.base = downsample(body)
        self.mask = downsample(mask[..., None].repeat(4, 2))[..., 0]

    def local(self, tr):
        return self.sheen(self.base, self.mask, tr), self.mask


class ListCard(Element):
    """Чек-лист: пункты появляются по одному с лаймовой галочкой."""

    def __init__(self, spec, t0, dur):
        super().__init__(spec, t0, dur)
        k = SS * self.scale
        pad, row, isz = int(44 * k), int(76 * k), int(46 * k)
        ft, fi = font("Medium", 32 * k), font("SemiBold", 42 * k)
        title, items = spec.get("title", ""), spec.get("items", [])
        w = max([text_w(title, ft)] + [isz + int(22 * k) + text_w(i, fi) for i in items]) + 2 * pad
        w = int(max(w, 380 * k))
        y0 = pad + (int(56 * k) if title else 0)
        h = y0 + row * len(items) + pad - int(14 * k)
        mask, body = glass_body(w, h, int(ST.get("radius", 40) * k))
        if title:
            over(body, solid(text_mask(title, ft), MUTED), pad, pad)
        self.base = downsample(body)
        self.mask = downsample(mask[..., None].repeat(4, 2))[..., 0]
        self.items = []
        for n, it in enumerate(items):
            lay = blank(w, h)
            y = y0 + n * row
            over(lay, icon_layer(icon_mask("check", isz), LIME), pad, y + (row - isz) // 2 - int(8 * k))
            tm = text_mask(it, fi)
            over(lay, solid(tm, TEXT), pad + isz + int(22 * k), y + (row - tm.shape[0]) // 2 - int(8 * k))
            self.items.append(downsample(lay))
        self.step = float(spec.get("step", 0.32))  # пауза между пунктами — под речь

    def local(self, tr):
        out = self.base.copy()
        for n, lay in enumerate(self.items):
            a = ease_out_cubic((tr - 0.45 - n * self.step) / 0.25)
            if a > 0:
                over(out, lay * a)
        return self.sheen(out, self.mask, tr), self.mask


class Icon(Element):
    """Серебряная иконка; glass=true — выдавленная в стеклянной плашке (как «</>» на референсе)."""

    def __init__(self, spec, t0, dur):
        super().__init__(spec, t0, dur)
        s = int(spec.get("size", 200) * SS * self.scale)
        tint = LIME if spec.get("accent") else None
        if spec.get("glass"):
            w, h = int(s * 1.55), s
            mask, body = glass_body(w, h, int(h * 0.42))
            g = int(s * 0.62)
            over(body, icon_layer(icon_mask(spec["icon"], g), tint) * 0.9, (w - g) // 2, (h - g) // 2)
            self.base = downsample(body)
            self.mask = downsample(mask[..., None].repeat(4, 2))[..., 0]
        else:
            self.base = downsample(icon_layer(icon_mask(spec["icon"], s), tint))
            self.mask = None

    def local(self, tr):
        sil = self.mask if self.mask is not None else self.base[..., 3]
        return self.sheen(self.base, sil, tr), self.mask


TYPES = {"card": Card, "pill": Pill, "list": ListCard, "icon": Icon}
DEFAULT_DUR = {"card": 2.8, "pill": 2.2, "icon": 2.0}


# ---------- композитинг ----------

def project(w, h, cx, cy, s, rx, ry, rz, f=1900.0):
    """Углы плашки после поворота в 3D и перспективы."""
    pts = np.array([[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]],
                   np.float32) * s
    ax, ay, az = map(math.radians, (rx, ry, rz))
    Rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    Ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    Rz = np.array([[math.cos(az), -math.sin(az), 0], [math.sin(az), math.cos(az), 0], [0, 0, 1]])
    p = pts @ (Rz @ Ry @ Rx).T
    z = f + p[:, 2]
    return np.stack([cx + p[:, 0] * f / z, cy + p[:, 1] * f / z], 1).astype(np.float32)


class Compositor:
    def __init__(self, specs, caps=None, style=None):
        if style is None:
            sys.path.insert(0, str(Path(__file__).parent))
            import brand
            style = brand.load()["fx"]
        configure(style)
        self.els = []
        side = 1
        for sp in specs:
            t0 = sp.get("t")
            if t0 is None and "cap" in sp and caps:
                t0 = caps[min(int(sp["cap"]), len(caps) - 1)]["start"]
            if t0 is None:
                continue
            typ = sp.get("type", "card")
            dur = float(sp.get("dur", DEFAULT_DUR.get(
                typ, 1.4 + float(sp.get("step", 0.32)) * len(sp.get("items", [])) + 1.4)))
            el = TYPES[typ](sp, float(t0), dur)
            pos = sp.get("pos", "auto")
            if "x" in sp and "y" in sp:
                el.cx, el.cy = float(sp["x"]), float(sp["y"])
            elif pos in POS:
                el.cx, el.cy = POS[pos]
            else:
                el.cx, el.cy = POS["right" if side > 0 else "left"]
                side = -side
            h, w = el.base.shape[:2]
            el.cx = min(max(el.cx, w / 2 + 28), W - w / 2 - 28)
            el.cy = min(max(el.cy, h / 2 + 60), H - h / 2 - 60)
            el.dir = -1 if el.cx > W / 2 else 1  # разворот к центру кадра
            self.els.append(el)

    def apply(self, frame, t):
        """frame — uint8 RGB H×W×3, меняется на месте."""
        for el in self.els:
            tr = t - el.t0
            if not 0 <= tr < el.dur:
                continue
            p_in = tr / IN_DUR
            p_out = (el.dur - tr) / OUT_DUR
            op = ease_out_cubic(p_in * 1.6) * ease_out_cubic(p_out)
            ent, tilt = ST.get("entrance", "swing"), float(ST.get("tilt", 14))
            fl = 1.0 if ST.get("float", True) else 0.0
            rest = 1 - ease_out_cubic(p_in)
            s_in = {"swing": 0.8 + 0.2 * ease_out_back(p_in),
                    "pop": 0.55 + 0.45 * ease_out_back(p_in * 1.15, 2.2)}.get(ent, 1.0)
            s = s_in * (0.94 + 0.06 * ease_out_cubic(p_out))
            swing = rest if ent == "swing" else 0.0
            ry = el.dir * (tilt + 30 * swing) + fl * 2.5 * math.sin(2 * math.pi * tr / 4.1)
            rx = tilt / 2 + fl * 1.5 * math.sin(2 * math.pi * tr / 3.3)
            rz = -el.dir * 2.0 * (tilt / 14)
            dy = ({"swing": 70, "slide": 140}.get(ent, 0) * rest
                  + fl * 7 * math.sin(2 * math.pi * tr / 3.2) - 26 * (1 - ease_out_cubic(p_out)))
            if op <= 0.003:
                continue
            layer, mask = el.local(tr)
            h, w = layer.shape[:2]
            dst = project(w, h, el.cx, el.cy + dy, s, rx, ry, rz)
            m = 48  # поле под тень
            x0, y0 = int(max(dst[:, 0].min() - m, 0)), int(max(dst[:, 1].min() - m, 0))
            x1, y1 = int(min(dst[:, 0].max() + m, W)), int(min(dst[:, 1].max() + m + 20, H))
            if x1 <= x0 or y1 <= y0:
                continue
            src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
            M = cv2.getPerspectiveTransform(src, dst - np.array([x0, y0], np.float32))
            size = (x1 - x0, y1 - y0)
            lay = cv2.warpPerspective(layer, M, size, flags=cv2.INTER_LINEAR)
            roi = frame[y0:y1, x0:x1].astype(np.float32)
            sil = cv2.warpPerspective(mask if mask is not None else layer[..., 3], M, size)
            # тень
            sh = cv2.GaussianBlur(sil, (0, 0), 16)
            sh = np.roll(sh, 18, axis=0)
            roi *= (1 - float(ST.get("shadow", 0.5)) * op * sh)[..., None]
            # матовое стекло: размытое видео под плашкой (затемнённое или осветлённое)
            surface = ST.get("surface", "glass-dark")
            if mask is not None and surface.startswith("glass"):
                blur = cv2.GaussianBlur(roi, (0, 0), float(ST.get("blur", 14)))
                d = float(ST.get("darken", 0.5))
                blur = blur * d if surface == "glass-dark" else blur * (1 - d * 0.6) + 255 * d * 0.6
                g = (sil * op)[..., None]
                roi = roi * (1 - g) + blur * g
            roi = lay[..., :3] * op + roi * (1 - lay[..., 3:4] * op)
            frame[y0:y1, x0:x1] = np.clip(roi, 0, 255).astype(np.uint8)
        return frame


def load(wd):
    f = wd / "fx.json"
    if not f.exists():
        return None
    specs = json.loads(f.read_text())
    caps_f = wd / "captions.json"
    caps = json.loads(caps_f.read_text()) if caps_f.exists() else []
    return Compositor(specs, caps) if specs else None


def main():
    import subprocess
    sys.path.insert(0, str(Path(__file__).parent))
    from common import workdir_for

    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["still"])
    ap.add_argument("video", help="исходник — по нему находится рабочая папка с fx.json")
    ap.add_argument("--t", type=float, required=True, help="время по шкале captions.json")
    ap.add_argument("--from", dest="src", required=True,
                    help="собранный ролик без эффектов, из которого взять кадр")
    ap.add_argument("--offset", type=float, default=0.1, help="длина обложки в начале ролика")
    ap.add_argument("-o", "--out", default="fx_still.png")
    a = ap.parse_args()
    comp = load(workdir_for(a.video))
    if not comp:
        raise SystemExit("Нет fx.json")
    buf = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a.t + a.offset:.3f}", "-i", a.src,
                          "-frames:v", "1", "-vf", f"scale={W}:{H}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
    comp.apply(frame, a.t)
    Image.fromarray(frame).save(a.out)
    print(a.out)


if __name__ == "__main__":
    main()

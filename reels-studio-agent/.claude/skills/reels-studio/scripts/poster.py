"""Обложка-постер: огромный заголовок из 1–2 слов + человек, вырезанный из кадра.

Вид задаёт профиль стиля (brand.json → cover), выведенный из референсов пользователя:
  layout     knockout — сквозь буквы видно фото; text-behind — сплошные буквы за человеком
  bg         paper | solid | photo-blur
  title_fill photo | solid;  front_word graphite | accent | solid
  person     sandwich (перед первым словом, за вторым) | front | none
  и шрифт, цвета, растяжка букв, зерно, «вырезка в бумаге», мелкая типографика.
Человека вырезает rembg (u2net_human_seg).
"""
import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

from fx import FONTS

W, H = 1080, 1920
PAPER = np.array([236, 233, 227], np.float32)
INK = (26, 26, 26)
LIME = np.array([200, 255, 46], np.float32)
TITLE_FONT = None  # путь к шрифту заголовка — задаёт make_poster по стилю
MARGIN = 54


def paper_bg(seed=7):
    rng = np.random.default_rng(seed)
    grain = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 0.8) * 5
    fibers = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 18) * 9
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    vign = 1 - 0.07 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    return np.clip(PAPER[None, None] * vign[..., None] + (grain + fibers)[..., None], 0, 255)


def person_mask(img):
    """Маска людей (0..1) того же размера, что img (RGB uint8)."""
    try:
        from rembg import new_session, remove
    except ImportError:
        return None
    h, w = img.shape[:2]
    sc = 1280 / max(h, w)
    small = Image.fromarray(img).resize((int(w * sc), int(h * sc)), Image.LANCZOS)
    m = remove(small, session=new_session("u2net_human_seg"), only_mask=True)
    m = np.asarray(m.resize((w, h), Image.LANCZOS), np.float32) / 255
    return m


def main_person(mask, face_xy=None):
    """Оставляет одного человека: того, у кого лицо, иначе самого крупного."""
    bin_ = (mask > 0.5).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bin_)
    if n <= 1:
        return None, None
    pick = None
    if face_xy is not None:
        fx, fy = int(face_xy[0] * mask.shape[1]), int(face_xy[1] * mask.shape[0])
        # ищем компоненту рядом с лицом (лицо может чуть выходить за маску)
        for r in (0, 15, 40):
            win = lab[max(fy - r, 0):fy + r + 1, max(fx - r, 0):fx + r + 1]
            ids = [i for i in np.unique(win) if i]
            if ids:
                pick = max(ids, key=lambda i: stats[i, cv2.CC_STAT_AREA])
                break
    if pick is None:
        pick = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    keep = (lab == pick).astype(np.float32)
    keep = cv2.dilate(keep, np.ones((5, 5), np.uint8))
    # подрезаем край: по контуру маска цепляет фон и даёт светлый ореол
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=2)
    x, y, w, h = stats[pick, :4]
    return mask * keep, (x, y, w, h)


def crop_to_person(img, mask, box, fill=0.6):
    """Кадр 9:16 вокруг человека: рост ≈ fill от высоты, человек по центру, ноги у низа."""
    ih, iw = img.shape[:2]
    x, y, w, h = box
    ch = min(ih, max(h / fill, iw * 16 / 9 * 0.35))
    cw = ch * 9 / 16
    if cw > iw:
        cw, ch = iw, iw * 16 / 9
    cx = x + w / 2
    y1 = min(ih, y + h + ch * 0.15)  # ноги заканчиваются над нижней строкой текста
    x0 = int(min(max(cx - cw / 2, 0), iw - cw))
    y0 = int(min(max(y1 - ch, 0), ih - ch))
    sl = (slice(y0, y0 + int(ch)), slice(x0, x0 + int(cw)))
    out = cv2.resize(img[sl], (W, H), interpolation=cv2.INTER_LANCZOS4)
    m = cv2.resize(mask[sl], (W, H), interpolation=cv2.INTER_LINEAR) if mask is not None else None
    return out, m


def cover_fit(img):
    ih, iw = img.shape[:2]
    sc = max(W / iw, H / ih)
    r = cv2.resize(img, (int(iw * sc + 0.5), int(ih * sc + 0.5)), interpolation=cv2.INTER_LANCZOS4)
    y0, x0 = (r.shape[0] - H) // 2, (r.shape[1] - W) // 2
    return r[y0:y0 + H, x0:x0 + W]


def grade(img):
    """Журнальная обработка: плотнее контраст, чуть меньше насыщенности, зерно."""
    f = img.astype(np.float32) / 255
    f = np.clip((f - 0.5) * 1.12 + 0.5, 0, 1)
    gray = f.mean(axis=2, keepdims=True)
    f = gray + (f - gray) * 0.88
    rng = np.random.default_rng(3)
    f = f + rng.normal(0, 0.012, f.shape[:2])[..., None]
    return np.clip(f * 255, 0, 255).astype(np.float32)


def word_mask(word, box_w, max_h, stretch=2.7):
    """Слово огромными узкими буквами: ширина box_w, по высоте тянется до stretch раз
    (Oswald, вытянутый по вертикали, даёт плакатный ультраузкий силуэт)."""
    f = ImageFont.truetype(str(TITLE_FONT), 600)
    l, t, r, b = f.getbbox(word)
    img = Image.new("L", (r - l + 20, b - t + 20))
    ImageDraw.Draw(img).text((10 - l, 10 - t), word, font=f, fill=255)
    m = np.asarray(img, np.float32) / 255
    ys, xs = np.nonzero(m > 0.05)
    m = m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    nat_h = box_w * m.shape[0] / m.shape[1]
    h = int(min(max_h, nat_h * stretch))
    return cv2.resize(m, (box_w, max(h, 1)), interpolation=cv2.INTER_AREA)


def tracked(draw, xy, text, font, fill, spacing, anchor_right=False):
    """Текст с разрядкой (PIL её не умеет)."""
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x, y = xy
    if anchor_right:
        x -= total
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + spacing
    return total


def stamp(text, size=150):
    """Лаймовый штамп: квадратная рамка, монограмма, неровная «краска»."""
    s = size * 2
    img = Image.new("L", (s, s))
    d = ImageDraw.Draw(img)
    d.rectangle((8, 8, s - 9, s - 9), outline=255, width=14)
    f = ImageFont.truetype(str(TITLE_FONT), int(s * 0.62))
    l, t, r, b = f.getbbox(text)
    d.text(((s - (r - l)) / 2 - l, (s - (b - t)) / 2 - t), text, font=f, fill=255)
    m = np.asarray(img.resize((size, size), Image.LANCZOS), np.float32) / 255
    rng = np.random.default_rng(11)
    ink = cv2.GaussianBlur(rng.random((size, size)).astype(np.float32), (0, 0), 1.2)
    return m * np.clip(0.55 + ink, 0, 1)


def _rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def apply_style(style, brand):
    """Глобальные цвета и шрифт заголовка — из профиля стиля."""
    global PAPER, INK, LIME, TITLE_FONT
    import brand as brandmod
    PAPER = _rgb(style["bg_color"])
    INK = tuple(int(c) for c in _rgb(style["ink"]))
    LIME = _rgb(style.get("accent") or brand["subtitles"]["accent"] or "#C8FF2E")
    path, _ = brandmod.install_font(style["title_font"], int(style["title_weight"]), "title")
    TITLE_FONT = path


def make_poster(photo, title_words, face_xy=None, kicker="", year="", note="", issue="",
                side="", stamp_text="", accent=None, pop_out=True, style=None, brand=None):
    """photo — RGB uint8 любого размера; title_words — 1–2 слова; accent — номер слова
    в цвете акцента. Возвращает RGB uint8 1080×1920."""
    import brand as brandmod
    brand = brand or brandmod.load()
    st = brandmod.merge(brandmod.DEFAULT["cover"], style or brand["cover"])
    apply_style(st, brand)
    dec = st["decor"]
    kicker, year, note = (kicker if dec["kicker"] else ""), (year if dec["year"] else ""), \
        (note if dec["note"] else "")
    issue, side, stamp_text = (issue if dec["issue"] else ""), (side if dec["side"] else ""), \
        (stamp_text if dec["stamp"] else "")
    person_mode = st["person"] if pop_out else "none"

    mask = person_mask(photo) if person_mode != "none" else None
    box = None
    if mask is not None:
        mask, box = main_person(mask, face_xy)
    if box is not None:
        img, pmask = crop_to_person(photo, mask, box)
    else:
        img, pmask = cover_fit(photo), None
    img = grade(img)

    # Заголовок: слова друг под другом во всю ширину
    words = [w.upper() if st["title_case"] == "upper" else w for w in title_words][:2]
    top, bottom, gap = 170, 1660, 22
    left = MARGIN + (44 if side else 0)  # место под вертикальную подпись
    box_w = W - left - MARGIN
    per = (bottom - top - gap * (len(words) - 1)) // len(words)
    masks = [word_mask(w, box_w, per, float(st["stretch"])) for w in words]
    block = sum(m.shape[0] for m in masks) + gap * (len(masks) - 1)
    y = top + (bottom - top - block) // 2
    letters = np.zeros((H, W), np.float32)
    front = np.zeros((H, W), np.float32)
    accent_m = np.zeros((H, W), np.float32)
    for i, m in enumerate(masks):
        letters[y:y + m.shape[0], left:left + box_w] = np.maximum(
            letters[y:y + m.shape[0], left:left + box_w], m)
        if accent == i:
            accent_m[y:y + m.shape[0], left:left + box_w] = m
        if i == len(masks) - 1 and len(masks) > 1 and person_mode == "sandwich":
            front[y:y + m.shape[0], left:left + box_w] = m  # последнее слово — перед человеком
        y += m.shape[0] + gap

    # фон
    if st["bg"] == "photo-blur":
        out = cv2.GaussianBlur(img, (0, 0), 28) * 0.55
    else:
        out = paper_bg() if st["grain"] else np.broadcast_to(PAPER, (H, W, 3)).astype(np.float32)
        if st["bg"] == "solid" and not st["grain"]:
            out = np.broadcast_to(PAPER, (H, W, 3)).astype(np.float32).copy()

    # заливка букв
    knockout = st["layout"] == "knockout" and st["title_fill"] == "photo"
    inside = img.copy() if knockout else np.broadcast_to(_rgb(st["title_color"]), (H, W, 3)).astype(np.float32).copy()
    if accent is not None:
        if knockout:
            lum = inside.mean(axis=2, keepdims=True)
            sel = accent_m > 0.5
            lo, hi = np.percentile(lum[sel], (4, 96)) if sel.any() else (0, 255)
            lum = np.clip((lum - lo) / max(hi - lo, 1), 0, 1) ** 0.8
            fill = (1 - lum) * np.array(INK, np.float32) + lum * LIME
        else:
            fill = np.broadcast_to(LIME, (H, W, 3))
        inside = inside * (1 - accent_m[..., None]) + fill * accent_m[..., None]
    # слово перед человеком не должно сливаться с ним (оно залито тем же фото)
    dark_m = front * (1 - accent_m) if pmask is not None else np.zeros_like(front)
    if knockout and (dark_m > 0.5).any():
        if st["front_word"] == "accent":
            col = (np.broadcast_to(LIME, (H, W, 3)))
        elif st["front_word"] == "solid":
            col = np.broadcast_to(_rgb(st["title_color"]), (H, W, 3))
        else:
            lum = inside.mean(axis=2, keepdims=True)
            lo, hi = np.percentile(lum[dark_m > 0.5], (4, 96))
            lum = np.clip((lum - lo) / max(hi - lo, 1), 0, 1)
            col = (1 - lum) * np.array([22, 22, 24], np.float32) + lum * np.array([92, 94, 98], np.float32)
        inside = inside * (1 - dark_m[..., None]) + col * dark_m[..., None]
    if knockout:
        # светлые места фото внутри букв приглушаем попиксельно — буквы темнее фона
        lum = inside @ np.array([0.299, 0.587, 0.114], np.float32)
        bg_lum = float(PAPER @ np.array([0.299, 0.587, 0.114], np.float32))
        knee = min(105.0, bg_lum * 0.48)
        target = np.where(lum > knee, knee + (lum - knee) * 0.3, lum)
        inside = inside * (target / np.maximum(lum, 1))[..., None]
    if st["paper_cut"] and st["bg"] != "photo-blur":
        shadow = cv2.GaussianBlur(1 - letters, (0, 0), 9)
        shadow = np.roll(np.roll(shadow, 12, axis=0), 9, axis=1) * letters
        inside = inside * (1 - 0.6 * shadow[..., None])
    out = out * (1 - letters[..., None]) + inside * letters[..., None]
    if knockout:
        # тёмная кромка по контуру — форма буквы читается даже на светлом фото
        edge = np.clip(letters - cv2.erode(letters, np.ones((5, 5), np.uint8)), 0, 1)
        out = out * (1 - 0.55 * edge[..., None])
    if st["paper_cut"] and st["bg"] != "photo-blur":
        rim = np.clip(letters - np.roll(np.roll(letters, 2, 0), 2, 1), 0, 1)  # толщина бумаги
        out = out * (1 - 0.6 * rim[..., None]) + PAPER * 1.04 * (0.6 * rim[..., None])

    # человек перед буквами, с мягкой тенью
    if pmask is not None:
        pm = cv2.GaussianBlur(pmask, (0, 0), 1.2)
        pm = pm * (1 - front)  # sandwich: человек за последним словом
        sh = np.roll(np.roll(cv2.GaussianBlur(pm, (0, 0), 16), 22, 0), 14, 1)
        out = out * (1 - 0.32 * sh[..., None])
        out = out * (1 - pm[..., None]) + img * pm[..., None]
    ink_rgb = INK if st["bg"] != "photo-blur" else (240, 240, 240)
    # журнальная типографика
    pil = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pil)
    small = ImageFont.truetype(str(FONTS / "Inter-Medium.otf"), 26)
    INKc = ink_rgb
    if kicker or year:
        wl = tracked(d, (MARGIN, 96), kicker.upper(), small, INKc, 7) if kicker else 0
        wr = tracked(d, (W - MARGIN, 96), year, small, INKc, 7, anchor_right=True) if year else 0
        x0, x1 = MARGIN + wl + 28, W - MARGIN - wr - 28
        if x1 > x0:
            d.line((x0, 112, x1, 112), fill=INKc, width=2)
    if note:
        for i, line in enumerate(note.upper().split("|")[:3]):
            tracked(d, (MARGIN, 1700 + i * 40), line.strip(), small, INKc, 6)
    if issue:
        f = ImageFont.truetype(str(FONTS / "Inter-SemiBold.otf"), 34)
        w = tracked(d, (W - MARGIN, 1772), issue.upper(), f, INKc, 3, anchor_right=True)
        d.line((W - MARGIN - w, 1822, W - MARGIN, 1822), fill=INKc, width=2)
    if side:
        f = ImageFont.truetype(str(FONTS / "Inter-Medium.otf"), 30)
        tw = int(sum(f.getlength(c) for c in side.upper()) + 10 * len(side)) + 20
        lay = Image.new("RGBA", (tw, 44), (0, 0, 0, 0))
        tracked(ImageDraw.Draw(lay), (10, 4), side.upper(), f, INKc + (255,), 10)
        lay = lay.rotate(90, expand=True)
        pil.paste(lay, (14, (H - lay.height) // 2), lay)
    out = np.asarray(pil, np.float32)
    if stamp_text:
        sm = stamp(stamp_text[:3].upper(), 150)
        sm = cv2.warpAffine(sm, cv2.getRotationMatrix2D((75, 75), -6, 1.0), (150, 150))
        x0, y0 = W - MARGIN - 150, 1575
        reg = out[y0:y0 + 150, x0:x0 + 150]
        lum = float(LIME @ np.array([0.299, 0.587, 0.114], np.float32))
        ink = LIME * min(1.0, 110 / max(lum, 1))  # тёмный оттенок акцента — читается на бумаге
        a = (sm * 0.92)[..., None]
        out[y0:y0 + 150, x0:x0 + 150] = reg * (1 - a) + (reg * ink / 255 * 0.35 + ink * 0.65) * a
    return np.clip(out, 0, 255).astype(np.uint8)

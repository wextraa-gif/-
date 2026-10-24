#!/usr/bin/env python3
"""Фирменный стиль пользователя: шрифт и цвет субтитров, стиль анимаций, стиль обложек.

Все скрипты монтажа берут оформление отсюда — из brand/brand.json.
Пока онбординг не пройден (status ≠ ready), монтаж не запускается.

Команды:
  brand.py status                      — что уже настроено и чего не хватает
  brand.py fonts                       — каталог бесплатных шрифтов
  brand.py fonts-preview [-o f.png]    — сетка образцов субтитров всеми шрифтами
  brand.py set-font <ключ> [--weight 600]
  brand.py set-accent "#C8FF2E"
  brand.py add-ref fx|cover <файл или ссылка> [...]   — сохранить референсы
  brand.py set fx '<json>' | set cover '<json>' | set subtitles '<json>'
  brand.py board [-o board.png] [--frame кадр.jpg]  — превью всего стиля на одном листе
  brand.py approve                     — пользователь одобрил стиль → можно монтировать
  brand.py reset                       — начать онбординг заново
"""
import argparse
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
BRAND_DIR = SKILL / "brand"
BRAND = BRAND_DIR / "brand.json"
FONTS = SKILL / "fonts"
GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"

# Бесплатные шрифты с кириллицей (SIL Open Font License — можно в коммерции)
CATALOG = {
    "oswald":     ("Oswald", "oswald/Oswald[wght].ttf", "узкий вытянутый, плакатный"),
    "robotocond": ("Roboto Condensed", "robotocondensed/RobotoCondensed[wght].ttf", "узкий, нейтральный"),
    "ptnarrow":   ("PT Sans Narrow", "ptsansnarrow/PT_Sans-Narrow-Web-Bold.ttf", "узкий, классика (только жирный)"),
    "montserrat": ("Montserrat", "montserrat/Montserrat[wght].ttf", "геометрический, популярный в Reels"),
    "inter":      ("Inter", "inter/Inter[opsz,wght].ttf", "нейтральный, интерфейсный"),
    "golos":      ("Golos Text", "golostext/GolosText[wght].ttf", "современный гротеск"),
    "onest":      ("Onest", "onest/Onest[wght].ttf", "мягкий гротеск"),
    "manrope":    ("Manrope", "manrope/Manrope[wght].ttf", "технологичный"),
    "rubik":      ("Rubik", "rubik/Rubik[wght].ttf", "округлые углы, дружелюбный"),
    "nunito":     ("Nunito", "nunito/Nunito[wght].ttf", "округлый, мягкий"),
    "jost":       ("Jost", "jost/Jost[wght].ttf", "геометрический, в духе Futura"),
    "unbounded":  ("Unbounded", "unbounded/Unbounded[wght].ttf", "широкий, дерзкий"),
    "exo2":       ("Exo 2", "exo2/Exo2[wght].ttf", "техно, футуризм"),
    "russo":      ("Russo One", "russoone/RussoOne-Regular.ttf", "спортивный, жирный (одно начертание)"),
    "comfortaa":  ("Comfortaa", "comfortaa/Comfortaa[wght].ttf", "круглый, лёгкий"),
    "playfair":   ("Playfair Display", "playfairdisplay/PlayfairDisplay[wght].ttf", "антиква, журнальная"),
}

ACCENTS = {"лайм": "#C8FF2E", "жёлтый": "#FFD60A", "коралловый": "#FF5A5F",
           "голубой": "#3DD6FF", "розовый": "#FF4FD8", "оранжевый": "#FF8A00"}

DEFAULT = {
    "version": 1,
    "status": "new",  # new → configured → ready (после approve)
    "subtitles": {
        "font": None, "weight": 600, "font_file": None, "font_name": None,
        "size": 118, "color": "#FFFFFF", "accent": None, "case": "as-is",
        "plate": "glass-dark",      # glass-dark | glass-light | solid | none
        "plate_color": "#000000", "plate_opacity": 0.42,
        "max_words": 3, "y": 1250,
    },
    "fx": {
        "configured": False, "refs": [], "notes": "",
        "surface": "glass-dark",    # glass-dark | glass-light | solid | outline
        "fill": "#FFFFFF", "fill_opacity": 0.11, "darken": 0.5, "blur": 14,
        "radius": 40, "rim_color": "#ECF0F7", "rim_width": 3, "rim_opacity": 0.95,
        "shadow": 0.5, "sheen": True, "tilt": 14, "float": True,
        "entrance": "swing",        # swing | pop | slide | fade
        "icon_finish": "chrome",    # chrome | flat | outline
        "icon_color": "#ECF0F7", "text_color": "#FFFFFF", "muted_color": "#C4CAD4",
        "ui_font": "inter", "accent": None,
    },
    "cover": {
        "configured": False, "refs": [], "notes": "",
        "layout": "knockout",       # knockout | text-behind | glass
        "bg": "paper",              # paper | solid | photo-blur
        "bg_color": "#ECE9E3", "ink": "#1A1A1A",
        "title_font": "oswald", "title_weight": 700, "title_case": "upper",
        "title_fill": "photo",      # photo | solid
        "title_color": "#1A1A1A", "front_word": "graphite",  # graphite | accent | solid
        "stretch": 2.7, "person": "sandwich",  # sandwich | front | none
        "grain": True, "paper_cut": True,
        "decor": {"kicker": True, "year": True, "note": True, "issue": True,
                  "side": True, "stamp": True},
        "accent": None,
    },
}


# ---------- хранение ----------

def merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load():
    data = json.loads(BRAND.read_text()) if BRAND.exists() else {}
    return merge(DEFAULT, data)


def save(b):
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    BRAND.write_text(json.dumps(b, ensure_ascii=False, indent=1))


def missing(b):
    m = []
    if not b["subtitles"]["font_file"] or not Path(SKILL / b["subtitles"]["font_file"]).exists():
        m.append("шрифт субтитров")
    if not b["subtitles"]["accent"]:
        m.append("цвет акцентных слов")
    if not b["fx"]["configured"]:
        m.append("стиль анимаций (референсы)")
    if not b["cover"]["configured"]:
        m.append("стиль обложек (референсы)")
    return m


def require_ready():
    """Вызывается перед монтажом: без пройденного онбординга работать нельзя."""
    b = load()
    miss = missing(b)
    if miss or b["status"] != "ready":
        what = ", ".join(miss) if miss else "одобрение стиля пользователем (brand.py approve)"
        raise SystemExit(f"Онбординг не пройден — не хватает: {what}. "
                         "Сначала настройте стиль (SKILL.md, раздел «Онбординг»).")
    return b


# ---------- цвета ----------

def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def ass_color(h, alpha=0):
    r, g, b = hex_rgb(h)
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def ink_for(h):
    """Тёмный вариант цвета — для штампа и мелких элементов на светлой бумаге."""
    r, g, b = hex_rgb(h)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    k = min(1.0, 110 / max(lum, 1))
    return tuple(int(c * k) for c in (r, g, b))


def accent(b, part):
    return b[part].get("accent") or b["subtitles"]["accent"] or "#C8FF2E"


# ---------- шрифты ----------

def install_font(key, weight=600, role="sub"):
    """Скачивает шрифт из Google Fonts, делает статичное начертание weight и даёт ему
    уникальное имя (чтобы libass не перепутал с системным). → (файл, имя семейства)."""
    if key not in CATALOG:
        raise SystemExit(f"Нет шрифта «{key}». Есть: {', '.join(CATALOG)}")
    family, path, _ = CATALOG[key]
    FONTS.mkdir(exist_ok=True)
    out = FONTS / f"{role}-{key}-{weight}.ttf"
    name = f"RS {family} {role} {weight}"
    if out.exists():
        return out, name
    url = GF + path.replace("[", "%5B").replace("]", "%5D")
    raw = FONTS / f".dl-{key}.ttf"
    urllib.request.urlretrieve(url, raw)
    from fontTools.ttLib import TTFont
    font = TTFont(raw)
    if "fvar" in font:
        from fontTools.varLib import instancer
        axes = {a.axisTag: (min(max(weight, a.minValue), a.maxValue) if a.axisTag == "wght"
                            else a.defaultValue) for a in font["fvar"].axes}
        font = instancer.instantiateVariableFont(font, axes)
    for rec in font["name"].names:
        if rec.nameID in (1, 4, 16):
            rec.string = name
        elif rec.nameID == 6:
            rec.string = re.sub(r"[^A-Za-z0-9-]", "", name.replace(" ", "-"))
        elif rec.nameID in (2, 17):
            rec.string = "Regular"
    font.save(out)
    raw.unlink()
    return out, name


def fonts_preview(out, accent_hex=None, keys=None):
    """Сетка: каждый шрифт — образец титра «Главная ошибка в монтаже» с акцентом."""
    from PIL import Image, ImageDraw, ImageFont
    keys = keys or list(CATALOG)
    acc = hex_rgb(accent_hex or "#C8FF2E")
    rows, cols, cw, ch = (len(keys) + 1) // 2, 2, 900, 190
    img = Image.new("RGB", (cw * cols, ch * rows + 20), (18, 19, 22))
    d = ImageDraw.Draw(img)
    label = ImageFont.truetype(str(FONTS / "Inter-Medium.otf"), 24)
    for i, key in enumerate(keys):
        f_path, _ = install_font(key, 600 if key not in ("ptnarrow", "russo") else 400, "preview")
        f = ImageFont.truetype(str(f_path), 74)
        x0, y0 = (i % cols) * cw + 40, (i // cols) * ch + 30
        d.text((x0, y0), f"{i + 1}. {CATALOG[key][0]} — {CATALOG[key][2]}", font=label,
               fill=(150, 156, 166))
        x = x0
        for word, col in (("Главная ", (255, 255, 255)), ("ошибка", acc)):
            d.text((x, y0 + 46), word, font=f, fill=col)
            x += f.getlength(word)
    img.save(out)
    return out


# ---------- референсы ----------

def fetch_ref(src, kind):
    """Файл или ссылка (в т.ч. Pinterest: берём og:image со страницы пина)."""
    dst_dir = BRAND_DIR / "refs" / kind
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = len([p for p in dst_dir.iterdir() if not p.name.startswith(".")]) + 1
    if re.match(r"https?://", src):
        url = src
        if not re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", src, re.I):
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
            m = (re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
                 or re.search(r'"(https://i\.pinimg\.com/originals/[^"]+)"', html)
                 or re.search(r'"(https://i\.pinimg\.com/736x/[^"]+)"', html))
            if not m:
                raise SystemExit(f"Не нашёл картинку на странице {src} — пришлите файл")
            url = m.group(1)
        ext = re.search(r"\.(jpe?g|png|webp|gif)", url, re.I)
        dst = dst_dir / f"{kind}_{n}.{ext.group(1).lower() if ext else 'jpg'}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        dst.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    else:
        p = Path(src)
        dst = dst_dir / f"{kind}_{n}{p.suffix.lower() or '.jpg'}"
        shutil.copy(p, dst)
    b = load()
    b[kind]["refs"].append(str(dst.relative_to(SKILL)))
    save(b)
    return dst


# ---------- превью стиля ----------

def backdrop(frame=None):
    """Фон для превью: кадр пользователя или нейтральный «интерьер» с боке."""
    import numpy as np
    import cv2
    W, H = 1080, 1920
    if frame:
        img = cv2.cvtColor(cv2.imread(str(frame)), cv2.COLOR_BGR2RGB)
        sc = max(W / img.shape[1], H / img.shape[0])
        img = cv2.resize(img, (int(img.shape[1] * sc) + 1, int(img.shape[0] * sc) + 1))
        y0, x0 = (img.shape[0] - H) // 2, (img.shape[1] - W) // 2
        return img[y0:y0 + H, x0:x0 + W].copy()
    rng = np.random.default_rng(5)
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    img = (np.array([70, 62, 58], np.float32) * (1 - yy) + np.array([28, 30, 36], np.float32) * yy)
    img = np.repeat(np.repeat(img, W, axis=1), 1, axis=2)
    for _ in range(26):
        x, y, r = rng.integers(0, W), rng.integers(0, H), rng.integers(30, 140)
        c = rng.choice([[255, 200, 140], [180, 200, 255], [255, 240, 220]])
        blob = np.zeros((H, W), np.float32)
        cv2.circle(blob, (int(x), int(y)), int(r), 1.0, -1)
        img += cv2.GaussianBlur(blob, (0, 0), r / 2)[..., None] * np.array(c, np.float32) * 0.25
    return np.clip(img, 0, 255).astype(np.uint8)


def board(out, frame=None):
    """Лист согласования: титры, анимации и обложка в выбранном стиле."""
    import numpy as np
    from PIL import Image
    sys.path.insert(0, str(Path(__file__).parent))
    import captions
    import fx as fxmod
    import poster
    b = load()
    bg = backdrop(frame)
    tiles = []
    # 1) титры
    sub = captions.render_still(bg.copy(), ["Главная", "ошибка"], 1)
    tiles.append(sub)
    # 2) анимации
    specs = [{"type": "card", "t": 0, "title": "Удержание", "value": "+120%", "icon": "arrow_up",
              "accent": True, "pos": "right", "countup": False},
             {"type": "pill", "t": 0, "text": "Без воды", "icon": "check", "pos": "top-left"},
             {"type": "list", "t": 0, "title": "Чек-лист", "items": ["Паузы", "Титры", "Обложка"],
              "pos": "low-left", "step": 0.0},
             {"type": "icon", "t": 0, "icon": "cursor", "size": 150, "x": 860, "y": 1300}]
    comp = fxmod.Compositor(specs, [], style=b["fx"])
    tiles.append(comp.apply(bg.copy(), 1.6))
    # 3) обложка
    cov = poster.make_poster(bg.copy(), ["Свой", "чек"], accent=0, pop_out=bool(frame),
                             kicker="Рубрика", year="2026", note="Почему страшно|назвать цену",
                             issue="Вып. 01", side="тема ролика", stamp_text="АБ", style=b["cover"],
                             brand=b)
    tiles.append(cov)
    sheet = np.concatenate([np.asarray(Image.fromarray(t).resize((540, 960))) for t in tiles], 1)
    Image.fromarray(sheet).save(out)
    return out


# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status")
    sp.add_parser("fonts")
    p = sp.add_parser("fonts-preview")
    p.add_argument("-o", "--out", default=str(BRAND_DIR / "fonts_preview.png"))
    p.add_argument("--keys", help="через запятую; по умолчанию все")
    p = sp.add_parser("set-font")
    p.add_argument("key")
    p.add_argument("--weight", type=int, default=600)
    p = sp.add_parser("set-accent")
    p.add_argument("color")
    p = sp.add_parser("add-ref")
    p.add_argument("kind", choices=["fx", "cover"])
    p.add_argument("src", nargs="+")
    p = sp.add_parser("set")
    p.add_argument("part", choices=["fx", "cover", "subtitles"])
    p.add_argument("json")
    p = sp.add_parser("board")
    p.add_argument("-o", "--out", default=str(BRAND_DIR / "style_board.png"))
    p.add_argument("--frame", help="кадр из ролика пользователя для фона")
    sp.add_parser("approve")
    sp.add_parser("reset")
    a = ap.parse_args()

    b = load()
    if a.cmd == "status":
        miss = missing(b)
        s = b["subtitles"]
        print(f"Статус: {b['status']}")
        print(f"  шрифт субтитров: {CATALOG.get(s['font'], ['—'])[0]} {s['weight'] if s['font'] else ''}")
        print(f"  акцент: {s['accent'] or '—'}")
        print(f"  анимации: {'настроены' if b['fx']['configured'] else '—'} "
              f"(референсов: {len(b['fx']['refs'])})")
        print(f"  обложки: {'настроены' if b['cover']['configured'] else '—'} "
              f"(референсов: {len(b['cover']['refs'])})")
        print("Не хватает: " + ", ".join(miss) if miss else
              ("Готово к работе." if b["status"] == "ready" else "Осталось показать лист стиля и получить одобрение."))
    elif a.cmd == "fonts":
        for i, (k, (fam, _, desc)) in enumerate(CATALOG.items(), 1):
            print(f"{i:2}. {k:11} {fam} — {desc}")
    elif a.cmd == "fonts-preview":
        keys = a.keys.split(",") if a.keys else None
        print(fonts_preview(a.out, b["subtitles"]["accent"], keys))
    elif a.cmd == "set-font":
        f, name = install_font(a.key, a.weight, "sub")
        b["subtitles"].update(font=a.key, weight=a.weight, font_file=str(f.relative_to(SKILL)),
                              font_name=name)
        b["status"] = "configured" if not missing(b) else "new"
        save(b)
        print(f"Шрифт субтитров: {CATALOG[a.key][0]} {a.weight} → {f.name}")
    elif a.cmd == "set-accent":
        if not re.fullmatch(r"#?[0-9A-Fa-f]{6}", a.color):
            raise SystemExit("Цвет нужен в виде #RRGGBB")
        b["subtitles"]["accent"] = "#" + a.color.lstrip("#").upper()
        b["status"] = "configured" if not missing(b) else "new"
        save(b)
        print(f"Акцент: {b['subtitles']['accent']}")
    elif a.cmd == "add-ref":
        for src in a.src:
            print(fetch_ref(src, a.kind))
    elif a.cmd == "set":
        upd = json.loads(a.json)
        b[a.part] = merge(b[a.part], upd)
        if a.part in ("fx", "cover"):
            b[a.part]["configured"] = True
        if a.part == "cover" and b["cover"].get("title_font"):
            key = b["cover"]["title_font"]
            f, name = install_font(key, int(b["cover"].get("title_weight", 700)), "title")
            b["cover"]["title_font_file"] = str(f.relative_to(SKILL))
        b["status"] = "configured" if not missing(b) else "new"
        save(b)
        print(json.dumps(b[a.part], ensure_ascii=False, indent=1))
    elif a.cmd == "board":
        print(board(a.out, a.frame))
    elif a.cmd == "approve":
        miss = missing(b)
        if miss:
            raise SystemExit("Нельзя одобрить — не хватает: " + ", ".join(miss))
        b["status"] = "ready"
        save(b)
        print("Стиль одобрен — можно монтировать.")
    elif a.cmd == "reset":
        if BRAND.exists():
            BRAND.unlink()
        print("Профиль стиля сброшен — нужен онбординг.")


if __name__ == "__main__":
    main()

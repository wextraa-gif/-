#!/usr/bin/env python3
"""Субтитры: группировка по 2–3 слова, акцентное слово, ASS-файлы.

Шаг 1 (build): words.json + edl.json → captions.json во временной шкале готового ролика.
  В captions.json можно поправить текст и акцент (поле "accent" — номер слова
  в "words" или null), затем отрендерить.
  Поле "zoom" — плавный наезд камеры на этом титре (true/false).
Шаг 2 (ass): captions.json → captions.ass (текст) и plates.ass (маска «стекла»).

Использование:
  captions.py build video.mp4
  captions.py ass video.mp4 [--y 1250]
"""
import argparse
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).parent))
import brand as brandmod  # noqa: E402
from common import workdir_for  # noqa: E402

SKILL = Path(__file__).resolve().parent.parent
_SUB = brandmod.load()["subtitles"]  # оформление — из профиля стиля пользователя
FONT_FILE = SKILL / (_SUB["font_file"] or "fonts/Inter-SemiBold.otf")
FONT_NAME = _SUB["font_name"] or "Inter SemiBold"

W, H = 1080, 1920
STYLE = {
    "size": _SUB["size"],            # кегль ASS
    "y": _SUB["y"],                  # центр строки: выше интерфейса Reels/Клипов
    "max_words": _SUB["max_words"],
    "max_width": 860,                # ширина строки, px: с полями плашки остаётся воздух
    "white": brandmod.ass_color(_SUB["color"]),
    "accent": brandmod.ass_color(_SUB["accent"] or "#C8FF2E"),
    "upper": _SUB["case"] == "upper",
    "plate": _SUB["plate"],          # glass-dark | glass-light | solid | none
    "zoom_gap": 4.0,                 # минимум секунд между зумами
    "pad_x": 44, "pad_y": 40, "radius": 34, "feather": 14,
}

FILLER = re.compile(r"^(э+|э-э+|ээ+|м+|мм+|ммм+|а-а+|аа+|эм+|хм+)[.,!?…]*$", re.I)
STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни
быть был него до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть
надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто
этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были
куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти
нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше
чуть том нельзя такой им более всегда конечно всю между это очень просто вообще короче типа
значит которые который которая это""".split())


def clean(word):
    w = word.strip()
    w = re.sub(r"^[«\"'(\[—–-]+", "", w)
    w = re.sub(r"[«»\"'()\[\],.;:…—–]+$", "", w)
    return w


def remap(t, edl):
    """Время исходника → время готового ролика (None, если кусок вырезан)."""
    off = 0.0
    for s in edl:
        if s["start"] - 0.02 <= t <= s["end"] + 0.02:
            return off + min(max(t, s["start"]), s["end"]) - s["start"]
        off += s["end"] - s["start"]
    return None


# Слова, на которых титр не должен обрываться: «для | рилс» читается хуже, чем «| для рилс»
WEAK_END = set("и а но или в во на с со к ко у о об по из за от до для без при не ни что как "
               "чтобы если же ли бы то".split())


def split_phrase(ph):
    """Делит фразу на титры: предпочтительно 2–3 слова, без висячих предлогов."""
    n = len(ph)
    best = [0.0] + [float("inf")] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        for size in range(1, STYLE["max_words"] + 1):
            i = j - size
            if i < 0:
                break
            chunk = ph[i:j]
            width = text_size(" ".join(x["text"] for x in chunk))[0]
            cost = 0.0 if size > 1 else (0.5 if n == 1 else 6.0)
            if width > STYLE["max_width"]:
                cost += 10 + (width - STYLE["max_width"]) / 40
            if j < n and chunk[-1]["text"].lower() in WEAK_END:
                cost += 4
            cost += 0.3 * size  # при равенстве — чуть больше титров, легче читать
            if best[i] + cost < best[j]:
                best[j], back[j] = best[i] + cost, i
    out, j = [], n
    while j > 0:
        out.append(ph[back[j]:j])
        j = back[j]
    return out[::-1]


def pick_accent(words):
    best, score = None, 0
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in STOP:
            continue
        s = len(lw) + (6 if re.search(r"\d", lw) else 0) + (2 if lw.endswith(("!", "?")) else 0)
        if s > score:
            best, score = i, s
    return best if score >= 5 else None


def build(words, edl):
    items = []
    for w in words:
        if FILLER.match(w["text"]):
            continue
        a, b = remap(w["start"], edl), remap(w["end"], edl)
        text = clean(w["text"])
        if a is None or b is None or not text:
            continue
        hard = bool(re.search(r"[.!?…]$", w["text"]))
        soft = bool(re.search(r"[,;:—]$", w["text"]))
        if w["text"].startswith("-") and items:  # Whisper отдаёт «как» и «-то» отдельно
            items[-1].update(text=items[-1]["text"] + "-" + text, t1=max(b, items[-1]["t1"]),
                             hard=hard, soft=soft)
            continue
        items.append({"t0": a, "t1": max(b, a + 0.05), "text": text, "hard": hard, "soft": soft})

    # Фразы — по точкам, паузам и запятым; одно слово перед запятой («Ну,», «Так,»)
    # не отрываем — иначе титры мелькают по слову. Внутри фразы — титры по 2–3 слова.
    phrases, cur = [], []
    for i, it in enumerate(items):
        cur.append(it)
        nxt = items[i + 1] if i + 1 < len(items) else None
        if (not nxt or it["hard"] or (it["soft"] and len(cur) >= 2)
                or nxt["t0"] - it["t1"] > 0.6):
            phrases.append(cur)
            cur = []
    merged = [g for ph in phrases for g in split_phrase(ph)]

    caps, prev_end = [], 0.0
    for k, g in enumerate(merged):
        # слова собеседников могут перекрываться — титры идут строго друг за другом
        start = max(g[0]["t0"], prev_end)
        end = max(g[-1]["t1"] + 0.15, start + 0.35)
        if k + 1 < len(merged):
            nstart = merged[k + 1][0]["t0"]
            if nstart - g[-1]["t1"] < 0.5:
                end = nstart
            end = max(min(end, nstart), start + 0.2)
        ws = [x["text"] for x in g]
        caps.append({"start": round(start, 3), "end": round(end, 3),
                     "words": ws, "accent": pick_accent(ws), "zoom": False})
        prev_end = end
    mark_zooms(caps)
    return caps


def mark_zooms(caps, gap=STYLE["zoom_gap"]):
    """Зум — на акцентных титрах, но не чаще раза в gap секунд: иначе кадр «дышит»."""
    last = -1e9
    for c in caps:
        if c.get("accent") is not None and c["start"] - last >= gap:
            c["zoom"] = True
            last = c["start"]


def ts(t):
    h, r = divmod(max(t, 0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def rounded_rect(w, h, r):
    k = 0.5523 * r  # аппроксимация дуги кривой Безье
    return (f"m {r} 0 l {w - r} 0 b {w - r + k} 0 {w} {r - k} {w} {r} "
            f"l {w} {h - r} b {w} {h - r + k} {w - r + k} {h} {w - r} {h} "
            f"l {r} {h} b {r - k} {h} 0 {h - r + k} 0 {h - r} "
            f"l 0 {r} b 0 {r - k} {r - k} 0 {r} 0")


def header(styles):
    return ("[Script Info]\nScriptType: v4.00+\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
            f"PlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            + styles +
            "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


@lru_cache(maxsize=None)
def text_size(text, size=None):
    """Ширина строки и высота прописных в px при кегле ASS size."""
    # Кегль ASS = высота ячейки (winAscent+winDescent), PIL меряет в em — пересчитываем
    font = ImageFont.truetype(str(FONT_FILE), 200)
    asc, desc = font.getmetrics()
    em = 200 * (size or STYLE["size"]) / (asc + desc)
    font = ImageFont.truetype(str(FONT_FILE), max(1, round(em)))
    l, t, r, b = font.getbbox(text)
    _, cap_t, _, cap_b = font.getbbox("Н")
    return r - l, cap_b - cap_t


def write_ass(caps, wd, y):
    s = STYLE
    # без подложки текст держится на обводке и тени, с подложкой — чистый
    outline, shadow = (4, 2) if s["plate"] == "none" else (0, 0)
    text_style = (f"Style: Cap,{FONT_NAME},{s['size']},{s['white']},{s['white']},&H00000000,"
                  f"&H64000000,0,0,0,0,100,100,1,0,1,{outline},{shadow},5,0,0,0,1\n")
    plate_style = ("Style: Plate,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
                   "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n")
    text_lines, plate_lines = [header(text_style)], [header(plate_style)]
    for c in caps:
        words = [w.upper() for w in c["words"]] if s["upper"] else c["words"]
        parts = []
        for i, w in enumerate(words):
            parts.append(f"{{\\c{s['accent']}}}{w}{{\\c{s['white']}}}" if i == c.get("accent") else w)
        body = " ".join(parts)
        a, b = ts(c["start"]), ts(c["end"])
        text_lines.append(f"Dialogue: 1,{a},{b},Cap,,0,0,0,,{{\\pos({W // 2},{y})\\fad(60,0)}}{body}\n")

        tw, th = text_size(" ".join(words))
        pw, ph = tw + 2 * s["pad_x"], th + 2 * s["pad_y"]
        x0, y0 = W // 2 - pw // 2, y - ph // 2
        plate_lines.append(
            f"Dialogue: 0,{a},{b},Plate,,0,0,0,,{{\\pos({x0},{y0})\\blur{s['feather']}"
            f"\\bord0\\shad0\\fad(60,0)\\p1}}{rounded_rect(pw, ph, s['radius'])}{{\\p0}}\n")
    (wd / "captions.ass").write_text("".join(text_lines))
    (wd / "plates.ass").write_text("".join(plate_lines))


def render_still(frame, words, accent_idx):
    """Один титр на картинке (для листа согласования стиля). frame — RGB uint8 1080×1920."""
    import subprocess
    import tempfile
    import numpy as np
    from PIL import Image
    from render import subtitle_chain
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        write_ass([{"start": 0, "end": 5, "words": words, "accent": accent_idx}], td, STYLE["y"])
        Image.fromarray(frame).save(td / "f.png")
        # титр появляется с fade — снимаем кадр через секунду после начала
        fc = subtitle_chain("[0:v]setpts=PTS+1/TB,", td, 3.0, "[v]", fps=1)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(td / "f.png"), "-filter_complex",
                        fc, "-map", "[v]", "-frames:v", "1", "-update", "1", str(td / "o.png")], check=True)
        return np.asarray(Image.open(td / "o.png").convert("RGB"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "ass"])
    ap.add_argument("video")
    ap.add_argument("--y", type=int, default=STYLE["y"], help="центр строки по вертикали, px из 1920")
    a = ap.parse_args()
    wd = workdir_for(a.video)
    if a.cmd == "build":
        words = json.loads((wd / "words.json").read_text())
        edl = json.loads((wd / "edl.json").read_text())
        caps = build(words, edl)
        (wd / "captions.json").write_text(json.dumps(caps, ensure_ascii=False, indent=1))
        print(f"Титров: {len(caps)} → {wd / 'captions.json'}")
    else:
        caps = json.loads((wd / "captions.json").read_text())
        write_ass(caps, wd, a.y)
        print(f"ASS готов: {wd / 'captions.ass'}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Обложка — первый кадр ролика. Два стиля:

  poster (по умолчанию) — журнальный постер: заголовок из 1–2 слов огромными узкими
    буквами во весь кадр, сквозь буквы видно фото, человек стоит перед буквами,
    буквы «вырезаны» в бумаге; мелкая типографика с разрядкой (см. poster.py).
  glass — кадр с заголовком в стиле субтитров на матовом стекле.

Шаг 1 — кандидаты: лучшие кадры (крупное резкое лицо, открытые глаза) из того,
что вошло в монтаж → cover_candidates.jpg (сетка с номерами) и .json с таймкодами.
  cover.py candidates video.mp4 [--n 4]

Шаг 2 — обложка: кадр + заголовок. Акцентные слова — в *звёздочках*,
перенос строки — «|» (иначе строки разобьются сами по ширине).
  cover.py make video.mp4 --t 12.4 --title "Главная *ошибка* |в монтаже *Reels*"
         [--y 1250] [--size 170]
Постер:
  cover.py make video.mp4 --t 12.0 --title "Синдром *самозванца*" --kicker "Живой разговор"
         --year 2026 --note "Мне не нравится|мой чек" --issue "Вып. 01" --side "о деньгах"
         --stamp СС [--image photo.jpg] [--no-pop]
  *слово* в постере — залить фото в лаймовом дуотоне.

Результат: .<имя>_montage/cover.jpg — его render.py ставит первым кадром ролика,
и его же можно загрузить обложкой вручную.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from captions import FONT_NAME, STYLE, header, rounded_rect, text_size  # noqa: E402
from common import probe, run, workdir_for  # noqa: E402
from faces import available, detect, frames_at  # noqa: E402
from render import FONTS, H, W, esc, resolve_mode, video_filter  # noqa: E402

TITLE = {"size": 170, "max_width": 900, "pad_x": 60, "pad_y": 64, "line_gap": 1.75,
         "radius": 44, "feather": 18}


def reframed_frame(video, t, out, scale=None):
    info = probe(video)
    mode, faces = resolve_mode(video, [{"start": t, "end": t + 0.04}], "auto")
    fx = faces[0][0] if faces[0] else None
    vf = video_filter(info, mode, fx) + ",setsar=1" + (f",scale={scale}" if scale else "")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
         "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-frames:v", "1", "-q:v", "2", str(out)])


def candidates(video, n=4):
    wd = workdir_for(video)
    edl = json.loads((wd / "edl.json").read_text())
    times = [s["start"] + k * 0.4 for s in edl
             for k in range(int((s["end"] - s["start"] - 0.2) / 0.4) + 1)]
    scored = []
    if available():
        for t, fr in frames_at(video, times):
            d = fr is not None and detect(fr)
            if d:
                score = d["size"] * 10 + d["score"] * 3 + math.log1p(d["sharp"]) * 0.5
                scored.append((score, t))
    if not scored:  # лиц нет — берём равномерно
        scored = [(0, t) for t in times[:: max(1, len(times) // n)]]
    picked = []
    for score, t in sorted(scored, reverse=True):
        if all(abs(t - p) >= 2.5 for p in picked):
            picked.append(t)
        if len(picked) == n:
            break
    picked.sort()

    tiles = []
    for i, t in enumerate(picked, 1):
        tile = wd / f"cand_{i}.jpg"
        reframed_frame(video, t, tile, scale="360:640")
        tiles.append(tile)
    inputs = sum((["-i", str(p)] for p in tiles), [])
    labels = ";".join(
        f"[{i}:v]drawtext=text='{i + 1}':x=16:y=12:fontsize=48:fontcolor=white:"
        f"box=1:boxcolor=black@0.6:boxborderw=10[l{i}]" for i in range(len(tiles)))
    stack = "".join(f"[l{i}]" for i in range(len(tiles)))
    sheet = wd / "cover_candidates.jpg"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex",
         f"{labels};{stack}hstack=inputs={len(tiles)}:shortest=1" if len(tiles) > 1
         else f"{labels};[l0]null", "-frames:v", "1", "-q:v", "3", str(sheet)])
    for p in tiles:
        p.unlink()
    (wd / "cover_candidates.json").write_text(json.dumps(
        [{"n": i, "t": round(t, 2)} for i, t in enumerate(picked, 1)], indent=1))
    return sheet, picked


def parse_title(title):
    """'Главная *ошибка* |в монтаже' → строки из слов [(слово, акцент)]."""
    manual = "|" in title
    words = []
    for token in re.findall(r"\*[^*]+\*|\||[^\s*|]+", title):
        if token == "|":
            words.append(("|", False))
        elif token.startswith("*"):
            words += [(w, True) for w in token.strip("*").split()]
        else:
            words.append((token, False))
    lines, cur = [], []
    for w, acc in words:
        if w == "|":
            lines.append(cur)
            cur = []
            continue
        trial = " ".join(x for x, _ in cur + [(w, acc)])
        if not manual and cur and text_size(trial, TITLE["size"])[0] > TITLE["max_width"]:
            lines.append(cur)
            cur = []
        cur.append((w, acc))
    lines.append(cur)
    return [ln for ln in lines if ln]


def make(video, t, title, y=1250, size=None):
    if size:
        TITLE["size"] = size
    wd = workdir_for(video)
    frame = wd / "cover_frame.png"
    reframed_frame(video, t, frame)

    lines = parse_title(title)
    sz = TITLE["size"]
    widths = [text_size(" ".join(w for w, _ in ln), sz)[0] for ln in lines]
    limit = W - 2 * TITLE["pad_x"] - 60
    if max(widths) > limit:  # длинное слово не влезает — уменьшаем кегль, а не режем текст
        sz = int(sz * limit / max(widths))
        widths = [text_size(" ".join(w for w, _ in ln), sz)[0] for ln in lines]
        print(f"Кегль заголовка уменьшен до {sz}, чтобы строка влезла", file=sys.stderr)
    cap_h = text_size("Н", sz)[1]
    pitch = cap_h * TITLE["line_gap"]
    block_h = cap_h + pitch * (len(lines) - 1)
    pw, ph = max(widths) + 2 * TITLE["pad_x"], block_h + 2 * TITLE["pad_y"]
    top = y - block_h / 2

    white, lime = STYLE["white"], STYLE["accent"]
    text = [header(f"Style: T,{FONT_NAME},{sz},{white},{white},&H00000000,&H00000000,"
                   f"0,0,0,0,100,100,1,0,1,0,0,5,0,0,0,1\n")]
    for i, ln in enumerate(lines):
        body = " ".join(f"{{\\c{lime}}}{w}{{\\c{white}}}" if acc else w for w, acc in ln)
        cy = round(top + cap_h / 2 + i * pitch)
        text.append(f"Dialogue: 1,0:00:00.00,0:00:10.00,T,,0,0,0,,{{\\pos({W // 2},{cy})}}{body}\n")
    plate = [header("Style: P,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
                    "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"),
             f"Dialogue: 0,0:00:00.00,0:00:10.00,P,,0,0,0,,{{\\pos({round(W / 2 - pw / 2)},"
             f"{round(y - ph / 2)})\\blur{TITLE['feather']}\\p1}}"
             f"{rounded_rect(round(pw), round(ph), TITLE['radius'])}{{\\p0}}\n"]
    (wd / "cover_text.ass").write_text("".join(text))
    (wd / "cover_plate.ass").write_text("".join(plate))

    k = 0.42
    fc = (f"[0:v]scale={W}:{H},setsar=1,split[main][g];"
          f"[g]boxblur=luma_radius=28:luma_power=2:chroma_radius=28:chroma_power=2,"
          f"colorchannelmixer=rr={k}:gg={k}:bb={k}[blur];"
          f"color=c=black:s={W}x{H}:d=1,format=rgb24,ass='{esc(wd / 'cover_plate.ass')}',"
          f"format=gray[mask];[blur][mask]alphamerge[glass];"
          f"[main][glass]overlay,ass='{esc(wd / 'cover_text.ass')}':fontsdir='{esc(FONTS)}'[v]")
    out = wd / "cover.jpg"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(frame), "-filter_complex", fc,
         "-map", "[v]", "-frames:v", "1", "-q:v", "2", str(out)])
    return out


def source_frame(video, t):
    """Полный кадр исходника (RGB) с учётом поворота из метаданных."""
    import subprocess
    import numpy as np
    info = probe(video)
    buf = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return np.frombuffer(buf, np.uint8).reshape(info["height"], info["width"], 3).copy()


def make_poster_cover(video, t, title, image=None, pop=True, **text):
    import numpy as np
    from PIL import Image
    from poster import make_poster
    wd = workdir_for(video)
    photo = (np.asarray(Image.open(image).convert("RGB")) if image else source_frame(video, t))
    face = detect(photo[..., ::-1].copy())
    words, accent = [], None
    for i, tok in enumerate(re.findall(r"\*[^*]+\*|\S+", title)[:2]):
        if tok.startswith("*"):
            accent = i
        words.append(tok.strip("*"))
    if len(re.findall(r"\S+", title.replace("*", ""))) > 2:
        print("В постер идут только первые 2 слова заголовка", file=sys.stderr)
    out = make_poster(photo, words, (face["cx"], face["cy"]) if face else None,
                      accent=accent, pop_out=pop, **text)
    path = wd / "cover.jpg"
    Image.fromarray(out).save(path, quality=93)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["candidates", "make"])
    ap.add_argument("video")
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--t", type=float, help="момент исходника для кадра обложки, сек")
    ap.add_argument("--title")
    ap.add_argument("--y", type=int, default=1250,
                    help="центр заголовка, px из 1920; держите в 240–1680 — сетка профиля режет до 3:4")
    ap.add_argument("--size", type=int)
    ap.add_argument("--style", choices=["auto", "poster", "glass"], default="auto",
                    help="auto — как в профиле стиля пользователя (brand.json → cover.layout)")
    ap.add_argument("--image", help="своя картинка по теме вместо кадра из видео")
    ap.add_argument("--no-pop", action="store_true", help="не вырезать человека перед буквами")
    for k in ("kicker", "year", "note", "issue", "side", "stamp"):
        ap.add_argument(f"--{k}", default="")
    a = ap.parse_args()
    if a.cmd == "candidates":
        sheet, picked = candidates(a.video, a.n)
        print(sheet)
        for i, t in enumerate(picked, 1):
            print(f"  {i}: t={t:.2f}")
    else:
        if (a.t is None and not a.image) or not a.title:
            raise SystemExit("Нужны --title и --t (или --image)")
        import brand as brandmod
        b = brandmod.require_ready()
        style = a.style if a.style != "auto" else (
            "glass" if b["cover"]["layout"] == "glass" else "poster")
        if style == "poster":
            print(make_poster_cover(a.video, a.t or 0, a.title, a.image, not a.no_pop,
                                    kicker=a.kicker, year=a.year, note=a.note, issue=a.issue,
                                    side=a.side, stamp_text=a.stamp))
        else:
            print(make(a.video, a.t, a.title, a.y, a.size))


if __name__ == "__main__":
    main()

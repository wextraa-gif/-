#!/usr/bin/env python3
"""Строит план склейки (edl.json): какие куски видео оставить.

Логика «живых» пауз:
  * паузы короче --min-pause не трогаем — это естественный ритм речи;
  * длинную паузу вырезаем, но оставляем «воздух»: в сумме --pause секунд
    (40% перед следующей фразой, 60% после предыдущей — голос успевает затихнуть);
  * после точки, «?» и «!» пауза длиннее (--sentence-pause), как у живого человека;
  * «э-э», «ммм» и т.п. вырезаются вместе с паузой вокруг них;
  * границы слов Whisper уточняются по громкости, чтобы не срезать окончания.

Использование:
  cut.py video.mp4 [--pause 0.30] [--sentence-pause 0.45] [--min-pause 0.45]
                   [--ranges 12.5-48,95-130]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import energy_db, load_audio, probe, speech_threshold, workdir_for  # noqa: E402

FILLER = re.compile(r"^(э+|э-э+|ээ+|м+|мм+|ммм+|а-а+|аа+|эм+|хм+|uh+|um+|hmm+)[.,!?…]*$",
                    re.IGNORECASE)
SENTENCE_END = re.compile(r"[.!?…]$")
HOP = 0.01


def parse_ranges(s, duration):
    if not s:
        return [(0.0, duration)]
    out = []
    for part in s.split(","):
        a, b = part.split("-")
        out.append((float(a), min(float(b), duration)))
    return out


def refine(t, db, thr, direction, limit):
    """Сдвигает границу наружу, пока в аудио ещё звучит голос (не дальше limit)."""
    i = int(t / HOP)
    steps = int(limit / HOP)
    for _ in range(steps):
        j = i + direction
        if j < 0 or j >= len(db) or db[j] < thr:
            break
        i = j
    return i * HOP


def build_edl(words, db, thr, ranges, fps, pause=0.30, sentence_pause=0.45, min_pause=0.45):
    # Слова только внутри выбранных диапазонов, паразиты отмечаем
    sel = []
    for w in words:
        if any(a <= w["start"] and w["end"] <= b + 0.05 for a, b in ranges):
            sel.append(dict(w, filler=bool(FILLER.match(w["text"]))))
    speech = [w for w in sel if not w["filler"]]
    if not speech:
        raise SystemExit("Не нашёл речи в выбранном фрагменте")

    # Группируем слова в фразы: новая фраза после длинной паузы, паразита или границы диапазона
    phrases, cur, cut_by_filler = [], [speech[0]], []
    for prev, w in zip(speech, speech[1:]):
        gap = w["start"] - prev["end"]
        filler_between = any(f["filler"] and prev["end"] <= f["start"] < w["start"] for f in sel)
        other_range = not any(a <= prev["start"] and w["end"] <= b + 0.05 for a, b in ranges)
        if gap > min_pause or filler_between or other_range:
            phrases.append(cur)
            cut_by_filler.append(filler_between)
            cur = [w]
        else:
            cur.append(w)
    phrases.append(cur)

    # Сырые границы фраз с уточнением по громкости
    spans = []
    for k, ph in enumerate(phrases):
        start = refine(ph[0]["start"], db, thr, -1, 0.12)
        end = refine(ph[-1]["end"], db, thr, +1, 0.30)
        # если рядом вырезан паразит — не расширяемся в его сторону
        if k > 0 and cut_by_filler[k - 1]:
            start = ph[0]["start"]
        if k < len(phrases) - 1 and cut_by_filler[k]:
            end = ph[-1]["end"]
        spans.append([start, end, SENTENCE_END.search(ph[-1]["text"]) is not None])

    # Раздаём «воздух» на стыках, не залезая дальше середины исходной паузы
    segs = []
    for k, (start, end, sent_end) in enumerate(spans):
        target = sentence_pause if (k > 0 and spans[k - 1][2]) else pause
        lead = 0.4 * target
        if k > 0:
            lead = min(lead, (start - spans[k - 1][1]) / 2)
        target_out = sentence_pause if sent_end else pause
        tail = 0.6 * target_out
        if k < len(spans) - 1:
            tail = min(tail, (spans[k + 1][0] - end) / 2)
        segs.append([max(0.0, start - lead), end + tail])

    # Ограничиваем выбранными диапазонами и выравниваем по кадрам (иначе звук «уплывёт»)
    out = []
    for a, b in segs:
        rng = next(((ra, rb) for ra, rb in ranges if ra - 0.5 <= a <= rb), ranges[0])
        a, b = max(a, rng[0]), min(b, rng[1])
        a, b = round(a * fps) / fps, round(b * fps) / fps
        if out and a - out[-1][1] < 0.15:
            out[-1][1] = max(out[-1][1], b)  # вырезать меньше 0,15 с нет смысла — только лишний стык
        elif b - a >= 2 / fps:
            out.append([a, b])
    return [{"start": round(a, 4), "end": round(b, 4)} for a, b in out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--pause", type=float, default=0.30,
                    help="сколько тишины оставить на месте вырезанной паузы, сек")
    ap.add_argument("--sentence-pause", type=float, default=0.45,
                    help="то же после конца предложения")
    ap.add_argument("--min-pause", type=float, default=0.45,
                    help="паузы короче этой не вырезаются")
    ap.add_argument("--ranges", help="фрагменты исходника, напр. 12.5-48,95-130")
    a = ap.parse_args()

    wd = workdir_for(a.video)
    words_file = wd / "words.json"
    if not words_file.exists():
        raise SystemExit("Сначала transcribe.py")
    words = json.loads(words_file.read_text())
    info = probe(a.video)
    db = energy_db(load_audio(a.video))
    edl = build_edl(words, db, speech_threshold(db), parse_ranges(a.ranges, info["duration"]),
                    info["fps"], a.pause, a.sentence_pause, a.min_pause)
    (wd / "edl.json").write_text(json.dumps(edl, indent=1))
    kept = sum(s["end"] - s["start"] for s in edl)
    print(f"Кусков: {len(edl)}, итог {kept:.1f} с из {info['duration']:.1f} с")


if __name__ == "__main__":
    main()

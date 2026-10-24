#!/usr/bin/env python3
"""Полный цикл: распознать → склеить с живыми паузами → субтитры → собрать 9:16.

Использование:
  make_clip.py video.mp4 out.mp4 [--ranges 12-48,95-130] [--pause 0.3]
               [--reframe auto] [--no-subs] [--no-zoom] [--cover-hold 0.1]
               [--stop-after-captions | --skip-to-render]

Обложку (cover.py) делают между этими двумя шагами: если .<имя>_montage/cover.jpg
есть, она станет первым кадром.

--stop-after-captions: остановиться после captions.json, чтобы поправить текст
и акценты, а потом собрать: `make_clip.py video out --skip-to-render`.
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import workdir_for  # noqa: E402


def step(*args):
    print("→", " ".join(map(str, args[:2])), flush=True)
    subprocess.run([sys.executable, str(HERE / args[0]), *map(str, args[1:])], check=True)


ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("out")
ap.add_argument("--ranges")
ap.add_argument("--pause", default="0.30")
ap.add_argument("--sentence-pause", default="0.45")
ap.add_argument("--min-pause", default="0.45")
ap.add_argument("--model", default="medium")
ap.add_argument("--reframe", default="auto")
ap.add_argument("--y", default="1250")
ap.add_argument("--no-subs", action="store_true")
ap.add_argument("--no-zoom", action="store_true")
ap.add_argument("--no-fx", action="store_true", help="без анимаций из fx.json")
ap.add_argument("--cover-hold", default="0.1", help="0 — без обложки первым кадром")
ap.add_argument("--stop-after-captions", action="store_true")
ap.add_argument("--skip-to-render", action="store_true")
a = ap.parse_args()

import brand  # noqa: E402
brand.require_ready()  # без пройденного онбординга (шрифт, цвет, референсы) не монтируем

if not a.skip_to_render:
    step("transcribe.py", a.video, "--model", a.model)
    cut = ["cut.py", a.video, "--pause", a.pause, "--sentence-pause", a.sentence_pause,
           "--min-pause", a.min_pause]
    step(*cut, *(["--ranges", a.ranges] if a.ranges else []))
    if not a.no_subs:
        step("captions.py", "build", a.video)
    if a.stop_after_captions:
        print(f"Правьте {workdir_for(a.video) / 'captions.json'} и запускайте с --skip-to-render")
        sys.exit(0)
if not a.no_subs:
    step("captions.py", "ass", a.video, "--y", a.y)
step("render.py", a.video, a.out, "--reframe", a.reframe, "--cover-hold", a.cover_hold,
     *(["--no-subs"] if a.no_subs else []), *(["--no-zoom"] if a.no_zoom else []), *(["--no-fx"] if a.no_fx else []))
step("preview.py", a.out)

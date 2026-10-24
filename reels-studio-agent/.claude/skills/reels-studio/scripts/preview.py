#!/usr/bin/env python3
"""Раскадровка для согласования: сетка кадров с таймкодами в один JPG.

Использование:
  preview.py out.mp4 [--n 12] [--cols 4] [-o sheet.jpg]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import probe, run  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("--n", type=int, default=12)
ap.add_argument("--cols", type=int, default=4)
ap.add_argument("-o", "--out")
a = ap.parse_args()

dur = probe(a.video)["duration"]
rows = -(-a.n // a.cols)
out = a.out or str(Path(a.video).with_suffix(".sheet.jpg"))
step = dur / a.n
vf = (f"fps=1/{step:.4f},scale=270:-2,"
      r"drawtext=text='%{pts\:hms}':x=8:y=8:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6,"
      f"tile={a.cols}x{rows}:padding=4:color=black")
run(["ffmpeg", "-y", "-v", "error", "-ss", f"{step / 2:.3f}", "-i", a.video,
     "-vf", vf, "-frames:v", "1", "-q:v", "3", out])
print(out)

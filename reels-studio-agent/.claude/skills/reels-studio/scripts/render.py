#!/usr/bin/env python3
"""Сборка ролика 1080×1920 по edl.json.

1. Каждый кусок: кадрирование под 9:16 (по лицу или с размытым фоном),
   микро-фейды звука 15 мс на стыках, точная длина в кадрах — без рассинхрона.
2. Склейка + плавные зумы на акцентах + «матовое стекло» под субтитрами + субтитры
   + обложка первым кадром + громкость −14 LUFS.

Использование:
  render.py video.mp4 out.mp4 [--reframe auto|face|center|blur|none] [--no-subs]
            [--no-zoom] [--zoom-depth 0.09] [--cover-hold 0.1] [--no-fx]
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import probe, run, workdir_for  # noqa: E402
from faces import available as faces_available, detect, frames_at, segment_faces  # noqa: E402
import brand as brandmod  # noqa: E402
import fx as fxmod  # noqa: E402
import numpy as np  # noqa: E402

SKILL = Path(__file__).resolve().parent.parent
FONTS = SKILL / "fonts"
W, H, FPS = 1080, 1920, 30
FADE = 0.015
ZOOM_IN, ZOOM_OUT = 0.35, 0.7  # длительность наезда и отъезда, сек


def video_filter(info, mode, fx):
    w, h = info["width"], info["height"]
    if mode == "none" or abs(w / h - W / H) < 0.01:
        return f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2"
    if mode == "blur" or w / h < W / H:
        return (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=30:2,eq=brightness=-0.08[bg];[b]scale={W}:-2[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2")
    cw = round(h * W / H / 2) * 2
    x = (fx if fx is not None else 0.5) * w - cw / 2
    x = int(min(max(x, 0), w - cw)) // 2 * 2
    return f"crop={cw}:{h}:{x}:0,scale={W}:{H}"


def resolve_mode(video, edl, mode):
    """Режим кадрирования и центры лиц по кускам."""
    faces = [None] * len(edl)
    if mode in ("auto", "face"):
        faces = segment_faces(video, edl)
        known = [f for f in faces if f]
        if mode == "auto" and not known:
            return "blur", faces  # лица нет — не режем вслепую, кладём на размытый фон
        if known:
            fb = (sorted(f[0] for f in known)[len(known) // 2],
                  sorted(f[1] for f in known)[len(known) // 2])
            faces = [f or fb for f in faces]
        mode = "face"
    return mode, faces


def smooth(arg):
    """Плавная ступенька 0→1 (smoothstep) для выражений ffmpeg."""
    k = f"clip({arg},0,1)"
    return f"({k}*{k}*(3-2*{k}))"


def out_to_src(t, edl):
    """Время готового ролика → время исходника."""
    off = 0.0
    for seg in edl:
        d = seg["end"] - seg["start"]
        if t < off + d:
            return seg["start"] + t - off
        off += d
    return edl[-1]["end"]


def face_point(cx, cy, info, mode):
    """Лицо (доли исходного кадра) → точка в кадре 1080×1920."""
    w, h = info["width"], info["height"]
    if abs(w / h - W / H) < 0.01:
        return cx * W, cy * H
    if mode in ("face", "center"):  # кадрирование по ширине: лицо около центра по X
        return W / 2, cy * H
    fgh = W * h / w  # blur/none: кадр целиком по ширине, по центру по высоте
    return cx * W, (H - fgh) / 2 + cy * fgh


def zoom_bumps(video, caps, edl, info, mode):
    """Плавные наезды на титрах с "zoom": выражение ступеньки и якорь — лицо в этот момент."""
    bumps = []
    for c in caps:
        if not c.get("zoom"):
            continue
        a, b = c["start"], c["end"]
        expr = (f"{smooth(f'(in/{FPS}-{a:.3f})/{ZOOM_IN}')}"
                f"*(1-{smooth(f'(in/{FPS}-{b:.3f})/{ZOOM_OUT}')})")
        ax, ay = W / 2, H * 0.4
        if faces_available():
            for _, fr in frames_at(video, [out_to_src((a + b) / 2, edl)]):
                d = fr is not None and detect(fr)
                if d:
                    ax, ay = face_point(d["cx"], d["cy"], info, mode)
        bumps.append((expr, ax, ay))
    return bumps


def zoom_filter(bumps, depth):
    """Наезд с якорем в лице: эта точка остаётся на месте, кадр не уезжает за край.
    Зумы не пересекаются по времени, поэтому их можно просто сложить."""
    def total(weight):
        return "+".join(f"{weight(ax, ay):.1f}*{depth}*{e}" for e, ax, ay in bumps)
    x0, y0 = total(lambda ax, ay: ax), total(lambda ax, ay: ay)
    x1 = f"{W}-({total(lambda ax, ay: W - ax)})"
    y1 = f"{H}-({total(lambda ax, ay: H - ay)})"
    return (f"perspective=x0='{x0}':y0='{y0}':x1='{x1}':y1='{y0}':"
            f"x2='{x0}':y2='{y1}':x3='{x1}':y3='{y1}':interpolation=cubic:eval=frame")


def pipe_fx(dec_cmd, enc_cmd, comp, log):
    """Кадры из декодера → эффекты → кодер."""
    with open(log, "w") as err:
        dec = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE, stderr=err)
        enc = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=err)
        size, n = W * H * 3, 0
        while True:
            buf = dec.stdout.read(size)
            if len(buf) < size:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
            enc.stdin.write(comp.apply(frame, n / FPS).tobytes())
            n += 1
        enc.stdin.close()
        dec.wait()
        enc.wait()
    if dec.returncode or enc.returncode:
        sys.stderr.write(Path(log).read_text())
        raise SystemExit("Сборка с эффектами упала")


def esc(p):
    return Path(p).as_posix().replace(":", r"\:")


def subtitle_chain(src, wd, total, out_label, fps=FPS):
    """Подложка под титры (по профилю стиля) + сами титры.
    glass-dark/glass-light — размытое видео, затемнённое/осветлённое; solid — плашка цвета;
    none — без подложки (у текста обводка и тень)."""
    sub = brandmod.load()["subtitles"]
    plate, k = sub["plate"], float(sub["plate_opacity"])
    ass = f"ass='{esc(wd / 'captions.ass')}':fontsdir='{esc(FONTS)}'"
    if plate == "none":
        return f"{src}format=yuv420p,{ass}{out_label}"
    if plate == "solid":
        r, g, b = brandmod.hex_rgb(sub["plate_color"])
        fill = f"[g]drawbox=c=0x{r:02X}{g:02X}{b:02X}@1:t=fill[blur];"
    elif plate == "glass-light":
        fill = (f"[g]boxblur=luma_radius=28:luma_power=2:chroma_radius=28:chroma_power=2,"
                f"lutrgb=r='val+(255-val)*{k}':g='val+(255-val)*{k}':b='val+(255-val)*{k}'[blur];")
    else:
        fill = (f"[g]boxblur=luma_radius=28:luma_power=2:chroma_radius=28:chroma_power=2,"
                f"colorchannelmixer=rr={k}:gg={k}:bb={k}[blur];")
    opacity = k if plate == "solid" else 1.0
    return (f"{src}split[main][g];{fill}"
            f"color=c=black:s={W}x{H}:r={fps}:d={total + 1:.3f},format=rgb24,"
            f"ass='{esc(wd / 'plates.ass')}',format=gray,lutyuv=y='val*{opacity}'[mask];"
            f"[blur][mask]alphamerge[glass];"
            f"[main][glass]overlay=shortest=1,format=yuv420p,{ass}{out_label}")


def render(video, out, mode="auto", subs=True, zoom=True, zoom_depth=0.09,
           cover_hold=0.1, use_fx=True, keep_temp=False):
    brandmod.require_ready()
    info = probe(video)
    wd = workdir_for(video)
    edl = json.loads((wd / "edl.json").read_text())
    caps_file = wd / "captions.json"
    caps = json.loads(caps_file.read_text()) if caps_file.exists() else []
    segdir = wd / "segments"
    shutil.rmtree(segdir, ignore_errors=True)
    segdir.mkdir()

    mode, faces = resolve_mode(video, edl, mode)
    listing = []
    for i, s in enumerate(edl):
        n = max(1, round((s["end"] - s["start"]) * FPS))
        dur = n / FPS
        seg = segdir / f"{i:04d}.mkv"
        fx = faces[i][0] if faces[i] else None
        vf = f"{video_filter(info, mode, fx)},fps={FPS},setsar=1,format=yuv420p"
        af = (f"afade=t=in:st=0:d={FADE},afade=t=out:st={dur - FADE:.4f}:d={FADE},"
              f"apad,atrim=end={dur:.4f},aresample=48000")
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{s['start']:.4f}", "-t", f"{dur + 0.1:.4f}",
               "-i", str(video), "-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]",
               "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "12"]
        if info["has_audio"]:
            cmd += ["-map", "0:a:0", "-af", af, "-c:a", "pcm_s16le", "-ac", "2"]
        run(cmd + [str(seg)])
        listing.append(f"file '{seg.name}'")
    (segdir / "list.txt").write_text("\n".join(listing) + "\n")
    total = sum(max(1, round((s["end"] - s["start"]) * FPS)) for s in edl) / FPS

    bumps = zoom_bumps(video, caps, edl, info, mode) if zoom else []
    zf = zoom_filter(bumps, zoom_depth) if bumps else None

    concat_in = ["-f", "concat", "-safe", "0", "-i", str(segdir / "list.txt")]
    comp = fxmod.load(wd) if use_fx else None
    chain = []
    if comp:
        # эффекты рисуются в Python: декодер → кадры → компоновщик → кодер с титрами
        inputs = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                  "-i", "-", *concat_in]
        src, asrc, nxt = "[0:v]", "[1:a]", 2
    else:
        inputs = concat_in
        src, asrc, nxt = "[0:v]", "[0:a]", 1
        if zf:
            chain.append(f"[0:v]{zf}[zv]")
            src = "[zv]"
    if subs and (wd / "captions.ass").exists():
        chain.append(subtitle_chain(src, wd, total, "[body]"))
    else:
        chain.append(f"{src}format=yuv420p[body]")

    cover = wd / "cover.jpg"
    hold = cover_hold if cover.exists() and cover_hold > 0 else 0
    if hold:
        # обложка — первые кадры ролика: её и видно в ленте до старта, и выбирается как кадр-обложка
        inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{hold:.3f}", "-i", str(cover)]
        chain.append(f"[{nxt}:v]scale={W}:{H},setsar=1,format=yuv420p[cv];"
                     f"[cv][body]concat=n=2:v=1:a=0[vout]")
    else:
        chain.append("[body]null[vout]")
    if info["has_audio"]:
        delay = f"adelay={round(hold * 1000)}:all=1," if hold else ""
        chain.append(f"{asrc}{delay}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    script = segdir / "graph.txt"
    script.write_text(";\n".join(chain))
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex_script", str(script),
           "-map", "[vout]", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart"]
    if info["has_audio"]:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    cmd.append(str(out))
    if comp:
        dec = ["ffmpeg", "-v", "error", *concat_in, "-map", "0:v", "-vf", zf or "null",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        pipe_fx(dec, cmd, comp, segdir / "ffmpeg.log")
    else:
        run(cmd)
    if not keep_temp:
        shutil.rmtree(segdir, ignore_errors=True)
    return total + hold


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out")
    ap.add_argument("--reframe", default="auto", choices=["auto", "face", "center", "blur", "none"])
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--no-zoom", action="store_true")
    ap.add_argument("--zoom-depth", type=float, default=0.09,
                    help="глубина наезда: 0.09 ≈ увеличение в 1,1 раза")
    ap.add_argument("--no-fx", action="store_true", help="не накладывать анимации из fx.json")
    ap.add_argument("--cover-hold", type=float, default=0.1,
                    help="сколько секунд держать обложку в начале; 0 — без обложки")
    a = ap.parse_args()
    t = render(a.video, a.out, a.reframe, not a.no_subs, not a.no_zoom, a.zoom_depth,
               a.cover_hold, not a.no_fx)
    print(f"Готово: {a.out} ({t:.1f} с)")

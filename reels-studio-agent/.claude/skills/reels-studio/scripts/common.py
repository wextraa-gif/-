"""Общие утилиты: ffprobe, чтение аудио, кеш транскрипции."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 16000  # частота для анализа речи и энергии


def run(cmd, quiet=True):
    res = subprocess.run(cmd, capture_output=quiet, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stderr or "")
        raise SystemExit(f"Команда упала: {' '.join(map(str, cmd[:6]))} ...")
    return res.stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_streams", "-show_format", str(path)])
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    info = {"duration": float(data["format"]["duration"]), "has_audio": a is not None}
    if v:
        num, den = v.get("avg_frame_rate", "30/1").split("/")
        fps = float(num) / float(den) if float(den) else 30.0
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []):
            if "rotation" in sd:
                rot = abs(int(sd["rotation"])) % 180
        if rot == 90:
            w, h = h, w
        info.update(width=w, height=h, fps=fps if 1 < fps < 121 else 30.0)
    return info


def load_audio(path):
    """Моно float32 16 кГц."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
         "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def energy_db(audio, hop=0.01):
    """Громкость по кадрам 10 мс в дБ."""
    n = int(SR * hop)
    frames = len(audio) // n
    if frames == 0:
        return np.array([-100.0])
    x = audio[: frames * n].reshape(frames, n)
    rms = np.sqrt((x ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def speech_threshold(db):
    """Порог «тут звучит голос»: между шумом комнаты и типичной речью."""
    noise = np.percentile(db, 15)
    speech = np.percentile(db, 90)
    return noise + 0.3 * (speech - noise)


def workdir_for(video):
    p = Path(video)
    d = p.parent / f".{p.stem}_montage"
    d.mkdir(exist_ok=True)
    return d

#!/usr/bin/env python3
"""Распознаёт речь с таймкодами каждого слова.

Пишет в рабочую папку рядом с видео:
  words.json      — слова [{start, end, text, prob}]
  transcript.txt  — фразы с таймкодами, чтобы выбирать моменты из выступления

Использование:
  transcribe.py video.mp4 [--model medium] [--alt large-v3-turbo] [--lang ru] [--force]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import load_audio, workdir_for  # noqa: E402


def fmt(t):
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def transcribe(video, model="medium", lang="ru", force=False, alt=False):
    """alt=True — только второй текст для сверки (transcript_<model>.txt), слова не трогаем."""
    wd = workdir_for(video)
    out = wd / "words.json"
    if out.exists() and not force and not alt:
        return json.loads(out.read_text())

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit("Нет faster-whisper: pip install -r requirements.txt")

    m = WhisperModel(model, device="cpu", compute_type="int8")
    # Подсказка с паразитами помогает Whisper записывать «э-э», а не выкидывать их:
    # тогда склейка вырежет их вместе с паузой.
    # Аудио декодируем своим ffmpeg: встроенный декодер faster-whisper (PyAV)
    # капризен к версиям и форматам с телефонов
    segments, _ = m.transcribe(
        load_audio(video), language=lang, word_timestamps=True, beam_size=5,
        vad_filter=True, vad_parameters={"min_silence_duration_ms": 300},
        initial_prompt="Ну, э-э, вот. Ммм, так, а-а, короче.",
        condition_on_previous_text=False,
    )
    words, lines = [], []
    for seg in segments:
        lines.append(f"[{fmt(seg.start)} – {fmt(seg.end)}] {seg.text.strip()}")
        for w in seg.words or []:
            words.append({"start": round(w.start, 3), "end": round(w.end, 3),
                          "text": w.word.strip(), "prob": round(w.probability, 3)})
    if alt:
        (wd / f"transcript_{model}.txt").write_text("\n".join(lines) + "\n")
        return words
    out.write_text(json.dumps(words, ensure_ascii=False, indent=1))
    (wd / "transcript.txt").write_text("\n".join(lines) + "\n")
    return words


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--model", default="medium",
                    help="small/medium/large-v3/large-v3-turbo; medium — точные таймкоды слов")
    ap.add_argument("--alt", metavar="MODEL",
                    help="дополнительно распознать другой моделью для сверки текста "
                         "(large-v3-turbo лучше слышит слова в шуме, но может терять фразы)")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--force", action="store_true", help="перераспознать заново")
    a = ap.parse_args()
    words = transcribe(a.video, a.model, a.lang, a.force)
    wd = workdir_for(a.video)
    print(f"Слов: {len(words)}. Транскрипт: {wd / 'transcript.txt'}")
    if a.alt:
        transcribe(a.video, a.alt, a.lang, alt=True)
        print(f"Для сверки: {wd / f'transcript_{a.alt}.txt'}")

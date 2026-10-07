#!/usr/bin/env python3
"""Rank collected suggestions into candidate post topics.

Reads the files collect_suggest.py wrote, tags every phrase with intent
signals (pain, distrust, money, choice, how-to, business, fresh), groups
tagged phrases by the seed they grew from, and writes a ranked shortlist the
agent turns into post headlines. Stdlib only, no network.

    python3 topics.py seeds.txt out/                 # -> out/темы-кандидаты.md
    python3 topics.py seeds.txt out/ --prev old_out/ # flag phrases new since then

A single run is a snapshot of demand, not a trend. --prev compares two runs;
phrases that appeared in between are the real "актуальное".
"""
import argparse
import datetime
import json
import re
from collections import defaultdict
from pathlib import Path

YEAR = datetime.date.today().year

# label, weight, pattern matched against the phrase with its seed removed.
SIGNALS = [
    ("боль", 3, r"не работа|не приносит|не набира|нет (клиент|просмотр|заяв|продаж|подписч)"
                r"|мало (подписч|просмотр|клиент|охват|заяв)|режет|урезает|упал|слил|бан\b|заблокир"),
    ("недоверие", 3, r"правда|развод|обман|мошен|стоит ли|работает ли|реально ли|отзыв|накрут"),
    ("деньги", 2, r"цена|стоимост|сколько стоит|окупа|прибыл|бюджет|дорого|бесплатно"),
    ("выбор", 2, r"что лучше|\bили\b|выбрать|нанять|найти (таргетолог|маркетолог|смм|специалист|подрядчик)"),
    ("бизнес", 2, r"для бизнеса|бизнес|клиент|продаж|заявк|лид\b|лид форм|магазин|салон|мастер"),
    ("свежее", 2, rf"\b({YEAR - 1}|{YEAR}|{YEAR + 1})\b|\bнов(ый|ая|ое|ые|ом)\b|нейросет|\bии\b|gpt|алгоритм"),
    ("как сделать", 1, r"^как |\bкак\b|с чего начать|с нуля|пошагов|инструкц|настро"),
]
# People learning the trade, not buying it. Tagged so they can be split out.
LEARNER = r"как стать|обучени|курс|научиться|сколько зарабатыва|работа\b|ваканси|резюме|професи"

QUESTION_WORDS = ["что делать если", "можно ли", "стоит ли", "нужно ли", "правда ли",
                  "как", "почему", "что", "где", "зачем", "сколько"]


def norm(s):
    return " ".join(s.lower().replace("ё", "е").split())


def read_list(path):
    p = Path(path)
    if not p.exists():
        return {}
    return {norm(l): l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}


def stems(text):
    """Five-letter stems of the words that carry meaning."""
    return {w[:5] for w in re.findall(r"[a-zа-я0-9]+", text) if len(w) >= 4}


def strip_probe(key):
    """Drop the question word collect_suggest.py put in front: it is ours, not theirs."""
    for q in QUESTION_WORDS:
        if key.startswith(q + " "):
            return key[len(q) + 1:]
    return key


def anchor_of(key, seeds, seed_stems):
    """The seed a phrase grew from: exact match first, then most shared stems.

    None means the phrase shares no meaningful word with any seed, which on
    YouTube usually means it drifted off topic ("стоит ли покупать s23 ultra").
    """
    for seed in seeds:
        if re.search(rf"(^| ){re.escape(seed)}( |$)", key):
            return seed
    words = stems(key)
    best, best_n = None, 0
    for seed in seeds:
        n = len(words & seed_stems[seed])
        if n > best_n:
            best, best_n = seed, n
    return best


def plural(n, one, few, many):
    """Russian plural: 1 корень, 2 корня, 5 корней, 21 корень."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def remainder(key, seed):
    """The phrase without its seed and its probe word: what people added."""
    key = strip_probe(key)
    if seed is None:
        return key
    rest = re.sub(rf"(^| ){re.escape(seed)}( |$)", " ", key)
    if rest == key:  # fuzzy anchor: drop the seed's words instead
        seed_words, seed_st = set(seed.split()), stems(seed)
        rest = " ".join(w for w in key.split() if w not in seed_words and w[:5] not in seed_st)
    # Single letters are the collector's alphabet probes, not people's words.
    return " ".join(w for w in rest.split() if len(w) > 1 or w.isdigit())


def tags_for(rest):
    """Signals present in what people added, strongest first."""
    return [(label, w) for label, w, pat in SIGNALS if re.search(pat, rest)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seeds", help="the seeds file given to collect_suggest.py")
    ap.add_argument("out", help="the directory collect_suggest.py wrote")
    ap.add_argument("--prev", help="an earlier run's output directory, to flag new phrases")
    ap.add_argument("-n", "--top", type=int, default=25, help="topics to list (default 25)")
    args = ap.parse_args()

    seeds = sorted({norm(l) for l in Path(args.seeds).read_text(encoding="utf-8").splitlines()
                    if l.strip() and not l.startswith("#")}, key=len, reverse=True)
    out = Path(args.out)
    google = read_list(out / "подсказки-поиск.txt")
    youtube = read_list(out / "подсказки-видео.txt")
    only_video = read_list(out / "только-видео.txt")
    if not google and not youtube:
        raise SystemExit(f"No suggestion files in {out}. Run collect_suggest.py first.")

    prev = set()
    if args.prev:
        for name in ("подсказки-поиск.txt", "подсказки-видео.txt"):
            prev |= set(read_list(Path(args.prev) / name))
        prev |= set(read_list(Path(args.prev) / "сырые" / "подсказки-поиск.txt"))
        prev |= set(read_list(Path(args.prev) / "сырые" / "подсказки-видео.txt"))

    # Google + the cleaned YouTube-only list. The full YouTube list is used
    # only to mark phrases both sources return.
    pool = dict(google)
    pool.update(only_video)

    seed_stems = {seed: stems(seed) for seed in seeds}
    clusters = defaultdict(lambda: {"score": 0, "phrases": [], "tags": defaultdict(int),
                                    "sources": defaultdict(int), "new": 0})
    # Endings people add to many different seeds ("не работает", "цена") are
    # series, not single posts: "почему X не работает" for every platform.
    endings = defaultdict(set)
    tagged = off_topic = 0
    for key, text in pool.items():
        seed = anchor_of(key, seeds, seed_stems)
        if seed is None:
            off_topic += 1
            continue
        rest = remainder(key, seed)
        tags = tags_for(rest)
        if not tags:
            continue
        tagged += 1
        in_g, in_y = key in google, key in youtube
        source = "оба" if in_g and in_y else ("YouTube" if key in only_video else "Google")
        score = sum(w for _, w in tags)
        if source == "YouTube":
            score += 2  # the method's main source: emotional, little competition
        elif source == "оба":
            score += 1  # demand confirmed twice
        is_new = bool(prev) and key not in prev
        if is_new:
            score += 3
        # One topic per seed and dominant signal. Learners get their own topic
        # so they never dilute what clients ask.
        primary = "ученики" if re.search(LEARNER, key) else tags[0][0]
        if primary != "ученики" and 0 < len(rest.split()) <= 3:
            endings[rest].add(seed)

        c = clusters[(seed, primary)]
        c["score"] += score
        c["phrases"].append((score, text, source, is_new))
        for label, _ in tags:
            c["tags"][label] += 1
        c["sources"][source] += 1
        c["new"] += is_new

    ranked = sorted(clusters.items(), key=lambda kv: kv[1]["score"], reverse=True)

    total_new = sum(c["new"] for c in clusters.values())
    lines = [
        "# Темы-кандидаты для постов",
        "",
        f"Собрано {datetime.date.today().isoformat()}: {plural(len(pool), 'фраза', 'фразы', 'фраз')}, "
        f"не по теме {off_topic}, с сигналами {tagged}, тем {len(clusters)}.",
        "",
        (f"Новых фраз с прошлого прогона: {total_new}." if prev else
         "Сравнения с прошлым прогоном нет: это срез спроса, а не динамика."),
        "",
        "Сигналы считаются только по тому, что дописали люди: пробные слова "
        "сборщика («стоит ли», «правда ли» и другие) отрезаны.",
        "",
    ]
    series = sorted(((e, sd) for e, sd in endings.items() if len(sd) >= 3),
                    key=lambda kv: (-len(kv[1]), kv[0]))[:10]
    if series:
        lines += ["## Сквозные темы", "",
                  "Одно и то же дописывают к разным корням. Это серия постов, "
                  "по одному на площадку или инструмент.", ""]
        for ending, sd in series:
            lines.append(f"- «{ending}»: {plural(len(sd), 'корень', 'корня', 'корней')} ({', '.join(sorted(sd))})")
        lines.append("")
    lines += ["## Темы по корням", ""]
    for i, ((seed, primary), c) in enumerate(ranked[: args.top], 1):
        tags = ", ".join(f"{k} {v}" for k, v in sorted(c["tags"].items(), key=lambda kv: -kv[1]))
        src = ", ".join(f"{k} {v}" for k, v in sorted(c["sources"].items()))
        lines.append(f"### {i}. {seed} · {primary} · {plural(c['score'], 'балл', 'балла', 'баллов')}")
        lines.append("")
        lines.append(f"Сигналы: {tags}. Источники: {src}."
                     + (f" Новых: {c['new']}." if c["new"] else ""))
        lines.append("")
        for score, text, source, is_new in sorted(c["phrases"], reverse=True)[:6]:
            lines.append(f"- {text} ({source}{', новое' if is_new else ''})")
        lines.append("")

    (out / "темы-кандидаты.md").write_text("\n".join(lines), encoding="utf-8")
    summary = [{"seed": seed, "signal": primary, "score": c["score"],
                "phrases": [t for _, t, _, _ in sorted(c["phrases"], reverse=True)[:6]],
                "new": c["new"], "tags": dict(c["tags"]), "sources": dict(c["sources"])}
               for (seed, primary), c in ranked[: args.top]]
    summary = {"series": [{"ending": e, "seeds": sorted(sd)} for e, sd in series],
               "topics": summary}
    (out / "темы-кандидаты.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

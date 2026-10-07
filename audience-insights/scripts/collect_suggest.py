#!/usr/bin/env python3
"""Blocks 1-2 of the insight method: Google and YouTube autocomplete.

Expands seed phrases with the Russian alphabet and question words, queries the
public suggest endpoint for both sources, and writes the three raw files plus
overlap statistics. Stdlib only. No browser, no login: the endpoint answers
plain HTTP requests.

    python3 collect_suggest.py seeds.txt -o out/
"""
import argparse
import concurrent.futures as cf
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://suggestqueries.google.com/complete/search"
ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
QUESTIONS = ["как", "почему", "что", "где", "можно ли", "стоит ли", "зачем",
             "сколько", "нужно ли", "правда ли", "что делать если"]
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"


class Blocked(Exception):
    pass


def expand(seed):
    yield seed
    for ch in ALPHABET:
        yield f"{seed} {ch}"
    for q in QUESTIONS:
        yield f"{q} {seed}"


def norm(s):
    return " ".join(s.lower().replace("ё", "е").split())


def seed_tail(key, seeds):
    """The words after the longest seed that starts `key`, or None."""
    for seed in sorted(seeds, key=len, reverse=True):
        if key.startswith(seed + " "):
            return key[len(seed) + 1:]
    return None


def is_fragment(key, seeds):
    """A seed plus one dangling token of <=3 letters: "копирайтинг ыры".

    YouTube answers alphabet probes with word stubs; Google does it less.
    Raw files keep everything.
    """
    tail = seed_tail(key, seeds)
    return tail is not None and " " not in tail and len(tail) <= 3


def is_yt_stub(key, seeds):
    """YouTube only: a seed plus exactly one word, "маркетолог яблони".

    In a 28-seed run these made 59% of the YouTube-only list and were almost
    all noise. Google's seed-plus-one-word completions carry real queries
    ("таргетолог развод"), so this rule cleans the YouTube-only list alone.
    """
    tail = seed_tail(key, seeds)
    return tail is not None and " " not in tail


def fetch(query, youtube, retries=4):
    params = {"client": "firefox", "hl": "ru", "gl": "ru",
              "ie": "utf-8", "oe": "utf-8", "q": query}
    if youtube:
        params["ds"] = "yt"
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    delay = 1.0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read().decode("utf-8")
            return json.loads(body)[1]
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                if attempt == retries - 1:
                    raise Blocked(f"HTTP {e.code} after {retries} tries")
                time.sleep(delay + random.random())
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == retries - 1:
                return []
            time.sleep(delay)
            delay *= 2
    return []


def collect(queries, youtube, workers, label):
    found, done, failures = {}, 0, 0
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, q, youtube): q for q in queries}
        for fut in cf.as_completed(futures):
            done += 1
            try:
                for s in fut.result():
                    found.setdefault(norm(s), s)
            except Blocked as e:
                failures += 1
                if failures >= 3:
                    pool.shutdown(cancel_futures=True)
                    sys.exit(f"\n{label}: rate-limited ({e}). Stopped to avoid "
                             f"hammering the endpoint. Lower --workers and retry.")
            if done % 50 == 0 or done == len(queries):
                print(f"\r{label}: {done}/{len(queries)} queries, "
                      f"{len(found)} unique", end="", file=sys.stderr)
    print(file=sys.stderr)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seeds", help="file with one seed phrase per line")
    ap.add_argument("-o", "--out", default=".", help="output directory")
    ap.add_argument("-w", "--workers", type=int, default=6,
                    help="parallel requests (default 6; raise with care)")
    args = ap.parse_args()

    seeds = [l.strip() for l in Path(args.seeds).read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    queries = list(dict.fromkeys(q for s in seeds for q in expand(s)))
    print(f"{len(seeds)} seeds -> {len(queries)} queries per source", file=sys.stderr)

    started = time.time()
    google = collect(queries, False, args.workers, "google ")
    youtube = collect(queries, True, args.workers, "youtube")

    out = Path(args.out)
    (out / "сырые").mkdir(parents=True, exist_ok=True)
    for name, data in (("подсказки-поиск.txt", google), ("подсказки-видео.txt", youtube)):
        (out / "сырые" / name).write_text(
            "\n".join(data[k] for k in sorted(data)) + "\n", encoding="utf-8")

    seed_keys = [norm(x) for x in seeds]
    g_raw, y_raw = set(google), set(youtube)
    g = {k for k in g_raw if not is_fragment(k, seed_keys)}
    y = {k for k in y_raw if not is_fragment(k, seed_keys)}
    # Overlap is measured on equally filtered sets. The one-word stub rule then
    # cleans only the YouTube-only list: a phrase Google also returns is
    # corroborated, and Google keeps its own one-word completions.
    only_y_all = y - g
    only_y = sorted(k for k in only_y_all if not is_yt_stub(k, seed_keys))
    (out / "подсказки-поиск.txt").write_text(
        "\n".join(google[k] for k in sorted(g)) + "\n", encoding="utf-8")
    (out / "подсказки-видео.txt").write_text(
        "\n".join(youtube[k] for k in sorted(y)) + "\n", encoding="utf-8")
    (out / "только-видео.txt").write_text(
        "\n".join(youtube[k] for k in only_y) + "\n", encoding="utf-8")

    union = g | y
    stats = {
        "seeds": len(seeds),
        "queries_per_source": len(queries),
        "google_raw": len(g_raw),
        "youtube_raw": len(y_raw),
        "fragments_dropped_google": len(g_raw - g),
        "fragments_dropped_youtube": len(y_raw - y),
        "google_unique": len(g),
        "youtube_unique": len(y),
        "only_google": len(g - y),
        "only_youtube": len(only_y_all),
        "only_youtube_stubs_dropped": len(only_y_all) - len(only_y),
        "only_youtube_kept": len(only_y),
        "both": len(g & y),
        "jaccard_overlap_pct": round(100 * len(g & y) / len(union), 1) if union else 0,
        "youtube_also_in_google_pct": round(100 * len(g & y) / len(y), 1) if y else 0,
        "seconds": round(time.time() - started, 1),
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

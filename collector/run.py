"""Сбор базы: категории × города → docs/data.js для прототипа.

    python -m collector.run                     # всё из config.json
    python -m collector.run --only "рис для суши" --city Москва

Повторный запуск не ходит заново на сайты и в модель: ответы лежат в
data/cache (в git не идёт).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor

from . import extract, fetch, score, search

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
SEARCH = search.yandex_search  # подменяется в тестах и при запуске через свой поиск
LLM_CHARS = int(os.environ.get("LLM_CHARS", "12000"))  # окно модели: бесплатные тарифы длиннее не берут
DEAD: set[str] = set()  # сайты, что не открылись в этом прогоне: второй раз не ждём таймаут


def _cached(kind: str, key: str, make):
    path = CACHE / kind / (hashlib.sha1(key.encode()).hexdigest()[:16] + ".json")
    if path.exists():
        return json.loads(path.read_text())
    value = make()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))
    return value


def candidates(category: dict, city: dict, per_pair: int) -> list[search.Hit]:
    """Сайты поставщиков из выдачи: без агрегаторов и повторов домена."""
    lists = []
    for q in category["queries"]:
        query = q.format(city=city["name"])
        raw = _cached("search", f"{query}|{city['yandex_region']}",
                      lambda q=query: [h.__dict__ for h in SEARCH(q, 20, city["yandex_region"])])
        lists.append([search.Hit(**h) for h in raw])
    # запросы вперемешку: первый запрос не должен забрать все места
    hits = [h for row in itertools.zip_longest(*lists) for h in row if h]
    out, seen = [], set()
    for h in hits:
        if h.domain in seen or search.is_aggregator(h.domain):
            continue
        seen.add(h.domain)
        out.append(h)
    return out[:per_pair]


def build_card(hit: search.Hit, category: dict, city: dict) -> dict | None:
    """Карточка поставщика или None, если это не поставщик этой категории."""
    if hit.domain in DEAD:
        return None
    try:
        site = _cached("site", hit.domain, lambda: fetch.fetch_site(hit.url))
    except fetch.FETCH_ERRORS as e:  # сайт не открылся — пропускаем, без него карточки нет
        DEAD.add(hit.domain)
        print(f"skip {hit.domain}: {e.__class__.__name__}", file=sys.stderr)
        return None
    text = "\n\n".join(p["text"] for p in site["pages"])
    if len(text) < 300:
        return None
    # Модели — поровну с каждой страницы: иначе главная съест всё окно,
    # а условия опта и доставки живут как раз на подстраницах.
    share = LLM_CHARS // len(site["pages"])
    llm_text = "\n\n".join(p["text"][:share] for p in site["pages"])
    try:
        card = _cached("llm", f"{hit.domain}|{category['name']}|{city['name']}",
                       lambda: extract.llm_fields(llm_text, category["name"], city["name"], check=text))
    except (OSError, ValueError, KeyError, RuntimeError) as e:  # сеть, лимит, битый ответ
        print(f"llm fail {hit.domain}: {e}", file=sys.stderr)
        return None
    if not (card.get("is_supplier") and card.get("matches_category")):
        return None
    card.update(extract.rule_fields(text, site["links"]))
    card.update(domain=hit.domain, site=site["url"], category=category["name"],
                search_city=city["name"], found_by=hit.snippet,
                sources=[p["url"] for p in site["pages"]])
    return score.score(card, city["name"])


def collect(config: dict, only: str | None, only_city: str | None) -> list[dict]:
    jobs = [(h, cat, city)
            for cat in config["categories"] if not only or cat["name"] == only
            for city in config["cities"] if not only_city or city["name"] == only_city
            for h in candidates(cat, city, config.get("sites_per_pair", 6))]
    print(f"sites to read: {len(jobs)}", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=int(os.environ.get("COLLECT_WORKERS", "3"))) as pool:
        cards = list(pool.map(lambda j: build_card(*j), jobs))
    return [c for c in cards if c]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--only")
    ap.add_argument("--city")
    args = ap.parse_args(argv)
    config = json.loads(pathlib.Path(args.config).read_text())
    cards = sorted(collect(config, args.only, args.city), key=lambda c: -c["score"])
    payload = {"collected_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
               "cities": [c["name"] for c in config["cities"]],
               "categories": [c["name"] for c in config["categories"]],
               "suppliers": cards}
    (ROOT / "data" / "suppliers.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    (ROOT / "docs" / "data.js").write_text(
        "window.SUPPLIERS = " + json.dumps(payload, ensure_ascii=False) + ";\n")
    print(f"suppliers: {len(cards)}", file=sys.stderr)


if __name__ == "__main__":
    main()

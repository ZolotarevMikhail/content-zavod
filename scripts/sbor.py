#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Этап 0 «Контент-завода»: поиск в интернете тем-кандидатов по теме из config.json.

Что делает:
  1. Читает config.json (источники, ключевые слова).
  2. Забирает RSS-ленты и открытые страницы Telegram-каналов (t.me/s/...) — без ключей.
  3. Фильтрует по теме, убирает дубли.
  4. Сохраняет результат в candidates.json.
Дальше кандидатов суммирует и оценивает Claude по рубрике (config/rubrica.md).

Запуск:  python3 scripts/sbor.py     (периодичность — в config.json)
"""

import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"
OUT_FILE = ROOT / "candidates.json"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def fetch(url: str) -> bytes | None:
    """Скачать страницу. 2 попытки с паузой; при неудаче — None, не падаем."""
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except Exception as e:
            if attempt == 2:
                print(f"  ! не удалось скачать {url}: {e}")
                return None
            time.sleep(3)
    return None


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def parse_rss(url: str) -> list[dict]:
    data = fetch(url)
    if not data:
        return []
    items = []
    try:
        root = ET.fromstring(data)
        for item in root.iter("item"):
            title = strip_html(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            desc = strip_html(item.findtext("description") or "")[:500]
            pub = (item.findtext("pubDate") or "").strip()
            if title and link:
                items.append({"title": title, "url": link,
                              "summary": desc, "source": url, "date": pub})
    except ET.ParseError as e:
        print(f"  ! RSS не разобрался ({url}): {e}")
    return items


def parse_tg_channel(name: str) -> list[dict]:
    data = fetch(f"https://t.me/s/{name}")
    if not data:
        return []
    html = data.decode("utf-8", errors="ignore")
    items = []
    # блок сообщения: текст поста + ссылка на конкретный пост
    for m in re.finditer(
        r'tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>.*?'
        r'href="https://t\.me/%s/(\d+)"' % re.escape(name),
        html, re.S,
    ):
        text = strip_html(m.group(1))
        if text:
            items.append({
                "title": text[:150],
                "url": f"https://t.me/{name}/{m.group(2)}",
                "summary": text[:500],
                "source": f"tg:{name}",
                "date": "",
            })
    return items


def on_topic(item: dict, keywords: list[str]) -> bool:
    text = (item["title"] + " " + item["summary"]).lower()
    return any(k in text for k in keywords)


def main() -> int:
    if not CONFIG_FILE.exists():
        print(f"Нет файла {CONFIG_FILE.name} — положи рядом с scripts/")
        return 1
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    keywords = cfg["ключевые_слова"]
    sources = cfg["источники"]

    print(f"Сбор тем: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Тема: {cfg['тема']}")
    all_items: list[dict] = []
    errors = 0

    for url in sources.get("rss", []):
        print(f"RSS: {url}")
        got = parse_rss(url)
        if not got:
            errors += 1
        all_items.extend(got)
        print(f"  +{len(got)} записей")

    for name in sources.get("tg_каналы", []):
        print(f"TG: t.me/s/{name}")
        got = parse_tg_channel(name)
        if not got:
            errors += 1
        all_items.extend(got)
        print(f"  +{len(got)} записей")

    # фильтр по теме и дедупликация по ссылке
    seen, filtered = set(), []
    for it in all_items:
        if not on_topic(it, keywords):
            continue
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        filtered.append(it)

    OUT_FILE.write_text(
        json.dumps(filtered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nИтого собрано: {len(all_items)}, по теме: {len(filtered)}")
    print(f"Сохранено: {OUT_FILE.name}")
    if errors:
        print(f"Внимание: {errors} источник(ов) не ответили — не критично, "
              f"остальные собраны")
    return 0


if __name__ == "__main__":
    sys.exit(main())
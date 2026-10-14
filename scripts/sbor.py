#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Этап 0 «Контент-завода»: поиск в интернете тем-кандидатов
по теме из config.json.

Что делает:
  1. Читает config.json (источники, ключевые слова).
  2. Забирает RSS-ленты и открытые страницы Telegram-каналов
     (t.me/s/...) — без ключей.
  3. Фильтрует по теме, убирает дубли.
  4. Сохраняет результат в candidates.json.
Дальше кандидатов суммирует и оценивает Claude по рубрике
(config/rubrica.md).

Запуск:  python3 scripts/sbor.py     (периодичность — в config.json)
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import publish  # noqa: E402  (единая сеть: download через publish._get)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
CONFIG_FILE = ROOT / "config.json"
OUT_FILE = ROOT / "candidates.json"
ARCHIVE_FILE = ROOT / "архив-тем.json"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def fetch(url: str) -> bytes | None:
    """Скачать страницу (2 попытки с паузой). При неудаче — None, не падаем.

    Сеть — единая точка проекта (publish._get); здесь только параметры
    источника и свой отчёт об ошибке."""
    data, err = publish._get(url, timeout=15, user_agent=UA, attempts=2,
                             pause=3)
    if data is None:
        print(f"  ! не удалось скачать {url}: {err or 'причина неизвестна'}")
    return data


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
            # Habr отдаёт служебные заголовки
            # («Пост @автор — хаб — дата»), осмысленный текст —
            # в description: берём оттуда первую фразу
            if title.startswith("Пост @") and desc:
                first = re.split(
                    r"[.!?](?:\s|$)", desc, maxsplit=1)[0].strip()
                title = (first[:100] + "…") if len(first) > 100 \
                    else (first or title)
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


# Термины, понятные только специалистам: широкая аудитория такое пролистает.
JARGON = [
    "llm", "gpt-4", "gpt-5", "transformer", "бенчмарк", "benchmark",
    "open-source", "опенсорс", "инференс", "fine-tuning", "файнтюн",
    "датасет", "gpu", "видеокарт", "rlhf", "токен", "api", "agent sdk",
    "веса модели", "дистилл", "moе", "диффузи", "diffusion", "latency",
    "контекстное окно", "мультимодальн",
]
# Сигналы «живой» темы для не-специалиста: люди, деньги, последствия.
BROAD = [
    "человек", "люд", "врач", "юрист", "школ", "студент", "дет",
    "деньг", "млрд", "миллион", "работу", "зарплат", "запрет", "запрет",
    "штраф", "суд", "смерт", "мозг", "тело", "животн", "обман", "краж",
    "боятся", "страх", "впервые", "рекорд", "назвал", "показал",
]


def score(item: dict) -> int:
    """Предварительная оценка: 1 — тема для широкой аудитории, 0 — узкая."""
    text = (item["title"] + " " + item["summary"]).lower()
    s = 0
    jargon = sum(1 for t in JARGON if t in text)
    s -= min(jargon, 3)
    s += min(sum(1 for t in BROAD if t in text), 2)
    n = len(item["title"])
    if 30 <= n <= 120:
        s += 1  # внятный заголовок — признак понятной темы
    elif n > 160:
        s -= 1
    return s


def load_archive() -> list[dict]:
    if ARCHIVE_FILE.exists():
        return json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
    return []


def add_to_archive(items: list[dict]) -> None:
    """Дополнить архив тем лучшими темами дня.

    Решение Михаила 06.10.2026: ежедневно в архив кладём до 3 тем,
    повторы по ссылке не нужны, темы старше 6 месяцев без выбора
    удаляются."""
    archive = load_archive()
    known = {row["url"] for row in archive}
    added = []
    for it in items:
        if it["url"] in known:
            continue
        known.add(it["url"])
        added.append({
            "дата_добавления": datetime.now().strftime("%Y-%m-%d"),
            "статус": "в архиве",
            "title": it["title"],
            "url": it["url"],
            "summary": it["summary"],
            "source": it["source"],
            "дата_публикации": it["date"],
        })
    archive.extend(added)

    # чистка: невыбранные темы старше полугода — вон
    предел = (datetime.now() - timedelta(days=183)).strftime("%Y-%m-%d")
    до_чистки = len(archive)
    archive = [
        row for row in archive
        if row.get("статус") == "выбрано"
        or row.get("дата_добавления", "0000-00-00") >= предел
    ]

    ARCHIVE_FILE.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Архив тем: добавлено {len(added)}, всего {len(archive)} "
          f"(удалено просроченных: {до_чистки - len(archive)})")


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

    # оставляем только 5–10 лучших тем для широкой аудитории (лимит из конфига)
    limit = int(cfg.get("правила", {}).get("лимит_тем", 10))
    filtered.sort(key=score, reverse=True)
    kept = filtered[:limit]
    print(f"\nИтого собрано: {len(all_items)}, по теме: {len(filtered)}")
    print(f"Оставлено лучших: {len(kept)} (лимит {limit}) "
          "— отбор по понятности широкой аудитории")
    print("Топ-5 заголовков:")
    for it in kept[:5]:
        print(f"  • {it['title'][:90]}")
    filtered = kept

    OUT_FILE.write_text(
        json.dumps(filtered, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Сохранено: {OUT_FILE.name}")

    # первые 3 лучшие темы дня — в архив тем (для выбора 2 раза в неделю)
    add_to_archive(kept[:3])

    if errors:
        print(f"Внимание: {errors} источник(ов) не ответили — не критично, "
              f"остальные собраны")
    return 0


if __name__ == "__main__":
    sys.exit(main())

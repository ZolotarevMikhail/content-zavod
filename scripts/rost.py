#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Модуль «рост» контент-завода: замеряет эффект публикаций.

Раз в сутки снимает показатели канала и пишет их в историю:
  - подписчики   — через Bot API getChatMemberCount
                   (бот-администратор канала имеет право это вызывать);
  - просмотры    — с открытой страницы t.me/s/<канал>
                   (число просмотров каждого поста; если страница
                   недоступна, в снимке ставится null — модуль не падает).

История: статистика.json в корне проекта. Отчёт — сообщением в бот
(ADMIN_CHAT_ID): подписчики сегодня/вчера, прирост, топ постов недели.
Отчёт отправляется ОДИН раз в сутки: факт успешной отправки хранится
в статистика.json (ключ «отчёты»), повторные запуски молча выходят.

Запуск:  python3 scripts/rost.py            # снимок + отчёт в чат
         python3 scripts/rost.py --тихо     # только снимок, без отправки
Модуль вежлив к повторным запускам: за один день снимок делается один
раз (охранник «не чаще раза в сутки» — рецепт launchd при включениях).

Часовой запуск на launchd (после включения Mac) живёт в
ru.zolmy.contentzavod-rost.plist — скрипт сам решает, пора или нет.
"""

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import publish  # noqa: E402  (единая сеть проекта: load_env, _post, _get)

API = publish.API_TG  # шаблон Bot API — из publish (единая точка)
СТАТИСТИКА = ROOT / "статистика.json"
CONFIG = ROOT / "config.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36")


def канал_из_конфига() -> str:
    """Имя канала из config.json (@zolotmixail_ai)."""
    try:
        conf = json.loads(CONFIG.read_text(encoding="utf-8"))
        return conf["соцсети"]["telegram"]["канал_id"]
    except (OSError, KeyError, json.JSONDecodeError):
        return ""


def load_историю() -> dict:
    """Прочитать историю снимков (нет файла — пустая история)."""
    if СТАТИСТИКА.exists():
        try:
            return json.loads(СТАТИСТИКА.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # битый файл — не молча терять историю: сдвигаем в отдельный
            # файл, чтобы её можно было восстановить руками
            копия = СТАТИСТИКА.with_name("статистика-битая.json")
            СТАТИСТИКА.replace(копия)
            print(f"! статистика.json битый ({exc}) — сохранён как "
                  "статистика-битая.json; восстанови вручную и верни")
            return {"снимки": {}}
    return {"снимки": {}}


def сохранить_историю(история: dict) -> None:
    """Записать историю на диск: сначала во временный файл, затем замена —
    сон Mac или сбой посреди записи не оставит битый JSON."""
    врем = СТАТИСТИКА.with_name("статистика-временная.json")
    врем.write_text(
        json.dumps(история, ensure_ascii=False, indent=2),
        encoding="utf-8")
    врем.replace(СТАТИСТИКА)


def число_подписчиков(token: str, канал: str) -> int | None:
    """Подписчики канала через Bot API getChatMemberCount.

    Возвращает None, если сеть или права не позволили — это не срывает
    весь снимок, просто этого показателя сегодня не будет."""
    res = publish._post(API.format(token=token, method="getChatMemberCount"),
                        {"chat_id": канал}, attempts=2)
    if res.get("ok"):
        return int(res["result"])
    print(f"! подписчики не снялись: {res.get('error')}")
    return None


def _к_числу(счётчик: str) -> int | None:
    """«1.2K» / «1 234» из разметки t.me — в целое число."""
    text = счётчик.strip().replace(" ", "").replace(",", "")
    text = re.sub(r"\s+", "", text)
    if text.endswith("K"):
        try:
            return int(float(text[:-1]) * 1000)
        except ValueError:
            return None
    if text.endswith("M"):
        try:
            return int(float(text[:-1]) * 1_000_000)
        except ValueError:
            return None
    if text.isdigit():
        return int(text)
    return None


def просмотры_с_tme(канал: str) -> dict[int, int] | None:
    """Просмотры каждого поста с открытой страницы t.me/s/<канал>.

    Возвращает {id поста: просмотры} или None, если страница не открылась
    (t.me в этой сети бывает недоступен — тогда просто пропускаем).
    Если разметка страницы изменится, вернём пустой словарь — не падение,
    но сигнал, что парсер нужно обновить."""
    html_bytes, err = publish._get(f"https://t.me/s/{канал}",
                                   timeout=25, user_agent=UA, attempts=2)
    if html_bytes is None:
        print(f"! страница t.me не открылась: {err}")
        return None
    html = html_bytes.decode("utf-8", errors="replace")
    result: dict[int, int] = {}
    # один блок = всё содержимое одного сообщения на странице
    for блок in re.split(r"tgme_widget_message_wrap", html)[1:]:
        id_m = re.search(r'data-post="@?[^/"]+/(\d+)"', блок)
        view_m = re.search(r"tgme_widget_message_views[^>]*>\s*"
                           r"(?:<[^>]*>\s*)*([0-9][0-9.,\s]*?[KM]?)\s*<",
                           блок)
        if id_m and view_m:
            post_id = int(id_m.group(1))
            число = _к_числу(view_m.group(1))
            if число is not None:
                result[post_id] = число
    return result


def сделать_снимок(token: str, канал: str) -> dict:
    """Собрать показатели за сегодня. Отсутствующий показатель — None."""
    return {
        "дата": date.today().isoformat(),
        "время": datetime.now().strftime("%H:%M"),
        "подписчиков": число_подписчиков(token, канал) if token else None,
        "просмотры": просмотры_с_tme(канал.lstrip("@")),
    }


def снимок_полный(снимок: dict) -> bool:
    """Снимок удался, если хотя бы один показатель числовой."""
    if снимок.get("подписчиков") is not None:
        return True
    return bool(снимок.get("просмотры"))


def отчёт_уже_шёл(история: dict, сегодня: str) -> bool:
    """True, если успешный отчёт за сегодня уже отправлен (один в сутки)."""
    return bool(история.get("отчёты", {}).get(сегодня))


def пометить_отчёт(история: dict, сегодня: str) -> None:
    """Запомнить, что отчёт за сегодня отправлен — чтобы не дублировать."""
    отчёты = история.setdefault("отчёты", {})
    отчёты[сегодня] = True
    # держим только последние 7 дат — файл не растёт бесконечно
    for дата in sorted(отчёты)[:-7]:
        отчёты.pop(дата, None)


def снять_или_взять_уже(история: dict, token: str, канал: str) -> dict:
    """Охранник однократности: за сегодня полный снимок берётся один раз.

    Повторный запуск (launchd после каждого включения Mac) перечитывает
    сеть только если сегодняшний снимок неполный — например, сеть была
    порвана. Так до-снимается пропущенное, а удачные поля не перетираются."""
    key = date.today().isoformat()
    есть = история["снимки"].get(key)
    if есть is not None and снимок_полный(есть):
        return есть
    fresh = сделать_снимок(token, канал)
    if есть is None:
        история["снимки"][key] = fresh
        return fresh
    # дозаполнение неполного снимка свежими данными
    if fresh.get("подписчиков") is not None:
        есть["подписчиков"] = fresh["подписчиков"]
    if fresh.get("просмотры"):
        есть["просмотры"] = fresh["просмотры"]
    есть["время"] = fresh["время"]
    return есть


def снимки_сортированные(снимки: dict) -> list[dict]:
    """Снимки по датам от старых к новым (для поисков «последнего»)."""
    return sorted((s for s in снимки.values() if s), key=lambda x: x["дата"])


def первое_число_подписчиков(снимки: dict):
    """Самое раннее известное число подписчиков в истории."""
    for снимок in снимки_сортированные(снимки):
        if снимок.get("подписчиков") is not None:
            return снимок["подписчиков"]
    return None


def обновить(token: str) -> dict:
    """Один шаг «роста» для бота: снять/дозаполнить снимок и сохранить.

    Возвращает свежую историю — бот потом просит по ней отчёт текстом."""
    канал = канал_из_конфига()
    история = load_историю()
    if канал:
        снять_или_взять_уже(история, token, канал)
        сохранить_историю(история)
    return история


def отчёт_текст(история: dict) -> str:
    """Отчёт для бота: подписчики (+прирост) и топ постов по просмотрам."""
    снимки = история["снимки"]
    строки = ["📈 <b>Рост канала</b>"]

    подпис = None
    дата_числа = ""
    for снимок in reversed(снимки_сортированные(снимки)):  # свежее первое
        if снимок.get("подписчиков") is not None:
            подпис = снимок["подписчиков"]
            дата_числа = снимок["дата"]
            break
    if подпис is None:
        строки.append("Подписчики: пока неизвестны (нет связи с Bot API).")
    else:
        строки.append(f"Подписчики: <b>{подпис}</b> (замер {дата_числа})"
                      if дата_числа != date.today().isoformat() else
                      f"Подписчики сегодня: <b>{подпис}</b>")
        вчерашний = снимки.get((date.today() - timedelta(days=1))
                               .isoformat(), {})
        вчера = вчерашний.get("подписчиков")
        if вчера is not None:
            дельта = подпис - вчера
            знак = "+" if дельта >= 0 else ""
            строки.append(f"За сутки: {знак}{дельта}")
        первый = первое_число_подписчиков(снимки)
        if первый is not None and первый != подпис:
            строки.append(f"От первого замера: +{подпис - первый}")

    # топ постов: последний снимок, где просмотры дались
    for снимок in reversed(снимки_сортированные(снимки)):
        просмотры = снимок.get("просмотры") or {}
        if not просмотры:
            continue
        строки.append("")
        строки.append("Просмотры последних постов:")
        for post_id in sorted(просмотры, reverse=True)[:5]:
            строки.append(f"• пост {post_id}: {просмотры[post_id]}")
        лучший = max(просмотры, key=просмотры.get, default=None)
        if лучший is not None:
            строки.append(f"🏆 Лучший: пост {лучший} "
                          f"({просмотры[лучший]} просмотров)")
        break
    else:
        строки.append("")
        строки.append("Просмотры снять не удалось (t.me недоступен) — "
                      "попробую в следующий запуск.")
    return "\n".join(строки)


def отправить_отчёт(token: str, chat_id: str, текст: str) -> bool:
    """Отчёт в чат владельцу через Bot API."""
    res = publish._post(API.format(token=token, method="sendMessage"),
                        {"chat_id": chat_id, "text": текст,
                         "parse_mode": "HTML",
                         "disable_web_page_preview": True}, attempts=2)
    if not res.get("ok"):
        print(f"! отчёт не ушёл: {res.get('error')}")
    return bool(res.get("ok"))


def main(argv: list[str]) -> int:
    тихий = "--тихо" in argv
    env = publish.load_env()
    token = env.get("BOT_TOKEN", "")
    chat_id = env.get("ADMIN_CHAT_ID", "")
    канал = канал_из_конфига()
    if not канал:
        print("Нет канала в config.json (соцсети.telegram.канал_id).")
        return 1
    if not тихий and (not token or not chat_id):
        print("Нет BOT_TOKEN/ADMIN_CHAT_ID в .env — снимок сделаю, "
              "но отчёт послать некуда.")
        тихий = True

    история = load_историю()
    снимок = снять_или_взять_уже(история, token, канал)
    сохранить_историю(история)

    if not снимок_полный(снимок):
        print("Сеть недоступна, показатели не сняты. Попробую позже.")
        return 1

    print(f"Снимок за {снимок['дата']} сохранён в статистика.json")
    if тихий:
        return 0
    if отчёт_уже_шёл(история, снимок["дата"]):
        print("Отчёт за сегодня уже отправлялся — выхожу тихо.")
        return 0
    if отправить_отчёт(token, chat_id, отчёт_текст(история)):
        пометить_отчёт(история, снимок["дата"])
        сохранить_историю(история)
        print("Отчёт отправлен в чат владельца.")
        return 0
    print("Отчёт не отправился — сеть моргнула; следующий "
          "запуск повторит попытку.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

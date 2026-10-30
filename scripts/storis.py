#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сторис-конвейер «Контент-завода» (ядро надстройки).

Файл-план дня (storis_plan.py — месяцы стратегии Coral Club) →
сборка пакета: карточка для Telegram + фоновая картинка 1080×1920
у Pollinations (тот же ключ и стиль, что у анимации постов) →
единая отправка пакета в чат с кнопками (отправить_пакет).

Публикацию в Instagram всегда делает человек: руки, а не код
(неофициальный постинг = бан аккаунта, а официального API для
стикеров нет). Пользуются ядром два клиента: bot.py (кнопки,
команды «фон» / «сторис дня N») и storis_send.py (сборка пакета
из сессии Claude).
"""

import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402  (общие: .env, единая сеть GET/POST с повторами)
from storis_plan import (ПЛАН_МЕСЯЦ_1, ПЛАН_МЕСЯЦ_2,  # noqa: E402
                         ПАКЕТ_МЕСЯЦ_2)

ROOT = Path(__file__).resolve().parent.parent
STORIS_DIR = ROOT / "storis"
STATE = STORIS_DIR / "state.json"

# Активные месяцы: номер → список дней. Новый месяц добавляется сюда
# (скилл: «собери план сторис месяца N») — счётчик дней у месяца свой.
ПЛАНЫ = {1: ПЛАН_МЕСЯЦ_1, 2: ПЛАН_МЕСЯЦ_2}

# Полные пакеты: месяц → словарь «день → что ещё выкладывается кроме
# сторис» (рилс, пост ВК/ТГ). Тексты — в файлах пакетов Coral Club.
ПАКЕТЫ = {2: ПАКЕТ_МЕСЯЦ_2}

# Режим работы (решение Михаила 10.10.2026): темы идут по цепочке БЕЗ
# привязки к датам. День публикуется, следующий готовится только ПОСЛЕ
# кнопки «Опубликовал». Пропуски дней не важны.

WIDTH, HEIGHT = 1080, 1920  # формат Instagram-сторис


def load_state() -> dict:
    """Прочитать/создать состояние: месяц, текущий день, история отправок."""
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        state.setdefault("месяц", 1)  # старые state — это месяц 1
        return state
    return {"месяц": 1, "день": 1, "отправлено": {}, "кончился": False}


def активный_план() -> list:
    """Дни активного месяца (поле «месяц» в state)."""
    return ПЛАНЫ.get(load_state().get("месяц", 1), ПЛАН_МЕСЯЦ_1)


def load_plan() -> dict:
    """Дни активного месяца в словаре: номер дня → запись."""
    return {зап["день"]: зап for зап in активный_план()}


def max_plan_day() -> int:
    """Последний авторский день плана (после него — резерв и конец месяца)."""
    return max(зап["день"] for зап in активный_план())


def save_state(state: dict) -> None:
    STORIS_DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def fon_path(day: int, месяц: int | None = None) -> Path:
    """Имя фона с месяцем: дни разных месяцев не путают файлы."""
    if месяц is None:
        месяц = load_state().get("месяц", 1)
    return STORIS_DIR / f"фон-м{месяц}-день-{day}.png"


def make_fon(day: int, prompt: str, attempts: int = 2, seed: int | None = None):
    """Скачать фон дня у Pollinations. Путь или None (с честной причиной).

    Сеть — единая точка проекта: каждая попытка через publish._get,
    здесь только проверка «это картинка?» и свой отчёт об ошибке.
    seed по умолчанию детерминированный (день), при перегенерации «фон»
    передаётся случайный — иначе та же картинка возвращается той же."""
    env = publish.load_env()
    key = env.get("POLLINATIONS_KEY", "")
    if not key:
        return None, "нет ключа POLLINATIONS_KEY в .env"

    if seed is None:
        seed = 100 + day
    from urllib.parse import quote
    url = (f"https://gen.pollinations.ai/image/{quote(prompt)}"
           f"?model=flux&width={WIDTH}&height={HEIGHT}"
           f"&seed={seed}&nologo=true&key={key}")
    path = fon_path(day)
    STORIS_DIR.mkdir(exist_ok=True)
    reason = ""
    for attempt in range(1, attempts + 1):
        data, err = publish._get(url, timeout=120, user_agent="curl/8")
        if data is None:
            reason = err or "сеть не ответила"
        elif data[:8] == b"\x89PNG\r\n\x1a\n" or data[:2] == b"\xff\xd8":
            path.write_bytes(data)
            return path, None
        else:
            path.with_suffix(".txt").write_bytes(data[:2000])
            reason = "пришёл не-графический ответ"
        time.sleep(6 * attempt)  # 6с, 12с — и хватит
    return None, f"после {attempts} попыток не скачался ({reason})"


def карточка(entry: dict) -> str:
    """Текст карточки дня для Telegram: понятно и по делу."""
    день = entry["день"]
    title = f"📱 Сторис дня {день}: {entry['тема']}"
    parts = [title]
    пакет_месяца = ПАКЕТЫ.get(load_state().get("месяц", 1), {})
    if день in пакет_месяца:
        parts.append(
            f"📌 Полный пакет: сегодня ещё {пакет_месяца[день]} "
            "— сценарий и текст в файле «Полный пакет — месяц 2» "
            "(папка Coral Club, Paket_mesyaca_2_full.txt)")
    tip = {"текст": "готовая сторис: текст ниже берётся словами в кадр, "
                    "фоновую картинку пришлю следом",
           "опрос": "готовая сторис: текст в кадр + стикер-опрос добавишь "
                    "руками (автоматика стикеры не умеет), фон пришлю следом",
           "каркас": "личная сторис: готового текста быть не может — "
                     "пишет и снимает сама; ниже готовая подводка "
                     "и чек-лист, о чём говорить"}
    parts.append(f"Тип: {tip[entry['тип']]}")
    parts.append("")
    if entry.get("текст"):
        parts.append(f"Текст в кадре:\n{entry['текст']}")
    if entry.get("подводка"):
        parts.append(f"Подводка (потри под себя):\n{entry['подводка']}")
    if entry.get("вопрос"):
        parts.append(f"Стикер-опрос: «{entry['вопрос']}»\n"
                     "варианты: " + " / ".join(entry["варианты"]))
    if entry.get("подсказки"):
        parts.append("Чек-лист, о чём рассказать:")
        parts += [f"— {s}" for s in entry["подсказки"]]
    if entry.get("пометка_бад"):
        parts.append("⚠️ Помни ст. 25 закона «О рекламе»: продукт упомянут, "
                     "значит рядом заметно — «БАД. Не является "
                     "лекарственным средством»")
    parts.append("")
    parts.append("Как публикуют: скопировать текст/подводку в кадр (или "
                 "снять своё), выложить у себя в Instagram как «Свою "
                 "историю» и нажать в боте «Опубликовал» — это отметка "
                 "«уже выложила», код сам ничего не публикует. После "
                 "отметки приду со следующим днём.")
    return "\n".join(parts)


def регенерировать_фон(day: int):
    """Пересоздать фон дня по требованию («фон» в боте). Путь или None.
    Seed случайный: детерминированный сид возвращал ТО ЖЕ изображение,
    regeneration не имел смысла (ревью 30.10.2026)."""
    entry = load_plan().get(day)
    if entry is None or entry["тип"] == "каркас":
        return None, "у этого дня фона нет по плану"
    import random
    return make_fon(day, entry["промпт"], seed=random.randint(1, 10 ** 6))


def подготовить_день(day: int) -> tuple[str, object, object]:
    """Собрать пакет дня: (карточка, путь к фону | None, причина_без_фона).

    Для «каркаса» фона нет по плану (None, None)."""
    entry = load_plan()[day]
    text = карточка(entry)
    if entry["тип"] == "каркас":
        return text, None, None
    # готовый файл не пересоздаём: запросы к генератору — по минимуму
    if fon_path(day).exists():
        return text, fon_path(day), None
    path, err = make_fon(day, entry["промпт"])
    if path is not None:
        text += "\n\n🖼 Фон прилагаю отдельным фото — сохрани его."
    else:
        text += (f"\n\n⚠️ Фон не сгенерировался ({err}). Напиши мне "
                 "«фон» — попробую ещё раз.")
    return text, path, err


def кнопки_пакета(markup: bool = True, day: int | None = None) -> str | None:
    """Клавиатура пакета сторис (ЕДИНАЯ для бота и storis_send).
    День зашивается в callback_data: кнопка старой карточки не должна
    двигать уже продвинутый день (ревью 30.10.2026)."""
    if not markup:
        return None
    суффикс = f"_{day}" if day is not None else ""
    return json.dumps({"inline_keyboard": [[
        {"text": "✅ Опубликовал", "callback_data": f"story_ok{суффикс}"},
        {"text": "➡️ Пропустил", "callback_data": f"story_skip{суффикс}"},
        {"text": "✏️ Написать по-другому",
         "callback_data": f"story_again{суффикс}"},
    ]]}, ensure_ascii=False)


def отправить_пакет(token: str, chat_id: str, day: int,
                    без_кнопок: bool = False) -> tuple[bool, str]:
    """ЕДИНАЯ отправка пакета: карточка + фон + запись в state.

    Пользуются bot.py («сторис дня N») и storis_send.py (сессия Claude).
    Отправленный текущий день отмечается в state; пакет заднего числа
    ничего не двигает. Возвращает (успех, отчёт)."""
    plan = load_plan()
    if day not in plan:
        return False, f"Дня {day} нет в плане (дни 1–{max_plan_day()})."
    state = load_state()
    text, fon, err = подготовить_день(day)
    res = publish._post(
        f"https://api.telegram.org/bot{token}/sendMessage", {
            "chat_id": chat_id,
            # 4096 — лимит Telegram; обрезку обозначаем, не молчим
            "text": (text if len(text) <= 4000 else text[:3995] + "\n…"),
            "reply_markup": кнопки_пакета(
                not (без_кнопок or day != state["день"]), day=day),
        })
    if not res.get("ok"):
        return False, f"НЕ отправилось: {res.get('error') or res}"
    фон_стат = "нет фона по плану"
    if fon is not None and fon.exists():
        фото = publish._upload_multipart(
            f"https://api.telegram.org/bot{token}/sendPhoto", {
                "chat_id": chat_id,
                "caption": (f"🖼 Фон для сторис дня {day} — сохрани и "
                            "открой в Instagram в «Своей истории»"),
            }, "photo", fon.name, fon.read_bytes())
        фон_стат = ("ок" if фото.get("ok")
                    else f"не ушло ({фото.get('error') or фото})")
    elif err:
        фон_стат = f"фон не собрался: {err}"
    if day == state["день"]:
        state["отправлено"][str(day)] = {
            "версия": plan[day]["версия"],
            "дата": date.today().isoformat(),
            "фон": фон_стат,
        }
        save_state(state)
    return True, f"День {day} в чате. Фон: {фон_стат}."

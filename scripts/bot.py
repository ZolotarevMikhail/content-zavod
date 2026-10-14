#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Этапы 2–3 «Контент-завода»: одобрение идей и постов
в Telegram-боте + публикация.

Сторис-конвейер (надстройка): раз в сутки бот сам присылает пакет
сторис по плану storis_plan.py (месяц 1 стратегии Coral Club) —
карточку с текстом и фоновую картинку. Публикует в Instagram всегда
человек, руками. Кнопки: готово / пропустить / написать по-другому;
команда «фон» — пересобрать картинку дня. Куда присылать —
STORY_CHAT_ID в .env (не задан — идёт в ADMIN_CHAT_ID).

Как работает (всё через файлы, без базы данных):
  drafts/idea.json   — идея поста, ждёт одобрения
                       (кладёт Claude после сбора тем)
  drafts/post.md     — черновик поста, ждёт одобрения
                       (кладёт Claude после идеи)
  drafts/archive/    — сюда уходят обработанные файлы с меткой времени

Запуск:  python3 scripts/bot.py
Бот работает, пока открыто окно (для постоянной работы — запуск в фоне позже).

Кнопки в боте:
  идея:  ✅ Одобрить идею  /  ❌ Отклонить
  пост:  ✅ Опубликовать   /  ✏️ Переписать  /  ❌ Отклонить
"""

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import publish  # noqa: E402  (наш модуль публикации в соцсети)
import storis  # noqa: E402  (сторис-конвейер: план, пакет дня, фон)

DRAFTS = ROOT / "drafts"
ARCHIVE = DRAFTS / "archive"
IDEA = DRAFTS / "idea.json"
POST = DRAFTS / "post.md"
POST_VK = DRAFTS / "post_vk.txt"
ANIMATION = DRAFTS / "animation.gif"
APPROVED = DRAFTS / "approved_idea.json"

API = "https://api.telegram.org/bot{token}/{method}"
OFFSET = 0  # курсор обновлений (long polling)

# .env читаем через publish.load_env — единый читатель проекта
load_env = publish.load_env


def api(token: str, method: str, params: dict, attempts: int = 3) -> dict:
    """Вызов Bot API — одна обёртка над publish._post (логика повторов
    и сертификаты в одном месте, без дублей)."""
    return publish._post(API.format(token=token, method=method), params,
                         attempts=attempts)


def уведом(text: str) -> None:
    """Уведомление macOS на стол владельца: сигнал о событии, требующем
    внимания вне бота (например, «идея одобрена — работай с Claude»).

    Только по-тихому: если уведомления не работают (Linux, отсутствие
    osascript), бот не должен упасть из-за этого."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text}" with title "Контент-завод"'],
            timeout=10, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass  # уведомление — приятный бонус, не критичный путь


def send_photo_tg(token: str, chat_id: str, path: Path,
                  caption: str = "") -> bool:
    """Фото в чат (фон сторис) через multipart — обёртка над publish."""
    try:
        res = publish._upload_multipart(
            API.format(token=token, method="sendPhoto"),
            {"chat_id": chat_id, "caption": caption[:1000]},
            "photo", path.name, path.read_bytes())
        return bool(res.get("ok"))
    except Exception:
        return False


def kb(buttons: list[list[dict]]) -> str:
    """Собрать inline-клавиатуру в JSON для Bot API."""
    return json.dumps({"inline_keyboard": buttons}, ensure_ascii=False)


def archive(path: Path, tag: str) -> None:
    """Убрать файл в архив с датой, чтобы история не терялась."""
    ARCHIVE.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    shutil.move(path, ARCHIVE / f"{tag}-{stamp}{path.suffix}")


def idea_card_text(idea: dict) -> str:
    links = "\n".join(f"• {u}" for u in idea.get("ссылки", [])) or "—"
    return (
        f"💡 <b>Идея поста</b>\n\n"
        f"<b>{idea.get('тема', 'Без названия')}</b>\n\n"
        f"Суть: {idea.get('суть', '—')}\n"
        f"Почему спорно: {idea.get('почему_спорно', '—')}\n"
        f"Оценка: {idea.get('оценка', '—')}\n\n"
        f"Ссылки:\n{links}"
    )


def send_next(token: str, chat_id: str) -> None:
    """Показать, что ждёт решения: сначала идея, потом пост, иначе — тишина."""
    if IDEA.exists():
        idea = json.loads(IDEA.read_text(encoding="utf-8"))
        api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": idea_card_text(idea),
            "parse_mode": "HTML",
            "reply_markup": kb([[
                {"text": "✅ Одобрить идею", "callback_data": "idea_ok"},
                {"text": "❌ Отклонить", "callback_data": "idea_no"},
            ]]),
        })


def send_next_post(token: str, chat_id: str) -> None:
    """Показать черновик поста на одобрение."""
    if POST.exists():
        text = POST.read_text(encoding="utf-8")
        api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": text[:4000],
            "reply_markup": kb([[
                {"text": "✅ Опубликовать", "callback_data": "post_ok"},
                {"text": "✏️ Переписать", "callback_data": "post_retry"},
                {"text": "❌ Отклонить", "callback_data": "post_no"},
            ]]),
        })


def check_story(token: str, chat_id: str) -> None:
    """Статус сторис для /start (пакеты собирает только сессия Claude,
    бот сам ничего не генерирует и не пушит)."""
    state = storis.load_state()
    if state.get("кончился"):
        return
    day = state["день"]
    plan = storis.load_plan()
    if day not in plan:
        state["кончился"] = True
        storis.save_state(state)
        api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": ("🎉 Месяц 1 сторис пройден (последний авторский "
                     "день — 27-й). Дни 28–30 — резерв: доснять хвосты "
                     "и ответить на вопросы, без автопакетов. Для "
                     "следующего месяца попроси Claude: «собери план "
                     "сторис месяца 2»."),
        })
        return
    if not state["отправлено"].get(str(day)):
        api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": (f"📱 Сторис дня {day} ещё не собрана: пакеты "
                     "собирает сессия Claude. Попроси его: «собери "
                     "следующую сторис» — пакет упадёт сюда."),
        })


def handle_story_button(token: str, chat_id: str, data: str) -> str:
    """Кнопки пакета сторис: только отметки и движение дня. Пакет
    следующего дня собирает сессия Claude (storis_send.py) — бот
    сам ничего не генерирует."""
    state = storis.load_state()
    day = state["день"]
    if data in ("story_ok", "story_skip"):
        запись = state["отправлено"].get(str(day))
        if запись is None:
            return f"Пакет дня {day} ещё не приходил."
        запись["опубликован"] = (data == "story_ok")
        fon = storis.fon_path(day)
        if fon.exists():
            tag = ("storis-фон-опубликован" if data == "story_ok"
                   else "storis-фон-пропущен")
            archive(fon, tag)
        следующий = day + 1
        state["день"] = следующий
        storis.save_state(state)
        план = storis.load_plan()
        if data == "story_ok":
            итог = f"✅ День {day} отмечен опубликованным."
        else:
            итог = (f"➡️ День {day} пропущен (тема не потерялась: "
                    f"вернуть — «сторис дня {day}»).")
        if следующий in план:
            итог += (f"\nДень {следующий} соберёт сессия Claude: "
                     "попроси «собери следующую сторис».")
        return итог
    if data == "story_again":
        return ("✏️ Принял. Попроси Claude: «переделай сторис дня "
                f"{day}» — он перепишет текст и пришлёт пакет заново.")
    return "Неизвестная кнопка."


def handle_callback(token: str, chat_id: str, data: str) -> str:
    """Реакция на кнопку. Отчёт — короткой строкой для ответа в чат."""

    if data.startswith("story"):
        return handle_story_button(token, chat_id, data)

    if data.startswith("idea"):
        if not IDEA.exists():
            return "Идея уже обработана."
        if data == "idea_ok":
            shutil.move(IDEA, APPROVED)  # Claude увидит и напишет пост
            уведом("Идея одобрена — открой Claude: он начнёт писать пост.")
            return "✅ Идея одобрена. Пишу пост — пришлю черновик."
        archive(IDEA, "idea-отклонена")
        return "❌ Идея отклонена. Предложу другую."

    if data.startswith("post"):
        if not POST.exists():
            return "Черновик уже обработан."
        text = POST.read_text(encoding="utf-8")
        # ВК-версия — отдельный файл; если её нет,
        # publish сам вычистит разметку
        text_vk = POST_VK.read_text(encoding="utf-8") \
            if POST_VK.exists() else None
        if data == "post_ok":
            anim = ANIMATION if ANIMATION.exists() else None
            reports = publish.publish(text, text_vk, animation=anim)
            archive(POST, "post-опубликован")
            if POST_VK.exists():
                archive(POST_VK, "post-vk-опубликован")
            if anim is not None:
                archive(anim, "post-анимация")
            return "🚀 Публикация:\n" + "\n".join(reports)
        if data == "post_retry":
            archive(POST, "post-на-переписывание")
            if POST_VK.exists():
                archive(POST_VK, "post-vk-на-переписывание")
            return "✏️ Понял, черновик на переписывание."
        archive(POST, "post-отклонён")
        if POST_VK.exists():
            archive(POST_VK, "post-vk-отклонён")
        return "❌ Черновик отклонён."
    return "Неизвестная кнопка."


def main() -> int:
    env = load_env()
    token = env.get("BOT_TOKEN", "")
    chat_id = env.get("ADMIN_CHAT_ID", "")  # куда присылать идеи/черновики
    if not token or token.startswith("УКАЖИ"):
        print("Нет BOT_TOKEN в .env — см. README, раздел «Настройка».")
        return 1
    if not chat_id or chat_id.startswith("УКАЖИ"):
        print("Нет ADMIN_CHAT_ID в .env — id чата, куда бот присылает идеи.")
        return 1

    offset = 0
    story_chat = env.get("STORY_CHAT_ID", "") or chat_id
    admin_chat = chat_id
    print("Бот запущен. Жду команды /start — пришлю, что требует решения.")
    while True:
        res = api(token, "getUpdates",
                  {"offset": offset, "timeout": 30, "limit": 5}, attempts=1)
        if not res.get("ok"):
            print(f"Сеть моргнула, продолжаю: {res.get('error')}")
            time.sleep(5)
            continue
        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            cb = upd.get("callback_query")

            if cb:
                chat = str(cb["message"]["chat"]["id"])
                report = handle_callback(token, chat, cb.get("data", ""))
                api(token, "sendMessage", {"chat_id": chat, "text": report})
            elif msg.get("text", ""):
                chat = str(msg["chat"]["id"])
                text = msg["text"].strip()
                if text.startswith("/start"):
                    if chat in (admin_chat, story_chat):
                        api(token, "sendMessage", {
                            "chat_id": chat,
                            "text": "🏭 Контент-завод на связи.",
                        })
                        send_next(token, chat)
                        send_next_post(token, chat)
                        check_story(token, story_chat)
                        after = storis.load_state()
                        entry = storis.load_plan().get(after["день"])
                        sent = after["отправлено"].get(str(after["день"]))
                        if (not IDEA.exists() and not POST.exists()
                                and (entry is None or sent and
                                     sent.get("версия") == entry["версия"])):
                            api(token, "sendMessage", {
                                "chat_id": chat,
                                "text": ("Очередь пуста — новых идей и "
                                         "черновиков нет."),
                            })
                    else:  # незнакомый чат: даём свой id, чтобы записали
                        api(token, "sendMessage", {
                            "chat_id": chat,
                            "text": (f"Это бот контент-завода. Твой chat "
                                     f"id: {chat}. Если ты получатель "
                                     "пакетов сторис — покажи id Михаилу, "
                                     "он запишет его в .env "
                                     "(STORY_CHAT_ID), и бот присылать "
                                     "пакеты будет тебе."),
                        })
                elif text.lower() == "фон" and chat in (
                        admin_chat, story_chat):
                    # пересобрать фон текущего дня по требованию
                    state = storis.load_state()
                    day = state["день"]
                    path, err = storis.регенерировать_фон(day)
                    if path is not None and path.exists():
                        ok = send_photo_tg(
                            token, chat, path, f"🖼 Фон дня {day} — заново")
                        report = ("✅ Фон готов" if ok
                                  else "⚠️ Фото не ушло, попробуй ещё раз")
                    else:
                        report = f"⚠️ Фон не получился: {err}"
                    api(token, "sendMessage", {"chat_id": chat,
                                               "text": report})
                elif text.lower().startswith("сторис") and chat in (
                        admin_chat, story_chat):
                    # «сторис дня 5» / «сторис 5» — пакет по номеру дня:
                    # текущего (с кнопками) или задним числом (без кнопок)
                    число = "".join(t for t in text if t.isdigit())
                    if число and int(число) in storis.load_plan():
                        day = int(число)
                        state = storis.load_state()
                        ok, отчёт = storis.отправить_пакет(
                            token, chat, day,
                            без_кнопок=day != state["день"])
                        print(("" if ok else "! ") + отчёт)
                    else:
                        api(token, "sendMessage", {
                            "chat_id": chat,
                            "text": "Дай номер дня плана, например: "
                                    "«сторис дня 5».",
                        })
                elif text.startswith("/"):
                    pass  # прочие команды пока не обрабатываются


if __name__ == "__main__":
    sys.exit(main())

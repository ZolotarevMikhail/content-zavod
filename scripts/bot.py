#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Этапы 2–3 «Контент-завода»: одобрение идей и постов в Telegram-боте + публикация.

Как работает (всё через файлы, без базы данных):
  drafts/idea.json   — идея поста, ждёт одобрения (кладёт Claude после сбора тем)
  drafts/post.md     — черновик поста, ждёт одобрения (кладёт Claude после идеи)
  drafts/archive/    — сюда уходят обработанные файлы с меткой времени

Запуск:  python3 scripts/bot.py
Бот работает, пока открыто окно (для постоянной работы — запуск в фоне позже).

Кнопки в боте:
  идея:  ✅ Одобрить идею  /  ❌ Отклонить
  пост:  ✅ Опубликовать   /  ✏️ Переписать  /  ❌ Отклонить
"""

import json
import shutil
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import publish  # noqa: E402  (наш модуль публикации в соцсети)

DRAFTS = ROOT / "drafts"
ARCHIVE = DRAFTS / "archive"
IDEA = DRAFTS / "idea.json"
POST = DRAFTS / "post.md"
POST_VK = DRAFTS / "post_vk.md"
APPROVED = DRAFTS / "approved_idea.json"

API = "https://api.telegram.org/bot{token}/{method}"
OFFSET = 0  # курсор обновлений (long polling)


def load_env() -> dict:
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def api(token: str, method: str, params: dict, attempts: int = 3) -> dict:
    """Вызов Bot API. Пауза между попытками растёт; при неудаче — честная ошибка."""
    data = urllib.parse.urlencode(params).encode()
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(
                API.format(token=token, method=method), data=data)
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last = str(e)
            time.sleep(2 * attempt)
    return {"ok": False, "description": last}


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


def handle_callback(token: str, chat_id: str, data: str) -> str:
    """Реакция на кнопку. Отчёт — короткой строкой для ответа в чат."""
    now = datetime.now().strftime("%d.%m %H:%M")

    if data.startswith("idea"):
        if not IDEA.exists():
            return "Идея уже обработана."
        if data == "idea_ok":
            shutil.move(IDEA, APPROVED)  # Claude увидит и напишет пост
            return "✅ Идея одобрена. Пишу пост — пришлю черновик."
        archive(IDEA, "idea-отклонена")
        return "❌ Идея отклонена. Предложу другую."

    if data.startswith("post"):
        if not POST.exists():
            return "Черновик уже обработан."
        text = POST.read_text(encoding="utf-8")
        # ВК-версия — отдельный файл; если её нет, publish сам вычистит разметку
        text_vk = POST_VK.read_text(encoding="utf-8") if POST_VK.exists() else None
        if data == "post_ok":
            reports = publish.publish(text, text_vk)
            archive(POST, "post-опубликован")
            if POST_VK.exists():
                archive(POST_VK, "post-vk-опубликован")
            return f"🚀 Публикация:\n" + "\n".join(reports)
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
    print("Бот запущен. Жду команды /start — пришлю, что требует решения.")
    while True:
        res = api(token, "getUpdates",
                  {"offset": offset, "timeout": 30, "limit": 5}, attempts=1)
        if not res.get("ok"):
            print(f"Сеть моргнула, продолжаю: {res.get('description')}")
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
            elif msg.get("text", "").startswith("/start"):
                chat = str(msg["chat"]["id"])
                api(token, "sendMessage", {
                    "chat_id": chat,
                    "text": "🏭 Контент-завод на связи.",
                })
                send_next(token, chat)
                send_next_post(token, chat)
                if not IDEA.exists() and not POST.exists():
                    api(token, "sendMessage", {
                        "chat_id": chat,
                        "text": "Очередь пуста — новых идей и черновиков нет.",
                    })


if __name__ == "__main__":
    sys.exit(main())
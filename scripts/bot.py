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
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import publish  # noqa: E402  (наш модуль публикации в соцсети)
import sbor  # noqa: E402  (сборщик тем: блокировка архива, load_archive)
import storis  # noqa: E402  (сторис-конвейер: план, пакет дня, фон)
import rost  # noqa: E402  (модуль «рост»: просмотры и подписчики канала)

DRAFTS = ROOT / "drafts"
ARCHIVE = DRAFTS / "archive"
IDEA = DRAFTS / "idea.json"
POST = DRAFTS / "post.md"
POST_VK = DRAFTS / "post_vk.txt"
ANIMATION = DRAFTS / "animation.gif"
APPROVED = DRAFTS / "approved_idea.json"
АРХИВ_ТЕМ = ROOT / "архив-тем.json"
ВЫБОР_ТЕМ = DRAFTS / "темы-на-выбор.json"

API = publish.API_TG  # шаблон Bot API — из publish (единая точка)

# Стража глухоты (26.10): туннель VPN периодически «умирает наполовину» —
# процессы живы, а TLS-соединения зависают, и бот молчит, пока его не
# перезапустить руками. Если успешного getUpdates нет дольше 30 минут —
# бот сам себя перезапускает: свежие соединения вместо зависших.
DEAF_LIMIT_SEC = 30 * 60  # сколько секунд молчания = перезапуск

last_ok = time.time()  # момент последнего успешного getUpdates


def _restart_self() -> None:
    """Перезапуск своего процесса с сохранением лога (fd наследуется)."""
    print("Бот глух дольше 30 минут — перезапускаю себя.", flush=True)
    os.execv(  # noqa: S606 — запускаем сами себя, путь известен
        sys.executable, [sys.executable, str(Path(__file__).resolve())]
    )


def _watchdog() -> None:
    """Фоновая стража: раз в минуту проверяет свежесть связи с Telegram."""
    while True:
        time.sleep(60)
        if time.time() - last_ok > DEAF_LIMIT_SEC:
            _restart_self()


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
        безопасный = (text.replace("\\", "\\\\").replace('"', '\\"'))
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{безопасный}" with title '
             f'"Контент-завод"'],
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
    if not path.exists():
        return  # досылающий клик может найти файл уже без нужной части
    ARCHIVE.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    целевой = ARCHIVE / f"{tag}-{stamp}{path.suffix}"
    # два файла в одну минуту — имена не должны сталкиваться: shutil.move
    # молча перезаписал бы старый архивный файл (ревью 30.10.2026)
    счёт = 1
    while целевой.exists():
        целевой = ARCHIVE / f"{tag}-{stamp}-{счёт}{path.suffix}"
        счёт += 1
    shutil.move(path, целевой)


def запуск_черновика() -> None:
    """Фоновый запуск авто-черновика: бот не ждёт, отчёт о готовности
    уходит в Telegram из самого скрипта. Никакого караула: запуск
    происходит только по нажатию кнопки «Одобрить» (или «Переписать»)."""
    лог = open(ROOT / "черновик-авто.log", "a", encoding="utf-8")
    try:
        subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "авто-черновик.py")],
            stdout=лог, stderr=лог, start_new_session=True,
        )
    finally:
        лог.close()  # дескриптор нужен только дочернему процессу


# Автоподбор по рубрике (грубый, финальный выбор остаётся за человеком):
# спорность весом вдвое, как в config/rubrica.md.
СПОРНО = ("запрещ", "запрет", "спор", "риск", "штраф", "суд", "обман",
          "заменит", "замена", "отберут", "стоит ли", "нужно ли",
          "польза", "вред", "блокир", "налог", "banned", "risk",
          "court", "criticiz", "warn")
ЭМОЦИЯ = ("впервые", "рекорд", "млрд", "миллион", "шок", "прорыв",
          "невозможн", "неожиданн", "страх", "first", "record", "warn")
ПОПУЛ = ("как работает", "что такое", "простыми", "объясн", "разбор",
         "как понять", "study")


def оценка_темы(text: str) -> int:
    """Грубая оценка по рубрике: спорность×2 + эмоция + популяризация."""
    спорно = min(2, sum(1 for к in СПОРНО if к in text))
    эмоция = min(2, sum(1 for к in ЭМОЦИЯ if к in text))
    понятно = min(2, sum(1 for к in ПОПУЛ if к in text))
    return 2 * спорно + эмоция + понятно


def темы_для_выбора(n: int = 3) -> list[dict]:
    """Лучшие темы со статусом «в архиве», отсортированные по рубрике."""
    if not АРХИВ_ТЕМ.exists():
        return []
    with sbor.блокировка_архива():
        темы = sbor.load_archive()
    свободные = [r for r in темы if r.get("статус") == "в архиве"]
    for r in свободные:
        r["оценка"] = оценка_темы(
            (r["title"] + " " + r["summary"]).lower())
    свободные.sort(
        key=lambda r: (r["оценка"], r["дата_добавления"]), reverse=True)
    return свободные[:n]


def показать_темы(token: str, chat: str) -> bool:
    """Вторник и пятница, когда конвейер пуст: предложить три темы
    из архива прямо в боте. True — показ состоялся."""
    if datetime.now().weekday() not in (1, 4):  # вторник / пятница
        return False
    if IDEA.exists() or POST.exists() or APPROVED.exists():
        return False
    if ВЫБОР_ТЕМ.exists():
        # список уже ждёт выбора в чате — перезапись его меняла номера
        # под кнопками старого сообщения: «Беру 2» брало тему нового
        # списка (ревью 30.10.2026)
        return False
    темы = темы_для_выбора()
    if not темы:
        return False
    ВЫБОР_ТЕМ.write_text(
        json.dumps(темы, ensure_ascii=False), encoding="utf-8")
    строки = ["🧭 Темы на выбор (автоподбор по рубрике):"]
    for i, t in enumerate(темы, 1):
        строки.append(f"{i}. {t['title'][:120]}")
        if t.get("суть"):
            строки.append(t["суть"][:600])
        if t.get("почему_спорно"):
            строки.append("Чем спорно: " + t["почему_спорно"][:300])
        строки.append("")
    api(token, "sendMessage", {
        "chat_id": chat,
        "text": "\n".join(строки),
        "reply_markup": kb([[
            {"text": f"Беру {i}", "callback_data": f"тема_{i - 1}"}
            for i in range(1, len(темы) + 1)
        ]]),
    })
    return True


def выбрать_тему(token: str, chat: str, data: str) -> str:
    """Кнопка «Беру N»: тема из списка становится карточкой идеи."""
    if not ВЫБОР_ТЕМ.exists():
        return "Список тем не найден — нажми /start заново."
    темы = json.loads(ВЫБОР_ТЕМ.read_text(encoding="utf-8"))
    idx = int(data.split("_")[1])
    if idx >= len(темы):
        return "Такой темы больше нет — нажми /start."
    row = темы[idx]
    идея = {
        "тема": row["title"],
        "суть": row.get("суть") or row["summary"][:300],
        "почему_спорно": row.get("почему_спорно",
                                 "автоподбор: рамку спора задаст черновик"),
        "ссылки": [row["url"]],
        "оценка": f"авто-оценка {row.get('оценка', '?')} из 6",
    }
    IDEA.write_text(
        json.dumps(идея, ensure_ascii=False, indent=2), encoding="utf-8")
    if АРХИВ_ТЕМ.exists():
        # под блокировкой (сборщик перезаписывает архив целиком каждые
        # 15 минут — правка статуса затиралась), запись атомарно
        with sbor.блокировка_архива():
            архив = sbor.load_archive()
            for r in архив:
                if r["url"] == row["url"]:
                    r["статус"] = ("взята в работу (выбор из бота "
                                   + datetime.now().strftime("%d.%m.%Y")
                                   + ")")
            sbor._записать_архив(архив)
    ВЫБОР_ТЕМ.unlink(missing_ok=True)
    send_next(token, chat)
    return "Карточка идеи выше — одобри кнопкой."


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
            # 4096 — лимит Telegram; обрезку обозначаем, не молчим
            "text": (text if len(text) <= 4000 else text[:3995] + "\n…"),
            # превью-карточку ТГ не строим: она рисовала картинку
            # прямо под ссылкой «Источник: …» (правило Михаила)
            "disable_web_page_preview": "true",
            "reply_markup": kb([[
                {"text": "✅ Опубликовать", "callback_data": "post_ok"},
                {"text": "✏️ Переписать", "callback_data": "post_retry"},
                {"text": "❌ Отклонить", "callback_data": "post_no"},
            ]]),
        })


def авто_сторис(token: str, chat_id: str) -> bool:
    """Автопакет на /start (решение Михаила 15.10.2026): если текущий
    день ещё не отправлялся — бот сам собирает пакет (карточка + фон)
    и присылает. Уже отправленный день не дублируем: он ждёт кнопки.
    Возвращает True, если пакет только что ушёл в чат."""
    state = storis.load_state()
    if state.get("кончился"):
        return False
    day = state["день"]
    план = storis.load_plan()
    if day not in план:
        state["кончился"] = True
        storis.save_state(state)
        месяц = state.get("месяц", 1)
        последний = storis.max_plan_day()
        api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": (f"🎉 Месяц {месяц} сторис пройден (последний "
                     f"авторский день — {последний}-й). Дни "
                     f"{последний + 1}–{последний + 3} — резерв: доснять "
                     "хвосты и ответить на вопросы, без автопакетов. "
                     "Для следующего месяца попроси Claude: «собери "
                     f"план сторис месяца {месяц + 1}»."),
        })
        return False
    entry = план[day]
    sent = state["отправлено"].get(str(day))
    if sent and sent.get("версия") == entry["версия"]:
        return False  # день уже в чате, ждёт публикации
    ok, отчёт = storis.отправить_пакет(token, chat_id, day)
    if not ok:
        api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": (f"⚠️ Пакет дня {day} не собрался, напиши «фон» — "
                     f"попробую снова. Причина: {отчёт}"),
        })
    return ok


def handle_story_button(token: str, chat_id: str, data: str) -> str:
    """Кнопки пакета сторис: отметка «опубликовал/пропустил» двигает день.
    Следующий день бот отправляет сам (решение Михаила 15.10.2026);
    тексты дней правит Claude в storis_plan.py.
    День кнопки зашит в callback_data (новые пакеты): клик по старой
    карточке не двигает уже продвинутый день (ревью 30.10.2026)."""
    день_кнопки = None
    действие = data
    for префикс in ("story_ok_", "story_skip_", "story_again_"):
        if data.startswith(префикс):
            действие = префикс[:-1]
            try:
                день_кнопки = int(data[len(префикс):])
            except ValueError:
                день_кнопки = None
            break
    state = storis.load_state()
    day = state["день"]
    if день_кнопки is not None and день_кнопки != day:
        return (f"Это кнопка под старой карточкой (день {день_кнопки}), "
                f"а сейчас в работе день {day}. Ничего не двигаю.")
    if day not in storis.load_plan():
        # под старыми карточками кнопки не привязаны к дню — бот считает
        # нажатие обращением к «текущему дню», а его в плане уже нет
        месяц = state.get("месяц", 1)
        return (f"Месяц {месяц} сторис уже закончился — это кнопка "
                "под старым днём. Новые пакеты приходят на /start.")
    if действие in ("story_ok", "story_skip"):
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
            ok, отчёт = storis.отправить_пакет(token, chat_id, следующий)
            if ok:
                итог += f"\n📱 День {следующий} пришёл следом: {отчёт}"
            else:
                итог += (f"\n⚠️ Пакет дня {следующий} не собрался: "
                         f"{отчёт}. Вернётся при следующем /start.")
        return итог
    if действие == "story_again":
        return ("✏️ Принял. Попроси Claude: «переделай сторис дня "
                f"{day}» — он перепишет текст и пришлёт пакет заново.")
    return "Неизвестная кнопка."


def handle_callback(token: str, chat_id: str, data: str) -> str:
    """Реакция на кнопку. Отчёт — короткой строкой для ответа в чат."""

    if data.startswith("story"):
        return handle_story_button(token, chat_id, data)

    if data.startswith("тема_"):
        return выбрать_тему(token, chat_id, data)

    if data.startswith("idea"):
        if not IDEA.exists():
            return "Идея уже обработана."
        if data == "idea_ok":
            # одобрение идеи запускает авто-черновик, который пишет post.md
            # поверх: если черновик поста ещё ждёт решения — не даём
            # перезаписать (ревью 30.10.2026)
            if POST.exists() or (DRAFTS / "публикация-статус.json").exists():
                return ("⚠️ Сначала реши черновик поста выше "
                        "(опубликовать / переписать / отклонить) — "
                        "иначе новая идея затрёт его.")
            shutil.move(IDEA, APPROVED)  # Claude увидит и напишет пост
            уведом("Идея одобрена — черновик создаётся сам.")
            запуск_черновика()
            return ("✅ Идея одобрена. Черновик готовлю сейчас сам — "
                    "пришлю сообщение в Telegram, когда будет готов.")
        archive(IDEA, "idea-отклонена")
        return "❌ Идея отклонена. Предложу другую."

    if data.startswith("post"):
        # статус этой публикации: что уже доставлено в прошлые клики
        status = DRAFTS / "публикация-статус.json"
        prior = {}
        if status.exists():
            try:
                prior = json.loads(status.read_text(encoding="utf-8"))
            except Exception:
                prior = {}  # битый статус — считаем, что ничего не ушло
        if not POST.exists() and not prior:
            return "Черновик уже обработан."
        if data == "post_ok":
            text = POST.read_text(encoding="utf-8") if POST.exists() else None
            # ВК-версия — отдельный файл; если её нет,
            # publish сам вычистит разметку
            text_vk = POST_VK.read_text(encoding="utf-8") \
                if POST_VK.exists() else None
            anim = ANIMATION if ANIMATION.exists() else None
            state: dict = {}
            reports = publish.publish(
                text, text_vk, animation=anim, state=state,
                only_vk=bool(prior.get("tg")),
                only_tg=bool(prior.get("vk")) and not prior.get("tg"))
            tg_needed = prior.get("tg_needed", "tg_text" in state)
            vk_needed = prior.get("vk_needed", "vk" in state)
            tg_done = prior.get("tg", False) or state.get("tg_text", False)
            vk_done = prior.get("vk", False) or state.get("vk", False)
            status.write_text(json.dumps(
                {"tg": tg_done, "vk": vk_done,
                 "tg_needed": tg_needed, "vk_needed": vk_needed},
                ensure_ascii=False), encoding="utf-8")
            # архивируем только то, что реально ушло, — повтор не задвоит
            if tg_done:
                archive(POST, "post-опубликован")
            if anim is not None and (state.get("tg_anim", False)
                                     or state.get("tg_text", False)):
                # анимация ушла (или ТГ-текст закрыл вопрос) — из drafts долой
                archive(anim, "post-анимация")
            if (vk_done or not vk_needed) and POST_VK.exists():
                archive(POST_VK, "post-vk-опубликован")
            if vk_needed and not vk_done and not POST_VK.exists() \
                    and text is not None:
                # ВК не принял, отдельного ВК-текста нет — сохраняем
                # вычищенную версию, чтобы досылающим кликом уйти в ВК
                POST_VK.write_text(publish.vk_sanitize(text),
                                   encoding="utf-8")
            if (tg_done or not tg_needed) and (vk_done or not vk_needed):
                if APPROVED.exists():
                    archive(APPROVED, "idea-использована")
                status.unlink()
                return "🚀 Публикация:\n" + "\n".join(reports)
            return ("⚠️ публикации не всё приняли:\n" + "\n".join(reports)
                    + "\nКогда сеть заработает, нажми «Опубликовал» ещё раз "
                      "— заново уйдёт только то, чего не хватает.")
        if not POST.exists():
            return "Черновик уже обработан."
        if data == "post_retry":
            archive(POST, "post-на-переписывание")
            if POST_VK.exists():
                archive(POST_VK, "post-vk-на-переписывание")
            if ANIMATION.exists():
                archive(ANIMATION, "post-анимация-на-переписывание")
            (DRAFTS / "публикация-статус.json").unlink(missing_ok=True)
            запуск_черновика()
            return "✏️ Понял, черновик на переписывание. Прислаю новый."
        archive(POST, "post-отклонён")
        if POST_VK.exists():
            archive(POST_VK, "post-vk-отклонён")
        (DRAFTS / "публикация-статус.json").unlink(missing_ok=True)
        return "❌ Черновик отклонён."
    return "Неизвестная кнопка."


def main() -> int:
    global last_ok  # стража в соседнем потоке читает этот маркер
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
    threading.Thread(target=_watchdog, daemon=True).start()
    while True:
        # poll-таймаут (15 с) должен быть МЕНЬШЕ http-таймаута _post (20 с):
        # иначе пустой long poll рвётся по read timeout каждые 20 с,
        # бот спит лишние 5 с и лог забивается ложными «сеть моргнула».
        res = api(token, "getUpdates",
                  {"offset": offset, "timeout": 15, "limit": 5}, attempts=1)
        if not res.get("ok"):
            print(f"Сеть моргнула, продолжаю: {res.get('error')}")
            time.sleep(5)
            continue
        last_ok = time.time()  # связь есть — стражу глухоты сдерживаем
        try:
            for upd in res.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                cb = upd.get("callback_query")

                if cb:
                    chat = str(cb["message"]["chat"]["id"])
                    if chat not in (admin_chat, story_chat):
                        # чужая нажатая кнопка (кнопки раньше не проверяли
                        # чат — несимметрично с командами)
                        api(token, "answerCallbackQuery", {
                            "callback_query_id": cb.get("id", ""),
                        })
                        continue
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
                            story_sent = авто_сторис(token, story_chat)
                            after = storis.load_state()
                            entry = storis.load_plan().get(after["день"])
                            sent = after["отправлено"].get(str(after["день"]))
                            темы_показаны = показать_темы(token, chat)
                            if (not story_sent and not темы_показаны
                                    and not IDEA.exists()
                                    and not POST.exists()
                                    and (entry is None or sent and
                                         sent.get("версия") == entry["версия"])):
                                if APPROVED.exists():
                                    api(token, "sendMessage", {
                                        "chat_id": chat,
                                        "text": ("💡 Идея одобрена, черновик "
                                                 "пишет Claude — пост придёт "
                                                 "сюда кнопками, когда будет "
                                                 "готов."),
                                    })
                                else:
                                    api(token, "sendMessage", {
                                        "chat_id": chat,
                                        "text": ("Очередь пуста. Новая идея "
                                                 "появится после отбора тем "
                                                 "(вторник и пятница) — сборщик "
                                                 "каждый день пополняет архив "
                                                 "тем."),
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
                    elif text.lower() == "рост" and chat in (
                            admin_chat, story_chat):
                        # модуль «рост»: снимок просмотров/подписчиков + отчёт.
                        # Снимка не было за сегодня или он неполный — делаем;
                        # отчёт присылаем всегда (по команде спама не бывает).
                        история = rost.обновить(token)  # снимок + дозаполнение
                        api(token, "sendMessage", {
                            "chat_id": chat,
                            "text": rost.отчёт_текст(история),
                            "parse_mode": "HTML",
                            "disable_web_page_preview": "true",
                        })
                    elif text.startswith("/"):
                        pass  # прочие команды пока не обрабатываются
        except Exception:
            traceback.print_exc()  # битый файл или сбой не убивают бот
            continue


if __name__ == "__main__":
    sys.exit(main())

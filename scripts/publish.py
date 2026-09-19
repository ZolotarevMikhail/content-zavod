#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Публикация поста в выбранные соцсети (Telegram-канал и ВК).

Токены берутся из файла .env в корне проекта (в гит не попадает):
  BOT_TOKEN=...    — токен бота от @BotFather (бот — админ канала)
  ADMIN_CHAT_ID=.. — id чата, куда бот присылает идеи и черновики

Куда публикуем — в config.json, раздел «соцсети»:
  telegram.канал_id   — @имя_канала или числовой id канала
  vk.владелец_id       — id личной страницы (положительный) или группы
                         (отрицательный, с минусом); знак выбирает режим сам

Используется ботом (bot.py) на этапе 3 — после одобрения поста человеком.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict:
    """Прочитать .env в словарь (без библиотек, формат KEY=значение)."""
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _post(url: str, params: dict, attempts: int = 3) -> dict:
    """POST с автоповтором (ограниченным) и понятной ошибкой вместо падения."""
    data = urllib.parse.urlencode(params).encode()
    last_err = ""
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": "content-zavod/1.0",
                "Content-Type": "application/x-www-form-urlencoded",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last_err = str(e)
            time.sleep(2 * attempt)  # пауза растёт: 2с, 4с — и хватит
    return {"ok": False, "error": last_err}


def post_telegram(chat_id: str, text: str) -> dict:
    """Пост в Telegram-канал через Bot API."""
    token = load_env().get("BOT_TOKEN")
    if not token or token.startswith("УКАЖИ"):
        return {"ok": False, "error": "нет BOT_TOKEN в .env"}
    res = _post(f"https://api.telegram.org/bot{token}/sendMessage", {
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    })
    if res.get("ok"):
        return {"ok": True}
    return {"ok": False, "error": f"Telegram: {res.get('error') or res}"}


def vk_sanitize(text: str) -> str:
    """Убрать HTML-разметку для ВК: теги в никуда, абзацы сохранить."""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)  # теги Telegram-разметки ВК не понимает
    return text.strip()


def post_vk(owner_id: str, text: str) -> dict:
    """Пост на стену ВК через wall.post.

    owner_id положительный — личная страница (from_group=0),
    отрицательный — группа (from_group=1). Знак решает всё."""
    token = load_env().get("VK_TOKEN")
    if not token or token.startswith("УКАЖИ"):
        return {"ok": False, "error": "нет VK_TOKEN в .env"}
    from_group = 1 if owner_id.strip().startswith("-") else 0
    res = _post("https://api.vk.com/method/wall.post", {
        "access_token": token,
        "v": "5.199",
        "owner_id": owner_id,
        "from_group": from_group,
        "message": text,
    })
    if res.get("response"):
        return {"ok": True}
    return {"ok": False, "error": f"VK: {res.get('error') or res}"}


def publish(text_tg: str, text_vk: str | None = None) -> list[str]:
    """Публикует во все соцсети, включённые в config.json. Возвращает отчёт.

    В ВК уходит text_vk (адаптированная версия); если её нет —
    telegram-текст с вычищенной HTML-разметкой (вместо падения)."""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    reports = []
    soc = cfg.get("соцсети", {})

    tg = soc.get("telegram", {})
    if tg.get("вкл"):
        chat_id = tg.get("канал_id", "")
        if chat_id.startswith("УКАЖИ"):
            reports.append("Telegram: не указан канал_id в config.json")
        else:
            r = post_telegram(chat_id, text_tg)
            reports.append("Telegram: опубликовано" if r["ok"]
                           else f"Telegram: НЕ вышло — {r['error']}")

    vk = soc.get("vk", {})
    if vk.get("вкл"):
        owner_id = vk.get("владелец_id") or vk.get("группа_id") or ""
        if owner_id.startswith("УКАЖИ") or not owner_id:
            reports.append("VK: не указан владелец_id в config.json")
        else:
            r = post_vk(owner_id, text_vk or vk_sanitize(text_tg))
            reports.append("VK: опубликовано" if r["ok"]
                           else f"VK: НЕ вышло — {r['error']}")
    return reports
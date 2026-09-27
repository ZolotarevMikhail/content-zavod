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
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict:
    """Прочитать .env в словарь (без библиотек, формат KEY=значение).
    Локальный content-zavod/.env приоритетнее, корневой — запасной источник."""
    import os
    env = {}
    for f in (ROOT / ".env", Path(os.environ.get("HOME", "")) / "claudecode" / ".env"):
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
    return env


def _post(url: str, params: dict, attempts: int = 3) -> dict:
    """POST с автоповтором (ограниченным) и понятной ошибкой вместо падения."""
    try:  # сертификаты для urllib (у Питона с python.org их нет из коробки)
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = None
    data = urllib.parse.urlencode(params).encode()
    last_err = ""
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, data=data, headers={
                "User-Agent": "content-zavod/1.0",
                "Content-Type": "application/x-www-form-urlencoded",
            })
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last_err = str(e)
            time.sleep(2 * attempt)  # пауза растёт: 2с, 4с — и хватит
    return {"ok": False, "error": last_err}


def _upload_multipart(url: str, fields: dict, file_field: str,
                      filename: str, content: bytes) -> dict:
    """POST файла (multipart/form-data) без сторонних библиотек."""
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = None
    boundary = "czBoundary7f3a9c1e5b2d"
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f"name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                 f"name=\"{file_field}\"; filename=\"{filename}\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n".encode())
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": "content-zavod/1.0",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
        return json.loads(resp.read().decode())


def send_animation_tg(chat_id: str, path: Path, caption: str = "") -> dict:
    """GIF-анимация в Telegram-канал (sendAnimation)."""
    token = load_env().get("BOT_TOKEN")
    if not token or token.startswith("УКАЖИ"):
        return {"ok": False, "error": "нет BOT_TOKEN в .env"}
    try:
        res = _upload_multipart(
            f"https://api.telegram.org/bot{token}/sendAnimation",
            {"chat_id": chat_id, "caption": caption[:1000]},
            "animation", path.name, path.read_bytes(),
        )
        if res.get("ok"):
            return {"ok": True}
        return {"ok": False, "error": f"Telegram: {res.get('description') or res}"}
    except Exception as e:
        return {"ok": False, "error": f"Telegram: {e}"}


def _vk_upload_gif(token: str, owner_id: str, path: Path) -> str | None:
    """Загрузить GIF в ВК как документ. Возвращает 'doc{id}_{id}' или None."""
    try:
        r = _post("https://api.vk.com/method/docs.getWallUploadServer", {
            "access_token": token, "v": "5.199", "type": "doc",
            "group_id": owner_id.lstrip("-"),
        })
        url = (r.get("response") or {}).get("upload_url")
        if not url:
            return None
        up = _upload_multipart(url, {}, "file", path.name, path.read_bytes())
        r = _post("https://api.vk.com/method/docs.save", {
            "access_token": token, "v": "5.199",
            "file": up.get("file", ""), "title": "post.gif", "tags": "content_zavod",
        })
        resp = r.get("response")
        doc = resp.get("doc") if isinstance(resp, dict) else None
        if doc and doc.get("owner_id") and doc.get("id"):
            return f"doc{doc['owner_id']}_{doc['id']}"
        return None
    except Exception:
        return None


def post_vk(owner_id: str, text: str, gif_path: Path | None = None) -> dict:
    """Пост на стену ВК через wall.post, с GIF-вложением если получилось.

    owner_id положительный — личная страница (from_group=0),
    отрицательный — группа (from_group=1). Знак решает всё."""
    token = load_env().get("VK_TOKEN")
    if not token or token.startswith("УКАЖИ"):
        return {"ok": False, "error": "нет VK_TOKEN в .env"}
    from_group = 1 if owner_id.strip().startswith("-") else 0
    params = {
        "access_token": token,
        "v": "5.199",
        "owner_id": owner_id,
        "from_group": from_group,
        "message": text,
    }
    attachment = None
    if gif_path is not None and gif_path.exists():
        attachment = _vk_upload_gif(token, owner_id, gif_path)
    if attachment:
        params["attachments"] = attachment
    res = _post("https://api.vk.com/method/wall.post", params)
    if res.get("response"):
        return {"ok": True, "media": bool(attachment)}
    # вложение могло не пройти — пробуем чистый текст, это лучше тишины
    if attachment:
        params.pop("attachments", None)
        res = _post("https://api.vk.com/method/wall.post", params)
        if res.get("response"):
            return {"ok": True, "media": False}
    return {"ok": False, "error": f"VK: {res.get('error') or res}"}


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


def publish(text_tg: str, text_vk: str | None = None,
            animation: Path | None = None) -> list[str]:
    """Публикует во все соцсети, включённые в config.json. Возвращает отчёт.

    В ВК уходит text_vk (адаптированная версия); если её нет —
    telegram-текст с вычищенной HTML-разметкой (вместо падения).
    animation (GIF) — в Telegram уходит отдельным сообщением перед текстом,
    в ВК — вложением к посту (не получилось — текст без вложения)."""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    reports = []
    soc = cfg.get("соцсети", {})

    tg = soc.get("telegram", {})
    if tg.get("вкл"):
        chat_id = tg.get("канал_id", "")
        if chat_id.startswith("УКАЖИ"):
            reports.append("Telegram: не указан канал_id в config.json")
        else:
            anim_report = ""
            if animation is not None and animation.exists():
                r = send_animation_tg(chat_id, animation)
                anim_report = "с анимацией" if r["ok"] else \
                    f" (анимация не ушла: {r['error']})"
            r = post_telegram(chat_id, text_tg)
            reports.append(f"Telegram: опубликовано {anim_report}".strip()
                           if r["ok"] else f"Telegram: НЕ вышло — {r['error']}")

    vk = soc.get("vk", {})
    if vk.get("вкл"):
        owner_id = vk.get("владелец_id") or vk.get("группа_id") or ""
        if owner_id.startswith("УКАЖИ") or not owner_id:
            reports.append("VK: не указан владелец_id в config.json")
        else:
            r = post_vk(owner_id, text_vk or vk_sanitize(text_tg),
                        gif_path=animation)
            if r["ok"]:
                reports.append("VK: опубликовано с анимацией"
                               if r.get("media") else
                               "VK: опубликовано (без анимации)")
            else:
                reports.append(f"VK: НЕ вышло — {r['error']}")
    return reports
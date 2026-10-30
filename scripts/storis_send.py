#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка и отправка пакета сторис — ВЫЗЫВАЕТСЯ ТОЛЬКО ИЗ СЕССИИ CLAUDE.

Режим работы цепочки (решение Михаила 10.10.2026): брендзавод-бот живёт
в фоне (launchd) и занимается только кнопками и статусом по /start.
Пакеты собирает сессия Claude этим скриптом:

    python3 scripts/storis_send.py              # текущий день из state
    python3 scripts/storis_send.py 5            # явный номер дня
    python3 scripts/storis_send.py 5 --без-кнопок   # заднее число

Скрипт — тонкая обёртка: всю сборку пакета, отправку и запись state
делает storis.отправить_пакет. Ничего не публикует в Instagram.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import publish  # noqa: E402  (.env: BOT_TOKEN, адресат)
import storis  # noqa: E402  (план, state, сборка и отправка пакета)


def main() -> int:
    args = sys.argv[1:]
    без_кнопок = "--без-кнопок" in args
    day = None
    for arg in args:
        if arg.isdigit():
            day = int(arg)
    state = storis.load_state()
    if day is None:
        day = state["день"]

    env = publish.load_env()
    token = env.get("BOT_TOKEN", "")
    # адресат: STORY_CHAT_ID, без него — ADMIN_CHAT_ID
    chat = env.get("STORY_CHAT_ID", "") or env.get("ADMIN_CHAT_ID", "")
    if not token or not chat:
        print("Нет BOT_TOKEN или адресата (STORY_CHAT_ID/"
              "ADMIN_CHAT_ID) в .env — см. README.")
        return 1

    ok, отчёт = storis.отправить_пакет(
        token, chat, day, без_кнопок=без_кнопок or day != state["день"])
    print(отчёт)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Разовый фикс: подключить запуск_черновика() к кнопке «Одобрить».
Заменяет два сообщения в блоке idea_ok bot.py, проверяет компиляцию
и PEP8, перезапускает бота. После успеха можно удалить."""

from pathlib import Path
import py_compile
import subprocess
import sys

P = Path(__file__).resolve().parent.parent / "scripts" / "bot.py"
S = P.read_text(encoding="utf-8")

СТАР1 = 'уведомл("Идея одобрена — открой Claude: он начнёт писать пост.")'
НОВ1 = ('уведомл("Идея одобрена — черновик создаётся сам.")\n'
        '            запуск_черновика()')
СТАР2 = ('return "✅ Идея одобрена. Черновик напишет Claude — '
         'пост придёт в бот."')
НОВ2 = ('return ("✅ Идея одобрена. Черновик готовлю сейчас сам — "\n'
        '                    "пришлю сообщение в Telegram, '
        'когда будет готов.")')

если1 = СТАР1 in S
если2 = СТАР2 in S
if not (если1 or если2):
    print("Обе строки уже заменены? Ничего не делаю.")
    sys.exit(0)
S = (S.replace(СТАР1, НОВ1) if если1 else S)
S = (S.replace(СТАР2, НОВ2) if если2 else S)
P.write_text(S, encoding="utf-8")
print("строк заменено:", int(если1) + int(если2))

py_compile.compile(str(P), doraise=True)
print("компиляция: ок")
r = subprocess.run(
    [sys.executable, "-m", "flake8", "--max-line-length", "88",
     str(Path(__file__).resolve().parent / "bot.py"),
     str(Path(__file__).resolve().parent / "авто-черновик.py")],
    capture_output=True, text=True)
print("flake8:", "чисто" if r.returncode == 0 else r.stdout or r.stderr)
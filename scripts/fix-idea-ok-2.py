#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Дочинка после первого фикс-скрипта:
1) вернуть функции архива имя archive (все вызовы — archive(...));
2) подключить запуск_черновика() к кнопке «Одобрить» (строка уведомл);
3) добавить перевод строки в конце авто-черновик.py (flake8 W292);
4) прогнать py_compile и flake8."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "scripts" / "bot.py"
AUTO = ROOT / "scripts" / "авто-черновик.py"

s = BOT.read_text(encoding="utf-8")
правки = 0

if "def архив(" in s:
    s = s.replace("def архив(", "def archive(", 1)
    правки += 1

СТАР = 'уведомл("Идея одобрена — открой Claude: он начнёт писать пост.")'
НОВ = ('уведомл("Идея одобрена — черновик создаётся сам.")\n'
       '            запуск_черновика()')
if СТАР in s:
    s = s.replace(СТАР, НОВ, 1)
    правки += 1

BOT.write_text(s, encoding="utf-8")

a = AUTO.read_text(encoding="utf-8")
if not a.endswith("\n"):
    AUTO.write_text(a + "\n", encoding="utf-8")
    правки += 1

print("правок:", правки)
sys.exit(0 if правки == 3 else 1)
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Разовая утилита: дописать переменные авторизации Claude в .env проекта
(значения берутся из окружения текущей сессии, на экран не печатаются).
После запуска можно удалить."""

import os
from pathlib import Path

ДОБАВИТЬ = ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL"]

ENV_ФАЙЛ = Path(__file__).resolve().parent.parent / ".env"


def main() -> int:
    есть = set()
    if ENV_ФАЙЛ.exists():
        for line in ENV_ФАЙЛ.read_text(encoding="utf-8").splitlines():
            строка = line.strip()
            if строка and not строка.startswith("#"):
                есть.add(строка.split("=", 1)[0].strip())
    дописано = 0
    with ENV_ФАЙЛ.open("a", encoding="utf-8") as файл:
        for имя in ДОБАВИТЬ:
            значение = os.getenv(имя, "")
            if значение and имя not in есть:
                файл.write(f"{имя}={значение}\n")
                дописано += 1
                print("дописано:", имя)
    print("всего строк в .env:", len(ENV_ФАЙЛ.read_text().splitlines()))
    return 0 if дописано or not os.getenv("ANTHROPIC_AUTH_TOKEN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
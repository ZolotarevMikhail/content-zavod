#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Этап 2.5 «Контент-завода»: анимация к посту.

Скачивает кадры нейрокартинок у Pollinations (бесплатный ключ из .env)
и собирает их в drafts/animation.gif.

Промпты задаются в KADRY ниже. Правило для серии кадров: описание героя
повторять слово в слово во всех кадрах и использовать один SEED.
Промпты — только на английском: flux русскую речь игнорирует.
Тексты в кадрах не просить — у нейросетей они выходят с ошибками,
вместо запретов писать позитивное замещение.

Запуск:  python3 scripts/animacia.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402  (единая сеть проекта: кадры через publish._get)

ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = ROOT / "drafts" / "animation.gif"
CADR_DIR = ROOT / "чекпойнты"
# ключи ищем в локальном .env проекта и в корневом ~/claudecode/.env
SEED = 7
WIDTH, HEIGHT = 1024, 1024
MS_PER_CADR = 950  # ~1 кадр в секунду, 5-8 кадров/сек не обязателен

# Описание героя повторяется в каждом кадре по-буквально — так нейросеть
# держит его одинаковым от кадра к кадру.
GEROY = ("a small glossy black robot with a rounded head and two round "
         "glowing yellow eyes")
STIL = ("flat 3D render illustration, dark charcoal background, warm "
        "amber accent light, soft shadows, no text, no letters, "
        "clean surfaces, unmarked")

KADRY = [
    # 1. Робот-врач спокойно читает обычный снимок сердца: всё выглядит нормальным
    f"{GEROY}, holding a translucent medical scan sheet with a small dark "
    f"red heart inside and gently clean vessel lines, calm relaxed mood, "
    f"{STIL}",
    # 2. Ряд пробирок анализов стоит тусклый и тихий, всё в норме на бумаге
    f"{GEROY}, standing beside a long row of dim clear test tubes and one "
    f"pale blank health report on a low table, everything quiet and "
    f"unremarkable, mild skeptical mood, {STIL}",
    # 3. На том же снимке у артерии сердца вспыхивает яркое скрытое пятно
    f"{GEROY}, staring closely at the same translucent scan sheet where the "
    f"dark red heart now shows one bright glowing inflammation spot on a "
    f"thin vessel line, the glow lighting the robot face from below, alert "
    f"focused mood, {STIL}",
    # 4. Финал: робот показывает на пятно во все глаза, анализы тускло позади
    f"{GEROY}, pointing one finger at the bright glowing inflammation spot "
    f"on the heart in the scan sheet, the dim test tubes far behind in "
    f"shadow, wide-eyed solemn surprise, {STIL}",
]


def load_key() -> str:
    """Ключ фоновых картинок: единый читатель .env проекта
    (publish.load_env) — без собственной копии разбора."""
    key = publish.load_env().get("POLLINATIONS_KEY", "")
    if not key:
        print("! Нет POLLINATIONS_KEY в .env — "
              "ключ нужен для скачивания кадров")
    return key


def download(url: str, path: Path) -> bool:
    """Скачать кадр. До 3 попыток с паузой; при неудаче — False.

    Сеть — единая точка проекта: каждая попытка через publish._get,
    здесь только проверка «это картинка?» и отчёт о ходе."""
    for attempt in (1, 2, 3):
        data, err = publish._get(url, timeout=120, user_agent="curl/8")
        if data is None:
            print(f"  ! {path.name}: {err or 'сеть не ответила'} "
                  f"(попытка {attempt}/3)")
            time.sleep(8)
            continue
        # картинка может прийти PNG или JPEG; другое — ошибка очереди
        if data[:8] != b"\x89PNG\r\n\x1a\n" and data[:2] != b"\xff\xd8":
            # сохраняем ответ рядом для разбора
            path.with_suffix(".txt").write_bytes(data[:2000])
            print(f"  ! {path.name}: не картинка, попытка {attempt}, "
                  f"ответ в {path.stem}.txt")
            time.sleep(5)
            continue
        path.with_suffix(".txt").unlink(missing_ok=True)
        path.write_bytes(data)
        return True
    return False


def main() -> int:
    key = load_key()
    if not key:
        return 1

    from urllib.parse import quote
    CADR_DIR.mkdir(exist_ok=True)

    paths = []
    for i, prompt in enumerate(KADRY, 1):
        path = CADR_DIR / f"kadr-{i}.png"
        url = (f"https://gen.pollinations.ai/image/{quote(prompt)}"
               f"?model=flux&width={WIDTH}&height={HEIGHT}"
               f"&seed={SEED}&nologo=true&key={key}")
        ok = download(url, path)
        if ok:
            print(f"Кадр {i}: ок")
            paths.append(path)
        else:
            print(f"Кадр {i}: НЕ УДАЛСЯ — сборка без него")

    if len(paths) < 3:
        print("Меньше 3 кадров — анимацию не собираю.")
        return 1

    from PIL import Image
    frames = [Image.open(p).convert("RGB") for p in paths]
    frames[0].save(
        OUT_FILE, save_all=True, append_images=frames[1:],
        duration=MS_PER_CADR, loop=0, optimize=True,
    )
    size_kb = OUT_FILE.stat().st_size // 1024
    print(f"Анимация: {OUT_FILE.name}, кадров: {len(frames)}, {size_kb} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())

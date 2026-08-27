# services/eyesauron/_capture_core.py — ядро захвата экрана EyeSauron
#
# Самодостаточный модуль БЕЗ mesh-зависимостей: используется и процессом узла
# (прямой захват в dev-режиме), и хелпером пользовательской сессии
# (_session_helper.py). Порт рабочего кода eye_agent.py проекта EyeSauron с
# заменами:
#   - pywin32 (win32gui) → чистый ctypes для заголовка активного окна;
#   - imagehash.average_hash → собственная реализация на PIL+numpy;
#   - HTTP-отправка → spool-файлы (очередь разбирает сервис узла).
#
# ВАЖНО: импортируется в отдельном процессе хелпера — держать лёгким.

import ctypes
import hashlib
import io
import logging
import re
import time
from pathlib import Path

try:
    import mss
except ImportError:
    mss = None

try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = None
    ImageGrab = None

try:
    import numpy as np
except ImportError:
    np = None

log = logging.getLogger('eye.capture')

MIN_SCREENSHOT_SIZE = 10_000   # PNG меньше этого размера считаем мусором
BLACK_BRIGHTNESS = 5           # средняя яркость ниже — «чёрный экран»
TITLE_MAX_LEN = 60             # как в оригинале
HASH_SIZE = 8                  # average_hash 8x8 = 64 бита


# ------------------------------------------------------------------ #
#  Захват кадра (каскад методов с фолбэком)
# ------------------------------------------------------------------ #

def _grab_mss():
    if mss is None:
        raise RuntimeError('mss недоступен')
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[0])
        return Image.frombytes('RGB', shot.size, shot.rgb)


def _grab_pil():
    if ImageGrab is None:
        raise RuntimeError('PIL.ImageGrab недоступен')
    img = ImageGrab.grab()
    if img is None:
        raise RuntimeError('ImageGrab вернул None')
    return img


def _grab_ctypes_gdi():
    """Чистый GDI через ctypes — последний рубеж без сторонних библиотек."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

    hdesktop = user32.GetDesktopWindow()
    desktop_dc = user32.GetWindowDC(hdesktop)
    img_dc = gdi32.CreateCompatibleDC(desktop_dc)
    bmp = gdi32.CreateCompatibleBitmap(desktop_dc, width, height)
    gdi32.SelectObject(img_dc, bmp)
    gdi32.BitBlt(img_dc, 0, 0, width, height, desktop_dc, 0, 0, 0x00CC0020)  # SRCCOPY

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_long),
                    ('biHeight', ctypes.c_long), ('biPlanes', ctypes.c_uint16),
                    ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                    ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_long),
                    ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', ctypes.c_uint32),
                    ('biClrImportant', ctypes.c_uint32)]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height          # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32

    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(desktop_dc, bmp, 0, height, buf, ctypes.byref(bmi), 0)

    img = Image.frombuffer('RGBA', (width, height), buf.raw, 'raw', 'BGRA', 0, 1)
    img = img.convert('RGB')

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(img_dc)
    user32.ReleaseDC(hdesktop, desktop_dc)
    return img


def grab_frame():
    """Один кадр рабочего стола: PIL.Image или None (все методы не сработали)."""
    for method in (_grab_mss, _grab_pil, _grab_ctypes_gdi):
        try:
            return method()
        except Exception as e:
            log.debug('захват %s не сработал: %s', method.__name__, e)
    return None


# ------------------------------------------------------------------ #
#  Валидация кадра
# ------------------------------------------------------------------ #

def is_black_screen(img) -> bool:
    try:
        gray = np.asarray(img.convert('L'), dtype=np.uint8)
        return float(gray.mean()) < BLACK_BRIGHTNESS
    except Exception:
        return False


def png_bytes(img) -> bytes | None:
    """PNG кадра в памяти или None, если кадр невалиден."""
    try:
        if img is None or not img.size or img.size[0] == 0 or img.size[1] == 0:
            return None
        with io.BytesIO() as buf:
            img.save(buf, format='PNG')
            data = buf.getvalue()
        if len(data) < MIN_SCREENSHOT_SIZE or is_black_screen(img):
            return None
        return data
    except Exception as e:
        log.debug('кадр невалиден: %s', e)
        return None


# ------------------------------------------------------------------ #
#  Перцептивный хеш (порт imagehash.average_hash на numpy)
# ------------------------------------------------------------------ #

def avg_hash(img) -> int:
    """64-битный average_hash: 8x8 grayscale, биты = пиксель > среднее."""
    small = img.convert('L').resize((HASH_SIZE, HASH_SIZE))
    pixels = np.asarray(small, dtype=np.float32)
    bits = pixels > pixels.mean()
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def hash_distance(a: int, b: int) -> int:
    """Расстояние Хэмминга между двумя хешами."""
    return bin(a ^ b).count('1')


# ------------------------------------------------------------------ #
#  Метаданные
# ------------------------------------------------------------------ #

_SANITIZE_RE = re.compile(r'[<>:"/\\|?*]')


def window_title() -> str:
    """Заголовок активного окна (ctypes, без pywin32)."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = _SANITIZE_RE.sub('', buf.value).strip()
        return title[:TITLE_MAX_LEN] or 'NoTitle'
    except Exception:
        return 'UnknownWindow'


def frame_timestamp() -> str:
    return time.strftime('%Y-%m-%d_%H-%M-%S')


def spool_name(timestamp: str, title: str, hostname: str) -> str:
    """Имя файла в spool: md5 от состава кадра (как кэш оригинала)."""
    return hashlib.md5(f'{timestamp}__{title}__{hostname}'.encode('utf-8')).hexdigest()


# ------------------------------------------------------------------ #
#  Spool — офлайн-очередь кадров (<work_dir>/eyesauron/spool)
#
# Формат унаследован от офлайн-кэша eye_agent.py:
#   <md5>        — PNG без расширения
#   <md5>.meta   — построчно key=value (hostname/timestamp/title)
# ------------------------------------------------------------------ #

def spool_write(spool_dir: Path, data: bytes, meta: dict) -> Path:
    spool_dir.mkdir(parents=True, exist_ok=True)
    name = spool_name(meta['timestamp'], meta['title'], meta['hostname'])
    path = spool_dir / name
    path.write_bytes(data)
    with open(spool_dir / f'{name}.meta', 'w', encoding='utf-8') as f:
        for key in ('hostname', 'timestamp', 'title'):
            f.write(f'{key}={meta.get(key, "")}\n')
    return path


def spool_read(path: Path) -> tuple[bytes, dict] | None:
    """(png_bytes, meta) или None (нет .meta / битый файл)."""
    meta_path = path.with_name(path.name + '.meta')
    if not meta_path.is_file():
        return None
    meta = {}
    for line in meta_path.read_text(encoding='utf-8').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            meta[key.strip()] = value.strip()
    try:
        return path.read_bytes(), meta
    except OSError:
        return None


def spool_remove(path: Path):
    for p in (path, path.with_name(path.name + '.meta')):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def spool_files(spool_dir: Path) -> list[Path]:
    """Кадры очереди, старейшие первыми (по mtime); только файлы без расширения."""
    if not spool_dir.is_dir():
        return []
    files = [f for f in spool_dir.iterdir() if f.is_file() and not f.suffix]
    files.sort(key=lambda f: f.stat().st_mtime)
    return files

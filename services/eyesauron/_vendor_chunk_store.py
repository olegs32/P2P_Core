# VENDOR: порт tools/chunk_store.py проекта EyeSauron (снимок 2026-08-26,
# код не менялся, кроме этого заголовка). Файл с '_'-префиксом — ServiceLoader
# его не грузит; модуль сохранён как ОБРАЗЕЦ дедупликации чанками.
# ВАЖНО (docs/eyeSauron.md): формат из тысяч мелких PNG-чанков убивает NAS —
# при реальном включении дедупа переделать на пакование чанков в крупные
# файлы-контейнеры с отложенной заливкой на NAS (см. TODO в service.py).

"""
ChunkStore — блочная дедупликация изображений.

Структура хранилища:
  store/
    chunks/
      ab/abcdef1234....png        # чанк 256x256, PNG
    maps/
      hostname/
        2024-01-15/
          1234__title.json        # карта — ключ "hostname/2024-01-15/1234__title"
    hashes.json                   # персистентный кэш известных хешей
    index.json                    # лёгкий индекс {map_key → {rel_path, w, h}}

Использование:
  store = ChunkStore("./store")

  # Сжатие папки
  for p in store.stream_folder("photos", recursive=True, workers=4):
      print(f"\\r{p}", end="", flush=True)

  # Восстановление
  for p in store.stream_restore_folder("restored", workers=4):
      print(f"\\r{p}", end="", flush=True)

  # Из FastAPI upload (без записи на диск):
  result = store.store_image_bytes(data, rel_path="host/2024-01-15/frame.png")
"""

import hashlib
import json
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

CHUNK_SIZE = 256
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
HASHES_FILE = "hashes.json"
INDEX_FILE = "index.json"


# Кеш байт чанков на уровне модуля.
# Ключ — абсолютный строковый путь к файлу чанка.
# При высокой дедупликации большинство чанков переиспользуются между картинками —
# повторные чтения с NAS/сети заменяются попаданием в RAM.
@lru_cache(maxsize=8192)
def _read_chunk_bytes(path: str) -> bytes:
    return Path(path).read_bytes()


def invalidate_chunk_cache() -> None:
    """Сбросить кеш чанков (например после добавления новых чанков в store)."""
    _read_chunk_bytes.cache_clear()


# ── dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class FileResult:
    path: Path
    total_chunks: int = 0
    new_chunks: int = 0
    new_hashes: list[str] = field(default_factory=list)
    index_entry: dict | None = None  # {map_name: {rel_path, width, height}}
    error: str | None = None

    @property
    def dedup_pct(self) -> float:
        if not self.total_chunks:
            return 0.0
        return (self.total_chunks - self.new_chunks) / self.total_chunks * 100


@dataclass
class FolderStats:
    total: int = 0
    processed: int = 0
    errors: int = 0
    total_chunks: int = 0
    new_chunks: int = 0
    error_files: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def dedup_pct(self) -> float:
        if not self.total_chunks:
            return 0.0
        return (self.total_chunks - self.new_chunks) / self.total_chunks * 100

    def __str__(self) -> str:
        return (
            f"processed={self.processed}/{self.total} "
            f"errors={self.errors} "
            f"chunks={self.total_chunks} "
            f"new={self.new_chunks} "
            f"dedup={self.dedup_pct:.1f}%"
        )


@dataclass
class FolderProgress:
    index: int
    total: int
    current_file: Path
    stats: FolderStats
    last_result: FileResult | None = None
    elapsed: float = 0.0
    eta: float = 0.0

    @property
    def pct(self) -> float:
        return self.index / self.total * 100 if self.total else 0.0

    def __str__(self) -> str:
        if self.total > 0:
            bar_len = 30
            filled = int(bar_len * self.index / self.total)
            bar = "█" * filled + "░" * (bar_len - filled)
            eta_s = f"ETA {self.eta:.0f}s" if self.eta > 0 else "done "
            pct = f"{self.pct:5.1f}%"
        else:
            bar = "·" * 30
            eta_s = f"{self.elapsed:.0f}s"
            pct = "  ···"
        err = f" ⚠ {self.stats.errors} err" if self.stats.errors else ""
        spd = f" {self.index / self.elapsed:.0f}/s" if self.elapsed > 1 else ""
        return (
            f"[{bar}] {pct} {self.index}/?"
            f" | dedup {self.stats.dedup_pct:.1f}%"
            f" | {eta_s}{err}{spd}"
            f" | {self.current_file.name}"
        )


@dataclass
class RestoreProgress:
    index: int
    total: int
    current_map: str
    output_path: Path
    error: str | None = None
    elapsed: float = 0.0
    eta: float = 0.0

    @property
    def pct(self) -> float:
        return self.index / self.total * 100 if self.total else 0.0

    def __str__(self) -> str:
        bar_len = 30
        filled = int(bar_len * self.index / self.total) if self.total else 0
        bar = "█" * filled + "░" * (bar_len - filled)
        eta_s = f"ETA {self.eta:.0f}s" if self.eta > 0 else "done "
        status = f"⚠ {self.error[:40]}" if self.error else self.output_path.name
        return f"[{bar}] {self.pct:5.1f}% {self.index}/{self.total} | {eta_s}| {status}"


# ── multiprocessing workers ───────────────────────────────────────────────────

def _worker_store(
        store_root: str,
        chunk_size: int,
        image_path: str,
        source_root: str,
) -> dict:
    store = ChunkStore.__new__(ChunkStore)
    store.root = Path(store_root)
    store.chunk_size = chunk_size
    store._known_hashes = store._load_hashes()
    result = store._store_one(
        path=Path(image_path),
        stats=FolderStats(),
        source_root=Path(source_root),
    )
    return {
        "path": str(result.path),
        "total_chunks": result.total_chunks,
        "new_chunks": result.new_chunks,
        "new_hashes": result.new_hashes,
        "index_entry": result.index_entry,
        "error": result.error,
    }


def _worker_restore(
        store_root: str,
        chunk_size: int,
        map_paths: list[str],  # список путей к JSON картам
        output_dir: str,
        overwrite: bool,
) -> list[dict]:
    """Восстанавливает порцию карт в дочернем процессе."""
    store = ChunkStore.__new__(ChunkStore)
    store.root = Path(store_root)
    store.chunk_size = chunk_size
    store._known_hashes = set()  # не нужен при восстановлении

    results = []
    for map_path_str in map_paths:
        map_path = Path(map_path_str)
        out_file = Path(output_dir)
        error = None
        try:
            data = json.loads(map_path.read_text(encoding="utf-8"))
            w, h, cs = data["width"], data["height"], data["chunk_size"]

            rel = data.get("rel_path")
            out_file = (
                Path(output_dir) / Path(rel).with_suffix(".png")
                if rel
                else Path(output_dir) / f"{map_path.stem}.png"
            )

            if out_file.exists() and not overwrite:
                results.append({
                    "map": map_path.stem,
                    "out": str(out_file),
                    "error": "skipped",
                })
                continue

            out_file.parent.mkdir(parents=True, exist_ok=True)
            canvas = np.zeros((h, w, 3), dtype=np.uint8)

            for gy, row in enumerate(data["grid"]):
                for gx, chunk_hash in enumerate(row):
                    chunk_path = store.root / "chunks" / chunk_hash[:2] / f"{chunk_hash}.png"
                    chunk = np.array(Image.open(chunk_path))
                    y, x = gy * cs, gx * cs
                    real_h = min(cs, h - y)
                    real_w = min(cs, w - x)
                    canvas[y: y + real_h, x: x + real_w] = chunk[:real_h, :real_w]

            Image.fromarray(canvas).save(out_file)

        except Exception as exc:
            error = str(exc)

        results.append({"map": map_path.stem, "out": str(out_file), "error": error})

    return results


# ── main class ────────────────────────────────────────────────────────────────

class ChunkStore:
    def __init__(self, store_path: str, chunk_size: int = CHUNK_SIZE):
        self.root = Path(store_path)
        self.chunk_size = chunk_size
        (self.root / "chunks").mkdir(parents=True, exist_ok=True)
        (self.root / "maps").mkdir(parents=True, exist_ok=True)
        self._known_hashes: set[str] = self._load_hashes()
        self._index: dict[str, dict] = self._load_index()
        print(f"[init] {len(self._known_hashes)} hashes, {len(self._index)} indexed images")

    # ── hashes persistence ────────────────────────────────────────────────────

    def _hashes_path(self) -> Path:
        return self.root / HASHES_FILE

    def _load_hashes(self) -> set[str]:
        p = self._hashes_path()
        if p.exists():
            return set(json.loads(p.read_text(encoding="utf-8")))
        return set()

    def save_hashes(self) -> None:
        self._hashes_path().write_text(
            json.dumps(list(self._known_hashes), separators=(",", ":")),
            encoding="utf-8",
        )

    def merge_hashes(self, new_hashes: list[str]) -> int:
        before = len(self._known_hashes)
        self._known_hashes.update(new_hashes)
        return len(self._known_hashes) - before

    # ── index persistence ─────────────────────────────────────────────────────

    def _index_path(self) -> Path:
        return self.root / INDEX_FILE

    def _load_index(self) -> dict:
        p = self._index_path()
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {}

    def save_index(self) -> None:
        self._index_path().write_text(
            json.dumps(self._index, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )

    def merge_index(self, entries: dict) -> int:
        """Добавляет записи от воркеров в основной индекс. Возвращает кол-во новых."""
        before = len(self._index)
        self._index.update(entries)
        return len(self._index) - before

    def rebuild_index(self) -> int:
        """
        Перестраивает index.json из всех существующих карт (рекурсивно).
        Запустить один раз при миграции или если index.json потерян.
        """
        self._index = {}
        maps_root = self.root / "maps"
        maps = list(maps_root.rglob("*.json"))
        for mp in maps:
            try:
                data = json.loads(mp.read_text(encoding="utf-8"))
                rel = data.get("rel_path", "")
                if not rel:
                    continue
                # map_name = путь от maps/ без расширения, нормализованный к '/'
                map_name = mp.relative_to(maps_root).with_suffix("").as_posix()
                self._index[map_name] = {
                    "rel_path": rel,
                    "width": data.get("width"),
                    "height": data.get("height"),
                }
            except Exception:
                pass
        self.save_index()
        print(f"[index] rebuilt: {len(self._index)}/{len(maps)} maps indexed")
        return len(self._index)

    # ── internal ──────────────────────────────────────────────────────────────

    def _chunk_path(self, h: str) -> Path:
        return self.root / "chunks" / h[:2] / f"{h}.png"

    def _map_path(self, map_name: str) -> Path:
        """map_name может содержать слэши: 'hostname/date/stem' → maps/hostname/date/stem.json"""
        return self.root / "maps" / f"{map_name}.json"

    def _hash_array(self, arr: np.ndarray) -> str:
        return hashlib.sha256(arr.tobytes()).hexdigest()

    def _pad_chunk(self, chunk: np.ndarray) -> np.ndarray:
        cs = self.chunk_size
        if chunk.shape[0] == cs and chunk.shape[1] == cs:
            return chunk
        channels = chunk.shape[2] if chunk.ndim == 3 else 1
        padded = np.zeros((cs, cs, channels), dtype=np.uint8)
        padded[: chunk.shape[0], : chunk.shape[1]] = chunk
        return padded

    def _save_chunk(self, h: str, data: np.ndarray) -> bool:
        if h in self._known_hashes:
            return False
        path = self._chunk_path(h)
        if path.exists():
            self._known_hashes.add(h)
            return False
        path.parent.mkdir(exist_ok=True)
        Image.fromarray(data).save(path, format="PNG", optimize=True)
        self._known_hashes.add(h)
        return True

    def _load_chunk(self, h: str) -> np.ndarray:
        """Загружает чанк, используя модульный LRU-кеш bytes для снижения нагрузки на NAS."""
        raw = _read_chunk_bytes(str(self._chunk_path(h).resolve()))
        return np.array(Image.open(BytesIO(raw)))

    def _rel_path(self, path: Path, source_root: Path | None) -> str:
        if source_root:
            try:
                rel = path.resolve().relative_to(source_root.resolve())
                # Нормализуем разделители к '/' для кросс-платформенности
                return rel.as_posix()
            except ValueError:
                pass
        return path.as_posix()

    def _store_one(
            self,
            path: Path,
            stats: FolderStats,
            source_root: Path | None = None,
    ) -> FileResult:
        try:
            img = np.array(Image.open(path).convert("RGB"))
        except Exception as exc:
            stats.errors += 1
            stats.error_files.append((path, str(exc)))
            return FileResult(path=path, error=str(exc))

        rel = self._rel_path(path, source_root)
        # map_name = rel_path без расширения: "hostname/date/stem"
        map_name = Path(rel).with_suffix("").as_posix()
        return self._store_array(
            img=img,
            map_name=map_name,
            rel_path=rel,
            source_str=str(path),
            stats=stats,
            path=path,
        )

    def _store_array(
            self,
            img: "np.ndarray",
            map_name: str,
            rel_path: str,
            source_str: str,
            stats: FolderStats,
            path: "Path | None" = None,
    ) -> FileResult:
        """Ядро дедупликации. map_name может содержать слэши (субдиректории)."""
        _path = path or Path(map_name)
        try:
            h, w = img.shape[:2]
            cs = self.chunk_size
            grid: list[list[str]] = []
            new_chunks = 0
            total_chunks = 0
            new_hashes: list[str] = []

            for y in range(0, h, cs):
                row: list[str] = []
                for x in range(0, w, cs):
                    chunk = img[y: y + cs, x: x + cs]
                    chunk = self._pad_chunk(chunk)
                    chunk_hash = self._hash_array(chunk)
                    if self._save_chunk(chunk_hash, chunk):
                        new_chunks += 1
                        new_hashes.append(chunk_hash)
                    total_chunks += 1
                    row.append(chunk_hash)
                grid.append(row)

            map_data = {
                "width": w,
                "height": h,
                "chunk_size": cs,
                "rel_path": rel_path,
                "source": source_str,
                "grid": grid,
            }
            mp = self._map_path(map_name)
            mp.parent.mkdir(parents=True, exist_ok=True)  # создаём hostname/date/
            mp.write_text(json.dumps(map_data, separators=(",", ":")), encoding="utf-8")

            idx_entry = {map_name: {"rel_path": rel_path, "width": w, "height": h}}
            self._index.update(idx_entry)

            stats.processed += 1
            stats.total_chunks += total_chunks
            stats.new_chunks += new_chunks
            return FileResult(
                path=_path,
                total_chunks=total_chunks,
                new_chunks=new_chunks,
                new_hashes=new_hashes,
                index_entry=idx_entry,
            )

        except Exception as exc:
            stats.errors += 1
            stats.error_files.append((_path, str(exc)))
            return FileResult(path=_path, error=str(exc))

    # ── public: store ─────────────────────────────────────────────────────────

    def store_image(self, image_path: str, map_name: str | None = None) -> Path:
        stats = FolderStats(total=1)
        result = self._store_one(Path(image_path), stats)
        if result.error:
            raise RuntimeError(result.error)
        self.save_hashes()
        self.save_index()
        # map_name теперь = rel_path без расширения, берём из index_entry
        actual_name = next(iter(result.index_entry)) if result.index_entry else Path(image_path).stem
        print(
            f"[store] {actual_name}: {result.total_chunks} chunks, {result.new_chunks} new, {result.dedup_pct:.1f}% dedup")
        return self._map_path(actual_name)

    def store_image_bytes(
            self,
            data: bytes,
            rel_path: str,
            map_name: str | None = None,
    ) -> FileResult:
        """
        Дедуплицирует изображение из bytes без записи на диск.
        Используется из FastAPI: передай bytes из UploadFile + виртуальный rel_path.

        rel_path определяет положение в виртуальной иерархии, например:
            "hostname/2024-01-15/frame_0042.png"
        Карта сохранится в maps/hostname/2024-01-15/frame_0042.json

        map_name — переопределить ключ (по умолчанию rel_path без расширения).
        Синхронный, запускай через run_in_executor в async контексте.
        """
        img = np.array(Image.open(BytesIO(data)).convert("RGB"))
        # map_name = rel_path без расширения, нормализуем слэши
        name = map_name or Path(rel_path).with_suffix("").as_posix()
        stats = FolderStats(total=1)
        result = self._store_array(
            img=img,
            map_name=name,
            rel_path=rel_path,
            source_str=rel_path,
            stats=stats,
        )
        if result.error:
            raise RuntimeError(result.error)
        self.save_hashes()
        self.save_index()
        return result

    def _iter_files(
            self,
            folder_path: Path,
            extensions: tuple[str, ...],
            recursive: bool,
            skip_existing: bool,
    ):
        """
        Lazy генератор файлов — не строит list в памяти.
        skip_existing проверяет self._index (O(1)), не glob.
        Возвращает (path, skipped_count) — skipped_count растёт пока пропускаем.
        """
        glob_fn = folder_path.rglob if recursive else folder_path.glob
        seen: set[Path] = set()
        skipped = 0

        for ext in extensions:
            for p in glob_fn(f"*{ext}"):
                rp = p.resolve()
                if not p.is_file() or rp in seen:
                    continue
                seen.add(rp)

                if skip_existing:
                    try:
                        rel = rp.relative_to(folder_path).with_suffix("").as_posix()
                    except ValueError:
                        rel = p.stem
                    if rel in self._index:
                        skipped += 1
                        continue

                yield p, skipped
                skipped = 0  # сбрасываем после каждого yield — caller суммирует

    def stream_folder(
            self,
            folder: str,
            extensions: tuple[str, ...] = IMAGE_EXTENSIONS,
            recursive: bool = False,
            skip_existing: bool = True,
            workers: int = 1,
            flush_every: int = 500,  # сохранять hashes/index каждые N файлов
            queue_depth: int = 2,  # окно = workers * queue_depth активных futures
    ) -> Generator[FolderProgress, None, FolderStats]:
        """
        Потоковая обработка папки.
        Работает как lazy генератор — не держит весь список файлов в памяти.
        При 10^7+ файлах потребление RAM остаётся константным.

        Args:
            folder:       корневая папка
            recursive:    обходить подпапки
            skip_existing: пропускать уже индексированные (по self._index)
            workers:      1 = однопоточно, >1 = ProcessPoolExecutor
            flush_every:  сохранять hashes+index на диск каждые N обработанных файлов
            queue_depth:  размер окна = workers * queue_depth (только для workers>1)
        """
        folder_path = Path(folder).resolve()
        if not folder_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {folder_path}")

        print(f"[stream] scanning {folder_path} ...")
        stats = FolderStats(total=0)  # total неизвестен заранее
        t0 = time.monotonic()

        file_gen = self._iter_files(folder_path, extensions, recursive, skip_existing)

        if workers > 1:
            yield from self._stream_parallel_store(
                file_gen, stats, t0, workers, folder_path, flush_every, queue_depth
            )
        else:
            total_skipped = 0
            for path, skipped in file_gen:
                total_skipped += skipped
                stats.total += 1
                result = self._store_one(path, stats, source_root=folder_path)
                elapsed = time.monotonic() - t0
                yield FolderProgress(
                    index=stats.processed + stats.errors,
                    total=0,  # unknown
                    current_file=path,
                    last_result=result,
                    stats=stats,
                    elapsed=elapsed,
                    eta=0.0,
                )
                if stats.processed % flush_every == 0:
                    self.save_hashes()
                    self.save_index()

            if total_skipped:
                print(f"[stream] skipped {total_skipped} already indexed")

        self.save_hashes()
        self.save_index()
        print(f"\n[stream] done: {stats} | index: {len(self._index)} | hashes: {len(self._known_hashes)}")
        return stats

    def _stream_parallel_store(
            self,
            file_gen,  # Iterator[(Path, int)]
            stats: FolderStats,
            t0: float,
            workers: int,
            source_root: Path,
            flush_every: int,
            queue_depth: int,
    ) -> Generator[FolderProgress, None, None]:
        """
        Sliding window — в памяти одновременно не более workers*queue_depth futures.
        Файлы читаются из генератора лениво по мере освобождения окна.
        """
        from concurrent.futures import ProcessPoolExecutor, as_completed
        import itertools

        WINDOW = workers * queue_depth
        all_new_hashes: list[str] = []
        total_skipped = 0

        def _submit(ex, path):
            return ex.submit(
                _worker_store,
                str(self.root), self.chunk_size, str(path), str(source_root)
            )

        with ProcessPoolExecutor(max_workers=workers) as ex:
            pending: dict = {}  # future → path

            # Заполняем начальное окно
            for path, skipped in itertools.islice(file_gen, WINDOW):
                total_skipped += skipped
                stats.total += 1
                pending[_submit(ex, path)] = path

            while pending:
                # Ждём первый завершившийся
                done_iter = as_completed(pending)
                future = next(done_iter)
                path = pending.pop(future)

                # try:
                rd = future.result()
                result = FileResult(
                    path=Path(rd["path"]),
                    total_chunks=rd["total_chunks"],
                    new_chunks=rd["new_chunks"],
                    new_hashes=rd["new_hashes"],
                    index_entry=rd.get("index_entry"),
                    error=rd["error"],
                )
                stats.processed += 1
                stats.total_chunks += result.total_chunks
                stats.new_chunks += result.new_chunks
                all_new_hashes.extend(result.new_hashes)
                if result.index_entry:
                    self._index.update(result.index_entry)
                # except Exception as exc:
                #     print(exc)
                #     result = FileResult(path=path, error=str(exc))
                #     stats.errors += 1
                #     stats.error_files.append((path, str(exc)))

                # Периодически сохраняем прогресс
                done_total = stats.processed + stats.errors
                if done_total % flush_every == 0:
                    self.merge_hashes(all_new_hashes)
                    all_new_hashes.clear()
                    self.save_hashes()
                    self.save_index()

                elapsed = time.monotonic() - t0
                yield FolderProgress(
                    index=done_total,
                    total=0,  # unknown — lazy gen не знает конца
                    current_file=path,
                    last_result=result,
                    stats=stats,
                    elapsed=elapsed,
                    eta=0.0,
                )

                # Подаём следующий файл из генератора в освободившийся слот
                try:
                    next_path, skipped = next(file_gen)
                    total_skipped += skipped
                    stats.total += 1
                    pending[_submit(ex, next_path)] = next_path
                except StopIteration:
                    pass  # генератор исчерпан — окно будет сжиматься

        if total_skipped:
            print(f"[stream] skipped {total_skipped} already indexed")

        added = self.merge_hashes(all_new_hashes)
        print(f"[parallel] merged {len(all_new_hashes)} hashes (+{added} unique)")

    # ── public: restore ───────────────────────────────────────────────────────

    def restore_image(self, map_name: str, output_path: str) -> Path:
        """Восстанавливает одно изображение из карты. map_name может содержать слэши."""
        mp = self._map_path(map_name)
        if not mp.exists():
            raise FileNotFoundError(f"Map not found: {mp}")
        data = json.loads(mp.read_text(encoding="utf-8"))
        w, h, cs = data["width"], data["height"], data["chunk_size"]
        canvas = np.zeros((h, w, 3), dtype=np.uint8)
        for gy, row in enumerate(data["grid"]):
            for gx, chunk_hash in enumerate(row):
                chunk = self._load_chunk(chunk_hash)
                y, x = gy * cs, gx * cs
                real_h = min(cs, h - y)
                real_w = min(cs, w - x)
                canvas[y: y + real_h, x: x + real_w] = chunk[:real_h, :real_w]
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(canvas).save(out)
        print(f"[restore] {map_name} → {out}")
        return out

    def assemble_to_bytes(self, map_name: str, fmt: str = "PNG") -> bytes:
        """
        Собирает изображение в память и возвращает bytes.
        Используется веб-сервером — без записи на диск.
        map_name может содержать слэши: "hostname/date/stem"
        """
        mp = self._map_path(map_name)
        if not mp.exists():
            raise FileNotFoundError(f"Map not found: {mp}")
        data = json.loads(mp.read_text(encoding="utf-8"))
        w, h, cs = data["width"], data["height"], data["chunk_size"]
        canvas = np.zeros((h, w, 3), dtype=np.uint8)
        for gy, row in enumerate(data["grid"]):
            for gx, chunk_hash in enumerate(row):
                chunk = self._load_chunk(chunk_hash)
                y, x = gy * cs, gx * cs
                real_h = min(cs, h - y)
                real_w = min(cs, w - x)
                canvas[y: y + real_h, x: x + real_w] = chunk[:real_h, :real_w]
        buf = BytesIO()
        Image.fromarray(canvas).save(buf, format=fmt)
        return buf.getvalue()

    def stream_restore_folder(
            self,
            output_dir: str,
            overwrite: bool = False,
            workers: int = 1,
    ) -> Generator[RestoreProgress, None, None]:
        """
        Потоковое восстановление всех сохранённых изображений.
        При workers > 1 делит карты поровну между процессами.

        Args:
            output_dir: куда восстанавливать (rel_path воссоздаётся внутри)
            overwrite:  перезаписывать существующие файлы
            workers:    количество параллельных процессов
        """
        out_root = Path(output_dir)
        out_root.mkdir(parents=True, exist_ok=True)
        maps = sorted((self.root / "maps").rglob("*.json"))  # рекурсивно
        total = len(maps)

        if total == 0:
            print("[restore] no maps found")
            return

        print(f"[restore] {total} images → {out_root} (workers={workers})")
        t0 = time.monotonic()

        if workers > 1:
            yield from self._stream_parallel_restore(maps, out_root, overwrite, workers, t0)
        else:
            for i, map_path in enumerate(maps, 1):
                elapsed = time.monotonic() - t0
                eta = (elapsed / i) * (total - i) if i < total else 0.0
                error = None
                out_file = out_root

                try:
                    data = json.loads(map_path.read_text(encoding="utf-8"))
                    w, h, cs = data["width"], data["height"], data["chunk_size"]
                    rel = data.get("rel_path")
                    out_file = (
                        out_root / Path(rel).with_suffix(".png") if rel
                        else out_root / f"{map_path.stem}.png"
                    )
                    if out_file.exists() and not overwrite:
                        yield RestoreProgress(
                            index=i, total=total, current_map=map_path.stem,
                            output_path=out_file, error="skipped",
                            elapsed=elapsed, eta=eta,
                        )
                        continue

                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    canvas = np.zeros((h, w, 3), dtype=np.uint8)
                    for gy, row in enumerate(data["grid"]):
                        for gx, chunk_hash in enumerate(row):
                            chunk = self._load_chunk(chunk_hash)
                            y, x = gy * cs, gx * cs
                            real_h = min(cs, h - y)
                            real_w = min(cs, w - x)
                            canvas[y: y + real_h, x: x + real_w] = chunk[:real_h, :real_w]
                    Image.fromarray(canvas).save(out_file)

                except Exception as exc:
                    error = str(exc)

                yield RestoreProgress(
                    index=i, total=total, current_map=map_path.stem,
                    output_path=out_file, error=error, elapsed=elapsed, eta=eta,
                )

    def _stream_parallel_restore(
            self,
            maps: list[Path],
            out_root: Path,
            overwrite: bool,
            workers: int,
            t0: float,
    ) -> Generator[RestoreProgress, None, None]:
        """
        Делит список карт на chunks по числу воркеров,
        каждый воркер получает свою порцию и восстанавливает независимо.
        Результаты стримятся по мере завершения воркеров.
        """
        from concurrent.futures import ProcessPoolExecutor, as_completed

        total = len(maps)

        # Делим на равные части
        chunk_size = max(1, (total + workers - 1) // workers)
        batches = [
            maps[i: i + chunk_size]
            for i in range(0, total, chunk_size)
        ]

        # future → (batch_index, batch)
        done_count = 0
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(
                    _worker_restore,
                    str(self.root),
                    self.chunk_size,
                    [str(p) for p in batch],
                    str(out_root),
                    overwrite,
                ): batch
                for batch in batches
            }
            for future in as_completed(futures):
                batch = futures[future]
                try:
                    results = future.result()
                except Exception as exc:
                    # весь батч упал — отмечаем все как ошибку
                    print(exc)
                    results = [
                        {"map": p.stem, "out": str(out_root), "error": str(exc)}
                        for p in batch
                    ]

                for r in results:
                    done_count += 1
                    elapsed = time.monotonic() - t0
                    eta = (elapsed / done_count) * (total - done_count) if done_count < total else 0.0
                    yield RestoreProgress(
                        index=done_count,
                        total=total,
                        current_map=r["map"],
                        output_path=Path(r["out"]),
                        error=r.get("error"),
                        elapsed=elapsed,
                        eta=eta,
                    )

    # ── public: browse (для веб-сервера) ─────────────────────────────────────

    def list_arg1(self) -> list[str]:
        """Уникальные значения первого уровня виртуального пути (из index)."""
        result: set[str] = set()
        for entry in self._index.values():
            parts = Path(entry["rel_path"]).parts
            if len(parts) >= 2:
                result.add(parts[0])
        return sorted(result)

    def list_arg2(self, arg1: str) -> list[str]:
        """Уникальные значения второго уровня виртуального пути для arg1."""
        result: set[str] = set()
        for entry in self._index.values():
            parts = Path(entry["rel_path"]).parts
            if len(parts) >= 3 and parts[0] == arg1:
                result.add(parts[1])
        return sorted(result)

    def list_images(self, arg1: str, arg2: str, filter_str: str = "") -> list[dict]:
        """
        Список изображений для виртуального пути arg1/arg2/*.
        Читает ТОЛЬКО индекс — map-файлы не трогает.
        filter_str — подстрока для фильтрации по имени файла (из rel_path).

        Возвращает: [{map_name, rel_path, width, height}]
        """
        filter_lower = filter_str.lower()
        result = []
        for map_name, entry in sorted(self._index.items()):
            rel = entry["rel_path"]
            parts = Path(rel).parts
            if len(parts) < 3 or parts[0] != arg1 or parts[1] != arg2:
                continue
            filename = parts[-1]
            if filter_lower and filter_lower not in filename.lower() and filter_lower not in map_name.lower():
                continue
            result.append({
                "map_name": map_name,
                "rel_path": rel,
                "width": entry.get("width"),
                "height": entry.get("height"),
            })
        return result

    # ── utils ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        chunks = list((self.root / "chunks").rglob("*.png"))
        maps = list((self.root / "maps").rglob("*.json"))
        total_size = sum(f.stat().st_size for f in chunks)
        map_size = sum(f.stat().st_size for f in maps)
        return {
            "unique_chunks": len(chunks),
            "known_hashes_cached": len(self._known_hashes),
            "images_stored": len(maps),
            "chunks_mb": round(total_size / 1024 ** 2, 2),
            "maps_kb": round(map_size / 1024, 2),
            "total_mb": round((total_size + map_size) / 1024 ** 2, 2),
        }

    def verify_image(self, map_name: str) -> bool:
        mp = self._map_path(map_name)
        if not mp.exists():
            print(f"[verify] MAP NOT FOUND: {mp}")
            return False
        data = json.loads(mp.read_text(encoding="utf-8"))
        missing = [
            h for row in data["grid"] for h in row
            if not self._chunk_path(h).exists()
        ]
        if missing:
            print(f"[verify] MISSING {len(missing)} chunks: {missing[:5]}...")
            return False
        print(f"[verify] {map_name}: OK")
        return True


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys


    def usage():
        print(
            "Usage:\n"
            "  python chunk_store.py store         <store_dir> <image> [map_name]\n"
            "  python chunk_store.py restore       <store_dir> <map_name> <output_path>\n"
            "  python chunk_store.py restore-all   <store_dir> <output_dir> [--overwrite] [--workers N]\n"
            "  python chunk_store.py stream        <store_dir> <photos_dir> [--recursive] [--workers N] [--no-skip]\n"
            "  python chunk_store.py stats         <store_dir>\n"
            "  python chunk_store.py verify        <store_dir> <map_name>\n"
            "  python chunk_store.py rebuild-index <store_dir>\n"
        )


    if len(sys.argv) < 3:
        usage()
        sys.exit(1)

    cmd = sys.argv[1]
    store = ChunkStore(sys.argv[2])

    match cmd:
        case "store":
            store.store_image(sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)

        case "restore":
            store.restore_image(sys.argv[3], sys.argv[4])

        case "restore-all":
            rest = sys.argv[3:]
            output_dir = rest[0]
            overwrite = "--overwrite" in rest
            workers = 1
            if "--workers" in rest:
                workers = int(rest[rest.index("--workers") + 1])
            errors = 0
            for p in store.stream_restore_folder(output_dir, overwrite=overwrite, workers=workers):
                print(f"\r{p}", end="", flush=True)
                if p.error and p.error != "skipped":
                    errors += 1
            print(f"\n[done] errors={errors}")

        case "stream":
            rest = sys.argv[3:]
            photos_dir = rest[0]
            recursive = "--recursive" in rest
            skip = "--no-skip" not in rest
            workers = 1
            if "--workers" in rest:
                workers = int(rest[rest.index("--workers") + 1])
            last = None
            for progress in store.stream_folder(
                    photos_dir, recursive=recursive, skip_existing=skip, workers=workers
            ):
                print(f"\r{progress}", end="", flush=True)
                last = progress
            print()
            if last:
                print(f"\n[done] {last.stats}")
                for fp, e in last.stats.error_files:
                    print(f"  ⚠ {Path(fp).name}: {e}")

        case "stats":
            print(store.stats())

        case "verify":
            store.verify_image(sys.argv[3])

        case "rebuild-index":
            store.rebuild_index()

        case _:
            usage()

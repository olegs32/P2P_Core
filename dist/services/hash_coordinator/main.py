"""
Hash Coordinator Service - Координатор распределенных вычислений хешей

Функции:
- Генерация и распределение chunk batches для воркеров
- Динамическая адаптация размера чанков под производительность
- Отслеживание прогресса через gossip
- Восстановление orphaned chunks
- Версионирование batches
"""

import asyncio
import hashlib
import json
import logging
import statistics
import string
import time
import csv
import os
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Any

from layers.service import BaseService, service_method

# Import PCAP parser
try:
    from .pcap_parser import PCAPParser, parse_hccapx, parse_22000
except ImportError:
    PCAPParser = None
    parse_hccapx = None
    parse_22000 = None


@dataclass
class ChunkInfo:
    """Информация о чанке"""
    chunk_id: int
    start_index: int
    end_index: int
    chunk_size: int
    assigned_worker: str
    status: str  # assigned, working, solved, timeout
    priority: int
    created_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BatchInfo:
    """Информация о batch чанков"""
    version: int
    chunks: List[ChunkInfo]
    created_at: float
    is_recovery: bool = False

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "chunks": [c.to_dict() for c in self.chunks],
            "created_at": self.created_at,
            "is_recovery": self.is_recovery
        }


class PerformanceAnalyzer:
    """Анализ производительности воркеров"""

    def __init__(self, base_chunk_size: int = 1_000_000):
        self.base_chunk_size = base_chunk_size
        self.worker_speeds: Dict[str, float] = {}  # {worker_id: hashes/sec}
        self.worker_history: Dict[str, List[Dict]] = defaultdict(list)

    def update_worker_performance(self, worker_id: str, chunk_size: int, time_taken: float):
        """Обновляет метрики воркера"""
        if time_taken <= 0:
            return

        hash_rate = chunk_size / time_taken
        self.worker_speeds[worker_id] = hash_rate

        self.worker_history[worker_id].append({
            "chunk_size": chunk_size,
            "time_taken": time_taken,
            "hash_rate": hash_rate,
            "timestamp": time.time()
        })

        # Храним только последние 10 записей
        if len(self.worker_history[worker_id]) > 10:
            self.worker_history[worker_id] = self.worker_history[worker_id][-10:]

    def calculate_cluster_stats(self) -> dict:
        """Статистика по кластеру"""
        if not self.worker_speeds:
            return {
                "avg_speed": 0,
                "median_speed": 0,
                "total_speed": 0,
                "min_speed": 0,
                "max_speed": 0,
                "std_dev": 0
            }

        speeds = list(self.worker_speeds.values())

        return {
            "avg_speed": statistics.mean(speeds),
            "median_speed": statistics.median(speeds),
            "total_speed": sum(speeds),
            "min_speed": min(speeds),
            "max_speed": max(speeds),
            "std_dev": statistics.stdev(speeds) if len(speeds) > 1 else 0
        }

    def calculate_adaptive_chunk_size(self, worker_id: str) -> int:
        """Вычисляет адаптивный размер чанка для воркера"""
        worker_speed = self.worker_speeds.get(worker_id, 0)

        if worker_speed == 0:
            return self.base_chunk_size

        stats = self.calculate_cluster_stats()
        avg_speed = stats["avg_speed"]

        if avg_speed == 0:
            return self.base_chunk_size

        # Коэффициент: worker_speed / avg_speed
        speed_ratio = worker_speed / avg_speed

        # Ограничиваем: 0.5x - 2.0x
        speed_ratio = max(0.5, min(2.0, speed_ratio))

        adaptive_size = int(self.base_chunk_size * speed_ratio)

        # Округляем до 100k
        adaptive_size = (adaptive_size // 100_000) * 100_000
        adaptive_size = max(100_000, adaptive_size)  # Минимум 100k

        return adaptive_size


class DynamicChunkGenerator:
    """Динамическая генерация chunk batches"""

    def __init__(
        self,
        charset: str,
        length: int,
        base_chunk_size: int = 1_000_000,
        lookahead_batches: int = 10
    ):
        self.charset = charset
        self.length = length
        self.base = len(charset)
        self.total_combinations = self.base ** length

        self.base_chunk_size = base_chunk_size
        self.lookahead_batches = lookahead_batches

        # Состояние
        self.current_version = 0
        self.current_global_index = 0
        self.generated_batches: Dict[int, BatchInfo] = {}
        self.completed_batches = set()

        # Производительность
        self.performance = PerformanceAnalyzer(base_chunk_size)

        # Lock для генерации
        self._generation_lock = asyncio.Lock()

        # Логирование для отладки
        self.logger = logging.getLogger("DynamicChunkGenerator")

    def index_to_combination(self, idx: int) -> str:
        """Преобразует индекс в комбинацию символов"""
        result = []
        for _ in range(self.length):
            result.append(self.charset[idx % self.base])
            idx //= self.base
        return ''.join(reversed(result))

    async def ensure_lookahead_batches(self, active_workers: List[str]):
        """
        Гарантирует наличие lookahead чанков для каждого воркера

        Подсчитывает доступные (незавершенные) чанки для каждого воркера
        и генерирует новые батчи, пока у самого "голодного" воркера
        есть меньше lookahead_batches чанков в очереди.
        """
        import logging
        logger = logging.getLogger("DynamicChunkGenerator")

        # Не генерировать батчи если нет воркеров
        if not active_workers:
            logger.debug("No active workers - skipping batch generation")
            return

        # Для каждого воркера считаем доступные (незавершенные) чанки
        min_available_chunks = float('inf')
        worker_chunk_counts = {}

        for worker_id in active_workers:
            available_chunks = 0
            for batch in self.generated_batches.values():
                for chunk in batch.chunks:
                    if chunk.assigned_worker == worker_id and chunk.status in ("assigned", "working"):
                        available_chunks += 1

            worker_chunk_counts[worker_id] = available_chunks
            min_available_chunks = min(min_available_chunks, available_chunks)

        if min_available_chunks == float('inf'):
            min_available_chunks = 0

        # Генерируем батчи пока у самого "голодного" воркера меньше lookahead_batches чанков
        needed = self.lookahead_batches - min_available_chunks

        logger.debug(
            f"Lookahead check: worker_chunks={worker_chunk_counts}, "
            f"min_available={min_available_chunks}, needed={needed}, "
            f"progress={self.current_global_index}/{self.total_combinations}"
        )

        if needed > 0 and self.current_global_index < self.total_combinations:
            logger.info(f"Generating {needed} new batches (min worker chunks: {min_available_chunks}/{self.lookahead_batches})")
            for _ in range(needed):
                if self.current_global_index >= self.total_combinations:
                    break
                batch = await self._generate_next_batch(active_workers)
                if batch is None:
                    logger.warning("Failed to generate batch despite having active workers")
                    break

    async def _generate_next_batch(self, active_workers: List[str]) -> Optional[BatchInfo]:
        """Генерирует следующий batch"""
        if not active_workers:
            return None

        async with self._generation_lock:
            self.current_version += 1

            batch_chunks = []

            for worker_id in active_workers:
                if self.current_global_index >= self.total_combinations:
                    break

                # Адаптивный размер
                chunk_size = self.performance.calculate_adaptive_chunk_size(worker_id)

                # Ограничиваем оставшимся пространством
                remaining = self.total_combinations - self.current_global_index
                chunk_size = min(chunk_size, remaining)

                chunk = ChunkInfo(
                    chunk_id=self.current_version * 10000 + len(batch_chunks),
                    start_index=self.current_global_index,
                    end_index=self.current_global_index + chunk_size,
                    chunk_size=chunk_size,
                    assigned_worker=worker_id,
                    status="assigned",
                    priority=1,
                    created_at=time.time()
                )

                batch_chunks.append(chunk)
                self.current_global_index += chunk_size

            if not batch_chunks:
                return None

            batch = BatchInfo(
                version=self.current_version,
                chunks=batch_chunks,
                created_at=time.time(),
                is_recovery=False
            )

            self.generated_batches[self.current_version] = batch

            return batch

    async def recover_orphaned_chunks(
        self,
        orphaned: List[dict],
        active_workers: List[str]
    ) -> Optional[BatchInfo]:
        """Создает recovery batch из orphaned chunks"""
        if not orphaned or not active_workers:
            return None

        async with self._generation_lock:
            self.current_version += 1

            recovery_chunks = []

            for i, orphan in enumerate(orphaned):
                # Продолжаем с последнего прогресса
                start_idx = orphan.get("progress", orphan["start_index"]) + 1
                end_idx = orphan["end_index"]

                if start_idx >= end_idx:
                    continue  # Уже завершен

                chunk_size = end_idx - start_idx
                worker_id = active_workers[i % len(active_workers)]

                chunk = ChunkInfo(
                    chunk_id=self.current_version * 10000 + len(recovery_chunks),
                    start_index=start_idx,
                    end_index=end_idx,
                    chunk_size=chunk_size,
                    assigned_worker=worker_id,
                    status="recovery",
                    priority=5,
                    created_at=time.time()
                )

                recovery_chunks.append(chunk)

            if not recovery_chunks:
                return None

            batch = BatchInfo(
                version=self.current_version,
                chunks=recovery_chunks,
                created_at=time.time(),
                is_recovery=True
            )

            self.generated_batches[self.current_version] = batch

            return batch

    def mark_batch_completed(self, version: int):
        """Помечает batch как завершенный"""
        self.completed_batches.add(version)

        # Очищаем старые батчи
        if len(self.completed_batches) > 20:
            old_versions = sorted(self.completed_batches)[:-20]
            for v in old_versions:
                if v in self.generated_batches:
                    del self.generated_batches[v]
                self.completed_batches.discard(v)

    def chunk_completed(self, chunk_id: int, hash_count: int, solutions: List[dict]):
        """
        Помечает чанк как завершенный

        Args:
            chunk_id: ID чанка
            hash_count: Количество вычисленных хешей
            solutions: Найденные решения
        """
        # Находим чанк в батчах
        found = False
        batch_version = None

        # ДИАГНОСТИКА: Логируем попытку обновления
        self.logger.info(f"🔍 [DIAG] chunk_completed called for chunk_id={chunk_id}, hash_count={hash_count}, solutions={len(solutions)}")

        for batch in self.generated_batches.values():
            for chunk in batch.chunks:
                # Приводим к int для сравнения (может быть строкой из gossip)
                if int(chunk.chunk_id) == int(chunk_id):
                    old_status = chunk.status
                    chunk.status = "solved"
                    found = True
                    batch_version = batch.version

                    self.logger.info(f"✅ [DIAG] Chunk {chunk_id} status: {old_status} → solved (batch_version={batch_version})")

                    # Производительность обновляется в _process_worker_chunk_status
                    # где есть доступ к time_taken из gossip
                    break
            if found:
                break

        if not found:
            available_chunks = [(b.version, c.chunk_id, c.status) for b in self.generated_batches.values() for c in b.chunks]
            self.logger.warning(f"❌ [DIAG] Chunk {chunk_id} NOT FOUND in batches! Available: {available_chunks}")
            return

        # Проверяем, все ли чанки батча завершены
        if batch_version is not None:
            batch = self.generated_batches[batch_version]
            all_solved = all(chunk.status == "solved" for chunk in batch.chunks)

            if all_solved:
                # Помечаем батч как завершенный
                self.mark_batch_completed(batch_version)
                self.logger.info(f"Batch {batch_version} completed (all {len(batch.chunks)} chunks solved)")

    def chunk_failed(self, chunk_id: int):
        """
        Помечает чанк как проваленный (для переназначения)

        Args:
            chunk_id: ID чанка
        """
        # Находим чанк и помечаем как recovery
        for batch in self.generated_batches.values():
            for chunk in batch.chunks:
                if chunk.chunk_id == chunk_id:
                    chunk.status = "timeout"  # Будет переназначен как recovery
                    return

    def get_progress(self) -> dict:
        """Возвращает прогресс выполнения"""
        processed = 0
        in_progress = 0

        for batch in self.generated_batches.values():
            for chunk in batch.chunks:
                if chunk.status == "solved":
                    processed += chunk.chunk_size
                elif chunk.status in ("working", "assigned"):
                    in_progress += chunk.chunk_size

        progress_pct = (processed / self.total_combinations * 100) if self.total_combinations > 0 else 0

        cluster_stats = self.performance.calculate_cluster_stats()
        total_hash_rate = cluster_stats["total_speed"]

        remaining = self.total_combinations - processed
        eta_seconds = (remaining / total_hash_rate) if total_hash_rate > 0 else 0

        return {
            "total_combinations": self.total_combinations,
            "processed": processed,
            "in_progress": in_progress,
            "pending": remaining,
            "progress_percentage": progress_pct,
            "eta_seconds": eta_seconds,
            "current_version": self.current_version,
            "completed_batches": len(self.completed_batches),
            "active_batches": len(self.generated_batches) - len(self.completed_batches)
        }


class Run(BaseService):
    SERVICE_NAME = "hash_coordinator"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.1.0"
        self.info.description = "Координатор распределенных вычислений хешей"

        # Активные задачи
        self.active_jobs: Dict[str, DynamicChunkGenerator] = {}

        # Найденные решения для каждой задачи
        self.job_solutions: Dict[str, List[dict]] = {}  # {job_id: [solutions]}

        # Состояние воркеров из gossip
        self.worker_states: Dict[str, dict] = {}

        # Background tasks
        self.monitor_task = None
        self.orphaned_detection_task = None

    async def initialize(self):
        """Инициализация сервиса"""
        # Только на координаторе
        if not self.context.config.coordinator_mode:
            self.logger.info("Hash coordinator disabled on worker node")
            return

        self.logger.info("Hash coordinator initialized")

        # Запускаем мониторинг
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        self.orphaned_detection_task = asyncio.create_task(self._orphaned_detection_loop())

    async def cleanup(self):
        """Очистка ресурсов"""
        if self.monitor_task:
            self.monitor_task.cancel()
        if self.orphaned_detection_task:
            self.orphaned_detection_task.cancel()

    @service_method(description="Создать новую задачу вычисления хешей", public=True)
    async def create_job(
        self,
        job_id: str,
        mode: str = "brute",  # "brute" or "dictionary"
        charset: Optional[str] = None,
        length: Optional[int] = None,
        wordlist: Optional[List[str]] = None,
        mutations: Optional[List[str]] = None,
        hash_algo: str = "sha256",
        target_hash: Optional[str] = None,
        target_hashes: Optional[List[str]] = None,  # Multi-target mode
        ssid: Optional[str] = None,  # For WPA/WPA2
        base_chunk_size: int = 1_000_000,
        lookahead_batches: int = 10  # Number of batches to generate ahead
    ) -> Dict[str, Any]:
        """
        Создает новую задачу вычисления хешей

        Args:
            job_id: Уникальный ID задачи
            mode: Режим работы ("brute" или "dictionary")
            charset: Набор символов для перебора (для brute mode)
            length: Длина комбинации (для brute mode)
            wordlist: Список слов (для dictionary mode)
            mutations: Правила мутации (для dictionary mode)
            hash_algo: Алгоритм хеширования
            target_hash: Целевой хеш (опционально)
            target_hashes: Список целевых хешей (multi-target mode)
            ssid: SSID для WPA/WPA2 cracking
            base_chunk_size: Базовый размер чанка
            lookahead_batches: Количество батчей генерируемых наперед (default: 10)
        """
        if job_id in self.active_jobs:
            return {"success": False, "error": "Job already exists"}

        # Валидация параметров
        if mode == "brute":
            if not charset or not length:
                return {"success": False, "error": "charset and length required for brute mode"}

            # Создаем генератор для brute force
            generator = DynamicChunkGenerator(
                charset=charset,
                length=length,
                base_chunk_size=base_chunk_size,
                lookahead_batches=lookahead_batches
            )
            total_items = generator.total_combinations

        elif mode == "dictionary":
            if not wordlist:
                return {"success": False, "error": "wordlist required for dictionary mode"}

            # Для dictionary mode используем количество слов как total
            total_items = len(wordlist)

            # Создаем генератор с фиктивным charset
            generator = DynamicChunkGenerator(
                charset="a",  # Dummy
                length=1,
                base_chunk_size=base_chunk_size,
                lookahead_batches=lookahead_batches
            )
            generator.total_combinations = total_items

        else:
            return {"success": False, "error": f"Unknown mode: {mode}"}

        self.active_jobs[job_id] = generator
        self.job_solutions[job_id] = []  # Инициализируем пустой список решений

        # Получаем активных воркеров из gossip
        active_workers = await self._get_active_workers()

        # Генерируем начальные батчи
        await generator.ensure_lookahead_batches(active_workers)

        # Публикуем job metadata в gossip
        await self._publish_job_metadata_v2(
            job_id=job_id,
            mode=mode,
            charset=charset,
            length=length,
            wordlist=wordlist,
            mutations=mutations,
            hash_algo=hash_algo,
            target_hash=target_hash,
            target_hashes=target_hashes,
            ssid=ssid
        )

        # Публикуем первые батчи
        await self._publish_batches(job_id, generator)

        return {
            "success": True,
            "job_id": job_id,
            "mode": mode,
            "total_combinations": total_items,
            "initial_batches": generator.current_version
        }

    @service_method(description="Получить статус задачи", public=True)
    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Возвращает статус задачи"""
        if job_id not in self.active_jobs:
            return {"success": False, "error": "Job not found"}

        generator = self.active_jobs[job_id]
        progress = generator.get_progress()
        cluster_stats = generator.performance.calculate_cluster_stats()
        solutions = self.job_solutions.get(job_id, [])

        return {
            "success": True,
            "job_id": job_id,
            "progress": progress,
            "cluster_stats": cluster_stats,
            "worker_speeds": generator.performance.worker_speeds,
            "solutions": solutions,
            "solutions_count": len(solutions)
        }

    @service_method(description="Получить список всех задач", public=True)
    async def get_all_jobs(self) -> Dict[str, Any]:
        """Возвращает список всех активных задач"""
        jobs = []

        for job_id, generator in self.active_jobs.items():
            progress = generator.get_progress()
            jobs.append({
                "job_id": job_id,
                "progress_percentage": progress["progress_percentage"],
                "processed": progress["processed"],
                "total": progress["total_combinations"],
                "eta_seconds": progress["eta_seconds"]
            })

        return {"success": True, "jobs": jobs}

    @service_method(description="Отчет о найденных решениях от воркера", public=True)
    async def report_solution(
        self,
        job_id: str,
        chunk_id: int,
        worker_id: str,
        solutions: List[dict]
    ) -> Dict[str, Any]:
        """
        Принимает уведомление о найденных решениях от воркера

        Args:
            job_id: ID задачи
            chunk_id: ID чанка
            worker_id: ID воркера
            solutions: Список найденных решений

        Returns:
            Подтверждение получения
        """
        if job_id not in self.active_jobs:
            return {"success": False, "error": f"Job {job_id} not found"}

        self.logger.warning(f"Worker {worker_id} found {len(solutions)} solutions in job {job_id}, chunk {chunk_id}!")
        for sol in solutions:
            self.logger.warning(f"  Solution: {sol.get('combination')} → {sol.get('hash')}")

        # Сохраняем решения
        if job_id not in self.job_solutions:
            self.job_solutions[job_id] = []

        # Добавляем новые решения (избегаем дубликатов по hash)
        existing_hashes = {sol.get('hash') for sol in self.job_solutions[job_id]}
        for sol in solutions:
            if sol.get('hash') not in existing_hashes:
                self.job_solutions[job_id].append(sol)
                existing_hashes.add(sol.get('hash'))

        return {
            "success": True,
            "job_id": job_id,
            "solutions_count": len(solutions),
            "total_solutions": len(self.job_solutions[job_id]),
            "acknowledged": True
        }

    @service_method(description="Импорт WPA handshake из PCAP", public=True)
    async def import_pcap(
        self,
        pcap_file: str,
        job_id_prefix: str = "wpa"
    ) -> Dict[str, Any]:
        """
        Импортирует WiFi handshakes из PCAP файла и создает задачи

        Args:
            pcap_file: Путь к PCAP файлу
            job_id_prefix: Префикс для job_id

        Returns:
            Список созданных задач
        """
        if PCAPParser is None:
            return {"success": False, "error": "PCAP parser not available"}

        try:
            parser = PCAPParser(pcap_file)
            handshakes = parser.parse()

            if not handshakes:
                return {"success": False, "error": "No handshakes found in PCAP"}

            created_jobs = []

            for i, hs in enumerate(handshakes):
                job_id = f"{job_id_prefix}_{hs['bssid'].replace(':', '')}_{i}"
                essid = hs['essid'] or f"unknown_{i}"

                # Создаем job для WPA cracking
                # Note: требуется словарь для dictionary attack
                # или charset для brute force

                created_jobs.append({
                    "job_id": job_id,
                    "bssid": hs['bssid'],
                    "essid": essid,
                    "eapol_frames": len(hs['eapol_frames'])
                })

            return {
                "success": True,
                "handshakes_found": len(handshakes),
                "jobs": created_jobs
            }

        except Exception as e:
            self.logger.error(f"Failed to import PCAP: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Экспорт результатов в JSON/CSV", public=True)
    async def export_results(
        self,
        job_id: str,
        format: str = "json",  # "json" or "csv"
        output_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Экспортирует результаты задачи в файл

        Args:
            job_id: ID задачи
            format: Формат экспорта ("json" или "csv")
            output_file: Путь к файлу (если None, возвращает данные)

        Returns:
            Результаты или путь к файлу
        """
        if job_id not in self.active_jobs:
            return {"success": False, "error": "Job not found"}

        generator = self.active_jobs[job_id]
        progress = generator.get_progress()

        # Получаем сохраненные решения
        solutions = self.job_solutions.get(job_id, [])

        # Формируем результаты
        results = {
            "job_id": job_id,
            "progress": progress,
            "solutions": solutions,
            "solutions_count": len(solutions),
            "exported_at": datetime.now().isoformat()
        }

        if output_file is None:
            # Возвращаем данные напрямую
            return {
                "success": True,
                "format": format,
                "data": results
            }

        try:
            # Экспорт в файл
            if format == "json":
                with open(output_file, 'w') as f:
                    json.dump(results, f, indent=2)

            elif format == "csv":
                with open(output_file, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=['combination', 'hash', 'index'])
                    writer.writeheader()
                    for sol in solutions:
                        writer.writerow(sol)

            else:
                return {"success": False, "error": f"Unknown format: {format}"}

            return {
                "success": True,
                "format": format,
                "output_file": output_file,
                "solutions_count": len(solutions)
            }

        except Exception as e:
            self.logger.error(f"Failed to export results: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Обновление от воркера", public=True)
    async def report_chunk_progress(
        self,
        job_id: str,
        worker_id: str,
        chunk_id: int,
        status: str,
        progress: Optional[int] = None,
        time_taken: Optional[float] = None,
        solutions: Optional[List[dict]] = None
    ) -> Dict[str, Any]:
        """
        Воркер отчитывается о прогрессе

        Примечание: В основном используется gossip, но этот метод
        может быть полезен для явных обновлений
        """
        if job_id not in self.active_jobs:
            return {"success": False, "error": "Job not found"}

        generator = self.active_jobs[job_id]

        # Обновляем производительность
        if status == "solved" and time_taken:
            for batch in generator.generated_batches.values():
                for chunk in batch.chunks:
                    if chunk.chunk_id == chunk_id:
                        generator.performance.update_worker_performance(
                            worker_id,
                            chunk.chunk_size,
                            time_taken
                        )
                        chunk.status = "solved"
                        break

        return {"success": True}

    async def _get_active_workers(self) -> List[str]:
        """Получает список активных воркеров из gossip"""
        network = self.context.get_shared("network")
        if not network:
            return []

        nodes = network.gossip.node_registry

        # Фильтруем воркеров с сервисом hash_worker
        workers = []
        for node_id, node_info in nodes.items():
            if node_info.role == "worker":
                # Проверяем наличие hash_worker в сервисах
                if "hash_worker" in node_info.services:
                    workers.append(node_id)

        return workers

    async def _publish_job_metadata(
        self,
        job_id: str,
        charset: str,
        length: int,
        hash_algo: str,
        target_hash: Optional[str]
    ):
        """Публикует метаданные задачи в gossip (legacy)"""
        await self._publish_job_metadata_v2(
            job_id=job_id,
            mode="brute",
            charset=charset,
            length=length,
            hash_algo=hash_algo,
            target_hash=target_hash
        )

    async def _publish_job_metadata_v2(
        self,
        job_id: str,
        mode: str,
        charset: Optional[str] = None,
        length: Optional[int] = None,
        wordlist: Optional[List[str]] = None,
        mutations: Optional[List[str]] = None,
        hash_algo: str = "sha256",
        target_hash: Optional[str] = None,
        target_hashes: Optional[List[str]] = None,
        ssid: Optional[str] = None
    ):
        """Публикует метаданные задачи в gossip (v2 с новыми параметрами)"""
        network = self.context.get_shared("network")
        if not network:
            return

        job_metadata = {
            "job_id": job_id,
            "mode": mode,
            "charset": charset,
            "length": length,
            "wordlist": wordlist,
            "mutations": mutations,
            "hash_algo": hash_algo,
            "target_hash": target_hash,
            "target_hashes": target_hashes,
            "ssid": ssid,
            "started_at": time.time()
        }

        # Use new versioned update_metadata API
        network.gossip.update_metadata(f"hash_job_{job_id}", job_metadata)

    def _merge_batch_statuses(self, current_batches: dict, new_batches: dict) -> dict:
        """
        Мержит статусы чанков в batches с приоритетом solved > working > recovery > assigned

        Args:
            current_batches: Текущие batches из gossip
            new_batches: Новые batches для публикации

        Returns:
            Замерженные batches
        """
        # Приоритеты статусов (выше = важнее)
        status_priority = {
            "solved": 4,
            "working": 3,
            "recovery": 2,
            "timeout": 1,
            "assigned": 0
        }

        merged = {}

        # Объединяем все версии из обоих источников
        all_versions = set(current_batches.keys()) | set(new_batches.keys())

        for version in all_versions:
            current_batch = current_batches.get(version, {})
            new_batch = new_batches.get(version, {})

            # Если версия только в одном источнике - берем как есть
            if not current_batch:
                merged[version] = new_batch
                continue
            if not new_batch:
                merged[version] = current_batch
                continue

            # Мержим chunks
            current_chunks = current_batch.get("chunks", {})
            new_chunks = new_batch.get("chunks", {})

            merged_chunks = {}
            all_chunk_ids = set(current_chunks.keys()) | set(new_chunks.keys())

            for chunk_id in all_chunk_ids:
                current_chunk = current_chunks.get(chunk_id)
                new_chunk = new_chunks.get(chunk_id)

                # Если chunk только в одном источнике
                if not current_chunk:
                    merged_chunks[chunk_id] = new_chunk
                    continue
                if not new_chunk:
                    merged_chunks[chunk_id] = current_chunk
                    continue

                # Оба chunk есть - выбираем по приоритету статуса
                current_status = current_chunk.get("status", "assigned")
                new_status = new_chunk.get("status", "assigned")

                current_priority = status_priority.get(current_status, 0)
                new_priority = status_priority.get(new_status, 0)

                if new_priority >= current_priority:
                    merged_chunks[chunk_id] = new_chunk
                else:
                    merged_chunks[chunk_id] = current_chunk

            # Формируем замерженный batch
            merged[version] = {
                "chunks": merged_chunks,
                "created_at": new_batch.get("created_at", current_batch.get("created_at")),
                "is_recovery": new_batch.get("is_recovery", current_batch.get("is_recovery", False))
            }

        return merged

    async def _publish_batches(self, job_id: str, generator: DynamicChunkGenerator):
        """Публикует batches в gossip с мержем текущего состояния"""
        network = self.context.get_shared("network")
        if not network:
            return

        # Публикуем только незавершенные батчи
        new_batches = {}

        # ДИАГНОСТИКА: Счетчики статусов ДО мержа
        new_status_counts = {"assigned": 0, "working": 0, "solved": 0, "recovery": 0, "timeout": 0}

        for version, batch in generator.generated_batches.items():
            if version not in generator.completed_batches:
                # Формируем структуру: {chunk_id: {assigned_worker: data}}
                chunks_dict = {}
                for chunk in batch.chunks:
                    chunks_dict[chunk.chunk_id] = {
                        "assigned_worker": chunk.assigned_worker,
                        "start_index": chunk.start_index,
                        "end_index": chunk.end_index,
                        "chunk_size": chunk.chunk_size,
                        "status": chunk.status,
                        "priority": chunk.priority
                    }

                    # ДИАГНОСТИКА: Считаем статусы
                    new_status_counts[chunk.status] = new_status_counts.get(chunk.status, 0) + 1

                new_batches[version] = {
                    "chunks": chunks_dict,
                    "created_at": batch.created_at,
                    "is_recovery": batch.is_recovery
                }

        # Читаем текущее состояние из gossip
        batches_key = f"hash_batches_{job_id}"
        current_batches = network.gossip.node_registry[network.gossip.node_id].metadata.get(batches_key, {})

        # МЕРЖИМ с текущим состоянием (solved имеет приоритет!)
        merged_batches = self._merge_batch_statuses(current_batches, new_batches)

        # ДИАГНОСТИКА: Считаем статусы ПОСЛЕ мержа
        merged_status_counts = {"assigned": 0, "working": 0, "solved": 0, "recovery": 0, "timeout": 0}
        for batch_data in merged_batches.values():
            for chunk_data in batch_data.get("chunks", {}).values():
                status = chunk_data.get("status", "assigned")
                merged_status_counts[status] = merged_status_counts.get(status, 0) + 1

        # ДИАГНОСТИКА: Логируем мерж
        if new_status_counts != merged_status_counts:
            self.logger.info(
                f"🔀 [DIAG] Merged batches for {job_id}: "
                f"new={new_status_counts} + current={len(current_batches)} versions → "
                f"merged={merged_status_counts}"
            )
        else:
            self.logger.info(f"📤 [DIAG] Publishing batches for {job_id}: {len(merged_batches)} batches, statuses: {merged_status_counts}")

        # Use new versioned update_metadata API
        network.gossip.update_metadata(batches_key, merged_batches)

    async def _monitor_loop(self):
        """Мониторинг состояния задач"""
        while True:
            try:
                await asyncio.sleep(10)  # Каждые 10 секунд

                # Обновляем состояние воркеров из gossip
                await self._update_worker_states()

                # Для каждой задачи
                for job_id, generator in self.active_jobs.items():
                    # Получаем активных воркеров
                    active_workers = await self._get_active_workers()

                    # Гарантируем lookahead батчи
                    await generator.ensure_lookahead_batches(active_workers)

                    # Публикуем обновленные батчи
                    await self._publish_batches(job_id, generator)

                    # Проверяем завершение
                    if generator.current_global_index >= generator.total_combinations:
                        progress = generator.get_progress()
                        if progress["pending"] == 0 and progress["in_progress"] == 0:
                            self.logger.info(f"Job {job_id} completed!")
                            # TODO: Сохранить результаты

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in monitor loop: {e}")

    async def _orphaned_detection_loop(self):
        """Обнаружение orphaned chunks"""
        while True:
            try:
                await asyncio.sleep(60)  # Каждую минуту

                for job_id, generator in self.active_jobs.items():
                    orphaned = await self._detect_orphaned_chunks(generator)

                    if orphaned:
                        self.logger.warning(f"Detected {len(orphaned)} orphaned chunks in {job_id}")

                        # Восстанавливаем
                        active_workers = await self._get_active_workers()
                        recovery_batch = await generator.recover_orphaned_chunks(
                            orphaned,
                            active_workers
                        )

                        if recovery_batch:
                            await self._publish_batches(job_id, generator)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in orphaned detection: {e}")

    async def _detect_orphaned_chunks(self, generator: DynamicChunkGenerator) -> List[dict]:
        """Обнаруживает orphaned chunks"""
        orphaned = []
        timeout_threshold = 300  # 5 минут
        now = time.time()

        for batch in generator.generated_batches.values():
            for chunk in batch.chunks:
                if chunk.status == "working":
                    age = now - chunk.created_at

                    if age > timeout_threshold:
                        # Проверяем: есть ли более новые решенные чанки?
                        has_newer_solved = any(
                            c.chunk_id > chunk.chunk_id and c.status == "solved"
                            for b in generator.generated_batches.values()
                            for c in b.chunks
                            if c.assigned_worker == chunk.assigned_worker
                        )

                        if has_newer_solved:
                            orphaned.append({
                                "chunk_id": chunk.chunk_id,
                                "start_index": chunk.start_index,
                                "end_index": chunk.end_index,
                                "progress": chunk.start_index,  # Без инфо о прогрессе
                                "stuck_worker": chunk.assigned_worker,
                                "age": age
                            })

        return orphaned

    async def _update_worker_states(self):
        """Обновляет состояния воркеров из gossip и обрабатывает завершенные чанки"""
        network = self.context.get_shared("network")
        if not network:
            return

        nodes = network.gossip.node_registry

        for node_id, node_info in nodes.items():
            if node_id in self.worker_states:
                # Обновляем существующие
                self.worker_states[node_id].update({
                    "last_seen": node_info.last_seen,
                    "status": node_info.status
                })
            else:
                # Добавляем новые
                self.worker_states[node_id] = {
                    "node_id": node_id,
                    "last_seen": node_info.last_seen,
                    "status": node_info.status
                }

            # Обрабатываем hash_worker_status из metadata
            worker_status = node_info.metadata.get("hash_worker_status")
            if worker_status and isinstance(worker_status, dict):
                await self._process_worker_chunk_status(node_id, worker_status)

    async def _process_worker_chunk_status(self, worker_id: str, status: dict):
        """
        Обрабатывает статус чанка от воркера из gossip

        Args:
            worker_id: ID воркера
            status: Данные из hash_worker_status
        """
        job_id = status.get("job_id")
        chunk_id = status.get("chunk_id")
        chunk_status = status.get("status")

        if not job_id or chunk_id is None:
            return

        # Проверяем что задача активна
        if job_id not in self.active_jobs:
            return

        generator = self.active_jobs[job_id]

        # ДИАГНОСТИКА: Логируем все статусы от воркеров
        self.logger.info(f"🔍 [DIAG] Worker {worker_id} reported chunk {chunk_id} status: {chunk_status}")

        # Обрабатываем статус "solved" - чанк завершен
        if chunk_status == "solved":
            hash_count = status.get("hash_count", 0)
            time_taken = status.get("time_taken", 0)
            solutions = status.get("solutions", [])

            # Обновляем статус чанка в generator
            generator.chunk_completed(chunk_id, hash_count, solutions)

            # Обновляем производительность воркера
            if time_taken > 0:
                # Находим chunk_size для расчета производительности
                chunk_size = 0
                for batch in generator.generated_batches.values():
                    for chunk in batch.chunks:
                        if int(chunk.chunk_id) == int(chunk_id):
                            chunk_size = chunk.chunk_size
                            break
                    if chunk_size > 0:
                        break

                if chunk_size > 0:
                    generator.performance.update_worker_performance(
                        worker_id,
                        chunk_size,
                        time_taken
                    )

            if solutions:
                self.logger.warning(f"Worker {worker_id} found {len(solutions)} solutions in chunk {chunk_id}!")
                for sol in solutions:
                    self.logger.warning(f"  Solution: {sol.get('combination')} → {sol.get('hash')}")

                # Сохраняем решения
                if job_id not in self.job_solutions:
                    self.job_solutions[job_id] = []

                # Добавляем новые решения (избегаем дубликатов по hash)
                existing_hashes = {sol.get('hash') for sol in self.job_solutions[job_id]}
                for sol in solutions:
                    if sol.get('hash') not in existing_hashes:
                        self.job_solutions[job_id].append(sol)
                        existing_hashes.add(sol.get('hash'))

            # Генерируем новые батчи если нужно (lookahead)
            active_workers = await self._get_active_workers()
            await generator.ensure_lookahead_batches(active_workers)

            # ВАЖНО: Публикуем обновленные batches в gossip
            await self._publish_batches(job_id, generator)

        # Обрабатываем статус "working" - обновляем прогресс
        elif chunk_status == "working":
            progress = status.get("progress")
            if progress is not None:
                # Можно обновить прогресс чанка, но пока не критично
                pass

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
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Any

from layers.service import BaseService, service_method


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
        lookahead_batches: int = 3
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

    def index_to_combination(self, idx: int) -> str:
        """Преобразует индекс в комбинацию символов"""
        result = []
        for _ in range(self.length):
            result.append(self.charset[idx % self.base])
            idx //= self.base
        return ''.join(reversed(result))

    async def ensure_lookahead_batches(self, active_workers: List[str]):
        """Гарантирует наличие lookahead батчей"""
        pending_count = len(self.generated_batches) - len(self.completed_batches)

        needed = self.lookahead_batches - pending_count

        if needed > 0 and self.current_global_index < self.total_combinations:
            for _ in range(needed):
                if self.current_global_index >= self.total_combinations:
                    break
                await self._generate_next_batch(active_workers)

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
        self.info.version = "1.0.0"
        self.info.description = "Координатор распределенных вычислений хешей"

        # Активные задачи
        self.active_jobs: Dict[str, DynamicChunkGenerator] = {}

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
        charset: str,
        length: int,
        hash_algo: str = "sha256",
        target_hash: Optional[str] = None,
        base_chunk_size: int = 1_000_000
    ) -> Dict[str, Any]:
        """
        Создает новую задачу вычисления хешей

        Args:
            job_id: Уникальный ID задачи
            charset: Набор символов для перебора
            length: Длина комбинации
            hash_algo: Алгоритм хеширования
            target_hash: Целевой хеш (опционально)
            base_chunk_size: Базовый размер чанка
        """
        if job_id in self.active_jobs:
            return {"success": False, "error": "Job already exists"}

        # Создаем генератор
        generator = DynamicChunkGenerator(
            charset=charset,
            length=length,
            base_chunk_size=base_chunk_size,
            lookahead_batches=3
        )

        self.active_jobs[job_id] = generator

        # Получаем активных воркеров из gossip
        active_workers = await self._get_active_workers()

        # Генерируем начальные батчи
        await generator.ensure_lookahead_batches(active_workers)

        # Публикуем job metadata в gossip
        await self._publish_job_metadata(job_id, charset, length, hash_algo, target_hash)

        # Публикуем первые батчи
        await self._publish_batches(job_id, generator)

        return {
            "success": True,
            "job_id": job_id,
            "total_combinations": generator.total_combinations,
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

        return {
            "success": True,
            "job_id": job_id,
            "progress": progress,
            "cluster_stats": cluster_stats,
            "worker_speeds": generator.performance.worker_speeds
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
        """Публикует метаданные задачи в gossip"""
        network = self.context.get_shared("network")
        if not network:
            return

        metadata = {
            f"hash_job_{job_id}": {
                "job_id": job_id,
                "charset": charset,
                "length": length,
                "hash_algo": hash_algo,
                "target_hash": target_hash,
                "started_at": time.time()
            }
        }

        network.gossip.update_metadata(metadata)

    async def _publish_batches(self, job_id: str, generator: DynamicChunkGenerator):
        """Публикует batches в gossip"""
        network = self.context.get_shared("network")
        if not network:
            return

        # Публикуем только незавершенные батчи
        active_batches = {}

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

                active_batches[version] = {
                    "chunks": chunks_dict,
                    "created_at": batch.created_at,
                    "is_recovery": batch.is_recovery
                }

        metadata = {
            f"hash_batches_{job_id}": active_batches
        }

        network.gossip.update_metadata(metadata)

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
        """Обновляет состояния воркеров из gossip"""
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

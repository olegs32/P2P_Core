"""
Hash Worker Service - Воркер для распределенных вычислений хешей

Функции:
- Получение chunk batches через gossip
- Высокопроизводительное вычисление хешей
- Отчетность о прогрессе через gossip
- Автоматический claim чанков без коллизий
"""

import asyncio
import hashlib
import logging
import time
from typing import Dict, List, Optional, Any

from layers.service import BaseService, service_method


class HashComputer:
    """Оптимизированное вычисление хешей"""

    def __init__(self, charset: str, length: int, hash_algo: str = "sha256"):
        self.charset = charset
        self.charset_list = list(charset)  # Предрасчитанный список
        self.length = length
        self.base = len(charset)
        self.hash_algo = hash_algo

        # Предрасчет для оптимизации
        self.powers_of_base = [self.base ** i for i in range(length)]

    def index_to_combination(self, idx: int) -> str:
        """
        Оптимизированное преобразование индекс → комбинация
        Минимум операций, максимум скорость
        """
        result = [None] * self.length

        for pos in range(self.length - 1, -1, -1):
            result[pos] = self.charset_list[idx % self.base]
            idx //= self.base

        return ''.join(result)

    def compute_chunk(
        self,
        start_index: int,
        end_index: int,
        target_hash: Optional[str] = None,
        progress_callback=None,
        progress_interval: int = 10000
    ) -> Dict[str, Any]:
        """
        Вычисляет хеши для диапазона с максимальной производительностью

        Args:
            start_index: Начальный индекс
            end_index: Конечный индекс
            target_hash: Целевой хеш (опционально)
            progress_callback: Функция для отчета о прогрессе
            progress_interval: Интервал отчетов (итераций)

        Returns:
            Результаты вычислений
        """
        start_time = time.time()
        hash_count = 0
        solutions = []

        # Предрасчет для сравнения
        target_hash_bytes = bytes.fromhex(target_hash) if target_hash else None

        # Основной цикл - БЕЗ ПРОВЕРОК для максимальной скорости
        batch_size = progress_interval
        current_idx = start_index

        while current_idx < end_index:
            batch_end = min(current_idx + batch_size, end_index)

            # Вычисления БЕЗ проверок
            for idx in range(current_idx, batch_end):
                combination = self.index_to_combination(idx)

                # Вычисляем хеш
                if target_hash_bytes:
                    # digest() быстрее hexdigest()
                    hash_bytes = hashlib.new(self.hash_algo, combination.encode()).digest()

                    if hash_bytes == target_hash_bytes:
                        # НАЙДЕНО!
                        hash_hex = hash_bytes.hex()
                        solutions.append({
                            "combination": combination,
                            "hash": hash_hex,
                            "index": idx
                        })
                else:
                    # Просто вычисляем (для статистики)
                    _ = hashlib.new(self.hash_algo, combination.encode()).digest()

                hash_count += 1

            current_idx = batch_end

            # Отчет о прогрессе (только после батча)
            if progress_callback:
                progress_callback(current_idx, hash_count)

        time_taken = time.time() - start_time
        hash_rate = hash_count / time_taken if time_taken > 0 else 0

        return {
            "hash_count": hash_count,
            "time_taken": time_taken,
            "hash_rate": hash_rate,
            "solutions": solutions,
            "start_index": start_index,
            "end_index": end_index
        }


class Run(BaseService):
    SERVICE_NAME = "hash_worker"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "1.0.0"
        self.info.description = "Воркер распределенных вычислений хешей"

        # Текущая задача
        self.current_job_id: Optional[str] = None
        self.current_chunk_id: Optional[int] = None

        # Метрики
        self.total_hashes_computed = 0
        self.completed_chunks = 0

        # Background tasks
        self.worker_task = None

        # Флаг работы
        self.running = False

    async def initialize(self):
        """Инициализация сервиса"""
        # Только на воркерах
        if self.context.config.coordinator_mode:
            self.logger.info("Hash worker disabled on coordinator node")
            return

        self.logger.info("Hash worker initialized")
        self.running = True

        # Запускаем основной цикл
        self.worker_task = asyncio.create_task(self._worker_loop())

    async def cleanup(self):
        """Очистка ресурсов"""
        self.running = False
        if self.worker_task:
            self.worker_task.cancel()

    async def _worker_loop(self):
        """Основной цикл воркера"""
        while self.running:
            try:
                await asyncio.sleep(5)  # Проверяем каждые 5 секунд

                # Получаем активные задачи из gossip
                jobs = await self._get_active_jobs()

                for job_id in jobs:
                    # Получаем доступные чанки
                    chunk = await self._get_available_chunk(job_id)

                    if chunk:
                        # Обрабатываем чанк
                        await self._process_chunk(job_id, chunk)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in worker loop: {e}")

    async def _get_active_jobs(self) -> List[str]:
        """Получает список активных задач из gossip"""
        network = self.context.get_shared("network")
        if not network:
            return []

        # Ищем hash_job_* в metadata координатора
        coordinator_nodes = [
            node for node in network.gossip.node_registry.values()
            if node.role == "coordinator"
        ]

        if not coordinator_nodes:
            return []

        coordinator = coordinator_nodes[0]
        metadata = coordinator.metadata

        jobs = []
        for key in metadata.keys():
            if key.startswith("hash_job_"):
                job_id = key.replace("hash_job_", "")
                jobs.append(job_id)

        return jobs

    async def _get_available_chunk(self, job_id: str) -> Optional[dict]:
        """
        Получает доступный чанк для обработки из gossip

        Логика: каждому воркеру предназначен свой чанк в структуре
        {ver: 5, chunks: {6: {assigned_worker: worker_id, ...}, 7: {...}}}
        """
        network = self.context.get_shared("network")
        if not network:
            return None

        # Получаем батчи из gossip
        coordinator_nodes = [
            node for node in network.gossip.node_registry.values()
            if node.role == "coordinator"
        ]

        if not coordinator_nodes:
            return None

        coordinator = coordinator_nodes[0]
        metadata = coordinator.metadata

        batches_key = f"hash_batches_{job_id}"
        if batches_key not in metadata:
            return None

        batches = metadata[batches_key]
        my_worker_id = self.context.config.node_id

        # Ищем чанк для меня
        for version, batch_data in batches.items():
            chunks = batch_data.get("chunks", {})

            for chunk_id, chunk_data in chunks.items():
                if chunk_data.get("assigned_worker") == my_worker_id:
                    # Проверяем статус
                    status = chunk_data.get("status", "assigned")

                    if status in ("assigned", "recovery"):
                        # Это мой чанк, можно брать
                        return {
                            "job_id": job_id,
                            "version": version,
                            "chunk_id": chunk_id,
                            "start_index": chunk_data["start_index"],
                            "end_index": chunk_data["end_index"],
                            "chunk_size": chunk_data["chunk_size"]
                        }

        return None

    async def _process_chunk(self, job_id: str, chunk: dict):
        """Обрабатывает чанк"""
        chunk_id = chunk["chunk_id"]
        start_index = chunk["start_index"]
        end_index = chunk["end_index"]

        self.logger.info(
            f"Processing chunk {chunk_id} for job {job_id}: "
            f"{start_index} - {end_index} ({chunk['chunk_size']} hashes)"
        )

        self.current_job_id = job_id
        self.current_chunk_id = chunk_id

        # Получаем параметры задачи
        job_metadata = await self._get_job_metadata(job_id)
        if not job_metadata:
            self.logger.error(f"Job metadata not found for {job_id}")
            return

        charset = job_metadata["charset"]
        length = job_metadata["length"]
        hash_algo = job_metadata.get("hash_algo", "sha256")
        target_hash = job_metadata.get("target_hash")

        # Создаем компьютер
        computer = HashComputer(charset, length, hash_algo)

        # Публикуем статус "working" в gossip
        await self._publish_chunk_status(job_id, chunk_id, "working", start_index)

        # Callback для прогресса
        last_progress_time = time.time()

        def progress_callback(current_idx, hash_count):
            nonlocal last_progress_time
            now = time.time()

            # Публикуем прогресс раз в 10 секунд
            if now - last_progress_time >= 10:
                asyncio.create_task(
                    self._publish_chunk_progress(job_id, chunk_id, current_idx)
                )
                last_progress_time = now

        # Вычисляем!
        start_time = time.time()
        result = computer.compute_chunk(
            start_index,
            end_index,
            target_hash,
            progress_callback=progress_callback,
            progress_interval=10000
        )
        time_taken = time.time() - start_time

        # Обновляем метрики
        self.total_hashes_computed += result["hash_count"]
        self.completed_chunks += 1

        # Публикуем результат
        await self._publish_chunk_completed(
            job_id,
            chunk_id,
            result["hash_count"],
            time_taken,
            result["solutions"]
        )

        self.logger.info(
            f"Completed chunk {chunk_id}: {result['hash_count']} hashes in "
            f"{time_taken:.2f}s ({result['hash_rate']:.0f} h/s)"
        )

        if result["solutions"]:
            self.logger.warning(f"FOUND {len(result['solutions'])} SOLUTIONS!")
            for sol in result["solutions"]:
                self.logger.warning(f"Solution: {sol['combination']} → {sol['hash']}")

        self.current_job_id = None
        self.current_chunk_id = None

    async def _get_job_metadata(self, job_id: str) -> Optional[dict]:
        """Получает метаданные задачи из gossip"""
        network = self.context.get_shared("network")
        if not network:
            return None

        coordinator_nodes = [
            node for node in network.gossip.node_registry.values()
            if node.role == "coordinator"
        ]

        if not coordinator_nodes:
            return None

        coordinator = coordinator_nodes[0]
        metadata = coordinator.metadata

        job_key = f"hash_job_{job_id}"
        return metadata.get(job_key)

    async def _publish_chunk_status(
        self,
        job_id: str,
        chunk_id: int,
        status: str,
        progress: int
    ):
        """Публикует статус чанка в gossip"""
        network = self.context.get_shared("network")
        if not network:
            return

        metadata = {
            f"hash_worker_status": {
                "job_id": job_id,
                "chunk_id": chunk_id,
                "status": status,
                "progress": progress,
                "timestamp": time.time(),
                "total_hashes": self.total_hashes_computed,
                "completed_chunks": self.completed_chunks
            }
        }

        network.gossip.update_metadata(metadata)

    async def _publish_chunk_progress(
        self,
        job_id: str,
        chunk_id: int,
        current_index: int
    ):
        """Публикует прогресс в gossip"""
        await self._publish_chunk_status(job_id, chunk_id, "working", current_index)

    async def _publish_chunk_completed(
        self,
        job_id: str,
        chunk_id: int,
        hash_count: int,
        time_taken: float,
        solutions: List[dict]
    ):
        """Публикует завершение чанка в gossip"""
        network = self.context.get_shared("network")
        if not network:
            return

        metadata = {
            f"hash_worker_status": {
                "job_id": job_id,
                "chunk_id": chunk_id,
                "status": "solved",
                "hash_count": hash_count,
                "time_taken": time_taken,
                "solutions": solutions,
                "timestamp": time.time(),
                "total_hashes": self.total_hashes_computed,
                "completed_chunks": self.completed_chunks
            }
        }

        network.gossip.update_metadata(metadata)

    @service_method(description="Получить статус воркера", public=True)
    async def get_worker_status(self) -> Dict[str, Any]:
        """Возвращает текущий статус воркера"""
        return {
            "success": True,
            "current_job": self.current_job_id,
            "current_chunk": self.current_chunk_id,
            "total_hashes_computed": self.total_hashes_computed,
            "completed_chunks": self.completed_chunks,
            "running": self.running
        }

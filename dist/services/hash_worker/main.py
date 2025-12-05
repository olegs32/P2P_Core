"""
Hash Worker Service - Воркер для распределенных вычислений хешей

Функции:
- Получение chunk batches через gossip
- Высокопроизводительное вычисление хешей
- Отчетность о прогрессе через gossip
- Автоматический claim чанков без коллизий
- Поддержка множества hash алгоритмов (SHA-2/3, NTLM, WPA2, etc.)
- Dictionary attack с мутациями
- Multi-target mode
"""

import asyncio
import hashlib
import logging
import time
import struct
import multiprocessing as mp
import psutil
import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any, Set

from layers.service import BaseService, service_method

# Import worker functions from separate module (required for multiprocessing pickling)
# Use importlib to dynamically load module from same directory as this file
_current_dir = Path(__file__).parent
_worker_module_path = _current_dir / "hash_computer_workers.py"
_worker_module_name = "hash_computer_workers"

# Add service directory to sys.path if not already there
if str(_current_dir) not in sys.path:
    sys.path.insert(0, str(_current_dir))

# Load the worker module
_spec = importlib.util.spec_from_file_location(_worker_module_name, _worker_module_path)
_worker_module = importlib.util.module_from_spec(_spec)
sys.modules[_worker_module_name] = _worker_module
_spec.loader.exec_module(_worker_module)

# Import from loaded module
compute_brute_subchunk = _worker_module.compute_brute_subchunk
compute_dict_subchunk = _worker_module.compute_dict_subchunk
HashAlgorithms = _worker_module.HashAlgorithms
MutationEngine = _worker_module.MutationEngine


class SystemMonitor:
    """Мониторинг загруженности системы"""

    def __init__(self, max_cpu_percent: float = 80.0, max_memory_percent: float = 80.0):
        self.max_cpu_percent = max_cpu_percent
        self.max_memory_percent = max_memory_percent
        self.process = psutil.Process()

    def get_current_load(self) -> Dict[str, float]:
        """Возвращает текущую загрузку системы"""
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory_percent = psutil.virtual_memory().percent

        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "process_cpu": self.process.cpu_percent(),
            "process_memory": self.process.memory_percent()
        }

    def is_overloaded(self) -> bool:
        """Проверяет, перегружена ли система"""
        load = self.get_current_load()
        return (load["cpu_percent"] > self.max_cpu_percent or
                load["memory_percent"] > self.max_memory_percent)

    def calculate_optimal_workers(self, max_workers: int = None) -> int:
        """
        Вычисляет оптимальное количество worker процессов

        Формула: workers = cpu_count * (1 - current_load/100) * safety_factor
        """
        if max_workers is None:
            max_workers = mp.cpu_count()

        load = self.get_current_load()
        cpu_load = load["cpu_percent"] / 100.0

        # Доступная мощность = 1 - текущая загрузка
        available_capacity = max(0.1, 1.0 - cpu_load)

        # Safety factor для предотвращения overload
        safety_factor = 0.8

        optimal = int(max_workers * available_capacity * safety_factor)

        # Минимум 1 worker, максимум max_workers
        return max(1, min(optimal, max_workers))


class HashComputer:
    """Оптимизированное вычисление хешей с multiprocessing"""

    def __init__(
        self,
        charset: str = None,
        length: int = None,
        hash_algo: str = "sha256",
        mode: str = "brute",  # "brute" or "dictionary"
        wordlist: List[str] = None,
        mutations: List[str] = None,
        ssid: str = None,  # Для WPA/WPA2
        use_multiprocessing: bool = True,
        max_workers: int = None,
        max_cpu_percent: float = 80.0,
        max_memory_percent: float = 80.0
    ):
        self.mode = mode
        self.hash_algo = hash_algo
        self.ssid = ssid
        self.use_multiprocessing = use_multiprocessing
        self.max_workers = max_workers or mp.cpu_count()

        # System monitor для dynamic load balancing
        self.system_monitor = SystemMonitor(max_cpu_percent, max_memory_percent)

        # Pool для multiprocessing (создается lazy)
        self.pool = None

        # Для brute force
        if mode == "brute":
            self.charset = charset
            self.charset_list = list(charset) if charset else []
            self.length = length
            self.base = len(charset) if charset else 0
            self.powers_of_base = [self.base ** i for i in range(length)] if charset and length else []

        # Для dictionary
        elif mode == "dictionary":
            self.wordlist = wordlist or []
            self.mutations = mutations or []
            self.mutation_engine = MutationEngine()

    def _get_or_create_pool(self) -> mp.Pool:
        """Получает или создает Pool с оптимальным количеством workers"""
        if not self.use_multiprocessing:
            return None

        # Вычисляем оптимальное количество workers на основе текущей загрузки
        optimal_workers = self.system_monitor.calculate_optimal_workers(self.max_workers)

        # Пересоздаем pool если количество workers изменилось
        if self.pool is None or self.pool._processes != optimal_workers:
            if self.pool is not None:
                self.pool.close()
                self.pool.join()

            self.pool = mp.Pool(processes=optimal_workers)

        return self.pool

    def cleanup(self):
        """Очистка ресурсов multiprocessing"""
        if self.pool is not None:
            self.pool.close()
            self.pool.join()
            self.pool = None

    def index_to_combination(self, idx: int) -> str:
        """
        Оптимизированное преобразование индекс → комбинация
        Минимум операций, максимум скорость
        """
        if self.mode != "brute":
            raise ValueError("index_to_combination only for brute mode")

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
        target_hashes: Optional[List[str]] = None,  # Multi-target mode
        progress_callback=None,
        progress_interval: int = 10000
    ) -> Dict[str, Any]:
        """
        Вычисляет хеши для диапазона с максимальной производительностью

        Args:
            start_index: Начальный индекс
            end_index: Конечный индекс
            target_hash: Целевой хеш (опционально)
            target_hashes: Список целевых хешей для multi-target mode
            progress_callback: Функция для отчета о прогрессе
            progress_interval: Интервал отчетов (итераций)

        Returns:
            Результаты вычислений
        """
        if self.mode == "brute":
            return self._compute_brute_force(
                start_index, end_index, target_hash, target_hashes,
                progress_callback, progress_interval
            )
        elif self.mode == "dictionary":
            return self._compute_dictionary(
                start_index, end_index, target_hash, target_hashes,
                progress_callback, progress_interval
            )
        else:
            raise ValueError(f"Unknown mode: {self.mode}")

    def _compute_brute_force(
        self,
        start_index: int,
        end_index: int,
        target_hash: Optional[str],
        target_hashes: Optional[List[str]],
        progress_callback,
        progress_interval: int
    ) -> Dict[str, Any]:
        """Brute force режим с multiprocessing"""
        start_time = time.time()
        total_hash_count = 0
        all_solutions = []

        # Multi-target mode - преобразуем в hex для передачи в worker
        if target_hashes:
            target_hash_set_hex = target_hashes
        elif target_hash:
            target_hash_set_hex = [target_hash]
        else:
            target_hash_set_hex = None

        # Если multiprocessing отключен - используем старый single-threaded код
        if not self.use_multiprocessing:
            return self._compute_brute_force_single(
                start_index, end_index, target_hash, target_hashes,
                progress_callback, progress_interval
            )

        # Получаем или создаем pool с optimal количеством workers
        pool = self._get_or_create_pool()
        if pool is None:
            return self._compute_brute_force_single(
                start_index, end_index, target_hash, target_hashes,
                progress_callback, progress_interval
            )

        # Разбиваем chunk на sub-chunks для параллельной обработки
        num_workers = pool._processes
        chunk_size = end_index - start_index
        subchunk_size = max(progress_interval, chunk_size // num_workers)

        tasks = []
        current = start_index

        while current < end_index:
            subchunk_end = min(current + subchunk_size, end_index)

            task_args = (
                current,
                subchunk_end,
                self.charset_list,
                self.length,
                self.hash_algo,
                self.ssid,
                target_hash_set_hex
            )

            tasks.append(task_args)
            current = subchunk_end

        # Выполняем параллельно с async результатами
        results = pool.map(compute_brute_subchunk, tasks)

        # Собираем результаты
        for solutions, hash_count in results:
            all_solutions.extend(solutions)
            total_hash_count += hash_count

            # Отчет о прогрессе
            if progress_callback:
                progress_callback(start_index + total_hash_count, total_hash_count)

        time_taken = time.time() - start_time
        hash_rate = total_hash_count / time_taken if time_taken > 0 else 0

        return {
            "hash_count": total_hash_count,
            "time_taken": time_taken,
            "hash_rate": hash_rate,
            "solutions": all_solutions,
            "start_index": start_index,
            "end_index": end_index,
            "mode": "brute",
            "workers_used": num_workers
        }

    def _compute_brute_force_single(
        self,
        start_index: int,
        end_index: int,
        target_hash: Optional[str],
        target_hashes: Optional[List[str]],
        progress_callback,
        progress_interval: int
    ) -> Dict[str, Any]:
        """Brute force single-threaded (fallback)"""
        start_time = time.time()
        hash_count = 0
        solutions = []

        # Multi-target mode
        if target_hashes:
            target_hash_set = {bytes.fromhex(h) for h in target_hashes}
        elif target_hash:
            target_hash_set = {bytes.fromhex(target_hash)}
        else:
            target_hash_set = None

        batch_size = progress_interval
        current_idx = start_index

        while current_idx < end_index:
            batch_end = min(current_idx + batch_size, end_index)

            # Основной цикл БЕЗ ПРОВЕРОК
            for idx in range(current_idx, batch_end):
                combination = self.index_to_combination(idx)

                # Вычисляем хеш
                if self.hash_algo.startswith("wpa"):
                    if not self.ssid:
                        raise ValueError("SSID required for WPA/WPA2")
                    hash_bytes = HashAlgorithms.compute_wpa_psk(combination, self.ssid)
                else:
                    hash_bytes = HashAlgorithms.compute_hash(
                        combination.encode(),
                        self.hash_algo
                    )

                # Проверка на совпадение
                if target_hash_set and hash_bytes in target_hash_set:
                    hash_hex = hash_bytes.hex()
                    solutions.append({
                        "combination": combination,
                        "hash": hash_hex,
                        "index": idx,
                        "mode": "brute"
                    })

                hash_count += 1

            current_idx = batch_end

            # Отчет о прогрессе
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
            "end_index": end_index,
            "mode": "brute",
            "workers_used": 1
        }

    def _compute_dictionary(
        self,
        start_index: int,
        end_index: int,
        target_hash: Optional[str],
        target_hashes: Optional[List[str]],
        progress_callback,
        progress_interval: int
    ) -> Dict[str, Any]:
        """Dictionary attack режим с multiprocessing"""
        start_time = time.time()
        total_hash_count = 0
        all_solutions = []

        # Multi-target mode - преобразуем в hex
        if target_hashes:
            target_hash_set_hex = target_hashes
        elif target_hash:
            target_hash_set_hex = [target_hash]
        else:
            target_hash_set_hex = None

        # Получаем слайс словаря
        wordlist_slice = self.wordlist[start_index:end_index]

        # Если multiprocessing отключен - single-threaded
        if not self.use_multiprocessing or len(wordlist_slice) < 100:
            return self._compute_dictionary_single(
                start_index, end_index, target_hash, target_hashes,
                progress_callback, progress_interval
            )

        # Получаем pool
        pool = self._get_or_create_pool()
        if pool is None:
            return self._compute_dictionary_single(
                start_index, end_index, target_hash, target_hashes,
                progress_callback, progress_interval
            )

        # Разбиваем словарь на sub-chunks
        num_workers = pool._processes
        words_per_subchunk = max(100, len(wordlist_slice) // num_workers)

        tasks = []
        current = 0

        while current < len(wordlist_slice):
            subchunk_end = min(current + words_per_subchunk, len(wordlist_slice))
            words_subchunk = wordlist_slice[current:subchunk_end]

            task_args = (
                words_subchunk,
                self.mutations,
                self.hash_algo,
                self.ssid,
                target_hash_set_hex,
                start_index + current  # base index для корректных индексов
            )

            tasks.append(task_args)
            current = subchunk_end

        # Параллельное выполнение
        results = pool.map(compute_dict_subchunk, tasks)

        # Собираем результаты
        for solutions, hash_count in results:
            all_solutions.extend(solutions)
            total_hash_count += hash_count

            # Отчет о прогрессе
            if progress_callback:
                progress_callback(start_index + len(all_solutions), total_hash_count)

        time_taken = time.time() - start_time
        hash_rate = total_hash_count / time_taken if time_taken > 0 else 0

        return {
            "hash_count": total_hash_count,
            "time_taken": time_taken,
            "hash_rate": hash_rate,
            "solutions": all_solutions,
            "start_index": start_index,
            "end_index": end_index,
            "mode": "dictionary",
            "workers_used": num_workers
        }

    def _compute_dictionary_single(
        self,
        start_index: int,
        end_index: int,
        target_hash: Optional[str],
        target_hashes: Optional[List[str]],
        progress_callback,
        progress_interval: int
    ) -> Dict[str, Any]:
        """Dictionary attack single-threaded (fallback)"""
        start_time = time.time()
        hash_count = 0
        solutions = []

        # Multi-target mode
        if target_hashes:
            target_hash_set = {bytes.fromhex(h) for h in target_hashes}
        elif target_hash:
            target_hash_set = {bytes.fromhex(target_hash)}
        else:
            target_hash_set = None

        # Получаем слайс словаря
        wordlist_slice = self.wordlist[start_index:end_index]

        batch_size = progress_interval
        current_idx = 0

        while current_idx < len(wordlist_slice):
            batch_end = min(current_idx + batch_size, len(wordlist_slice))

            for idx in range(current_idx, batch_end):
                word = wordlist_slice[idx]

                # Генерируем мутации
                if self.mutations:
                    candidates = self.mutation_engine.apply_mutations(word, self.mutations)
                else:
                    candidates = [word]

                # Проверяем каждый кандидат
                for candidate in candidates:
                    # Вычисляем хеш
                    if self.hash_algo.startswith("wpa"):
                        if not self.ssid:
                            raise ValueError("SSID required for WPA/WPA2")
                        hash_bytes = HashAlgorithms.compute_wpa_psk(candidate, self.ssid)
                    else:
                        hash_bytes = HashAlgorithms.compute_hash(
                            candidate.encode(),
                            self.hash_algo
                        )

                    # Проверка на совпадение
                    if target_hash_set and hash_bytes in target_hash_set:
                        hash_hex = hash_bytes.hex()
                        solutions.append({
                            "combination": candidate,
                            "hash": hash_hex,
                            "index": start_index + idx,
                            "base_word": word,
                            "mode": "dictionary"
                        })

                    hash_count += 1

            current_idx = batch_end

            # Отчет о прогрессе
            if progress_callback:
                progress_callback(start_index + current_idx, hash_count)

        time_taken = time.time() - start_time
        hash_rate = hash_count / time_taken if time_taken > 0 else 0

        return {
            "hash_count": hash_count,
            "time_taken": time_taken,
            "hash_rate": hash_rate,
            "solutions": solutions,
            "start_index": start_index,
            "end_index": end_index,
            "mode": "dictionary",
            "workers_used": 1
        }


class Run(BaseService):
    SERVICE_NAME = "hash_worker"

    def __init__(self, service_name: str, proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.info.version = "2.0.0"  # Updated with multiprocessing support
        self.info.description = "Воркер распределенных вычислений хешей с multiprocessing"

        # Текущая задача
        self.current_job_id: Optional[str] = None
        self.current_chunk_id: Optional[int] = None

        # Метрики
        self.total_hashes_computed = 0
        self.completed_chunks = 0

        # Локальный кэш обработанных чанков (для избежания повторной обработки)
        self.processed_chunks: Dict[str, set] = {}  # {job_id: set(chunk_ids)}

        # Background tasks
        self.worker_task = None

        # Флаг работы
        self.running = False

        # HashComputer instances (для cleanup)
        self.active_computers: List[HashComputer] = []

        # Конфигурация multiprocessing
        self.use_multiprocessing = True  # Можно настроить через config
        self.max_workers = mp.cpu_count()
        self.max_cpu_percent = 80.0  # Настраиваемое
        self.max_memory_percent = 80.0  # Настраиваемое

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

        # Cleanup multiprocessing pools
        for computer in self.active_computers:
            try:
                computer.cleanup()
            except Exception as e:
                self.logger.error(f"Error cleaning up computer: {e}")

        self.active_computers.clear()

    async def _worker_loop(self):
        """Основной цикл воркера"""
        while self.running:
            try:
                await asyncio.sleep(5)  # Проверяем каждые 5 секунд

                # Получаем активные задачи из gossip
                jobs = await self._get_active_jobs()

                found_work = False
                for job_id in jobs:
                    # Получаем доступные чанки
                    chunk = await self._get_available_chunk(job_id)

                    if chunk:
                        # Обрабатываем чанк
                        await self._process_chunk(job_id, chunk)
                        found_work = True
                        break  # Обрабатываем по одному чанку за итерацию

                # Если нет доступных чанков - делаем паузу
                if not found_work:
                    self.logger.debug("No available chunks, waiting...")
                    await asyncio.sleep(10)  # Дополнительная пауза если нет работы

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

        # Инициализируем set для job_id если его нет
        if job_id not in self.processed_chunks:
            self.processed_chunks[job_id] = set()

        # Ищем чанк для меня
        for version, batch_data in batches.items():
            chunks = batch_data.get("chunks", {})

            for chunk_id, chunk_data in chunks.items():
                if chunk_data.get("assigned_worker") == my_worker_id:
                    # Приводим chunk_id к int для сравнения
                    chunk_id_int = int(chunk_id)

                    # ВАЖНО: Пропускаем уже обработанные чанки (локальный кэш)
                    if chunk_id_int in self.processed_chunks[job_id]:
                        continue

                    # Проверяем статус
                    status = chunk_data.get("status", "assigned")

                    if status in ("assigned", "recovery"):
                        # Это мой чанк, можно брать
                        return {
                            "job_id": job_id,
                            "version": version,
                            "chunk_id": chunk_id_int,
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

        # Параметры задачи
        mode = job_metadata.get("mode", "brute")
        hash_algo = job_metadata.get("hash_algo", "sha256")
        target_hash = job_metadata.get("target_hash")
        target_hashes = job_metadata.get("target_hashes")  # Multi-target mode

        # Создаем компьютер в зависимости от режима с multiprocessing support
        if mode == "brute":
            charset = job_metadata["charset"]
            length = job_metadata["length"]
            ssid = job_metadata.get("ssid")  # Для WPA/WPA2

            computer = HashComputer(
                charset=charset,
                length=length,
                hash_algo=hash_algo,
                mode="brute",
                ssid=ssid,
                use_multiprocessing=self.use_multiprocessing,
                max_workers=self.max_workers,
                max_cpu_percent=self.max_cpu_percent,
                max_memory_percent=self.max_memory_percent
            )

        elif mode == "dictionary":
            wordlist = job_metadata.get("wordlist", [])
            mutations = job_metadata.get("mutations", [])
            ssid = job_metadata.get("ssid")

            computer = HashComputer(
                hash_algo=hash_algo,
                mode="dictionary",
                wordlist=wordlist,
                mutations=mutations,
                ssid=ssid,
                use_multiprocessing=self.use_multiprocessing,
                max_workers=self.max_workers,
                max_cpu_percent=self.max_cpu_percent,
                max_memory_percent=self.max_memory_percent
            )

        else:
            self.logger.error(f"Unknown mode: {mode}")
            return

        # Добавляем в active computers для cleanup
        self.active_computers.append(computer)

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
            target_hash=target_hash,
            target_hashes=target_hashes,
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

        # Получаем system load для логирования
        system_load = computer.system_monitor.get_current_load()
        workers_used = result.get("workers_used", 1)

        self.logger.info(
            f"Completed chunk {chunk_id}: {result['hash_count']} hashes in "
            f"{time_taken:.2f}s ({result['hash_rate']:.0f} h/s) | "
            f"Workers: {workers_used}/{mp.cpu_count()} | "
            f"CPU: {system_load['cpu_percent']:.1f}% | "
            f"Memory: {system_load['memory_percent']:.1f}%"
        )

        if result["solutions"]:
            self.logger.warning(f"FOUND {len(result['solutions'])} SOLUTIONS!")
            for sol in result["solutions"]:
                self.logger.warning(f"Solution: {sol['combination']} → {sol['hash']}")

            # Немедленно уведомляем координатор о находке через RPC
            try:
                await self.proxy.hash_coordinator.report_solution(
                    job_id=job_id,
                    chunk_id=chunk_id,
                    worker_id=self.context.config.node_id,
                    solutions=result["solutions"]
                )
                self.logger.info(f"Reported {len(result['solutions'])} solutions to coordinator")
            except Exception as e:
                self.logger.error(f"Failed to report solutions to coordinator: {e}")

        # Cleanup computer после завершения chunk
        try:
            computer.cleanup()
            self.active_computers.remove(computer)
        except Exception as e:
            self.logger.error(f"Error cleaning up computer: {e}")

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

        worker_status = {
            "job_id": job_id,
            "chunk_id": chunk_id,
            "status": status,
            "progress": progress,
            "timestamp": time.time(),
            "total_hashes": self.total_hashes_computed,
            "completed_chunks": self.completed_chunks
        }

        # Use new versioned update_metadata API
        network.gossip.update_metadata("hash_worker_status", worker_status)

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
        """Публикует завершение чанка в gossip и отправляет результат координатору через RPC"""
        network = self.context.get_shared("network")
        if not network:
            return

        # 1. Публикуем в gossip (для dashboard отображения)
        worker_status = {
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

        # Use new versioned update_metadata API
        network.gossip.update_metadata("hash_worker_status", worker_status)

        # Добавляем chunk_id в локальный кэш обработанных чанков
        if job_id not in self.processed_chunks:
            self.processed_chunks[job_id] = set()
        self.processed_chunks[job_id].add(chunk_id)

        self.logger.debug(f"Marked chunk {chunk_id} as processed for job {job_id}")

        # Координатор прочитает этот статус из gossip в _update_worker_states()

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

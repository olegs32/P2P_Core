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
from typing import Dict, List, Optional, Any, Set

from layers.service import BaseService, service_method


class HashAlgorithms:
    """Поддерживаемые hash алгоритмы"""

    ALGORITHMS = {
        # SHA-2 family
        "md5": lambda: hashlib.md5(),
        "sha1": lambda: hashlib.sha1(),
        "sha224": lambda: hashlib.sha224(),
        "sha256": lambda: hashlib.sha256(),
        "sha384": lambda: hashlib.sha384(),
        "sha512": lambda: hashlib.sha512(),
        "sha512_224": lambda: hashlib.new('sha512_224'),
        "sha512_256": lambda: hashlib.new('sha512_256'),

        # SHA-3 family
        "sha3_224": lambda: hashlib.sha3_224(),
        "sha3_256": lambda: hashlib.sha3_256(),
        "sha3_384": lambda: hashlib.sha3_384(),
        "sha3_512": lambda: hashlib.sha3_512(),
        "shake_128": lambda: hashlib.shake_128(),
        "shake_256": lambda: hashlib.shake_256(),

        # BLAKE2
        "blake2b": lambda: hashlib.blake2b(),
        "blake2s": lambda: hashlib.blake2s(),
    }

    @staticmethod
    def compute_hash(data: bytes, algo: str, output_length: int = None) -> bytes:
        """Вычисляет хеш с поддержкой различных алгоритмов"""
        if algo == "ntlm":
            # NTLM = MD4(UTF-16LE(password))
            return hashlib.new('md4', data.decode('utf-8').encode('utf-16le')).digest()

        elif algo.startswith("wpa"):
            # WPA/WPA2 обрабатывается отдельно
            raise ValueError("WPA requires SSID parameter, use compute_wpa_psk()")

        elif algo in HashAlgorithms.ALGORITHMS:
            hasher = HashAlgorithms.ALGORITHMS[algo]()
            hasher.update(data)

            # SHAKE требует output_length
            if algo.startswith("shake_"):
                if output_length is None:
                    output_length = 32  # default 256 bits
                return hasher.digest(output_length)
            else:
                return hasher.digest()
        else:
            raise ValueError(f"Unsupported algorithm: {algo}")

    @staticmethod
    def compute_wpa_psk(passphrase: str, ssid: str) -> bytes:
        """
        WPA/WPA2 PSK = PBKDF2-HMAC-SHA1(passphrase, SSID, 4096 iterations, 32 bytes)
        """
        return hashlib.pbkdf2_hmac(
            'sha1',
            passphrase.encode('utf-8'),
            ssid.encode('utf-8'),
            iterations=4096,
            dklen=32
        )


class MutationEngine:
    """Движок мутаций для dictionary attack"""

    @staticmethod
    def apply_mutations(word: str, rules: List[str]) -> List[str]:
        """
        Применяет правила мутации к слову

        Правила:
        - l: lowercase
        - u: uppercase
        - c: capitalize
        - $X: append character X
        - ^X: prepend character X
        - sa@: substitute 'a' with '@'
        - d: duplicate
        - r: reverse
        """
        mutations = [word]

        for rule in rules:
            new_mutations = []

            for w in mutations:
                if rule == "l":
                    new_mutations.append(w.lower())
                elif rule == "u":
                    new_mutations.append(w.upper())
                elif rule == "c":
                    new_mutations.append(w.capitalize())
                elif rule == "d":
                    new_mutations.append(w + w)
                elif rule == "r":
                    new_mutations.append(w[::-1])
                elif rule.startswith("$"):
                    # Append
                    new_mutations.append(w + rule[1:])
                elif rule.startswith("^"):
                    # Prepend
                    new_mutations.append(rule[1:] + w)
                elif rule.startswith("s"):
                    # Substitute: sab = replace 'a' with 'b'
                    if len(rule) == 3:
                        new_mutations.append(w.replace(rule[1], rule[2]))
                else:
                    new_mutations.append(w)

            mutations = new_mutations

        return mutations


class HashComputer:
    """Оптимизированное вычисление хешей"""

    def __init__(
        self,
        charset: str = None,
        length: int = None,
        hash_algo: str = "sha256",
        mode: str = "brute",  # "brute" or "dictionary"
        wordlist: List[str] = None,
        mutations: List[str] = None,
        ssid: str = None  # Для WPA/WPA2
    ):
        self.mode = mode
        self.hash_algo = hash_algo
        self.ssid = ssid

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
        """Brute force режим"""
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
            "mode": "brute"
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
        """Dictionary attack режим"""
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
            "mode": "dictionary"
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

        # Параметры задачи
        mode = job_metadata.get("mode", "brute")
        hash_algo = job_metadata.get("hash_algo", "sha256")
        target_hash = job_metadata.get("target_hash")
        target_hashes = job_metadata.get("target_hashes")  # Multi-target mode

        # Создаем компьютер в зависимости от режима
        if mode == "brute":
            charset = job_metadata["charset"]
            length = job_metadata["length"]
            ssid = job_metadata.get("ssid")  # Для WPA/WPA2

            computer = HashComputer(
                charset=charset,
                length=length,
                hash_algo=hash_algo,
                mode="brute",
                ssid=ssid
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
                ssid=ssid
            )

        else:
            self.logger.error(f"Unknown mode: {mode}")
            return

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

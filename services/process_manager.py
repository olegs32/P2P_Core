"""
Process management service for P2P Admin System
"""

import asyncio
import subprocess
import signal
import os
import time
import logging
from typing import Dict, List, Optional, Union
from pathlib import Path
import json
import shlex

import psutil

logger = logging.getLogger(__name__)


class ProcessManagerService:
    """Сервис управления процессами"""

    def __init__(self):
        self.managed_processes: Dict[str, dict] = {}
        self.process_configs: Dict[str, dict] = {}
        self.restart_policies: Dict[str, dict] = {}

        # Загрузка сохраненных конфигураций
        self._load_configs()

    def _load_configs(self):
        """Загрузка конфигураций процессов"""
        config_file = Path("data/process_configs.json")
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    data = json.load(f)
                    self.process_configs = data.get("configs", {})
                    self.restart_policies = data.get("policies", {})
                logger.info(f"Loaded {len(self.process_configs)} process configurations")
            except Exception as e:
                logger.error(f"Failed to load process configs: {e}")

    def _save_configs(self):
        """Сохранение конфигураций процессов"""
        config_file = Path("data/process_configs.json")
        config_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(config_file, "w") as f:
                json.dump({
                    "configs": self.process_configs,
                    "policies": self.restart_policies
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save process configs: {e}")

    async def list_processes(self, filter_managed: bool = False) -> List[dict]:
        """Получение списка процессов"""
        processes = []

        for proc in psutil.process_iter(['pid', 'name', 'username', 'status',
                                         'cpu_percent', 'memory_percent', 'create_time']):
            try:
                pinfo = proc.info

                # Проверка управляемых процессов
                is_managed = False
                managed_name = None

                for name, managed in self.managed_processes.items():
                    if managed.get("pid") == pinfo["pid"]:
                        is_managed = True
                        managed_name = name
                        break

                if filter_managed and not is_managed:
                    continue

                # Добавление информации об управлении
                pinfo["is_managed"] = is_managed
                pinfo["managed_name"] = managed_name

                # Дополнительная информация
                with proc.oneshot():
                    pinfo["cmdline"] = proc.cmdline()
                    pinfo["exe"] = proc.exe() if hasattr(proc, 'exe') else None
                    pinfo["cwd"] = proc.cwd() if hasattr(proc, 'cwd') else None

                    # Время работы
                    create_time = datetime.fromtimestamp(pinfo["create_time"])
                    pinfo["uptime"] = str(datetime.now() - create_time)

                processes.append(pinfo)

            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        return processes

    async def start_process(self, name: str, command: Union[str, List[str]],
                            cwd: str = None, env: dict = None,
                            restart_policy: dict = None) -> dict:
        """Запуск управляемого процесса"""

        # Проверка существующего процесса
        if name in self.managed_processes:
            existing = self.managed_processes[name]
            if existing.get("status") == "running":
                return {
                    "status": "error",
                    "message": f"Process {name} is already running"
                }

        try:
            # Подготовка команды
            if isinstance(command, str):
                command = shlex.split(command)

            # Подготовка окружения
            process_env = os.environ.copy()
            if env:
                process_env.update(env)

            # Запуск процесса
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=process_env
            )

            # Сохранение информации о процессе
            self.managed_processes[name] = {
                "pid": proc.pid,
                "command": command,
                "cwd": cwd,
                "env": env,
                "started_at": time.time(),
                "status": "running",
                "restart_count": 0
            }

            # Сохранение конфигурации
            self.process_configs[name] = {
                "command": command,
                "cwd": cwd,
                "env": env
            }

            # Сохранение политики перезапуска
            if restart_policy:
                self.restart_policies[name] = restart_policy

            self._save_configs()

            # Запуск мониторинга процесса
            asyncio.create_task(self._monitor_process(name, proc))

            logger.info(f"Started process {name} with PID {proc.pid}")

            return {
                "status": "success",
                "pid": proc.pid,
                "name": name
            }

        except Exception as e:
            logger.error(f"Failed to start process {name}: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    async def stop_process(self, name: str, force: bool = False) -> dict:
        """Остановка управляемого процесса"""

        if name not in self.managed_processes:
            return {
                "status": "error",
                "message": f"Process {name} not found"
            }

        process_info = self.managed_processes[name]
        pid = process_info.get("pid")

        if not pid:
            return {
                "status": "error",
                "message": f"No PID for process {name}"
            }

        try:
            proc = psutil.Process(pid)

            if force:
                # Принудительное завершение
                proc.kill()
                logger.info(f"Force killed process {name} (PID {pid})")
            else:
                # Мягкое завершение
                proc.terminate()

                # Ожидание завершения
                gone, alive = psutil.wait_procs([proc], timeout=10)

                if alive:
                    # Если не завершился, убиваем принудительно
                    for p in alive:
                        p.kill()
                    logger.warning(f"Had to force kill process {name} (PID {pid})")
                else:
                    logger.info(f"Gracefully stopped process {name} (PID {pid})")

            # Обновление статуса
            process_info["status"] = "stopped"
            process_info["stopped_at"] = time.time()

            # Удаление из политики перезапуска
            if name in self.restart_policies:
                self.restart_policies[name]["enabled"] = False

            return {
                "status": "success",
                "name": name,
                "pid": pid
            }

        except psutil.NoSuchProcess:
            # Процесс уже завершен
            process_info["status"] = "stopped"
            return {
                "status": "success",
                "message": "Process already stopped"
            }
        except Exception as e:
            logger.error(f"Failed to stop process {name}: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    async def restart_process(self, name: str) -> dict:
        """Перезапуск процесса"""

        # Остановка процесса
        stop_result = await self.stop_process(name)
        if stop_result["status"] != "success":
            return stop_result

        # Небольшая задержка
        await asyncio.sleep(1)

        # Получение конфигурации
        config = self.process_configs.get(name)
        if not config:
            return {
                "status": "error",
                "message": f"No configuration found for process {name}"
            }

        # Запуск процесса
        restart_policy = self.restart_policies.get(name)
        return await self.start_process(
            name,
            config["command"],
            config.get("cwd"),
            config.get("env"),
            restart_policy
        )

    async def _monitor_process(self, name: str, proc: asyncio.subprocess.Process):
        """Мониторинг процесса"""

        try:
            # Ожидание завершения процесса
            returncode = await proc.wait()

            # Обновление статуса
            if name in self.managed_processes:
                self.managed_processes[name]["status"] = "exited"
                self.managed_processes[name]["exit_code"] = returncode
                self.managed_processes[name]["exited_at"] = time.time()

                logger.info(f"Process {name} exited with code {returncode}")

                # Проверка политики перезапуска
                await self._check_restart_policy(name, returncode)

        except Exception as e:
            logger.error(f"Error monitoring process {name}: {e}")

    async def _check_restart_policy(self, name: str, exit_code: int):
        """Проверка и применение политики перезапуска"""

        policy = self.restart_policies.get(name)
        if not policy or not policy.get("enabled", False):
            return

        process_info = self.managed_processes.get(name)
        if not process_info:
            return

        # Увеличение счетчика перезапусков
        restart_count = process_info.get("restart_count", 0) + 1
        max_restarts = policy.get("max_restarts", 3)

        # Проверка лимита перезапусков
        if restart_count > max_restarts:
            logger.error(f"Process {name} exceeded restart limit ({max_restarts})")
            return

        # Проверка условий перезапуска
        restart_on = policy.get("restart_on", "failure")

        should_restart = False
        if restart_on == "always":
            should_restart = True
        elif restart_on == "failure" and exit_code != 0:
            should_restart = True
        elif restart_on == "success" and exit_code == 0:
            should_restart = True

        if should_restart:
            delay = policy.get("restart_delay", 5)
            logger.info(f"Restarting process {name} in {delay} seconds (attempt {restart_count}/{max_restarts})")

            await asyncio.sleep(delay)

            # Обновление счетчика перезапусков
            process_info["restart_count"] = restart_count

            # Перезапуск процесса
            config = self.process_configs.get(name)
            if config:
                await self.start_process(
                    name,
                    config["command"],
                    config.get("cwd"),
                    config.get("env"),
                    policy
                )

    async def get_process_info(self, name: str) -> Optional[dict]:
        """Получение информации о процессе"""

        if name not in self.managed_processes:
            return None

        info = self.managed_processes[name].copy()
        pid = info.get("pid")

        if pid:
            try:
                proc = psutil.Process(pid)

                # Добавление актуальной информации
                with proc.oneshot():
                    info["cpu_percent"] = proc.cpu_percent()
                    info["memory_info"] = proc.memory_info()._asdict()
                    info["num_threads"] = proc.num_threads()
                    info["connections"] = len(proc.connections())
                    info["open_files"] = len(proc.open_files())

            except psutil.NoSuchProcess:
                info["status"] = "not_found"
            except psutil.AccessDenied:
                info["status"] = "access_denied"

        return info

    async def get_process_logs(self, name: str, lines: int = 100) -> dict:
        """Получение логов процесса"""

        # В реальной реализации здесь должно быть чтение из файлов логов
        # или буферов stdout/stderr

        return {
            "name": name,
            "stdout": ["Log line 1", "Log line 2"],  # Заглушка
            "stderr": []
        }

    async def execute_command(self, command: Union[str, List[str]],
                              timeout: int = 30, cwd: str = None) -> dict:
        """Выполнение одноразовой команды"""

        try:
            # Подготовка команды
            if isinstance(command, str):
                command = shlex.split(command)

            # Выполнение команды
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )

            # Ожидание завершения с таймаутом
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "status": "error",
                    "message": "Command timed out",
                    "timeout": timeout
                }

            return {
                "status": "success",
                "returncode": proc.returncode,
                "stdout": stdout.decode('utf-8', errors='replace'),
                "stderr": stderr.decode('utf-8', errors='replace')
            }

        except Exception as e:
            logger.error(f"Failed to execute command: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    def get_managed_processes(self) -> Dict[str, dict]:
        """Получение списка управляемых процессов"""
        return self.managed_processes.copy()

    def get_process_configs(self) -> Dict[str, dict]:
        """Получение конфигураций процессов"""
        return self.process_configs.copy()

    def update_restart_policy(self, name: str, policy: dict):
        """Обновление политики перезапуска"""
        self.restart_policies[name] = policy
        self._save_configs()
        logger.info(f"Updated restart policy for {name}")


# Для импорта datetime
from datetime import datetime
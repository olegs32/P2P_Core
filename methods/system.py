import logging
import platform
import re
import shutil
import socket
from datetime import datetime
import psutil
import os
import subprocess
from typing import Dict, List, Any, Optional
from pathlib import Path
import asyncio
from fastapi import HTTPException

from layers.cache import P2PMultiLevelCache
from layers.service import (
    P2PServiceHandler, BaseService, ServiceManager,
    service_method, P2PAuthBearer,
    RPCRequest, RPCResponse
)
import time


class CommandValidator:
    """Валидатор команд для безопасного выполнения"""

    ALLOWED_COMMANDS = {
        # Информационные команды
        'ls', 'dir', 'ps', 'tasklist', 'df', 'free', 'uptime', 'whoami',
        'pwd', 'cd', 'echo', 'cat', 'type', 'find', 'where', 'which',
        # Системные команды
        'systemctl', 'service', 'netstat', 'ipconfig', 'ifconfig',
        # Архивирование
        'tar', 'zip', 'unzip',
    }

    DANGEROUS_PATTERNS = [
        r'[|&;`$(){}[\]*?~<>!]',  # Опасные символы shell
        r'\.\.',  # Path traversal
        r'sudo|su\s',  # Elevation
        r'rm\s+-rf',  # Dangerous deletion
        r'format\s+[a-z]',  # Format drive
        r'del\s+/[a-z]',  # Windows deletion
        r'shutdown|reboot',  # System control
        r'wget|curl.*http',  # Network downloads
    ]

    @classmethod
    def validate_command(cls, command: str) -> tuple[bool, Optional[str]]:
        """
        Валидация команды
        Returns: (is_valid, error_message)
        """
        if not command or not command.strip():
            return False, "Empty command"

        command = command.strip()

        # Проверка на опасные паттерны
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return False, f"Command contains dangerous pattern: {pattern}"

        # Проверка первого слова (команда)
        first_word = command.split()[0].lower()
        if first_word not in cls.ALLOWED_COMMANDS:
            return False, f"Command '{first_word}' is not in whitelist"

        # Проверка длины
        if len(command) > 500:
            return False, "Command too long"

        return True, None


class SystemService(BaseService):
    """Системный сервис с интегрированными метриками"""

    def __init__(self, service_name: str = "system", proxy_client=None):
        super().__init__(service_name, proxy_client)
        self.is_windows = platform.system().lower() == 'windows'
        self.cache = None

        # Инициализируем системные метрики
        self._setup_system_metrics()

    def _setup_system_metrics(self):
        """Настройка системных метрик"""
        # Базовая информация о системе
        self.metrics.gauge("system_platform", hash(platform.system()))  # Хешируем строку
        self.metrics.gauge("system_architecture", hash(platform.machine()))
        self.metrics.gauge("cpu_count", psutil.cpu_count())
        self.metrics.gauge("memory_total_bytes", psutil.virtual_memory().total)
        self.metrics.gauge("boot_time", psutil.boot_time())

        # Запускаем мониторинг системных ресурсов
        asyncio.create_task(self._monitor_system_resources())

    async def initialize(self):
        """Инициализация системного сервиса"""
        self.logger.info("Initializing system service")

        # Получаем cache из ServiceManager если доступен
        from layers.service import get_global_service_manager
        manager = get_global_service_manager()
        if manager and hasattr(manager, 'proxy_client'):
            pass

        # Базовые системные метрики
        self.metrics.gauge("service_initialized_at", time.time())
        self.metrics.gauge("hostname", hash(socket.gethostname()))  # Хешируем строку

        # ИСПРАВЛЕНИЕ: принудительно запускаем метрики
        self.metrics.gauge("system_service_active", 1)
        self.logger.info(f"System service initialized with {len(self.metrics.data)} metrics")

    def _collect_system_resources_sync(self) -> Dict[str, Any]:
        """Синхронный сбор системных ресурсов"""
        metrics = {}
        try:
            # CPU метрики
            metrics["cpu_usage_percent"] = psutil.cpu_percent(interval=0.1)

            # Память (быстро)
            memory = psutil.virtual_memory()
            metrics["memory_usage_percent"] = memory.percent
            metrics["memory_available_bytes"] = memory.available
            metrics["memory_used_bytes"] = memory.used

            # Остальные метрики только если быстро
            try:
                metrics["process_count"] = len(psutil.pids())
            except:
                pass

            # Дисковые метрики только изредка
            if hasattr(self, '_disk_metrics_counter'):
                self._disk_metrics_counter += 1
            else:
                self._disk_metrics_counter = 1

            if self._disk_metrics_counter % 5 == 0:  # Раз в 5 минут
                try:
                    disk_io = psutil.disk_io_counters()
                    if disk_io:
                        metrics.update({
                            "disk_read_bytes": disk_io.read_bytes,
                            "disk_write_bytes": disk_io.write_bytes,
                            "disk_read_count": disk_io.read_count,
                            "disk_write_count": disk_io.write_count,
                        })
                except:
                    pass

        except Exception as e:
            pass

        return metrics

    async def _monitor_system_resources(self):
        """Background мониторинг системных ресурсов - только для SystemService"""
        # ТОЛЬКО system сервис должен собирать системные метрики
        if self.service_name != "system":
            return

        await asyncio.sleep(10)  # Задержка перед началом

        while self.status.value == "running":
            try:
                # Запускаем в executor чтобы не блокировать
                loop = asyncio.get_event_loop()
                system_metrics = await loop.run_in_executor(
                    None, self._collect_system_resources_sync
                )

                # ИСПРАВЛЕНИЕ: используем self.metrics.gauge() вместо self.metric()
                for metric_name, value in system_metrics.items():
                    self.metrics.gauge(metric_name, value)

            except Exception as e:
                self.logger.warning(f"Error monitoring system resources: {e}")

            await asyncio.sleep(60)  # Раз в минуту

    def _bind_cache_to_methods(self, cache):
        """Привязка кеша к методам с декораторами"""
        for method_name in dir(self):
            if not method_name.startswith('_'):
                method = getattr(self, method_name)
                if hasattr(method, '__wrapped__') and hasattr(method, '__name__'):
                    method._cache = cache

    @service_method(description="Get detailed system information", public=True)
    async def get_system_info(self) -> Dict[str, Any]:
        """Получение информации о системе"""
        try:
            start_time = time.time()
            self.metrics.increment("get_system_info_calls")

            info = {
                "hostname": socket.gethostname(),
                "platform": platform.system(),
                "architecture": platform.machine(),
                "cpu_count": psutil.cpu_count(),
                "memory_total": psutil.virtual_memory().total,
                "boot_time": psutil.boot_time(),
                "disk_usage": self._get_disk_info(),
                "network_interfaces": list(psutil.net_if_addrs().keys()),
                "timestamp": datetime.now().isoformat()
            }

            # Записываем время выполнения
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.timer("get_system_info_duration_ms", duration_ms)
            self.metrics.increment("get_system_info_success")
            return info

        except Exception as e:
            self.metrics.increment("get_system_info_errors")
            raise HTTPException(status_code=500, detail=f"Failed to get system info: {e}")

    def _get_disk_info(self) -> Dict[str, Any]:
        """Получение информации о дисках"""
        disk_info = {}
        try:
            if self.is_windows:
                # Windows - проверяем несколько дисков
                for drive in ['C:', 'D:', 'E:']:
                    drive_path = f"{drive}\\"
                    if os.path.exists(drive_path):
                        try:
                            usage = psutil.disk_usage(drive_path)
                            disk_info[drive] = {
                                "total": usage.total,
                                "used": usage.used,
                                "free": usage.free
                            }
                        except Exception:
                            continue
            else:
                # Unix - основные mount points
                mount_points = ["/", "/home", "/var", "/tmp"]
                for mount_point in mount_points:
                    if os.path.exists(mount_point):
                        try:
                            usage = psutil.disk_usage(mount_point)
                            disk_info[mount_point] = {
                                "total": usage.total,
                                "used": usage.used,
                                "free": usage.free
                            }
                        except Exception:
                            continue

        except Exception as e:
            self.logger.warning(f"Error getting disk info: {e}")

        return disk_info

    @service_method(description="Get current system metrics", public=True)
    async def get_system_metrics(self) -> Dict[str, Any]:
        """Получение текущих метрик системы (не кешируется)"""
        try:
            start_time = time.time()
            self.metrics.increment("get_system_metrics_calls")

            # Используем interval=1.0 для точного измерения CPU за последнюю секунду
            # Это блокирующий вызов, но дает точные результаты
            cpu_percent = psutil.cpu_percent(interval=1.0)

            # Получаем информацию о диске
            disk_usage = psutil.disk_usage('/')

            # Получаем информацию о процессе
            process = psutil.Process()
            process_memory = process.memory_info()

            metrics = {
                "cpu_percent": cpu_percent,
                "cpu_count": psutil.cpu_count(),
                "memory": {
                    "percent": psutil.virtual_memory().percent,
                    "available": psutil.virtual_memory().available,
                    "used": psutil.virtual_memory().used,
                    "total": psutil.virtual_memory().total
                },
                "memory_usage_mb": process_memory.rss / 1024 / 1024,  # RSS in MB
                "disk": {
                    "percent": disk_usage.percent,
                    "free": disk_usage.free,
                    "used": disk_usage.used,
                    "total": disk_usage.total
                },
                "disk_io": psutil.disk_io_counters()._asdict() if psutil.disk_io_counters() else {},
                "network_io": psutil.net_io_counters()._asdict() if psutil.net_io_counters() else {},
                "load_average": list(os.getloadavg()) if hasattr(os, 'getloadavg') else [0, 0, 0],
                "process_count": len(psutil.pids()),
                "timestamp": datetime.now().isoformat()
            }

            # Обновляем метрики сервиса
            self.metrics.gauge("last_metrics_cpu_percent", metrics["cpu_percent"])
            self.metrics.gauge("last_metrics_memory_percent", metrics["memory"]["percent"])

            duration_ms = (time.time() - start_time) * 1000
            self.metrics.timer("get_system_metrics_duration_ms", duration_ms)
            self.metrics.increment("get_system_metrics_success")

            return metrics

        except Exception as e:
            self.metrics.increment("get_system_metrics_errors")
            raise HTTPException(status_code=500, detail=f"Failed to get system metrics: {e}")

    @service_method(description="Execute system command", public=True)
    async def execute_command(self, command: str, timeout: int = 30, working_dir: str = None) -> Dict[str, Any]:
        """Выполнение системной команды с принудительным использованием синхронного subprocess на Windows"""
        # Валидация команды
        is_valid, error_msg = CommandValidator.validate_command(command)
        if not is_valid:
            return {
                "command": command,
                "error": f"Command validation failed: {error_msg}",
                "success": False,
                "security_blocked": True,
                "timestamp": datetime.now().isoformat()
            }

        # Логирование для аудита
        logging.getLogger("SystemSecurity").warning(
            f"Executing command: {command} by user: {working_dir}"
        )

        start_time = time.time()
        self.metrics.increment("execute_command_calls")

        if not command or command.strip() == "":
            self.metrics.increment("execute_command_invalid")
            raise HTTPException(status_code=400, detail="Command cannot be empty")

        # Базовая защита от опасных команд
        dangerous_commands = ['rm -rf', 'format', 'del /q', 'shutdown', 'reboot', 'rmdir /s']
        if any(dangerous in command.lower() for dangerous in dangerous_commands):
            self.metrics.increment("execute_command_blocked")
            raise HTTPException(status_code=403, detail="Dangerous command not allowed")

        # Ограничиваем таймаут
        max_timeout = 60
        if timeout > max_timeout:
            timeout = max_timeout

        try:
            if self.is_windows:
                result = await self._execute_windows_command(command, timeout, working_dir)
            else:
                result = await self._execute_unix_command(command, timeout, working_dir)

            if result.get("success", False):
                self.metrics.increment("execute_command_success")
            else:
                self.metrics.increment("execute_command_failed")

            # Метрики по длительности
            if "timeout" not in result.get("error", ""):
                execution_time = result.get("execution_time", 0)
                self.metrics.timer("command_execution_time_ms", execution_time * 1000)

            return result

        except Exception as e:
            self.metrics.increment("execute_command_errors")
            raise HTTPException(status_code=500, detail=f"Command execution error: {e}")

    async def _execute_windows_command(self, command: str, timeout: int, working_dir: str) -> Dict[str, Any]:
        """Выполнение команды на Windows через синхронный subprocess"""
        import concurrent.futures

        def run_sync_command():
            start_time = time.time()
            try:
                # Находим cmd.exe
                cmd_path = shutil.which("cmd") or "cmd.exe"

                # Выполняем команду синхронно
                result = subprocess.run(
                    [cmd_path, "/c", command],
                    capture_output=True,
                    timeout=timeout,
                    cwd=working_dir,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                )

                execution_time = time.time() - start_time

                # Декодируем вывод
                try:
                    stdout_text = result.stdout.decode('cp866', errors='replace')
                    stderr_text = result.stderr.decode('cp866', errors='replace')
                except:
                    try:
                        stdout_text = result.stdout.decode('utf-8', errors='replace')
                        stderr_text = result.stderr.decode('utf-8', errors='replace')
                    except:
                        stdout_text = str(result.stdout)
                        stderr_text = str(result.stderr)

                return {
                    "command": command,
                    "return_code": result.returncode,
                    "stdout": stdout_text,
                    "stderr": stderr_text,
                    "success": result.returncode == 0,
                    "working_dir": working_dir,
                    "timeout_used": timeout,
                    "execution_time": execution_time,
                    "platform": "Windows",
                    "method": "sync_subprocess",
                    "timestamp": datetime.now().isoformat()
                }

            except subprocess.TimeoutExpired:
                execution_time = time.time() - start_time
                return {
                    "command": command,
                    "error": f"Command timeout after {timeout} seconds",
                    "success": False,
                    "timeout_used": timeout,
                    "execution_time": execution_time,
                    "platform": "Windows",
                    "method": "sync_subprocess",
                    "timestamp": datetime.now().isoformat()
                }
            except Exception as e:
                execution_time = time.time() - start_time
                return {
                    "command": command,
                    "error": str(e),
                    "success": False,
                    "timeout_used": timeout,
                    "execution_time": execution_time,
                    "platform": "Windows",
                    "method": "sync_subprocess",
                    "timestamp": datetime.now().isoformat()
                }

        # Выполняем в отдельном потоке
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            try:
                result = await loop.run_in_executor(executor, run_sync_command)
                return result
            except Exception as e:
                return {
                    "command": command,
                    "error": f"Executor error: {str(e)}",
                    "success": False,
                    "timeout_used": timeout,
                    "execution_time": 0,
                    "platform": "Windows",
                    "method": "sync_subprocess",
                    "timestamp": datetime.now().isoformat()
                }

    async def _execute_unix_command(self, command: str, timeout: int, working_dir: str) -> Dict[str, Any]:
        """Выполнение команды на Unix через асинхронный subprocess"""
        start_time = time.time()
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )

                execution_time = time.time() - start_time

                return {
                    "command": command,
                    "return_code": process.returncode,
                    "stdout": stdout.decode('utf-8', errors='replace'),
                    "stderr": stderr.decode('utf-8', errors='replace'),
                    "success": process.returncode == 0,
                    "working_dir": working_dir,
                    "timeout_used": timeout,
                    "execution_time": execution_time,
                    "platform": "Unix",
                    "method": "async_subprocess",
                    "timestamp": datetime.now().isoformat()
                }

            except asyncio.TimeoutError:
                execution_time = time.time() - start_time
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=5)
                except:
                    try:
                        process.kill()
                        await process.wait()
                    except:
                        pass

                return {
                    "command": command,
                    "error": f"Command timeout after {timeout} seconds",
                    "success": False,
                    "timeout_used": timeout,
                    "execution_time": execution_time,
                    "platform": "Unix",
                    "method": "async_subprocess",
                    "timestamp": datetime.now().isoformat()
                }

        except Exception as e:
            execution_time = time.time() - start_time
            return {
                "command": command,
                "error": str(e),
                "success": False,
                "timeout_used": timeout,
                "execution_time": execution_time,
                "platform": "Unix",
                "method": "async_subprocess",
                "timestamp": datetime.now().isoformat()
            }

    @service_method(description="Execute simple test command", public=True)
    async def execute_simple_test(self) -> Dict[str, Any]:
        """Простой тест команды для отладки"""
        start_time = time.time()
        self.metrics.increment("execute_simple_test_calls")

        if self.is_windows:
            result = await self.execute_command("echo Hello World", timeout=5)
        else:
            result = await self.execute_command("echo 'Hello World'", timeout=5)

        if result.get("success", False):
            self.metrics.increment("execute_simple_test_success")
        else:
            self.metrics.increment("execute_simple_test_failed")

        duration_ms = (time.time() - start_time) * 1000
        self.metrics.timer("execute_simple_test_duration_ms", duration_ms)

        return result

    @service_method(description="Get detailed process list", public=True)
    async def list_processes(self, filter_name: str = None) -> List[Dict[str, Any]]:
        """Список системных процессов"""
        start_time = time.time()
        self.metrics.increment("list_processes_calls")

        processes = []
        process_count = 0

        for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
            try:
                proc_info = proc.info
                if filter_name and filter_name not in proc_info['name']:
                    continue
                processes.append(proc_info)
                process_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self.metrics.gauge("list_processes_returned", process_count)
        self.metrics.increment("list_processes_success")

        duration_ms = (time.time() - start_time) * 1000
        self.metrics.timer("list_processes_duration_ms", duration_ms)

        return processes

    @service_method(description="Get detailed process information", public=True)
    async def get_process_info(self, pid: int) -> Dict[str, Any]:
        """Детальная информация о процессе"""
        start_time = time.time()
        self.metrics.increment("get_process_info_calls")

        try:
            proc = psutil.Process(pid)
            info = {
                "pid": proc.pid,
                "name": proc.name(),
                "status": proc.status(),
                "username": proc.username(),
                "cpu_percent": proc.cpu_percent(),
                "memory_percent": proc.memory_percent(),
                "memory_info": proc.memory_info()._asdict(),
                "create_time": proc.create_time(),
                "cmdline": proc.cmdline(),
                "connections": len(proc.connections())
            }

            self.metrics.increment("get_process_info_success")
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.timer("get_process_info_duration_ms", duration_ms)
            return info

        except psutil.NoSuchProcess:
            self.metrics.increment("get_process_info_not_found")
            raise HTTPException(status_code=404, detail=f"Process {pid} not found")

    @service_method(description="Terminate process", public=True)
    async def terminate_process(self, pid: int, force: bool = False) -> Dict[str, Any]:
        """Завершение процесса"""
        start_time = time.time()
        self.metrics.increment("terminate_process_calls")

        try:
            proc = psutil.Process(pid)
            proc_name = proc.name()

            if force:
                proc.kill()
                action = "killed"
                self.metrics.increment("terminate_process_killed")
            else:
                proc.terminate()
                action = "terminated"
                self.metrics.increment("terminate_process_terminated")

            result = {
                "pid": pid,
                "name": proc_name,
                "action": action,
                "success": True
            }

            self.metrics.increment("terminate_process_success")
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.timer("terminate_process_duration_ms", duration_ms)
            return result

        except psutil.NoSuchProcess:
            self.metrics.increment("terminate_process_not_found")
            raise HTTPException(status_code=404, detail=f"Process {pid} not found")
        except psutil.AccessDenied:
            self.metrics.increment("terminate_process_access_denied")
            raise HTTPException(status_code=403, detail=f"Access denied for process {pid}")

    @service_method(description="List system services", public=True)
    async def list_services(self) -> List[Dict[str, Any]]:
        """Список системных сервисов"""
        start_time = time.time()
        self.metrics.increment("list_services_calls")

        try:
            if self.is_windows:
                # Windows services через WMI или другие методы
                return await self._list_windows_services()
            else:
                # Linux systemctl
                return await self._list_linux_services()

        except Exception as e:
            self.metrics.increment("list_services_errors")
            raise HTTPException(status_code=500, detail=f"Failed to list services: {e}")
        finally:
            duration_ms = (time.time() - start_time) * 1000
            self.metrics.timer("list_services_duration_ms", duration_ms)

    async def _list_linux_services(self) -> List[Dict[str, Any]]:
        """Список Linux сервисов через systemctl"""
        try:
            result = await asyncio.create_subprocess_shell(
                "systemctl list-units --type=service --no-pager",
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()

            services = []
            for line in stdout.decode().split('\n')[1:]:  # Пропуск заголовка
                if line.strip() and not line.startswith('●'):
                    parts = line.split()
                    if len(parts) >= 4:
                        services.append({
                            "name": parts[0],
                            "load": parts[1],
                            "active": parts[2],
                            "sub": parts[3],
                            "description": ' '.join(parts[4:])
                        })

            self.metrics.gauge("list_services_linux_count", len(services))
            self.metrics.increment("list_services_success")
            return services

        except Exception as e:
            self.metrics.increment("list_services_linux_errors")
            raise e

    async def _list_windows_services(self) -> List[Dict[str, Any]]:
        """Список Windows сервисов"""
        try:
            # Простой способ через PowerShell
            result = await self._execute_windows_command(
                'powershell "Get-Service | Select-Object Name,Status,DisplayName | ConvertTo-Json"',
                timeout=30,
                working_dir=None
            )

            if result.get("success", False) and result.get("stdout"):
                import json
                try:
                    services_data = json.loads(result["stdout"])
                    if isinstance(services_data, list):
                        services = services_data
                    else:
                        services = [services_data]  # Один сервис

                    self.metrics.gauge("list_services_windows_count", len(services))
                    self.metrics.increment("list_services_success")
                    return services
                except json.JSONDecodeError:
                    pass

            # Fallback - возвращаем пустой список
            self.metrics.increment("list_services_windows_fallback")
            return []

        except Exception as e:
            self.metrics.increment("list_services_windows_errors")
            return []

    @service_method(description="Control system service", public=True)
    async def service_action(self, service_name: str, action: str) -> Dict[str, Any]:
        """Выполнение действия над сервисом"""
        start_time = time.time()
        self.metrics.increment("service_action_calls")

        if action not in ['start', 'stop', 'restart', 'enable', 'disable', 'status']:
            self.metrics.increment("service_action_invalid")
            raise HTTPException(status_code=400, detail=f"Invalid action: {action}")

        try:
            if self.is_windows:
                result = await self._windows_service_action(service_name, action)
            else:
                result = await self._linux_service_action(service_name, action)

            if result.get("success", False):
                self.metrics.increment(f"service_action_{action}_success")
            else:
                self.metrics.increment(f"service_action_{action}_failed")

            duration_ms = (time.time() - start_time) * 1000
            self.metrics.timer("service_action_duration_ms", duration_ms)
            return result

        except Exception as e:
            self.metrics.increment("service_action_errors")
            return {
                "service": service_name,
                "action": action,
                "error": str(e),
                "success": False
            }

    async def _linux_service_action(self, service_name: str, action: str) -> Dict[str, Any]:
        """Действие над Linux сервисом"""
        command = f"systemctl {action} {service_name}"

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            return {
                "service": service_name,
                "action": action,
                "return_code": process.returncode,
                "output": stdout.decode('utf-8'),
                "error": stderr.decode('utf-8'),
                "success": process.returncode == 0
            }
        except Exception as e:
            return {
                "service": service_name,
                "action": action,
                "error": str(e),
                "success": False
            }

    async def _windows_service_action(self, service_name: str, action: str) -> Dict[str, Any]:
        """Действие над Windows сервисом"""
        # Маппинг действий для Windows
        action_map = {
            'start': 'Start-Service',
            'stop': 'Stop-Service',
            'restart': 'Restart-Service',
            'status': 'Get-Service'
        }

        ps_action = action_map.get(action)
        if not ps_action:
            return {
                "service": service_name,
                "action": action,
                "error": f"Action {action} not supported on Windows",
                "success": False
            }

        command = f'powershell "{ps_action} -Name {service_name}"'
        result = await self._execute_windows_command(command, timeout=30, working_dir=None)

        return {
            "service": service_name,
            "action": action,
            "return_code": result.get("return_code", -1),
            "output": result.get("stdout", ""),
            "error": result.get("stderr", ""),
            "success": result.get("success", False)
        }

    @service_method(description="Get node configuration", public=True)
    async def get_config(self) -> Dict[str, Any]:
        """
        Get current node configuration as JSON

        Returns:
            Dictionary with configuration data
        """
        try:
            if not hasattr(self, 'context') or not self.context:
                return {"error": "Context not available", "config": {}}

            # Get config as dict
            config = self.context.config
            config_dict = {
                "node_id": config.node_id,
                "port": config.port,
                "bind_address": config.bind_address,
                "coordinator_mode": config.coordinator_mode,
                "coordinator_addresses": getattr(config, 'coordinator_addresses', []),
                "redis_url": config.redis_url,
                "redis_enabled": config.redis_enabled,
                "gossip_interval_min": config.gossip_interval_min,
                "gossip_interval_max": config.gossip_interval_max,
                "gossip_compression_enabled": config.gossip_compression_enabled,
                "https_enabled": config.https_enabled,
                "ssl_cert_file": config.ssl_cert_file,
                "ssl_key_file": config.ssl_key_file,
                "ssl_ca_cert_file": config.ssl_ca_cert_file,
                "ssl_verify": config.ssl_verify,
                "rate_limit_enabled": config.rate_limit_enabled,
                "rate_limit_rpc_requests": config.rate_limit_rpc_requests,
                "state_directory": config.state_directory,
                "max_log_entries": config.max_log_entries,
            }

            return {
                "success": True,
                "config": config_dict,
                "node_id": self.context.config.node_id
            }
        except Exception as e:
            self.logger.error(f"Failed to get config: {e}")
            return {"error": str(e), "success": False}

    @service_method(description="Update node configuration", public=True)
    async def update_config(self, config_updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update node configuration and save to storage

        Args:
            config_updates: Dictionary with configuration fields to update

        Returns:
            Success status and updated configuration
        """
        try:
            if not hasattr(self, 'context') or not self.context:
                return {"error": "Context not available", "success": False}

            # Get current config
            config = self.context.config
            storage = self.context.get_shared("storage_manager")

            if not storage:
                return {"error": "Storage manager not available", "success": False}

            # Update config fields (only safe fields)
            safe_fields = {
                'port', 'bind_address', 'redis_url', 'redis_enabled',
                'gossip_interval_min', 'gossip_interval_max', 'gossip_compression_enabled',
                'rate_limit_enabled', 'rate_limit_rpc_requests', 'max_log_entries',
                'coordinator_addresses'
            }

            updated_fields = []
            for field, value in config_updates.items():
                if field in safe_fields and hasattr(config, field):
                    setattr(config, field, value)
                    updated_fields.append(field)
                    self.logger.info(f"Updated config field: {field} = {value}")

            # Save config to storage
            # Use original config filename based on node mode
            if config.coordinator_mode:
                config_filename = "coordinator.yaml"
            else:
                config_filename = "worker.yaml"

            # Convert config to YAML format
            import yaml
            config_dict = {
                "node_id": config.node_id,
                "port": config.port,
                "bind_address": config.bind_address,
                "coordinator_mode": config.coordinator_mode,
                "coordinator_addresses": getattr(config, 'coordinator_addresses', []),
                "redis_url": config.redis_url,
                "redis_enabled": config.redis_enabled,
                "gossip_interval_min": config.gossip_interval_min,
                "gossip_interval_max": config.gossip_interval_max,
                "gossip_compression_enabled": config.gossip_compression_enabled,
                "https_enabled": config.https_enabled,
                "ssl_cert_file": config.ssl_cert_file,
                "ssl_key_file": config.ssl_key_file,
                "ssl_ca_cert_file": config.ssl_ca_cert_file,
                "ssl_verify": config.ssl_verify,
                "rate_limit_enabled": config.rate_limit_enabled,
                "rate_limit_rpc_requests": config.rate_limit_rpc_requests,
                "state_directory": config.state_directory,
                "max_log_entries": config.max_log_entries,
            }

            config_yaml = yaml.dump(config_dict, default_flow_style=False)
            storage.write_config(config_filename, config_yaml.encode('utf-8'))
            storage.save()

            return {
                "success": True,
                "updated_fields": updated_fields,
                "message": f"Configuration updated and saved ({len(updated_fields)} fields)"
            }

        except Exception as e:
            self.logger.error(f"Failed to update config: {e}")
            return {"error": str(e), "success": False}

    @service_method(description="List files in secure storage", public=True)
    async def list_storage_files(self) -> Dict[str, Any]:
        """
        List all files in secure storage

        Returns:
            Dictionary with file list categorized by type
        """
        try:
            if not hasattr(self, 'context') or not self.context:
                return {"error": "Context not available", "files": {}}

            storage = self.context.get_shared("storage_manager")
            if not storage:
                return {"error": "Storage manager not available", "files": {}}

            # Get file lists from storage
            files = {
                "configs": storage.list_configs(),
                "certs": storage.list_certs(),
                "data": storage.list_data_files()
            }

            return {
                "success": True,
                "files": files,
                "total_files": sum(len(f) for f in files.values())
            }

        except Exception as e:
            self.logger.error(f"Failed to list storage files: {e}")
            return {"error": str(e), "success": False}

    @service_method(description="Get file content from storage", public=True)
    async def get_storage_file(self, filename: str, file_type: str = "data") -> Dict[str, Any]:
        """
        Get content of a file from secure storage

        Args:
            filename: Name of file to read
            file_type: Type of file (config, cert, data)

        Returns:
            File content (base64 encoded for binary files)
        """
        try:
            if not hasattr(self, 'context') or not self.context:
                return {"error": "Context not available", "success": False}

            storage = self.context.get_shared("storage_manager")
            if not storage:
                return {"error": "Storage manager not available", "success": False}

            # Read file based on type
            import base64

            # Remove directory prefix if present (list_files returns full paths like "config/coordinator.yaml")
            # but read methods expect just filenames
            if '/' in filename:
                filename_only = filename.split('/')[-1]
            else:
                filename_only = filename

            if file_type == "config":
                # read_config returns str, not bytes
                content_str = storage.read_config(filename_only)
                is_binary = False
                content_size = len(content_str)
            elif file_type == "cert":
                # read_cert returns bytes
                content = storage.read_cert(filename_only)
                content_size = len(content)
                # Certs are usually binary, encode to base64
                try:
                    content_str = content.decode('utf-8')
                    is_binary = False
                except (UnicodeDecodeError, AttributeError):
                    content_str = base64.b64encode(content).decode('utf-8')
                    is_binary = True
            elif file_type == "data":
                # read returns bytes
                content = storage.read(filename_only)
                content_size = len(content)
                # Try to decode as text, otherwise base64 encode
                try:
                    content_str = content.decode('utf-8')
                    is_binary = False
                except (UnicodeDecodeError, AttributeError):
                    content_str = base64.b64encode(content).decode('utf-8')
                    is_binary = True
            else:
                return {"error": f"Invalid file type: {file_type}", "success": False}

            return {
                "success": True,
                "filename": filename,
                "file_type": file_type,
                "content": content_str,
                "is_binary": is_binary,
                "size": content_size
            }

        except Exception as e:
            self.logger.error(f"Failed to get storage file: {e}")
            return {"error": str(e), "success": False}

    @service_method(description="Add file to secure storage", public=True)
    async def add_storage_file(
        self,
        filename: str,
        content: str,
        file_type: str = "data",
        is_binary: bool = False
    ) -> Dict[str, Any]:
        """
        Add or update file in secure storage

        Args:
            filename: Name of file
            content: File content (base64 if binary)
            file_type: Type of file (config, cert, data)
            is_binary: Whether content is base64 encoded binary

        Returns:
            Success status
        """
        try:
            if not hasattr(self, 'context') or not self.context:
                return {"error": "Context not available", "success": False}

            storage = self.context.get_shared("storage_manager")
            if not storage:
                return {"error": "Storage manager not available", "success": False}

            # Decode content
            import base64
            if is_binary:
                content_bytes = base64.b64decode(content)
            else:
                content_bytes = content.encode('utf-8')

            # Remove directory prefix if present
            if '/' in filename:
                filename_only = filename.split('/')[-1]
            else:
                filename_only = filename

            # Write file based on type
            if file_type == "config":
                storage.write_config(filename_only, content_bytes)
            elif file_type == "cert":
                storage.write_cert(filename_only, content_bytes)
            elif file_type == "data":
                storage.write(filename_only, content_bytes)
            else:
                return {"error": f"Invalid file type: {file_type}", "success": False}

            # Save storage
            storage.save()

            return {
                "success": True,
                "filename": filename,
                "file_type": file_type,
                "size": len(content_bytes),
                "message": f"File {filename} saved to {file_type} storage"
            }

        except Exception as e:
            self.logger.error(f"Failed to add storage file: {e}")
            return {"error": str(e), "success": False}

    @service_method(description="Delete file from secure storage", public=True)
    async def delete_storage_file(self, filename: str, file_type: str = "data") -> Dict[str, Any]:
        """
        Delete file from secure storage

        Args:
            filename: Name of file to delete
            file_type: Type of file (config, cert, data)

        Returns:
            Success status
        """
        try:
            if not hasattr(self, 'context') or not self.context:
                return {"error": "Context not available", "success": False}

            storage = self.context.get_shared("storage_manager")
            if not storage:
                return {"error": "Storage manager not available", "success": False}

            # Remove directory prefix if present
            if '/' in filename:
                filename_only = filename.split('/')[-1]
            else:
                filename_only = filename

            # Delete file based on type
            # Note: P2PStorageManager doesn't have delete methods, need to use archive directly
            archive = storage.get_archive()

            if file_type == "config":
                file_path = f"config/{filename_only}"
            elif file_type == "cert":
                file_path = f"certs/{filename_only}"
            elif file_type == "data":
                file_path = f"data/{filename_only}"
            else:
                return {"error": f"Invalid file type: {file_type}", "success": False}

            # Try to delete from archive's virtual_fs
            try:
                if file_path in archive.virtual_fs:
                    del archive.virtual_fs[file_path]
                    success = True
                else:
                    success = False
            except Exception as e:
                self.logger.error(f"Failed to delete file: {e}")
                success = False

            if not success:
                return {"error": f"File not found or could not be deleted: {filename}", "success": False}

            # Save storage
            storage.save()

            return {
                "success": True,
                "filename": filename,
                "file_type": file_type,
                "message": f"File {filename} deleted from {file_type} storage"
            }

        except Exception as e:
            self.logger.error(f"Failed to delete storage file: {e}")
            return {"error": str(e), "success": False}

    # ========== Service Editor Methods ==========

    @service_method(description="List all available services", public=True)
    async def list_p2p_services(self) -> Dict[str, Any]:
        """
        Get list of all services in dist/services/
        
        Returns:
            Dictionary with services list
        """
        try:
            from pathlib import Path
            
            services_dir = Path("dist/services")
            if not services_dir.exists():
                return {"success": False, "error": "Services directory not found"}
            
            services = []
            for service_path in services_dir.iterdir():
                if service_path.is_dir() and not service_path.name.startswith('.'):
                    # Check if has main.py
                    main_file = service_path / "main.py"
                    manifest_file = service_path / "manifest.json"
                    
                    service_info = {
                        "name": service_path.name,
                        "path": str(service_path),
                        "has_main": main_file.exists(),
                        "has_manifest": manifest_file.exists(),
                        "version": "unknown",
                        "description": ""
                    }

                    # Read manifest if exists
                    if manifest_file.exists():
                        try:
                            import json
                            with open(manifest_file, 'r', encoding='utf-8') as f:
                                manifest = json.load(f)
                                service_info["version"] = manifest.get("version", "unknown")
                                service_info["description"] = manifest.get("description", "")
                        except Exception:
                            pass
                    
                    services.append(service_info)
            
            return {
                "success": True,
                "services": sorted(services, key=lambda x: x['name']),
                "count": len(services)
            }
        except Exception as e:
            self.logger.error(f"Failed to list services: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="List files in service directory", public=True)
    async def list_service_files(self, service_name: str) -> Dict[str, Any]:
        """
        Get file tree for a service
        
        Args:
            service_name: Name of the service
            
        Returns:
            File tree structure
        """
        try:
            from pathlib import Path
            
            service_dir = Path(f"dist/services/{service_name}")
            if not service_dir.exists():
                return {"success": False, "error": f"Service {service_name} not found"}
            
            def build_tree(path: Path, base_path: Path) -> Dict[str, Any]:
                """Recursively build file tree"""
                relative = path.relative_to(base_path)
                
                if path.is_file():
                    return {
                        "name": path.name,
                        "path": str(relative),
                        "type": "file",
                        "size": path.stat().st_size,
                        "modified": path.stat().st_mtime
                    }
                else:
                    children = []
                    for child in sorted(path.iterdir()):
                        if not child.name.startswith('.') and child.name != '__pycache__':
                            children.append(build_tree(child, base_path))
                    
                    return {
                        "name": path.name if path != base_path else service_name,
                        "path": str(relative) if path != base_path else "",
                        "type": "directory",
                        "children": children
                    }
            
            tree = build_tree(service_dir, service_dir)
            
            return {
                "success": True,
                "service": service_name,
                "tree": tree
            }
        except Exception as e:
            self.logger.error(f"Failed to list service files: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Get service file content", public=True)
    async def get_service_file(self, service_name: str, file_path: str) -> Dict[str, Any]:
        """
        Get content of a service file
        
        Args:
            service_name: Name of the service
            file_path: Relative path to file within service directory
            
        Returns:
            File content
        """
        try:
            from pathlib import Path
            import base64
            
            service_dir = Path(f"dist/services/{service_name}")
            full_path = service_dir / file_path
            
            # Security check: prevent directory traversal
            if not str(full_path.resolve()).startswith(str(service_dir.resolve())):
                return {"success": False, "error": "Invalid file path"}
            
            if not full_path.exists():
                return {"success": False, "error": "File not found"}
            
            if not full_path.is_file():
                return {"success": False, "error": "Path is not a file"}
            
            # Read file
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                is_binary = False
            except UnicodeDecodeError:
                # Binary file
                with open(full_path, 'rb') as f:
                    content = base64.b64encode(f.read()).decode('utf-8')
                is_binary = True
            
            return {
                "success": True,
                "service": service_name,
                "file_path": file_path,
                "content": content,
                "is_binary": is_binary,
                "size": full_path.stat().st_size
            }
        except Exception as e:
            self.logger.error(f"Failed to get service file: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Update service file content", public=True)
    async def update_service_file(
        self,
        service_name: str,
        file_path: str,
        content: str,
        is_binary: bool = False
    ) -> Dict[str, Any]:
        """
        Update content of a service file
        
        Args:
            service_name: Name of the service
            file_path: Relative path to file within service directory
            content: New file content (base64 if binary)
            is_binary: Whether content is base64 encoded binary
            
        Returns:
            Success status
        """
        try:
            from pathlib import Path
            import base64
            
            service_dir = Path(f"dist/services/{service_name}")
            full_path = service_dir / file_path
            
            # Security check: prevent directory traversal
            if not str(full_path.resolve()).startswith(str(service_dir.resolve())):
                return {"success": False, "error": "Invalid file path"}
            
            # Create parent directories if needed
            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write file
            if is_binary:
                content_bytes = base64.b64decode(content)
                with open(full_path, 'wb') as f:
                    f.write(content_bytes)
            else:
                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            return {
                "success": True,
                "service": service_name,
                "file_path": file_path,
                "size": full_path.stat().st_size,
                "message": f"File {file_path} updated successfully"
            }
        except Exception as e:
            self.logger.error(f"Failed to update service file: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Delete service file", public=True)
    async def delete_service_file(self, service_name: str, file_path: str) -> Dict[str, Any]:
        """
        Delete a service file
        
        Args:
            service_name: Name of the service
            file_path: Relative path to file within service directory
            
        Returns:
            Success status
        """
        try:
            from pathlib import Path
            
            service_dir = Path(f"dist/services/{service_name}")
            full_path = service_dir / file_path
            
            # Security check: prevent directory traversal
            if not str(full_path.resolve()).startswith(str(service_dir.resolve())):
                return {"success": False, "error": "Invalid file path"}
            
            if not full_path.exists():
                return {"success": False, "error": "File not found"}
            
            # Don't allow deleting main.py
            if full_path.name == "main.py" and full_path.parent == service_dir:
                return {"success": False, "error": "Cannot delete main.py"}
            
            # Delete file or directory
            if full_path.is_file():
                full_path.unlink()
            elif full_path.is_dir():
                import shutil
                shutil.rmtree(full_path)
            
            return {
                "success": True,
                "service": service_name,
                "file_path": file_path,
                "message": f"File {file_path} deleted successfully"
            }
        except Exception as e:
            self.logger.error(f"Failed to delete service file: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Rename service file", public=True)
    async def rename_service_file(
        self,
        service_name: str,
        old_path: str,
        new_name: str
    ) -> Dict[str, Any]:
        """
        Rename a service file
        
        Args:
            service_name: Name of the service
            old_path: Current relative path to file
            new_name: New file name (not full path, just name)
            
        Returns:
            Success status
        """
        try:
            from pathlib import Path
            
            service_dir = Path(f"dist/services/{service_name}")
            old_full_path = service_dir / old_path
            
            # Security check
            if not str(old_full_path.resolve()).startswith(str(service_dir.resolve())):
                return {"success": False, "error": "Invalid file path"}
            
            if not old_full_path.exists():
                return {"success": False, "error": "File not found"}
            
            # Don't allow renaming main.py
            if old_full_path.name == "main.py" and old_full_path.parent == service_dir:
                return {"success": False, "error": "Cannot rename main.py"}
            
            # New path is in same directory
            new_full_path = old_full_path.parent / new_name
            
            if new_full_path.exists():
                return {"success": False, "error": "File with new name already exists"}
            
            old_full_path.rename(new_full_path)
            
            # Calculate new relative path
            new_relative = new_full_path.relative_to(service_dir)
            
            return {
                "success": True,
                "service": service_name,
                "old_path": old_path,
                "new_path": str(new_relative),
                "message": f"File renamed to {new_name}"
            }
        except Exception as e:
            self.logger.error(f"Failed to rename service file: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Get service manifest", public=True)
    async def get_service_manifest(self, service_name: str) -> Dict[str, Any]:
        """
        Get manifest.json for a service
        
        Args:
            service_name: Name of the service
            
        Returns:
            Manifest data
        """
        try:
            from pathlib import Path
            import json
            
            service_dir = Path(f"dist/services/{service_name}")
            manifest_file = service_dir / "manifest.json"
            
            if not manifest_file.exists():
                # Return default manifest
                return {
                    "success": True,
                    "service": service_name,
                    "manifest": {
                        "name": service_name,
                        "version": "1.0.0",
                        "description": ""
                    },
                    "exists": False
                }
            
            with open(manifest_file, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
            
            return {
                "success": True,
                "service": service_name,
                "manifest": manifest,
                "exists": True
            }
        except Exception as e:
            self.logger.error(f"Failed to get service manifest: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Update service version in manifest", public=True)
    async def update_service_version(self, service_name: str, version: str) -> Dict[str, Any]:
        """
        Update version in manifest.json

        Args:
            service_name: Name of the service
            version: New version string (e.g., "1.2.3")

        Returns:
            Version update info
        """
        try:
            from pathlib import Path
            import json

            service_dir = Path(f"dist/services/{service_name}")
            if not service_dir.exists():
                return {"success": False, "error": f"Service {service_name} not found"}

            manifest_file = service_dir / "manifest.json"

            # Read or create manifest
            if manifest_file.exists():
                with open(manifest_file, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
            else:
                manifest = {
                    "name": service_name,
                    "version": "1.0.0",
                    "description": ""
                }

            old_version = manifest.get("version", "1.0.0")
            manifest["version"] = version

            # Save manifest
            with open(manifest_file, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            return {
                "success": True,
                "service": service_name,
                "old_version": old_version,
                "new_version": version,
                "message": f"Version updated from {old_version} to {version}"
            }
        except Exception as e:
            self.logger.error(f"Failed to update service version: {e}")
            return {"success": False, "error": str(e)}

    @service_method(description="Increment service version", public=True)
    async def increment_service_version(self, service_name: str) -> Dict[str, Any]:
        """
        Increment patch version in manifest.json (e.g., 1.0.0 -> 1.0.1)

        Args:
            service_name: Name of the service

        Returns:
            New version info
        """
        try:
            from pathlib import Path
            import json

            service_dir = Path(f"dist/services/{service_name}")
            manifest_file = service_dir / "manifest.json"

            # Read or create manifest
            if manifest_file.exists():
                with open(manifest_file, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
            else:
                manifest = {
                    "name": service_name,
                    "version": "1.0.0",
                    "description": ""
                }

            # Parse version
            current_version = manifest.get("version", "1.0.0")
            parts = current_version.split('.')

            # Increment patch version
            if len(parts) >= 3:
                parts[2] = str(int(parts[2]) + 1)
            else:
                parts = ["1", "0", "1"]

            new_version = '.'.join(parts)
            manifest["version"] = new_version

            # Save manifest
            with open(manifest_file, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            return {
                "success": True,
                "service": service_name,
                "old_version": current_version,
                "new_version": new_version,
                "message": f"Version incremented from {current_version} to {new_version}"
            }
        except Exception as e:
            self.logger.error(f"Failed to increment service version: {e}")
            return {"success": False, "error": str(e)}

    # ========== Autostart Management Methods ==========

    @service_method(description="Check autostart status", public=True)
    async def check_autostart_status(self) -> Dict[str, Any]:
        """
        Check if P2P Core is configured to start automatically

        Returns:
            Dictionary with autostart status and details
        """
        try:
            if self.is_windows:
                return await self._check_autostart_windows()
            else:
                return await self._check_autostart_linux()
        except Exception as e:
            self.logger.error(f"Failed to check autostart status: {e}")
            return {
                "success": False,
                "enabled": False,
                "error": str(e)
            }

    @service_method(description="Enable autostart", public=True)
    async def enable_autostart(
        self,
        service_name: str = "p2p_core",
        description: str = "P2P Core Network Service"
    ) -> Dict[str, Any]:
        """
        Enable autostart for P2P Core

        Args:
            service_name: Name for the autostart entry
            description: Description of the service

        Returns:
            Success status and details
        """
        try:
            if self.is_windows:
                return await self._enable_autostart_windows(service_name, description)
            else:
                return await self._enable_autostart_linux(service_name, description)
        except Exception as e:
            self.logger.error(f"Failed to enable autostart: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @service_method(description="Disable autostart", public=True)
    async def disable_autostart(self, service_name: str = "p2p_core") -> Dict[str, Any]:
        """
        Disable autostart for P2P Core

        Args:
            service_name: Name of the autostart entry

        Returns:
            Success status and details
        """
        try:
            if self.is_windows:
                return await self._disable_autostart_windows(service_name)
            else:
                return await self._disable_autostart_linux(service_name)
        except Exception as e:
            self.logger.error(f"Failed to disable autostart: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    # Linux autostart implementation
    async def _check_autostart_linux(self) -> Dict[str, Any]:
        """Check autostart status on Linux"""
        autostart_locations = []
        enabled = False

        # Check systemd service
        try:
            result = await asyncio.create_subprocess_shell(
                "systemctl is-enabled p2p_core.service 2>/dev/null",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            status = stdout.decode().strip()

            if status == "enabled":
                enabled = True
                autostart_locations.append({
                    "type": "systemd",
                    "path": "/etc/systemd/system/p2p_core.service",
                    "enabled": True
                })
        except Exception as e:
            self.logger.debug(f"Systemd check failed: {e}")

        # Check user autostart directory
        autostart_dir = Path.home() / ".config" / "autostart"
        desktop_file = autostart_dir / "p2p_core.desktop"

        if desktop_file.exists():
            enabled = True
            autostart_locations.append({
                "type": "desktop",
                "path": str(desktop_file),
                "enabled": True
            })

        return {
            "success": True,
            "enabled": enabled,
            "platform": "Linux",
            "locations": autostart_locations,
            "method": "systemd" if any(loc["type"] == "systemd" for loc in autostart_locations) else "desktop"
        }

    async def _enable_autostart_linux(self, service_name: str, description: str) -> Dict[str, Any]:
        """Enable autostart on Linux using .desktop file"""
        try:
            # Create autostart directory if it doesn't exist
            autostart_dir = Path.home() / ".config" / "autostart"
            autostart_dir.mkdir(parents=True, exist_ok=True)

            # Get current script path and Python interpreter
            import sys
            python_path = sys.executable
            script_path = Path(os.getcwd()) / "p2p.py"

            # Determine config file based on node mode
            if self.context.config.coordinator_mode:
                config_file = "config/coordinator.yaml"
            else:
                config_file = "config/worker.yaml"

            # Create .desktop file content
            desktop_content = f"""[Desktop Entry]
Type=Application
Name={service_name}
Comment={description}
Exec={python_path} {script_path} --config {config_file}
Terminal=false
StartupNotify=false
X-GNOME-Autostart-enabled=true
"""

            # Write .desktop file
            desktop_file = autostart_dir / f"{service_name}.desktop"
            with open(desktop_file, 'w') as f:
                f.write(desktop_content)

            # Make executable
            desktop_file.chmod(0o755)

            return {
                "success": True,
                "enabled": True,
                "platform": "Linux",
                "method": "desktop",
                "path": str(desktop_file),
                "message": f"Autostart enabled using .desktop file at {desktop_file}"
            }

        except Exception as e:
            self.logger.error(f"Failed to enable Linux autostart: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _disable_autostart_linux(self, service_name: str) -> Dict[str, Any]:
        """Disable autostart on Linux"""
        removed_locations = []

        try:
            # Remove .desktop file
            autostart_dir = Path.home() / ".config" / "autostart"
            desktop_file = autostart_dir / f"{service_name}.desktop"

            if desktop_file.exists():
                desktop_file.unlink()
                removed_locations.append(str(desktop_file))

            # Try to disable systemd service if exists
            try:
                result = await asyncio.create_subprocess_shell(
                    f"systemctl disable {service_name}.service 2>/dev/null",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await result.communicate()
                if result.returncode == 0:
                    removed_locations.append(f"/etc/systemd/system/{service_name}.service")
            except:
                pass

            return {
                "success": True,
                "enabled": False,
                "platform": "Linux",
                "removed_locations": removed_locations,
                "message": f"Autostart disabled. Removed {len(removed_locations)} entries."
            }

        except Exception as e:
            self.logger.error(f"Failed to disable Linux autostart: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    # Windows autostart implementation
    async def _check_autostart_windows(self) -> Dict[str, Any]:
        """Check autostart status on Windows"""
        autostart_locations = []
        enabled = False

        try:
            # Check Registry Run key
            import winreg

            # Check HKCU\Software\Microsoft\Windows\CurrentVersion\Run
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0,
                    winreg.KEY_READ
                )

                try:
                    value, _ = winreg.QueryValueEx(key, "P2P_Core")
                    enabled = True
                    autostart_locations.append({
                        "type": "registry",
                        "path": r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                        "value": value,
                        "enabled": True
                    })
                except FileNotFoundError:
                    pass
                finally:
                    winreg.CloseKey(key)
            except Exception as e:
                self.logger.debug(f"Registry check failed: {e}")

            # Check Startup folder
            import os
            startup_folder = Path(os.getenv('APPDATA')) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            shortcut_file = startup_folder / "P2P_Core.lnk"

            if shortcut_file.exists():
                enabled = True
                autostart_locations.append({
                    "type": "startup_folder",
                    "path": str(shortcut_file),
                    "enabled": True
                })

            return {
                "success": True,
                "enabled": enabled,
                "platform": "Windows",
                "locations": autostart_locations
            }

        except Exception as e:
            self.logger.error(f"Windows autostart check failed: {e}")
            return {
                "success": False,
                "enabled": False,
                "error": str(e)
            }

    async def _enable_autostart_windows(self, service_name: str, description: str) -> Dict[str, Any]:
        """Enable autostart on Windows using Registry"""
        try:
            import winreg
            import sys

            # Get current script path and Python interpreter
            python_path = sys.executable
            script_path = Path(os.getcwd()) / "p2p.py"

            # Determine config file
            if self.context.config.coordinator_mode:
                config_file = "config/coordinator.yaml"
            else:
                config_file = "config/worker.yaml"

            # Create command line
            command = f'"{python_path}" "{script_path}" --config {config_file}'

            # Open Registry key
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )

            # Set value
            winreg.SetValueEx(key, "P2P_Core", 0, winreg.REG_SZ, command)
            winreg.CloseKey(key)

            return {
                "success": True,
                "enabled": True,
                "platform": "Windows",
                "method": "registry",
                "path": r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                "command": command,
                "message": "Autostart enabled in Windows Registry"
            }

        except Exception as e:
            self.logger.error(f"Failed to enable Windows autostart: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    async def _disable_autostart_windows(self, service_name: str) -> Dict[str, Any]:
        """Disable autostart on Windows"""
        removed_locations = []

        try:
            import winreg

            # Remove from Registry
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0,
                    winreg.KEY_SET_VALUE
                )

                try:
                    winreg.DeleteValue(key, "P2P_Core")
                    removed_locations.append(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run")
                except FileNotFoundError:
                    pass
                finally:
                    winreg.CloseKey(key)
            except Exception as e:
                self.logger.debug(f"Registry removal failed: {e}")

            # Remove from Startup folder
            startup_folder = Path(os.getenv('APPDATA')) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            shortcut_file = startup_folder / "P2P_Core.lnk"

            if shortcut_file.exists():
                shortcut_file.unlink()
                removed_locations.append(str(shortcut_file))

            return {
                "success": True,
                "enabled": False,
                "platform": "Windows",
                "removed_locations": removed_locations,
                "message": f"Autostart disabled. Removed {len(removed_locations)} entries."
            }

        except Exception as e:
            self.logger.error(f"Failed to disable Windows autostart: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Для backward compatibility со старым кодом
class SystemMethods(SystemService):
    """Backward compatibility wrapper"""

    def __init__(self, cache: P2PMultiLevelCache = None):
        super().__init__("system")
        self.cache = cache
        if cache:
            self._bind_cache_to_methods(cache)


class ProcessMethods:
    """Методы управления процессами (deprecated - используйте SystemService)"""

    def __init__(self, cache: P2PMultiLevelCache):
        self.cache = cache

    async def list_processes(self, filter_name: str = None) -> List[Dict[str, Any]]:
        """Список системных процессов"""
        processes = []

        for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
            try:
                proc_info = proc.info
                if filter_name and filter_name not in proc_info['name']:
                    continue
                processes.append(proc_info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return processes

    async def get_process_info(self, pid: int) -> Dict[str, Any]:
        """Детальная информация о процессе"""
        try:
            proc = psutil.Process(pid)
            return {
                "pid": proc.pid,
                "name": proc.name(),
                "status": proc.status(),
                "username": proc.username(),
                "cpu_percent": proc.cpu_percent(),
                "memory_percent": proc.memory_percent(),
                "memory_info": proc.memory_info()._asdict(),
                "create_time": proc.create_time(),
                "cmdline": proc.cmdline(),
                "connections": len(proc.connections())
            }
        except psutil.NoSuchProcess:
            raise HTTPException(status_code=404, detail=f"Process {pid} not found")

    async def terminate_process(self, pid: int, force: bool = False) -> Dict[str, Any]:
        """Завершение процесса"""
        try:
            proc = psutil.Process(pid)
            proc_name = proc.name()

            if force:
                proc.kill()
                action = "killed"
            else:
                proc.terminate()
                action = "terminated"

            return {
                "pid": pid,
                "name": proc_name,
                "action": action,
                "success": True
            }
        except psutil.NoSuchProcess:
            raise HTTPException(status_code=404, detail=f"Process {pid} not found")
        except psutil.AccessDenied:
            raise HTTPException(status_code=403, detail=f"Access denied for process {pid}")


class ServiceMethods:
    """Методы управления сервисами (deprecated - используйте SystemService)"""

    def __init__(self, cache: P2PMultiLevelCache):
        self.cache = cache

    async def list_services(self) -> List[Dict[str, Any]]:
        """Список системных сервисов"""
        try:
            result = await asyncio.create_subprocess_shell(
                "systemctl list-units --type=service --no-pager",
                stdout=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()

            services = []
            for line in stdout.decode().split('\n')[1:]:  # Пропуск заголовка
                if line.strip() and not line.startswith('●'):
                    parts = line.split()
                    if len(parts) >= 4:
                        services.append({
                            "name": parts[0],
                            "load": parts[1],
                            "active": parts[2],
                            "sub": parts[3],
                            "description": ' '.join(parts[4:])
                        })

            return services
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list services: {e}")

    async def service_action(self, service_name: str, action: str) -> Dict[str, Any]:
        """Выполнение действия над сервисом"""
        if action not in ['start', 'stop', 'restart', 'enable', 'disable', 'status']:
            raise HTTPException(status_code=400, detail=f"Invalid action: {action}")

        command = f"systemctl {action} {service_name}"

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            return {
                "service": service_name,
                "action": action,
                "return_code": process.returncode,
                "output": stdout.decode('utf-8'),
                "error": stderr.decode('utf-8'),
                "success": process.returncode == 0
            }
        except Exception as e:
            return {
                "service": service_name,
                "action": action,
                "error": str(e),
                "success": False
            }


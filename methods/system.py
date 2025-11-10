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

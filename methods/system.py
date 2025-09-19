import platform
import shutil
import socket
from datetime import datetime
import psutil
import os
import subprocess
from typing import Dict, List, Any
import asyncio
from fastapi import HTTPException
from layers.cache import P2PMultiLevelCache, cached_rpc
from layers.service_framework import BaseService, service_method
import time


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
        self.metric("system_platform", platform.system())
        self.metric("system_architecture", platform.machine())
        self.metric("cpu_count", psutil.cpu_count())
        self.metric("memory_total_bytes", psutil.virtual_memory().total)
        self.metric("boot_time", psutil.boot_time())

        # Запускаем мониторинг системных ресурсов
        asyncio.create_task(self._monitor_system_resources())

    async def _monitor_system_resources(self):
        """Background мониторинг системных ресурсов"""
        while self.status.value == "running":
            try:
                # CPU метрики
                cpu_percent = psutil.cpu_percent(interval=0.1)
                self.metric("cpu_usage_percent", cpu_percent)

                # Память
                memory = psutil.virtual_memory()
                self.metric("memory_usage_percent", memory.percent)
                self.metric("memory_available_bytes", memory.available)
                self.metric("memory_used_bytes", memory.used)

                # Диск I/O
                disk_io = psutil.disk_io_counters()
                if disk_io:
                    self.metric("disk_read_bytes", disk_io.read_bytes, "counter")
                    self.metric("disk_write_bytes", disk_io.write_bytes, "counter")
                    self.metric("disk_read_count", disk_io.read_count, "counter")
                    self.metric("disk_write_count", disk_io.write_count, "counter")

                # Сеть I/O
                network_io = psutil.net_io_counters()
                if network_io:
                    self.metric("network_bytes_sent", network_io.bytes_sent, "counter")
                    self.metric("network_bytes_recv", network_io.bytes_recv, "counter")
                    self.metric("network_packets_sent", network_io.packets_sent, "counter")
                    self.metric("network_packets_recv", network_io.packets_recv, "counter")

                # Load average (только на Unix)
                if hasattr(os, 'getloadavg'):
                    load_avg = os.getloadavg()
                    self.metric("load_average_1min", load_avg[0])
                    self.metric("load_average_5min", load_avg[1])
                    self.metric("load_average_15min", load_avg[2])

                # Количество процессов
                self.metric("process_count", len(psutil.pids()))

                # Disk usage
                disk_info = self._get_disk_info()
                for mount_point, usage in disk_info.items():
                    safe_mount = mount_point.replace(':', '_').replace('/', '_root')
                    self.metric(f"disk_{safe_mount}_total_bytes", usage['total'])
                    self.metric(f"disk_{safe_mount}_used_bytes", usage['used'])
                    self.metric(f"disk_{safe_mount}_free_bytes", usage['free'])
                    if usage['total'] > 0:
                        usage_percent = (usage['used'] / usage['total']) * 100
                        self.metric(f"disk_{safe_mount}_usage_percent", usage_percent)

            except Exception as e:
                self.logger.warning(f"Error updating system metrics: {e}")
                self.metric("system_monitoring_errors", 1, "counter")

            await asyncio.sleep(30)  # Обновление каждые 30 секунд

    async def initialize(self):
        """Инициализация системного сервиса"""
        self.logger.info("Initializing system service")

        # Получаем cache из ServiceManager если доступен
        from layers.service_framework import get_global_service_manager
        manager = get_global_service_manager()
        if manager and hasattr(manager, 'proxy_client'):
            # Можно получить доступ к cache через другие компоненты
            pass

        # Базовые системные метрики
        self.metric("service_initialized_at", time.time())
        self.metric("hostname", socket.gethostname())

    async def cleanup(self):
        """Очистка ресурсов"""
        self.logger.info("Cleaning up system service")

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
            with self.metrics.timing_context("get_system_info_duration"):
                self.metric("get_system_info_calls", 1, "counter")

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

                self.metric("get_system_info_success", 1, "counter")
                return info

        except Exception as e:
            self.metric("get_system_info_errors", 1, "counter")
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
            with self.metrics.timing_context("get_system_metrics_duration"):
                self.metric("get_system_metrics_calls", 1, "counter")

                metrics = {
                    "cpu_percent": psutil.cpu_percent(interval=0.001),
                    "memory": {
                        "percent": psutil.virtual_memory().percent,
                        "available": psutil.virtual_memory().available,
                        "used": psutil.virtual_memory().used
                    },
                    "disk_io": psutil.disk_io_counters()._asdict() if psutil.disk_io_counters() else {},
                    "network_io": psutil.net_io_counters()._asdict() if psutil.net_io_counters() else {},
                    "load_average": list(os.getloadavg()) if hasattr(os, 'getloadavg') else [0, 0, 0],
                    "process_count": len(psutil.pids()),
                    "timestamp": datetime.now().isoformat()
                }

                # Обновляем метрики сервиса
                self.metric("last_metrics_cpu_percent", metrics["cpu_percent"])
                self.metric("last_metrics_memory_percent", metrics["memory"]["percent"])
                self.metric("get_system_metrics_success", 1, "counter")

                return metrics

        except Exception as e:
            self.metric("get_system_metrics_errors", 1, "counter")
            raise HTTPException(status_code=500, detail=f"Failed to get system metrics: {e}")

    @service_method(description="Execute system command", public=True)
    async def execute_command(self, command: str, timeout: int = 30, working_dir: str = None) -> Dict[str, Any]:
        """Выполнение системной команды с принудительным использованием синхронного subprocess на Windows"""

        with self.metrics.timing_context("execute_command_duration"):
            self.metric("execute_command_calls", 1, "counter")

            if not command or command.strip() == "":
                self.metric("execute_command_invalid", 1, "counter")
                raise HTTPException(status_code=400, detail="Command cannot be empty")

            # Базовая защита от опасных команд
            dangerous_commands = ['rm -rf', 'format', 'del /q', 'shutdown', 'reboot', 'rmdir /s']
            if any(dangerous in command.lower() for dangerous in dangerous_commands):
                self.metric("execute_command_blocked", 1, "counter")
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
                    self.metric("execute_command_success", 1, "counter")
                else:
                    self.metric("execute_command_failed", 1, "counter")

                # Метрики по длительности
                if "timeout" not in result.get("error", ""):
                    execution_time = result.get("execution_time", 0)
                    self.metric("command_execution_time_ms", execution_time * 1000, "timer")

                return result

            except Exception as e:
                self.metric("execute_command_errors", 1, "counter")
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
        with self.metrics.timing_context("execute_simple_test_duration"):
            self.metric("execute_simple_test_calls", 1, "counter")

            if self.is_windows:
                result = await self.execute_command("echo Hello World", timeout=5)
            else:
                result = await self.execute_command("echo 'Hello World'", timeout=5)

            if result.get("success", False):
                self.metric("execute_simple_test_success", 1, "counter")
            else:
                self.metric("execute_simple_test_failed", 1, "counter")

            return result

    @service_method(description="Get detailed process list", public=True)
    async def list_processes(self, filter_name: str = None) -> List[Dict[str, Any]]:
        """Список системных процессов"""
        with self.metrics.timing_context("list_processes_duration"):
            self.metric("list_processes_calls", 1, "counter")

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

            self.metric("list_processes_returned", process_count)
            self.metric("list_processes_success", 1, "counter")

            return processes

    @service_method(description="Get detailed process information", public=True)
    async def get_process_info(self, pid: int) -> Dict[str, Any]:
        """Детальная информация о процессе"""
        with self.metrics.timing_context("get_process_info_duration"):
            self.metric("get_process_info_calls", 1, "counter")

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

                self.metric("get_process_info_success", 1, "counter")
                return info

            except psutil.NoSuchProcess:
                self.metric("get_process_info_not_found", 1, "counter")
                raise HTTPException(status_code=404, detail=f"Process {pid} not found")

    @service_method(description="Terminate process", public=True)
    async def terminate_process(self, pid: int, force: bool = False) -> Dict[str, Any]:
        """Завершение процесса"""
        with self.metrics.timing_context("terminate_process_duration"):
            self.metric("terminate_process_calls", 1, "counter")

            try:
                proc = psutil.Process(pid)
                proc_name = proc.name()

                if force:
                    proc.kill()
                    action = "killed"
                    self.metric("terminate_process_killed", 1, "counter")
                else:
                    proc.terminate()
                    action = "terminated"
                    self.metric("terminate_process_terminated", 1, "counter")

                result = {
                    "pid": pid,
                    "name": proc_name,
                    "action": action,
                    "success": True
                }

                self.metric("terminate_process_success", 1, "counter")
                return result

            except psutil.NoSuchProcess:
                self.metric("terminate_process_not_found", 1, "counter")
                raise HTTPException(status_code=404, detail=f"Process {pid} not found")
            except psutil.AccessDenied:
                self.metric("terminate_process_access_denied", 1, "counter")
                raise HTTPException(status_code=403, detail=f"Access denied for process {pid}")

    @service_method(description="List system services", public=True)
    async def list_services(self) -> List[Dict[str, Any]]:
        """Список системных сервисов"""
        with self.metrics.timing_context("list_services_duration"):
            self.metric("list_services_calls", 1, "counter")

            try:
                if self.is_windows:
                    # Windows services через WMI или другие методы
                    return await self._list_windows_services()
                else:
                    # Linux systemctl
                    return await self._list_linux_services()

            except Exception as e:
                self.metric("list_services_errors", 1, "counter")
                raise HTTPException(status_code=500, detail=f"Failed to list services: {e}")

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

            self.metric("list_services_linux_count", len(services))
            self.metric("list_services_success", 1, "counter")
            return services

        except Exception as e:
            self.metric("list_services_linux_errors", 1, "counter")
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

                    self.metric("list_services_windows_count", len(services))
                    self.metric("list_services_success", 1, "counter")
                    return services
                except json.JSONDecodeError:
                    pass

            # Fallback - возвращаем пустой список
            self.metric("list_services_windows_fallback", 1, "counter")
            return []

        except Exception as e:
            self.metric("list_services_windows_errors", 1, "counter")
            return []

    @service_method(description="Control system service", public=True)
    async def service_action(self, service_name: str, action: str) -> Dict[str, Any]:
        """Выполнение действия над сервисом"""
        with self.metrics.timing_context("service_action_duration"):
            self.metric("service_action_calls", 1, "counter")

            if action not in ['start', 'stop', 'restart', 'enable', 'disable', 'status']:
                self.metric("service_action_invalid", 1, "counter")
                raise HTTPException(status_code=400, detail=f"Invalid action: {action}")

            try:
                if self.is_windows:
                    result = await self._windows_service_action(service_name, action)
                else:
                    result = await self._linux_service_action(service_name, action)

                if result.get("success", False):
                    self.metric(f"service_action_{action}_success", 1, "counter")
                else:
                    self.metric(f"service_action_{action}_failed", 1, "counter")

                return result

            except Exception as e:
                self.metric("service_action_errors", 1, "counter")
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

    async def initialize(self):
        """Инициализация системного сервиса"""
        self.logger.info("Initializing system service")

        # Получаем cache из ServiceManager если доступен
        from layers.service_framework import get_global_service_manager
        manager = get_global_service_manager()
        if manager and hasattr(manager, 'proxy_client'):
            pass

        # Базовые системные метрики
        self.metric("service_initialized_at", time.time())
        self.metric("hostname", socket.gethostname())

        # ДОБАВИТЬ: принудительно запускаем метрики
        self.metric("system_service_active", 1)
        self.logger.info(f"System service initialized with {len(self.metrics.data)} metrics")


# Для backward compatibility с старым кодом
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
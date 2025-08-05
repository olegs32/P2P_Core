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

import psutil
import os
import subprocess
import asyncio
import platform
from typing import Dict, List, Any
from datetime import datetime
from fastapi import HTTPException

from layers.cache import cached_rpc, P2PMultiLevelCache


class SystemMethods:
    """Методы системного администрирования с кешированием"""

    def __init__(self, cache: P2PMultiLevelCache = None):
        self.cache = cache
        self.is_windows = platform.system().lower() == 'windows'

    @cached_rpc(ttl=60, scope="system")
    async def get_system_info(self) -> Dict[str, Any]:
        """Получение информации о системе"""
        try:
            return {
                "hostname": os.uname().nodename if hasattr(os, 'uname') else platform.node(),
                "platform": platform.system(),
                "architecture": platform.machine(),
                "cpu_count": psutil.cpu_count(),
                "memory_total": psutil.virtual_memory().total,
                "boot_time": psutil.boot_time(),
                "disk_usage": self._get_disk_info(),
                "network_interfaces": list(psutil.net_if_addrs().keys()),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get system info: {e}")

    def _get_disk_info(self) -> Dict[str, Any]:
        """Получение информации о дисках"""
        disk_info = {}
        try:
            if self.is_windows:
                if os.path.exists("C:\\"):
                    usage = psutil.disk_usage("C:\\")
                    disk_info["C:"] = {
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free
                    }
            else:
                if os.path.exists("/"):
                    usage = psutil.disk_usage("/")
                    disk_info["/"] = {
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free
                    }
        except Exception:
            pass
        return disk_info

    async def get_system_metrics(self) -> Dict[str, Any]:
        """Получение текущих метрик системы (не кешируется)"""
        try:
            return {
                "cpu_percent": psutil.cpu_percent(interval=1),
                "memory": {
                    "percent": psutil.virtual_memory().percent,
                    "available": psutil.virtual_memory().available,
                    "used": psutil.virtual_memory().used
                },
                "disk_io": psutil.disk_io_counters()._asdict() if psutil.disk_io_counters() else {},
                "network_io": psutil.net_io_counters()._asdict() if psutil.net_io_counters() else {},
                "load_average": os.getloadavg() if hasattr(os, 'getloadavg') else [0, 0, 0],
                "process_count": len(psutil.pids()),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get system metrics: {e}")

    async def execute_command(self, command: str, timeout: int = 30,
                              working_dir: str = None) -> Dict[str, Any]:
        """Выполнение системной команды с принудительным использованием синхронного subprocess на Windows"""
        if not command or command.strip() == "":
            raise HTTPException(status_code=400, detail="Command cannot be empty")

        # Базовая защита от опасных команд
        dangerous_commands = ['rm -rf', 'format', 'del /q', 'shutdown', 'reboot', 'rmdir /s']
        if any(dangerous in command.lower() for dangerous in dangerous_commands):
            raise HTTPException(status_code=403, detail="Dangerous command not allowed")

        # Ограничиваем таймаут
        max_timeout = 60
        if timeout > max_timeout:
            timeout = max_timeout

        if self.is_windows:
            # На Windows используем синхронный subprocess в отдельном потоке
            return await self._execute_windows_command(command, timeout, working_dir)
        else:
            # На Unix используем асинхронный subprocess
            return await self._execute_unix_command(command, timeout, working_dir)

    async def _execute_windows_command(self, command: str, timeout: int, working_dir: str) -> Dict[str, Any]:
        """Выполнение команды на Windows через синхронный subprocess"""
        import concurrent.futures

        def run_sync_command():
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
                    "platform": "Windows",
                    "method": "sync_subprocess",
                    "timestamp": datetime.now().isoformat()
                }

            except subprocess.TimeoutExpired:
                return {
                    "command": command,
                    "error": f"Command timeout after {timeout} seconds",
                    "success": False,
                    "timeout_used": timeout,
                    "platform": "Windows",
                    "method": "sync_subprocess",
                    "timestamp": datetime.now().isoformat()
                }
            except Exception as e:
                return {
                    "command": command,
                    "error": str(e),
                    "success": False,
                    "timeout_used": timeout,
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
                    "platform": "Windows",
                    "method": "sync_subprocess",
                    "timestamp": datetime.now().isoformat()
                }

    async def _execute_unix_command(self, command: str, timeout: int, working_dir: str) -> Dict[str, Any]:
        """Выполнение команды на Unix через асинхронный subprocess"""
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

                return {
                    "command": command,
                    "return_code": process.returncode,
                    "stdout": stdout.decode('utf-8', errors='replace'),
                    "stderr": stderr.decode('utf-8', errors='replace'),
                    "success": process.returncode == 0,
                    "working_dir": working_dir,
                    "timeout_used": timeout,
                    "platform": "Unix",
                    "method": "async_subprocess",
                    "timestamp": datetime.now().isoformat()
                }

            except asyncio.TimeoutError:
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
                    "platform": "Unix",
                    "method": "async_subprocess",
                    "timestamp": datetime.now().isoformat()
                }

        except Exception as e:
            return {
                "command": command,
                "error": str(e),
                "success": False,
                "timeout_used": timeout,
                "platform": "Unix",
                "method": "async_subprocess",
                "timestamp": datetime.now().isoformat()
            }

    async def execute_simple_test(self) -> Dict[str, Any]:
        """Простой тест команды для отладки"""
        if self.is_windows:
            return await self.execute_command("echo Hello World", timeout=5)
        else:
            return await self.execute_command("echo 'Hello World'", timeout=5)


class ProcessMethods:
    """Методы управления процессами"""

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
    """Методы управления сервисами"""

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

import platform
import socket
from datetime import datetime

import psutil
import os
import subprocess
from typing import Dict, List, Any
import asyncio

from fastapi import HTTPException

from layers.cache import P2PMultiLevelCache, cached_rpc


class SystemMethods:
    """Методы системного администрирования"""

    def __init__(self, cache: P2PMultiLevelCache):
        self.cache = cache

    @cached_rpc(ttl=60, scope="system")
    async def get_system_info(self) -> Dict[str, Any]:
        """Получение информации о системе (поддержка Windows)"""
        return {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "architecture": platform.machine(),
            "cpu_count": psutil.cpu_count(),
            "memory_total": psutil.virtual_memory().total,
            "disk_usage": {
                partition.mountpoint: {
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free
                }
                for partition in psutil.disk_partitions()
                if os.path.exists(partition.mountpoint)
                   and (usage := psutil.disk_usage(partition.mountpoint))
            }
        }

    async def get_system_metrics(self) -> Dict[str, Any]:
        """Получение текущих метрик системы"""
        return {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_io": psutil.disk_io_counters()._asdict(),
            "network_io": psutil.net_io_counters()._asdict(),
            "load_average": os.getloadavg() if hasattr(os, 'getloadavg') else [0, 0, 0],
            "timestamp": datetime.now().isoformat()
        }

    async def execute_command(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        """Выполнение системной команды"""
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )

            return {
                "command": command,
                "return_code": process.returncode,
                "stdout": stdout.decode('utf-8'),
                "stderr": stderr.decode('utf-8'),
                "success": process.returncode == 0
            }

        except asyncio.TimeoutError:
            return {
                "command": command,
                "error": f"Command timeout after {timeout} seconds",
                "success": False
            }
        except Exception as e:
            return {
                "command": command,
                "error": str(e),
                "success": False
            }


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
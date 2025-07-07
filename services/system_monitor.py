"""
System monitoring service for P2P Admin System
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from collections import deque, defaultdict

import psutil
import platform

logger = logging.getLogger(__name__)


class SystemMonitorService:
    """Сервис мониторинга системы"""

    def __init__(self, history_size: int = 1000):
        self.history_size = history_size
        self.metrics_history = defaultdict(lambda: deque(maxlen=history_size))
        self.alerts = deque(maxlen=100)
        self.monitoring_active = False

        # Пороги для алертов
        self.thresholds = {
            "cpu_percent": 80.0,
            "memory_percent": 85.0,
            "disk_percent": 90.0,
            "network_errors": 100
        }

        # Кэш системной информации
        self._system_info_cache = None
        self._system_info_cache_time = 0

    async def start_monitoring(self, interval: int = 5):
        """Запуск фонового мониторинга"""
        if self.monitoring_active:
            return

        self.monitoring_active = True
        logger.info("System monitoring started")

        while self.monitoring_active:
            try:
                await self._collect_metrics()
                await asyncio.sleep(interval)
            except Exception as e:
                logger.error(f"Monitoring error: {e}")
                await asyncio.sleep(interval * 2)

    def stop_monitoring(self):
        """Остановка мониторинга"""
        self.monitoring_active = False
        logger.info("System monitoring stopped")

    async def _collect_metrics(self):
        """Сбор системных метрик"""
        timestamp = time.time()

        # CPU метрики
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_per_core = psutil.cpu_percent(interval=1, percpu=True)

        # Память
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # Диск
        disk = psutil.disk_usage('/')
        disk_io = psutil.disk_io_counters()

        # Сеть
        net_io = psutil.net_io_counters()
        net_connections = len(psutil.net_connections())

        # Процессы
        process_count = len(psutil.pids())

        # Сохранение метрик
        metrics = {
            "timestamp": timestamp,
            "cpu": {
                "percent": cpu_percent,
                "per_core": cpu_per_core,
                "count": psutil.cpu_count(),
                "freq": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None
            },
            "memory": {
                "total": memory.total,
                "available": memory.available,
                "percent": memory.percent,
                "used": memory.used,
                "free": memory.free,
                "swap_total": swap.total,
                "swap_used": swap.used,
                "swap_percent": swap.percent
            },
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent,
                "read_bytes": disk_io.read_bytes if disk_io else 0,
                "write_bytes": disk_io.write_bytes if disk_io else 0,
                "read_count": disk_io.read_count if disk_io else 0,
                "write_count": disk_io.write_count if disk_io else 0
            },
            "network": {
                "bytes_sent": net_io.bytes_sent,
                "bytes_recv": net_io.bytes_recv,
                "packets_sent": net_io.packets_sent,
                "packets_recv": net_io.packets_recv,
                "errin": net_io.errin,
                "errout": net_io.errout,
                "dropin": net_io.dropin,
                "dropout": net_io.dropout,
                "connections": net_connections
            },
            "processes": {
                "count": process_count
            }
        }

        # Сохранение в историю
        for key, value in metrics.items():
            if key != "timestamp":
                self.metrics_history[key].append({
                    "timestamp": timestamp,
                    "data": value
                })

        # Проверка порогов
        await self._check_thresholds(metrics)

        return metrics

    async def _check_thresholds(self, metrics: dict):
        """Проверка метрик на превышение порогов"""
        alerts = []

        # CPU
        if metrics["cpu"]["percent"] > self.thresholds["cpu_percent"]:
            alerts.append({
                "type": "cpu",
                "severity": "warning",
                "message": f"High CPU usage: {metrics['cpu']['percent']:.1f}%",
                "value": metrics["cpu"]["percent"],
                "threshold": self.thresholds["cpu_percent"]
            })

        # Память
        if metrics["memory"]["percent"] > self.thresholds["memory_percent"]:
            alerts.append({
                "type": "memory",
                "severity": "warning",
                "message": f"High memory usage: {metrics['memory']['percent']:.1f}%",
                "value": metrics["memory"]["percent"],
                "threshold": self.thresholds["memory_percent"]
            })

        # Диск
        if metrics["disk"]["percent"] > self.thresholds["disk_percent"]:
            alerts.append({
                "type": "disk",
                "severity": "critical",
                "message": f"High disk usage: {metrics['disk']['percent']:.1f}%",
                "value": metrics["disk"]["percent"],
                "threshold": self.thresholds["disk_percent"]
            })

        # Сетевые ошибки
        total_errors = metrics["network"]["errin"] + metrics["network"]["errout"]
        if total_errors > self.thresholds["network_errors"]:
            alerts.append({
                "type": "network",
                "severity": "warning",
                "message": f"High network errors: {total_errors}",
                "value": total_errors,
                "threshold": self.thresholds["network_errors"]
            })

        # Добавление алертов
        for alert in alerts:
            alert["timestamp"] = metrics["timestamp"]
            self.alerts.append(alert)
            logger.warning(f"Alert: {alert['message']}")

    async def get_status(self) -> dict:
        """Получение текущего статуса системы"""
        # Сбор текущих метрик
        metrics = await self._collect_metrics()

        # Системная информация
        system_info = await self.get_system_info()

        # Последние алерты
        recent_alerts = list(self.alerts)[-10:]

        return {
            "status": "healthy" if not recent_alerts else "warning",
            "metrics": metrics,
            "system_info": system_info,
            "alerts": recent_alerts,
            "monitoring_active": self.monitoring_active
        }

    async def get_system_info(self) -> dict:
        """Получение информации о системе"""
        # Кэширование на 60 секунд
        if self._system_info_cache and time.time() - self._system_info_cache_time < 60:
            return self._system_info_cache

        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time

        info = {
            "platform": {
                "system": platform.system(),
                "node": platform.node(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor()
            },
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation()
            },
            "boot_time": boot_time.isoformat(),
            "uptime": str(uptime),
            "users": [user._asdict() for user in psutil.users()]
        }

        self._system_info_cache = info
        self._system_info_cache_time = time.time()

        return info

    async def get_processes(self, sort_by: str = "cpu_percent", limit: int = 20) -> List[dict]:
        """Получение списка процессов"""
        processes = []

        for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent',
                                        'memory_percent', 'status', 'create_time']):
            try:
                pinfo = proc.info
                # Добавление дополнительной информации
                with proc.oneshot():
                    pinfo['memory_info'] = proc.memory_info()._asdict()
                    pinfo['num_threads'] = proc.num_threads()

                processes.append(pinfo)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        # Сортировка
        if sort_by in ['cpu_percent', 'memory_percent']:
            processes.sort(key=lambda x: x.get(sort_by, 0), reverse=True)

        return processes[:limit]

    async def get_network_connections(self, kind: str = 'inet') -> List[dict]:
        """Получение сетевых соединений"""
        connections = []

        for conn in psutil.net_connections(kind=kind):
            conn_dict = {
                "fd": conn.fd,
                "family": conn.family.name,
                "type": conn.type.name,
                "laddr": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else None,
                "raddr": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else None,
                "status": conn.status if hasattr(conn, 'status') else None,
                "pid": conn.pid
            }

            # Получение имени процесса
            if conn.pid:
                try:
                    proc = psutil.Process(conn.pid)
                    conn_dict["process_name"] = proc.name()
                except:
                    conn_dict["process_name"] = None

            connections.append(conn_dict)

        return connections

    async def get_disk_partitions(self) -> List[dict]:
        """Получение информации о дисковых разделах"""
        partitions = []

        for part in psutil.disk_partitions():
            part_dict = {
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "opts": part.opts
            }

            try:
                usage = psutil.disk_usage(part.mountpoint)
                part_dict.update({
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent
                })
            except PermissionError:
                part_dict.update({
                    "total": None,
                    "used": None,
                    "free": None,
                    "percent": None
                })

            partitions.append(part_dict)

        return partitions

    async def get_metrics_history(self, metric_type: str, duration_minutes: int = 60) -> List[dict]:
        """Получение истории метрик"""
        if metric_type not in self.metrics_history:
            return []

        # Фильтрация по времени
        cutoff_time = time.time() - (duration_minutes * 60)
        history = []

        for entry in self.metrics_history[metric_type]:
            if entry["timestamp"] >= cutoff_time:
                history.append(entry)

        return history

    async def kill_process(self, pid: int) -> dict:
        """Завершение процесса"""
        try:
            proc = psutil.Process(pid)
            proc.terminate()

            # Ожидание завершения
            gone, alive = psutil.wait_procs([proc], timeout=3)

            if alive:
                # Принудительное завершение
                for p in alive:
                    p.kill()

            return {"status": "success", "pid": pid}

        except psutil.NoSuchProcess:
            return {"status": "error", "message": "Process not found"}
        except psutil.AccessDenied:
            return {"status": "error", "message": "Access denied"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def set_threshold(self, metric: str, value: float):
        """Установка порога для метрики"""
        if metric in self.thresholds:
            self.thresholds[metric] = value
            logger.info(f"Threshold for {metric} set to {value}")

    def get_thresholds(self) -> dict:
        """Получение текущих порогов"""
        return self.thresholds.copy()

    def clear_alerts(self):
        """Очистка алертов"""
        self.alerts.clear()
        logger.info("Alerts cleared")
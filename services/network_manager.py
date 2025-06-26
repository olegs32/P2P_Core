"""
Network management service for P2P Admin System
"""

import asyncio
import socket
import struct
import logging
from typing import List, Dict, Optional, Tuple
from ipaddress import ip_address, ip_network
import netifaces
import subprocess
import platform

logger = logging.getLogger(__name__)


class NetworkManagerService:
    """Сервис управления сетью"""

    def __init__(self):
        self.platform = platform.system().lower()
        self.scan_results_cache = {}
        self.interface_cache = {}
        self.cache_ttl = 60  # секунд

    async def get_interfaces(self) -> List[dict]:
        """Получение списка сетевых интерфейсов"""
        interfaces = []

        try:
            for iface in netifaces.interfaces():
                iface_info = {
                    "name": iface,
                    "addresses": {},
                    "status": "unknown"
                }

                # Получение адресов
                addrs = netifaces.ifaddresses(iface)

                # IPv4 адреса
                if netifaces.AF_INET in addrs:
                    ipv4_info = []
                    for addr in addrs[netifaces.AF_INET]:
                        ipv4_info.append({
                            "address": addr.get('addr'),
                            "netmask": addr.get('netmask'),
                            "broadcast": addr.get('broadcast')
                        })
                    iface_info["addresses"]["ipv4"] = ipv4_info

                # IPv6 адреса
                if netifaces.AF_INET6 in addrs:
                    ipv6_info = []
                    for addr in addrs[netifaces.AF_INET6]:
                        ipv6_info.append({
                            "address": addr.get('addr'),
                            "netmask": addr.get('netmask')
                        })
                    iface_info["addresses"]["ipv6"] = ipv6_info

                # MAC адрес
                if netifaces.AF_LINK in addrs:
                    mac_info = addrs[netifaces.AF_LINK][0]
                    iface_info["mac_address"] = mac_info.get('addr')

                # Статус интерфейса
                iface_info["status"] = await self._get_interface_status(iface)

                # Статистика интерфейса
                stats = await self._get_interface_stats(iface)
                if stats:
                    iface_info["statistics"] = stats

                interfaces.append(iface_info)

            return interfaces

        except Exception as e:
            logger.error(f"Failed to get interfaces: {e}")
            return []

    async def _get_interface_status(self, interface: str) -> str:
        """Получение статуса интерфейса"""
        try:
            # Проверка через файловую систему (Linux)
            if self.platform == "linux":
                with open(f"/sys/class/net/{interface}/operstate", "r") as f:
                    return f.read().strip()

            # Для других платформ используем базовую проверку
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("8.8.8.8", 80))
                if sock.getsockname()[0]:
                    return "up"
            except:
                return "down"
            finally:
                sock.close()

        except Exception:
            return "unknown"

    async def _get_interface_stats(self, interface: str) -> Optional[dict]:
        """Получение статистики интерфейса"""
        try:
            if self.platform == "linux":
                stats = {}
                base_path = f"/sys/class/net/{interface}/statistics"

                stat_files = [
                    "rx_bytes", "tx_bytes",
                    "rx_packets", "tx_packets",
                    "rx_errors", "tx_errors",
                    "rx_dropped", "tx_dropped"
                ]

                for stat in stat_files:
                    try:
                        with open(f"{base_path}/{stat}", "r") as f:
                            stats[stat] = int(f.read().strip())
                    except:
                        stats[stat] = 0

                return stats

            return None

        except Exception as e:
            logger.error(f"Failed to get interface stats: {e}")
            return None

    async def scan_ports(self, target: str, port_range: str = "1-1000",
                         timeout: float = 1.0) -> dict:
        """Сканирование портов"""
        try:
            # Парсинг диапазона портов
            if "-" in port_range:
                start, end = map(int, port_range.split("-"))
            else:
                start = end = int(port_range)

            # Проверка валидности
            if start < 1 or end > 65535 or start > end:
                return {
                    "status": "error",
                    "message": "Invalid port range"
                }

            # Ограничение количества портов
            if end - start > 1000:
                return {
                    "status": "error",
                    "message": "Port range too large (max 1000 ports)"
                }

            # Проверка цели
            try:
                target_ip = str(ip_address(target))
            except ValueError:
                # Попытка резолвинга имени хоста
                try:
                    target_ip = socket.gethostbyname(target)
                except socket.gaierror:
                    return {
                        "status": "error",
                        "message": "Invalid target or hostname"
                    }

            # Выполнение сканирования
            open_ports = []
            tasks = []

            async def check_port(port: int):
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(target_ip, port),
                        timeout=timeout
                    )
                    writer.close()
                    await writer.wait_closed()
                    return port
                except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                    return None

            # Создание задач для параллельного сканирования
            for port in range(start, end + 1):
                tasks.append(check_port(port))

            # Выполнение с ограничением параллельности
            semaphore = asyncio.Semaphore(50)  # Макс 50 параллельных проверок

            async def limited_check(port_task):
                async with semaphore:
                    return await port_task

            results = await asyncio.gather(*[limited_check(task) for task in tasks])

            # Сбор открытых портов
            for port in results:
                if port is not None:
                    service = self._get_service_name(port)
                    open_ports.append({
                        "port": port,
                        "service": service,
                        "state": "open"
                    })

            return {
                "status": "success",
                "target": target,
                "target_ip": target_ip,
                "scanned_range": f"{start}-{end}",
                "open_ports": open_ports,
                "total_scanned": end - start + 1,
                "total_open": len(open_ports)
            }

        except Exception as e:
            logger.error(f"Port scan failed: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    def _get_service_name(self, port: int) -> str:
        """Получение имени сервиса по порту"""
        common_ports = {
            20: "ftp-data",
            21: "ftp",
            22: "ssh",
            23: "telnet",
            25: "smtp",
            53: "dns",
            80: "http",
            110: "pop3",
            111: "rpcbind",
            135: "msrpc",
            139: "netbios-ssn",
            143: "imap",
            443: "https",
            445: "microsoft-ds",
            993: "imaps",
            995: "pop3s",
            1723: "pptp",
            3306: "mysql",
            3389: "rdp",
            5432: "postgresql",
            5900: "vnc",
            6379: "redis",
            8080: "http-proxy",
            8443: "https-alt",
            27017: "mongodb"
        }

        try:
            # Попытка получить из системы
            service = socket.getservbyport(port)
            return service
        except:
            # Использование известных портов
            return common_ports.get(port, f"unknown")

    async def ping(self, target: str, count: int = 4) -> dict:
        """Пинг хоста"""
        try:
            # Команда ping в зависимости от платформы
            if self.platform == "windows":
                cmd = ["ping", "-n", str(count), target]
            else:
                cmd = ["ping", "-c", str(count), target]

            # Выполнение команды
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                output = stdout.decode('utf-8')

                # Парсинг результатов
                stats = self._parse_ping_output(output)

                return {
                    "status": "success",
                    "target": target,
                    "reachable": True,
                    "statistics": stats,
                    "output": output
                }
            else:
                return {
                    "status": "error",
                    "target": target,
                    "reachable": False,
                    "message": stderr.decode('utf-8')
                }

        except Exception as e:
            logger.error(f"Ping failed: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    def _parse_ping_output(self, output: str) -> dict:
        """Парсинг вывода ping"""
        stats = {
            "packets_sent": 0,
            "packets_received": 0,
            "packet_loss": 0.0,
            "min_rtt": 0.0,
            "avg_rtt": 0.0,
            "max_rtt": 0.0
        }

        try:
            lines = output.split('\n')

            for line in lines:
                # Статистика пакетов
                if "packets transmitted" in line or "packets sent" in line:
                    parts = line.split()
                    stats["packets_sent"] = int(parts[0])
                    stats["packets_received"] = int(parts[3])

                    # Процент потерь
                    for part in parts:
                        if "%" in part:
                            stats["packet_loss"] = float(part.rstrip('%'))

                # RTT статистика
                if "min/avg/max" in line:
                    # Формат: rtt min/avg/max/mdev = X.XXX/X.XXX/X.XXX/X.XXX ms
                    rtt_part = line.split('=')[1].strip()
                    rtt_values = rtt_part.split('/')[0:3]
                    stats["min_rtt"] = float(rtt_values[0])
                    stats["avg_rtt"] = float(rtt_values[1])
                    stats["max_rtt"] = float(rtt_values[2].split()[0])

        except Exception as e:
            logger.error(f"Failed to parse ping output: {e}")

        return stats

    async def traceroute(self, target: str, max_hops: int = 30) -> dict:
        """Трассировка маршрута"""
        try:
            # Команда traceroute в зависимости от платформы
            if self.platform == "windows":
                cmd = ["tracert", "-h", str(max_hops), target]
            else:
                cmd = ["traceroute", "-m", str(max_hops), target]

            # Выполнение команды
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Чтение вывода построчно
            hops = []
            hop_num = 0

            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

                line = line.decode('utf-8').strip()
                if not line:
                    continue

                # Парсинг строки с хопом
                hop_info = self._parse_traceroute_line(line)
                if hop_info:
                    hop_info["hop"] = hop_num
                    hops.append(hop_info)
                    hop_num += 1

            await proc.wait()

            return {
                "status": "success",
                "target": target,
                "hops": hops,
                "total_hops": len(hops)
            }

        except Exception as e:
            logger.error(f"Traceroute failed: {e}")
            return {
                "status": "error",
                "message": str(e)
            }

    def _parse_traceroute_line(self, line: str) -> Optional[dict]:
        """Парсинг строки traceroute"""
        try:
            # Пропускаем заголовки и пустые строки
            if not line or line.startswith('traceroute') or line.startswith('Tracing'):
                return None

            # Простой парсер для базовой информации
            parts = line.split()
            if len(parts) < 2:
                return None

            # Проверка на номер хопа
            if not parts[0].replace('.', '').isdigit():
                return None

            hop_info = {
                "address": None,
                "hostname": None,
                "rtt": []
            }

            # Поиск IP адреса и RTT
            for part in parts[1:]:
                if '.' in part and self._is_ip_address(part):
                    hop_info["address"] = part
                elif part.replace('.', '').replace('ms', '').isdigit():
                    try:
                        rtt = float(part.replace('ms', ''))
                        hop_info["rtt"].append(rtt)
                    except:
                        pass
                elif part == '*':
                    hop_info["rtt"].append(None)
                elif '(' not in part and ')' not in part and not part.startswith('-'):
                    if not hop_info["hostname"]:
                        hop_info["hostname"] = part

            return hop_info

        except Exception:
            return None

    def _is_ip_address(self, addr: str) -> bool:
        """Проверка, является ли строка IP адресом"""
        try:
            ip_address(addr)
            return True
        except ValueError:
            return False

    async def get_routing_table(self) -> List[dict]:
        """Получение таблицы маршрутизации"""
        routes = []

        try:
            if self.platform == "linux":
                # Чтение из /proc/net/route
                with open("/proc/net/route", "r") as f:
                    lines = f.readlines()[1:]  # Пропускаем заголовок

                for line in lines:
                    fields = line.strip().split()
                    if len(fields) >= 8:
                        route = {
                            "interface": fields[0],
                            "destination": self._hex_to_ip(fields[1]),
                            "gateway": self._hex_to_ip(fields[2]),
                            "flags": int(fields[3], 16),
                            "metric": int(fields[6])
                        }
                        routes.append(route)

            elif self.platform == "windows":
                # Использование netsh
                cmd = ["netsh", "interface", "ipv4", "show", "route"]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()

                # Простой парсинг вывода Windows
                # TODO: Реализовать парсинг

            return routes

        except Exception as e:
            logger.error(f"Failed to get routing table: {e}")
            return []

    def _hex_to_ip(self, hex_ip: str) -> str:
        """Конвертация hex в IP адрес"""
        try:
            # Linux хранит IP в little-endian hex
            addr = int(hex_ip, 16)
            return socket.inet_ntoa(struct.pack("<L", addr))
        except:
            return "0.0.0.0"

    async def get_arp_table(self) -> List[dict]:
        """Получение ARP таблицы"""
        arp_entries = []

        try:
            if self.platform == "linux":
                with open("/proc/net/arp", "r") as f:
                    lines = f.readlines()[1:]  # Пропускаем заголовок

                for line in lines:
                    fields = line.strip().split()
                    if len(fields) >= 6:
                        arp_entries.append({
                            "ip_address": fields[0],
                            "hw_type": fields[1],
                            "flags": fields[2],
                            "mac_address": fields[3],
                            "interface": fields[5]
                        })

            elif self.platform == "windows":
                # Использование arp -a
                cmd = ["arp", "-a"]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()

                # TODO: Парсинг вывода Windows

            return arp_entries

        except Exception as e:
            logger.error(f"Failed to get ARP table: {e}")
            return []
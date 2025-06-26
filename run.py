#!/usr/bin/env python3
"""
P2P Admin System - Main Runner
Главный файл для запуска P2P узла системы администрирования
"""

import asyncio
import argparse
import logging
import os
import signal
import sys
from pathlib import Path
from typing import List, Optional

import uvicorn
from dotenv import load_dotenv

# Добавляем корневую директорию в PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent))

from core.p2p_node import P2PNode
from api.main import create_app
from config.settings import Settings
from services.process_manager import ProcessManagerService
from services.file_manager import FileManagerService
from services.network_manager import NetworkManagerService
from services.system_monitor import SystemMonitorService

# Загрузка переменных окружения
load_dotenv()

# Глобальные переменные для обработки shutdown
shutdown_event = asyncio.Event()
p2p_node: Optional[P2PNode] = None


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None):
    """Настройка логирования"""
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Конфигурация корневого логгера
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=log_format,
        handlers=[]
    )

    # Консольный обработчик
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(console_handler)

    # Файловый обработчик (если указан)
    if log_file:
        log_dir = Path(log_file).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(log_format))
        logging.getLogger().addHandler(file_handler)

    # Настройка уровней для различных модулей
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_arguments():
    """Парсинг аргументов командной строки"""
    parser = argparse.ArgumentParser(
        description="P2P Admin System Node",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Запуск первого узла (bootstrap)
  python run.py --host 0.0.0.0 --port 8000 --dht-port 5678

  # Запуск узла с подключением к существующей сети
  python run.py --host 0.0.0.0 --port 8001 --dht-port 5679 --bootstrap 192.168.1.100:5678

  # Запуск с кастомной конфигурацией
  python run.py --config config/production.yaml
        """
    )

    # Основные параметры
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("NODE_HOST", "127.0.0.1"),
        help="IP адрес для привязки узла"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("NODE_PORT", "8000")),
        help="Порт для API сервера"
    )
    parser.add_argument(
        "--dht-port",
        type=int,
        default=int(os.getenv("DHT_PORT", "5678")),
        help="Порт для DHT сервера"
    )

    # Bootstrap узлы
    parser.add_argument(
        "--bootstrap",
        type=str,
        nargs="*",
        default=os.getenv("BOOTSTRAP_NODES", "").split(",") if os.getenv("BOOTSTRAP_NODES") else [],
        help="Bootstrap узлы в формате host:port"
    )

    # Конфигурация
    parser.add_argument(
        "--config",
        type=str,
        default=os.getenv("CONFIG_FILE", "config/settings.yaml"),
        help="Путь к файлу конфигурации"
    )

    # Логирование
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=os.getenv("LOG_LEVEL", "INFO"),
        help="Уровень логирования"
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=os.getenv("LOG_FILE"),
        help="Путь к файлу логов"
    )

    # Режим работы
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("WORKERS", "1")),
        help="Количество worker процессов (1 для debug режима)"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=os.getenv("RELOAD", "False").lower() == "true",
        help="Автоматическая перезагрузка при изменении кода"
    )

    # Сервисы
    parser.add_argument(
        "--disable-services",
        type=str,
        nargs="*",
        default=[],
        help="Список сервисов для отключения"
    )

    # Безопасность
    parser.add_argument(
        "--auth-secret",
        type=str,
        default=os.getenv("AUTH_SECRET", "your-secret-key-change-in-production"),
        help="Секретный ключ для JWT токенов"
    )
    parser.add_argument(
        "--ssl-cert",
        type=str,
        default=os.getenv("SSL_CERT"),
        help="Путь к SSL сертификату"
    )
    parser.add_argument(
        "--ssl-key",
        type=str,
        default=os.getenv("SSL_KEY"),
        help="Путь к SSL ключу"
    )

    return parser.parse_args()


def parse_bootstrap_nodes(bootstrap_strings: List[str]) -> List[tuple]:
    """Парсинг bootstrap узлов из строк"""
    nodes = []
    for node_str in bootstrap_strings:
        if not node_str:
            continue
        try:
            host, port = node_str.split(":")
            nodes.append((host, int(port)))
        except ValueError:
            logging.error(f"Invalid bootstrap node format: {node_str}")
    return nodes


async def initialize_services(node: P2PNode, disabled_services: List[str]):
    """Инициализация и регистрация сервисов"""
    logger = logging.getLogger(__name__)

    # Процесс менеджер
    if "process" not in disabled_services:
        process_service = ProcessManagerService()
        node.register_service("system.process", process_service)
        logger.info("Process Manager Service registered")

    # Файловый менеджер
    if "file" not in disabled_services:
        file_service = FileManagerService()
        node.register_service("system.file", file_service)
        logger.info("File Manager Service registered")

    # Сетевой менеджер
    if "network" not in disabled_services:
        network_service = NetworkManagerService()
        node.register_service("system.network", network_service)
        logger.info("Network Manager Service registered")

    # Системный монитор
    if "monitor" not in disabled_services:
        monitor_service = SystemMonitorService()
        node.register_service("system.monitor", monitor_service)

        # Запуск фонового мониторинга
        asyncio.create_task(monitor_service.start_monitoring())
        logger.info("System Monitor Service registered and started")


async def setup_task_handlers(node: P2PNode):
    """Настройка обработчиков задач"""

    # Обработчик для выполнения команд
    async def execute_command_handler(data: dict):
        command = data.get("command")
        args = data.get("args", [])

        import subprocess
        try:
            result = subprocess.run(
                [command] + args,
                capture_output=True,
                text=True,
                timeout=30
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode
            }
        except Exception as e:
            return {"error": str(e)}

    # Обработчик для сбора логов
    async def collect_logs_handler(data: dict):
        log_path = data.get("path")
        lines = data.get("lines", 100)

        try:
            with open(log_path, "r") as f:
                log_lines = f.readlines()[-lines:]
            return {"logs": log_lines}
        except Exception as e:
            return {"error": str(e)}

    # Обработчик для обновления конфигурации
    async def update_config_handler(data: dict):
        config_path = data.get("path")
        config_data = data.get("data")

        try:
            import json
            with open(config_path, "w") as f:
                json.dump(config_data, f, indent=2)
            return {"status": "success", "path": config_path}
        except Exception as e:
            return {"error": str(e)}

    # Регистрация обработчиков
    node.register_task_handler("execute_command", execute_command_handler)
    node.register_task_handler("collect_logs", collect_logs_handler)
    node.register_task_handler("update_config", update_config_handler)


def setup_signal_handlers():
    """Настройка обработчиков сигналов"""

    def signal_handler(sig, frame):
        logger = logging.getLogger(__name__)
        logger.info(f"Received signal {sig}")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


async def graceful_shutdown(node: P2PNode):
    """Graceful shutdown"""
    logger = logging.getLogger(__name__)
    logger.info("Starting graceful shutdown...")

    try:
        # Остановка приема новых задач
        node.accepting_tasks = False

        # Ожидание завершения активных задач (макс 30 сек)
        active_tasks = [t for t in node.active_tasks.values() if t["status"] == "running"]
        if active_tasks:
            logger.info(f"Waiting for {len(active_tasks)} tasks to complete...")
            await asyncio.wait_for(
                asyncio.gather(*[
                    wait_for_task_completion(node, task["id"])
                    for task in active_tasks
                ], return_exceptions=True),
                timeout=30.0
            )

        # Остановка узла
        await node.stop()
        logger.info("P2P node stopped")

    except asyncio.TimeoutError:
        logger.warning("Shutdown timeout reached, forcing shutdown")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


async def wait_for_task_completion(node: P2PNode, task_id: str, timeout: float = 30.0):
    """Ожидание завершения задачи"""
    start_time = asyncio.get_event_loop().time()
    while True:
        task = node.active_tasks.get(task_id)
        if not task or task["status"] in ["completed", "failed"]:
            break

        if asyncio.get_event_loop().time() - start_time > timeout:
            raise asyncio.TimeoutError(f"Task {task_id} timeout")

        await asyncio.sleep(0.1)


async def run_node(args):
    """Запуск P2P узла"""
    global p2p_node
    logger = logging.getLogger(__name__)

    # Создание настроек
    settings = Settings(
        node_host=args.host,
        node_port=args.port,
        dht_port=args.dht_port,
        auth_secret=args.auth_secret
    )

    # Создание P2P узла
    p2p_node = P2PNode(host=args.host, port=args.port)

    # Парсинг bootstrap узлов
    bootstrap_nodes = parse_bootstrap_nodes(args.bootstrap)

    # Запуск узла
    logger.info(f"Starting P2P node on {args.host}:{args.port}")
    await p2p_node.start(bootstrap_nodes)

    # Инициализация сервисов
    await initialize_services(p2p_node, args.disable_services)

    # Настройка обработчиков задач
    await setup_task_handlers(p2p_node)

    # Создание FastAPI приложения
    app = create_app(p2p_node, settings)

    # Конфигурация Uvicorn
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        workers=args.workers if not args.reload else 1,
        reload=args.reload,
        log_level=args.log_level.lower(),
        ssl_keyfile=args.ssl_key,
        ssl_certfile=args.ssl_cert,
        access_log=args.log_level == "DEBUG"
    )

    # Запуск сервера
    server = uvicorn.Server(config)

    # Запуск в отдельной задаче
    server_task = asyncio.create_task(server.serve())

    # Ожидание сигнала остановки
    await shutdown_event.wait()

    # Graceful shutdown
    logger.info("Shutting down server...")
    server.should_exit = True
    await server_task

    # Остановка P2P узла
    await graceful_shutdown(p2p_node)


def main():
    """Главная функция"""
    # Парсинг аргументов
    args = parse_arguments()

    # Настройка логирования
    setup_logging(args.log_level, args.log_file)
    logger = logging.getLogger(__name__)

    # Вывод информации о запуске
    logger.info("=" * 60)
    logger.info("P2P Admin System Node")
    logger.info(f"Host: {args.host}:{args.port}")
    logger.info(f"DHT Port: {args.dht_port}")
    logger.info(f"Bootstrap nodes: {args.bootstrap}")
    logger.info(f"Workers: {args.workers}")
    logger.info(f"Reload: {args.reload}")
    logger.info(f"Log level: {args.log_level}")
    logger.info("=" * 60)

    # Настройка обработчиков сигналов
    setup_signal_handlers()

    # Запуск асинхронного кода
    try:
        asyncio.run(run_node(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

    logger.info("Node stopped")


if __name__ == "__main__":
    main()
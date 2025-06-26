#!/usr/bin/env python3
"""
Test script for P2P Admin System
Демонстрирует основные возможности системы
"""

import asyncio
import sys
import time
from pathlib import Path

# Добавляем корневую директорию в PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

console = Console()


class P2PSystemTester:
    """Класс для тестирования P2P системы"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def check_health(self) -> bool:
        """Проверка здоровья узла"""
        try:
            response = await self.client.get(f"{self.base_url}/health")
            if response.status_code == 200:
                data = response.json()
                console.print(f"[green]✓ Node is healthy[/green] - ID: {data['node_id']}")
                return True
            else:
                console.print(f"[red]✗ Node health check failed[/red] - Status: {response.status_code}")
                return False
        except Exception as e:
            console.print(f"[red]✗ Failed to connect[/red]: {e}")
            return False

    async def get_network_status(self):
        """Получение статуса сети"""
        try:
            response = await self.client.get(f"{self.base_url}/api/stats")
            if response.status_code == 200:
                data = response.json()

                # Создание таблицы со статусом
                table = Table(title="Network Status")
                table.add_column("Metric", style="cyan")
                table.add_column("Value", style="green")

                # Системные метрики
                table.add_row("CPU Usage", f"{data['system']['cpu_percent']:.1f}%")
                table.add_row("Memory Usage", f"{data['system']['memory']['percent']:.1f}%")
                table.add_row("Disk Usage", f"{data['system']['disk']['percent']:.1f}%")

                # P2P метрики
                table.add_row("Connected Peers", str(data['p2p']['peers']))
                table.add_row("Active Tasks", str(data['p2p']['active_tasks']))
                table.add_row("Completed Tasks", str(data['p2p']['completed_tasks']))
                table.add_row("Failed Tasks", str(data['p2p']['failed_tasks']))

                console.print(table)
        except Exception as e:
            console.print(f"[red]Failed to get network status:[/red] {e}")

    async def list_services(self):
        """Список доступных сервисов"""
        try:
            response = await self.client.get(f"{self.base_url}/api/v1/p2p/services")
            if response.status_code == 200:
                services = response.json()

                table = Table(title="Available Services")
                table.add_column("Service Name", style="cyan")
                table.add_column("Status", style="green")

                for service in services:
                    table.add_row(service['name'], "Available")

                console.print(table)
        except Exception as e:
            console.print(f"[red]Failed to list services:[/red] {e}")

    async def execute_command(self, command: str):
        """Выполнение команды"""
        console.print(f"\n[yellow]Executing command:[/yellow] {command}")

        try:
            response = await self.client.post(
                f"{self.base_url}/api/v1/execute",
                json={"command": command, "timeout": 10}
            )

            if response.status_code == 200:
                result = response.json()
                if result['status'] == 'success':
                    console.print("[green]✓ Command executed successfully[/green]")
                    if result.get('stdout'):
                        console.print("\n[cyan]Output:[/cyan]")
                        console.print(result['stdout'])
                    if result.get('stderr'):
                        console.print("\n[red]Errors:[/red]")
                        console.print(result['stderr'])
                else:
                    console.print(f"[red]✗ Command failed:[/red] {result.get('message')}")
            else:
                console.print(f"[red]✗ Request failed:[/red] Status {response.status_code}")
        except Exception as e:
            console.print(f"[red]✗ Failed to execute command:[/red] {e}")

    async def submit_task(self, task_type: str, task_data: dict):
        """Отправка задачи"""
        console.print(f"\n[yellow]Submitting task:[/yellow] {task_type}")

        try:
            response = await self.client.post(
                f"{self.base_url}/api/v1/tasks",
                json={
                    "type": task_type,
                    "data": task_data
                }
            )

            if response.status_code == 200:
                result = response.json()
                task_id = result.get('task_id')
                console.print(f"[green]✓ Task submitted:[/green] {task_id}")

                # Ожидание выполнения задачи
                await self.wait_for_task(task_id)
            else:
                console.print(f"[red]✗ Failed to submit task:[/red] Status {response.status_code}")
        except Exception as e:
            console.print(f"[red]✗ Failed to submit task:[/red] {e}")

    async def wait_for_task(self, task_id: str, timeout: int = 30):
        """Ожидание выполнения задачи"""
        start_time = time.time()

        with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True,
        ) as progress:
            task = progress.add_task(f"Waiting for task {task_id}...", total=None)

            while time.time() - start_time < timeout:
                try:
                    response = await self.client.get(f"{self.base_url}/api/v1/tasks/{task_id}")
                    if response.status_code == 200:
                        task_data = response.json()
                        status = task_data.get('status')

                        if status == 'completed':
                            progress.stop()
                            console.print(f"[green]✓ Task completed successfully[/green]")
                            if task_data.get('result'):
                                console.print(f"Result: {task_data['result']}")
                            return
                        elif status == 'failed':
                            progress.stop()
                            console.print(f"[red]✗ Task failed:[/red] {task_data.get('error')}")
                            return

                except Exception:
                    pass

                await asyncio.sleep(1)

            progress.stop()
            console.print(f"[yellow]⚠ Task timeout after {timeout} seconds[/yellow]")

    async def test_file_operations(self):
        """Тестирование файловых операций"""
        console.print("\n[yellow]Testing file operations...[/yellow]")

        # Список файлов
        try:
            response = await self.client.get(
                f"{self.base_url}/api/v1/files/list",
                params={"path": "/tmp"}
            )

            if response.status_code == 200:
                result = response.json()
                console.print(f"[green]✓ Listed {result['count']} files in /tmp[/green]")
        except Exception as e:
            console.print(f"[red]✗ Failed to list files:[/red] {e}")

    async def test_network_operations(self):
        """Тестирование сетевых операций"""
        console.print("\n[yellow]Testing network operations...[/yellow]")

        # Получение интерфейсов
        try:
            response = await self.client.get(f"{self.base_url}/api/v1/network/interfaces")

            if response.status_code == 200:
                interfaces = response.json()

                table = Table(title="Network Interfaces")
                table.add_column("Interface", style="cyan")
                table.add_column("IP Address", style="green")
                table.add_column("Status", style="yellow")

                for iface in interfaces:
                    ipv4_addrs = iface.get('addresses', {}).get('ipv4', [])
                    ip = ipv4_addrs[0]['address'] if ipv4_addrs else 'N/A'
                    table.add_row(iface['name'], ip, iface['status'])

                console.print(table)
        except Exception as e:
            console.print(f"[red]✗ Failed to get interfaces:[/red] {e}")

    async def close(self):
        """Закрытие клиента"""
        await self.client.aclose()


async def main():
    """Основная функция тестирования"""
    console.print("[bold cyan]P2P Admin System Test Script[/bold cyan]\n")

    # Проверка аргументов командной строки
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    else:
        base_url = "http://localhost:8000"

    console.print(f"Testing node at: {base_url}\n")

    tester = P2PSystemTester(base_url)

    try:
        # 1. Проверка здоровья
        if not await tester.check_health():
            console.print("[red]Node is not healthy, exiting...[/red]")
            return

        # 2. Статус сети
        console.print("\n[bold]Network Status:[/bold]")
        await tester.get_network_status()

        # 3. Список сервисов
        console.print("\n[bold]Available Services:[/bold]")
        await tester.list_services()

        # 4. Выполнение команд
        console.print("\n[bold]Command Execution:[/bold]")
        await tester.execute_command("echo 'Hello from P2P system!'")
        await tester.execute_command("date")
        await tester.execute_command("pwd")

        # 5. Отправка задач
        console.print("\n[bold]Task Submission:[/bold]")
        await tester.submit_task("execute_command", {
            "command": "ls",
            "args": ["-la", "/tmp"]
        })

        # 6. Файловые операции
        await tester.test_file_operations()

        # 7. Сетевые операции
        await tester.test_network_operations()

        console.print("\n[green]✓ All tests completed![/green]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Test interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"\n[red]Test failed with error:[/red] {e}")
    finally:
        await tester.close()


if __name__ == "__main__":
    asyncio.run(main())
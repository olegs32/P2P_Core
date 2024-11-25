import json
import logging
import pathlib
import socket
import tarfile

import httpx
import asyncio
import os
import shutil
import configparser
from subprocess import Popen, call, PIPE
from pathlib import Path

logging.basicConfig(level=logging.INFO)


class CoreClient:
    def __init__(self, server='http://127.0.0.1:8081', project_dir='projects'):
        self.server = server
        self.project_dir = Path(project_dir)
        self.id = 'guest'
        # self.hostname = os.getenv("HOSTNAME", "DefaultHost")
        self.hostname = socket.gethostname()
        self.services = {'hosted_projects': {}}
        self.project_dir.mkdir(exist_ok=True)  # Убедимся, что каталог существует

    async def auth(self):
        """Метод для авторизации клиента на сервере."""
        async with httpx.AsyncClient() as cli:
            try:
                response = await cli.get(f'{self.server}/agent/auth', params={
                    'hostname': self.hostname,
                    'passphrase': self.hostname * 2
                })
                data = response.json()
                if data.get('success'):
                    self.id = data['client_id']
                    logging.info(f"Authorized with ID: {self.id}")
                else:
                    logging.error("Duplicate machine name detected. Exiting...")
                    await asyncio.sleep(20)
                    exit(403)
            except httpx.HTTPStatusError as e:
                logging.error(f"Authorization failed: {e}")

    def scan_services(self):
        """Scans projects in the directory and loads their configurations."""
        for project_path in self.project_dir.glob("*"):
            if project_path.is_dir():
                service_info = {
                    'files': [str(item) for item in project_path.iterdir()]  # List all files in the project directory
                }
                json_file = project_path / 'config.json'
                if json_file.exists():
                    try:
                        # Load configuration from config.json
                        with open(json_file, 'r') as f:
                            config = json.load(f)
                        service_info.update(config)
                        service_info.setdefault('status', 'stopped')  # Default status
                    except json.JSONDecodeError as e:
                        logging.error(f"Error decoding JSON in {json_file}: {e}")
                        continue
                else:
                    logging.warning(f"No config.json found in {project_path}")

                self.services['hosted_projects'][project_path.name] = service_info
                print(service_info)
        logging.info(f"Scanned services: {self.services['hosted_projects']}")

    ''' Control services '''

    async def start_service(self, service_name):
        """Starts a service with the given name."""
        service = self.services['hosted_projects'].get(service_name)
        if service is None:
            logging.warning(f"Service {service_name} not found")
            return

        # Build the absolute path to the loader script
        loader_path = self.project_dir / service_name / service['loader']
        if not loader_path.exists():
            logging.error(f"Loader script not found: {loader_path}")
            return

        # Ensure the path is absolute and prepare the command
        absolute_loader_path = str(loader_path.resolve())
        parameters = service.get('parameters', '')
        full_command = f"{absolute_loader_path} {parameters}"

        # Start the service
        try:
            proc = Popen(full_command, shell=True)
            service['pid'] = proc.pid
            service['status'] = 'running'
            logging.info(f"Started {service_name} with PID {proc.pid}")
        except Exception as e:
            logging.error(f"Failed to start service {service_name}: {e}")

    async def stop_service(self, service_name):
        """Останавливает сервис с заданным именем."""
        service = self.services['hosted_projects'].get(service_name)
        if service and 'pid' in service:
            call(['taskkill', '/F', '/T', '/PID', str(service['pid'])], stdout=PIPE)
            service['status'] = 'stopped'
            service.pop('pid', None)
            logging.info(f"Stopped {service_name}")
        else:
            logging.warning(f"Service {service_name} not running or not found")

    async def restart_service(self, service_name):
        """Перезапускает сервис с заданным именем."""
        await self.stop_service(service_name)
        await self.start_service(service_name)

    async def deploy_service(self, codename):
        """Асинхронный метод для развертывания, обновления и удаления сервиса."""
        url = f'{self.server}/store/deploy?project={codename}'
        project_path = self.project_dir / codename

        project_path.mkdir(exist_ok=True)
        async with httpx.AsyncClient() as cli:
            response = await cli.get(url)
            with open(project_path / 'deploy.tar.gz', 'wb') as file:
                file.write(response.content)
            with tarfile.open(project_path / 'deploy.tar.gz', "r:gz") as tar:
                tar.extractall(project_path)
        # shutil.unpack_archive(str(project_path / 'deploy.tar'), str(self.project_dir))
        logging.info(f"{codename} deployed successfully.")

        self.scan_services()

    async def remove_service(self, codename):
        project_path = self.project_dir / codename
        shutil.rmtree(project_path)
        logging.info(f"{codename} removed successfully.")

        self.scan_services()

    async def post_state(self, operation, state=None):
        if operation == 'all':
            logging.info('Init to post all states')

            async with httpx.AsyncClient() as cli:
                # data = json.loads(self.services.get('hosted_projects', {}))
                resp = await cli.post(f'{self.server}/agent/projects/{self.id}', json=self.services)
                print(resp)
        else:
            async with httpx.AsyncClient() as cli:
                await cli.get(f'{self.server}/agent/update', params={
                    'agent_id': self.id,
                    'operation_id': operation,
                    'state': state
                })


class LongPollClient:
    def __init__(self, client_id: str, server_url: str):
        self.client_id = client_id
        self.server_url = server_url
        self.last_id = 0  # Идентификатор последнего полученного сообщения

    async def get_updates(self):
        """Получает обновления от сервера через Long Polling."""
        logging.info('LP Running!')
        async with httpx.AsyncClient() as cli:
            while True:
                try:
                    response = await cli.get(
                        f"{self.server_url}/agent/lp",
                        params={"client_id": self.client_id, "last_id": self.last_id},
                        timeout=None
                    )
                    if response.status_code == 200:
                        data = response.json()
                        messages = data.get("messages", [])
                        for message in messages:
                            # Processing commands
                            logging.info(f"Received message: {message['msg']}")
                            self.last_id = message['id']
                            # Получаем команду (например, "start", "stop", "deploy")
                            print(message)

                            action = message.get("msg", {}).get('action', None)
                            print(action)
                            # params = message.get("msg").get("params", {})

                            # Проверяем, существует ли метод в CoreClient с таким именем
                            action_method = getattr(client, action, None)

                            if action_method is not None:  # Если метод найден
                                service = message.get("msg").get("service", '')

                                if service:
                                    # Вызов метода с параметром
                                    logging.info(f"Executing {action} for service {service}")
                                    await action_method(service)
                            else:
                                logging.warning(f"Unknown action: {action}")

                    await asyncio.sleep(5)
                except httpx.RequestError as e:
                    logging.error(f"Connection error: {e}")
                    await asyncio.sleep(5)  # Пауза перед повторным запросом


client = CoreClient()


async def main():
    await client.auth()
    client.scan_services()

    # Пример клиента для получения сообщений от сервера через Long Polling
    lp_client = LongPollClient(client_id=client.id, server_url=client.server)
    await asyncio.gather(
        lp_client.get_updates(),
        another_task(),  # Пример фоновой задачи
    )


async def another_task():
    """Пример фоновой задачи, выполняющейся параллельно."""
    while True:
        # logging.info("Running another task...")
        await asyncio.sleep(100)


# Запуск клиента
if __name__ == '__main__':
    asyncio.run(main())

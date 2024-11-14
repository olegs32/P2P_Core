import httpx
import asyncio
import base64
import configparser
import glob
import json
import os
from random import randint
import socket
import http.server
import socketserver
import shutil
import time
import zipfile
from subprocess import call, Popen, PIPE, CREATE_NEW_CONSOLE
from threading import Thread
import requests  # pip install requests

HP_BIND = "0.0.0.0"
HP_PORT = 3280


class CoreClient:
    def __init__(self):
        self.VERSION = 1.0
        self.PROJECT_DIR = 'projects'
        self.SERVER = 'http://127.0.0.1:8081'
        self.db_file = 'client_config.ini'
        self.id = -1
        self.hostname = socket.gethostname()
        self.passphrase = self.hostname + self.hostname
        self.services = {'hosted_projects': {}}
        # self.config = self.get_config()
        # self.auth()

        # self.scan_services()

        os.makedirs(self.PROJECT_DIR, exist_ok=True)

    async def auth(self):
        async with httpx.AsyncClient() as client:
            resp = await client.get(f'{self.SERVER}/agent/auth', params={
                'hostname': self.hostname,
                'passphrase': self.passphrase})
            resp = resp.json()
            if resp['success']:
                if resp['client_id'] != 'DUPLICATE':
                    self.id = resp['client_id']
            else:
                print('FATAL ERROR: DUPLICATE MACHINE WITH SAME NAME DETECTED!')
                print('TERMINATING IN 20 SECONDS')
                print('BYE!')
                await asyncio.sleep(20)
                exit(403)

    def scan_services(self):
        structure = list(os.walk(self.PROJECT_DIR))
        if len(structure) > 1:
            for project in structure[0][1]:
                project_path = os.path.join(self.PROJECT_DIR, project)
                self.services[project] = {'files': os.listdir(project_path)}

                if 'project.ini' in self.services[project]['files']:
                    conf = configparser.ConfigParser()
                    conf.read(os.path.join(project_path, 'project.ini'))
                    for spec in conf.sections():
                        self.services[project].update(conf.items(spec))
                    self.services[project].setdefault('status', 'stopped')

        print(self.services)

    def handle_actions(self, actions):
        for act, elements in actions.items():
            if isinstance(elements, list) and elements:
                print(act)
                for element in elements:
                    action_method = getattr(self, act, None)
                    if action_method:
                        action_method(element)
                        requests.get(f"http://{self.SERVER}/confirm/{self.id}/{act}/{element}")

    def upgrade(self, resp):
        self.deployer(resp['task'], resp['codename'], 'upgrade')

    def downgrade(self, resp):
        self.deployer(resp['task'], resp['codename'], 'downgrade')

    def deploy(self, resp):
        print(resp)
        self.deployer(resp, 'deploy')

    def remove(self, resp):
        self.deployer(resp, 'remove')

    def start(self, srv):
        print(srv)
        command = rf"projects\{srv}\{self.services[srv]['loader']}"
        parameters = self.services[srv]['parameters']
        if parameters:
            command += f" {parameters}"

        proc = Popen(command, creationflags=CREATE_NEW_CONSOLE)
        self.services[srv]['pid'] = proc.pid
        self.services[srv]['status'] = 'running'

    def stop(self, srv):
        call(['taskkill', '/F', '/T', '/PID', str(self.services[srv]['pid'])], stdout=PIPE)
        self.services[srv].update({'killed': True, 'status': 'stopped'})
        self.services[srv].pop('pid', None)

    def restart(self, srv):
        if 'pid' in self.services[srv]:
            self.stop(srv)
        self.start(srv)
        self.services[srv].update({'killed': False, 'status': 'running'})

    def deployer(self, codename, action, url='', silent=False):
        result = 'success'
        url = url or f'/lib/{codename}/deploy.tar'

        if action == 'deploy':
            os.makedirs(rf'projects\{codename}', exist_ok=True)
            resp = requests.get(f'http://{self.SERVER}{url}')
            if resp.status_code == 200:
                with open(rf'projects\{codename}_deploy.tar', 'wb') as tar:
                    tar.write(resp.content)
                shutil.unpack_archive(rf'projects\{codename}_deploy.tar', rf'projects')
            else:
                result = resp.status_code

        elif action == 'remove':
            shutil.rmtree(rf'projects\{codename}')
            os.remove(rf'projects\{codename}_deploy.tar')
            self.services.pop(codename, None)

        elif 'grade' in action:
            self.stop(codename) if 'pid' in self.services[codename] else None
            self.deployer(codename, 'remove', silent=True)
            self.deployer(codename, 'deploy', silent=True)

        self.scan_services()
        if not silent:
            requests.get(
                f"http://{self.SERVER}/cicd/{self.id}/deploy?action=confirm_{action}_{codename}&result={result}")


class LongPollClient:
    def __init__(self, client_id: str, server_url: str):
        self.client_id = client_id
        self.server_url = server_url
        self.last_id = 0  # идентификатор последнего полученного сообщения

    async def get_updates(self):
        """Получает обновления от сервера через Long Polling."""
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    response = await client.get(
                        f"{self.server_url}/agent/lp",
                        params={"client_id": self.client_id, "last_id": self.last_id},
                        timeout=None
                    )
                    if response.status_code == 200:
                        data = response.json()
                        messages = data.get("messages", [])
                        if messages:
                            for message in messages:
                                # Processing messages
                                print(f"Received message: {message['msg']} with id: {message['id']}")
                                self.last_id = message['id']  # обновляем last_id
                        else:
                            print("No new messages")
                    await asyncio.sleep(5)  # Ждем перед повторным запросом
                except Exception as e:
                    print(f"Error in get_updates: {e}")
                    await asyncio.sleep(5)  # Ждем перед повторным подключением в случае ошибки


async def another_task():
    # Пример другой фоновой задачи



    while True:
        print("Running another task...")
        print(client.id)
        await asyncio.sleep(10)


client = CoreClient()


# Пример использования клиента
async def main():
    await client.auth()
    await asyncio.sleep(5)
    lp_client = LongPollClient(client_id=client.id, server_url=client.SERVER)
    await asyncio.gather(
        lp_client.get_updates(),
        another_task(),
    )


# Запуск клиента
# asyncio.create_task(main())
asyncio.run(main())

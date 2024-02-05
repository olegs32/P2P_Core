import base64
import configparser
import glob
import json
import os
from random import randint
import socket
import http.server
from http.server import BaseHTTPRequestHandler
import socketserver

import shutil
import time
import zipfile
from subprocess import call, Popen, PIPE, CREATE_NEW_CONSOLE
from threading import Thread

import requests  # pip install requests

HP_BIND = "0.0.0.0"
HP_PORT = 3280


class client:
    def __init__(self):
        self.ping_timeout = 3
        self.VERSION = 1.0
        self.PROJECT_DIR = 'projects'
        # self.SERVER = '192.168.32.44:8080'
        self.SERVER = '127.0.0.1:8081'
        # self.SERVER = '127.0.0.1:5000'
        self.db_file = 'client_config.ini'
        # services_file = 'services.ini'
        # timeout = 20
        self.passphrase = '1qaz2wsx3edc'
        # self.id = randint(0, 204820482048)
        self.id = -1
        self.hostname = socket.gethostname()
        self.services = {'hosted_projects': {}}
        self.config = self.get_config()
        self.scan_services()
        self.register()

        # print(hostname)
        # global status
        # status = 'free'
        if not os.path.exists(rf'projects'):
            os.mkdir(rf'projects')

        # resp_code = requests.get(f'http://{self.SERVER}/cicd/auth?id={self.id}&hostname={self.hostname}').text
        # print(resp_code)
        # if int(resp_code) != 200:
        #     print('err')

    def register(self):
        resp = requests.get(
            f'http://{self.SERVER}/register?id={self.id}&hostname={self.hostname}&ts={time.time()}').text
        resp = json.loads(resp)
        self.id = resp['id']
        return resp
        # self.config = self.get_config()

    # raise error, file is truncated

    def get_config(self):
        data = requests.get(f'http://{self.SERVER}/cicd/get_config?id={self.id}&passphrase={self.passphrase}').text
        print('data', data)
        # data = data.json()
        data = json.loads(data.replace("'", '"'))
        return data

    # def get_services():
    #     conf = configparser.ConfigParser()
    #     if not os.path.exists(services_file):
    #         conf["test_service"] = {"status": "stopped", 'file': 'test_service.bat'}
    #         with open(services_file, "w") as f:
    #             conf.write(f)
    #     conf.read(services_file)
    #     return conf._sections

    def scan_services(self):
        structure = list(os.walk(self.PROJECT_DIR))
        print(structure)
        if len(structure) > 1:
            for project in structure[0][1]:
                for files in structure:
                    if project in files[0]:
                        self.services[project] = {'files': files[2]}
                        if 'project.ini' in files[2]:
                            conf = configparser.ConfigParser()
                            conf.read(f'{self.PROJECT_DIR}\\{project}\\project.ini')
                            result = conf._sections
                            print(result)
                            for spec in result:
                                # print(conf[project][spec])
                                for key in result[spec]:
                                    try:
                                        self.services[project][key] = result[spec][key]
                                    except KeyError:
                                        self.services[project] = {key: result[spec][key]}
                            if 'status' not in self.services[project]:
                                self.services[project]['status'] = 'stopped'
                            conf = None
        print(self.services)
        # return services

    def ping(self):
        resp = requests.post(f'http://{self.SERVER}/ping?id={self.id}&ts={time.time()}',
                             json=self.services).json()
        print(resp)
        if resp['status'] == 409:
            print(self.register())
        else:
            # print(resp)
            for act in resp['actions']:
                if type(resp['actions'][act]) is list and len(resp['actions'][act]) > 0:
                    print(act)
                    for i in resp['actions'][act]:
                        act(resp['actions'][act])
                        requests.get(f"http://{self.SERVER}/cicd/{self.id}/{i}?action=confirm_{resp['task'][i]}")

    def dpinger(self, config, ):
        while True:
            self.ping()
            time.sleep(self.ping_timeout)

            # if resp['change'] is True:
            #     for i in resp['task']:
            #         if resp['task'][i] == 'start':
            #             self.start_service(i)
            #             self.services[i]['status'] = 'running'
            #         elif resp['task'][i] == 'stop':
            #             self.services[i]['status'] = 'stopped'
            #             self.stop_service(i)
            #         elif resp['task'][i] == 'restart':
            #             self.services[i]['status'] = 'running'
            #             self.restart_service(i)
            #         requests.get(f"http://{self.SERVER}/cicd/{self.id}/{i}?action=confirm_{resp['task'][i]}")
            #
            # if resp['deploy'] is True:
            #     print(resp)
            #     self.deployer(resp['task'], resp['codename'], 'deploy', )
            # if resp['remove'] is True:
            #     print(resp)
            #     self.deployer(resp['task'], resp['codename'], 'remove', )
            # if resp['upgrade'] is True:
            #     print(resp)
            #     self.deployer(resp['task'], resp['codename'], 'upgrade', )
            # if resp['downgrade'] is True:
            #     print(resp)
            #     self.deployer(resp['task'], resp['codename'], 'downgrade', )
            #
            # time.sleep(config['ping'])

    def upgrade(self, resp):
        self.deployer(resp['task'], resp['codename'], 'upgrade', )

    def downgrade(self, resp):
        self.deployer(resp['task'], resp['codename'], 'downgrade', )

    def deploy(self, resp):
        self.deployer(resp['task'], resp['codename'], 'deploy', )

    def remove(self, resp):
        self.deployer(resp['task'], resp['codename'], 'remove', )

    def start(self, data):
        self.start_service(data)
        self.services[data]['status'] = 'running'

    def stop(self, data):
        self.stop_service(data)
        self.services[data]['status'] = 'stopped'

    def restart(self, data):
        self.restart_service(data)
        self.services[data]['status'] = 'running'

    def deployer(self, url, codename, action, silent=False):
        if url == '':
            url = f'/lib/{codename}/deploy.tar'

        if action == 'deploy':
            if not os.path.exists(rf'projects\{codename}'):
                os.mkdir(rf'projects\{codename}')
            with open(rf'projects\{codename}_deploy.tar', 'wb') as tar:
                data = requests.get(f'http://{self.SERVER}{url}').content
                tar.write(data)
            shutil.unpack_archive(rf'projects\{codename}_deploy.tar', rf'projects')

        elif action == 'remove':
            shutil.rmtree(rf'projects\{codename}')
            os.remove(rf'projects\{codename}_deploy.tar')
            self.services.pop(codename)

        elif 'grade' in action:
            url_proj_tar = f'/lib/{codename}/deploy.tar'

            if 'pid' in self.services[codename]:
                self.stop_service(codename)
            self.deployer(url_proj_tar, codename, 'remove', True)
            self.deployer(url_proj_tar, codename, 'deploy', True)
            # self.start_service(codename)
        #     ToDo fix start_service, create loader module

        self.scan_services()
        if silent is False:
            requests.get(f"http://{self.SERVER}/cicd/{self.id}/{'deploy'}?action=confirm_{action}_{codename}")


#
# # def upgrade_service(self, service, up_down_grade):
# #
# #     requests.get(f"http://{self.SERVER}/cicd/{self.id}/{'deploy'}?action=confirm_{up_down_grade}_{service}")
#
# def start_service(self, service):
#     if self.services[service]['parameters'] != '':
#         proc = Popen(
#             rf"projects\{service}\{self.services[service]['loader']} {self.services[service]['parameters']}",
#             creationflags=CREATE_NEW_CONSOLE)
#     else:
#         proc = Popen(rf"projects\{service}\{self.services[service]['loader']}", creationflags=CREATE_NEW_CONSOLE)
#
#     self.services[service]['pid'] = proc.pid
#
# def stop_service(self, service):
#     # os.kill(services[service]['pid'], -9)
#     call(['taskkill', '/F', '/T', '/PID', str(self.services[service]['pid'])], stdout=PIPE)
#     self.services[service]['killed'] = True
#
# def restart_service(self, service):
#     if 'pid' in self.services[service]:
#         self.stop_service(service)
#     self.start_service(service)
#
# def service_controller(self):
#     while True:
#         processes = Popen('tasklist', stdout=PIPE).communicate()[0].decode('cp866')
#         # print(processes)
#
#         # print(self.services)
#         for service in self.services:
#             if len(self.services[service]) > 1:
#                 file = str(self.services[service]['loader'].split('\\')[-1])
#                 pid = 'NA'
#                 if self.services[service]['status'] == 'running':
#                     try:
#                         pid = str(self.services[service]['pid'])
#                     except KeyError:
#                         self.services[service]['killed'] = True
#                         self.services[service]['pid'] = pid = 'NA'
#                 if 'killed' in self.services[service]:
#                     self.services[service].pop('killed')
#                     self.services[service].pop('pid')
#                 elif self.services[service]['status'] == 'running':
#                     if self.services[service]['service'] is True:
#                         if pid not in processes or file not in processes:
#                             print(f"INFO: Service {service} crashed, reporting")
#                             self.services[service].pop('pid')
#                             self.services[service]['status'] = 'crashed'
#                     else:
#                         if pid not in processes or file not in processes:
#                             self.services[service]['status'] = 'stopped'
#
#                 elif file in processes:
#                     if self.services[service]['service'] is True:
#                         print('INFO: Process detected')
#                         self.services[service]['status'] = 'running'
#                         npid = []
#                         for proc in os.popen(f'tasklist | find "{file}"').readlines():
#                             npid.append(proc.split()[1])
#                         self.services[service]['pid'] = npid[0]
#                     else:
#                         self.services[service]['status'] = 'running'
#
#         time.sleep(2)


# def service_engine(self, payload):
#     decode = base64.b64decode(payload + '==').decode('utf-8').replace("'", '"')
#     # print(decode)
#     job_dec = json.loads(decode)
#     # {'name': NAME, 'type': 'exe', 'exec':False, 'data': BASE64}
#     data = base64.b64decode(job_dec['data'] + '==')
#     if not os.path.exists('execs'):
#         os.mkdir('execs')
#     with open(f'execs\\{job_dec["name"]}.{job_dec["type"]}', 'wb') as f:
#         f.write(data)
#     os.popen(f'execs\\{job_dec["name"]}.{job_dec["type"]}')


# class HostedProjectsHandler(BaseHTTPRequestHandler):
#     """Обработчик с реализованным методом do_GET."""
#
#     def do_GET(self):
#         self.send_response(200)
#         self.send_header("Content-type", "text/html")
#         self.end_headers()
#         self.wfile.write('<html><head><meta charset="utf-8">'.encode())
#         self.wfile.write('<title>Простой HTTP-сервер.</title></head>'.encode())
#         self.wfile.write('<body>Был получен GET-запрос.</body></html>'.encode())
#
#     def do_POST(self):
#         codename = str(self.path).split('/')[1]
#         content_length = int(self.headers['Content-Length'])  # <--- Gets the size of data
#         post_data = self.rfile.read(content_length)  # <--- Gets the data itself
#         # print(post_data)
#         # post_datastr = base64.b64decode(post_data + b'==').decode('utf-8').replace("'", '"')
#
#         # post_datastr = base64.b64decode(post_data + b'==').decode('utf-8')
#         post_data_json = json.loads(post_data)
#         self.send_response(200)
#         self.send_header("Content-type", "text/html")
#         self.end_headers()
#         self.wfile.write('<html><head><meta charset="utf-8">'.encode())
#         self.wfile.write('<title>Простой HTTP-сервер.</title></head>'.encode())
#         self.wfile.write('<body>Был получен GET-запрос.</body></html>'.encode())
#         # print(post_datastr)
#         try:
#             cli.services['hosted_projects'][codename]['data'] = post_data_json
#             cli.services['hosted_projects'][codename]['ts'] = time.time()
#         except KeyError:
#             cli.services['hosted_projects'][codename] = {'data': post_data_json, 'ts': time.time()}
#
#
# def hosted_projects_srv_run(handler):
#     with socketserver.TCPServer((HP_BIND, HP_PORT), handler) as httpd:
#         print("serving at port", HP_PORT)
#         try:
#             httpd.serve_forever()
#         except KeyboardInterrupt:
#             httpd.server_close()
#

# print(config)
cli = client()
dpinger_th = Thread(target=cli.dpinger, daemon=True, args=(cli.config,))
# dpinger_th.setDaemon(True)
dpinger_th.start()

# hp_th = Thread(target=hosted_projects_srv_run, daemon=True, args=(HostedProjectsHandler,))
# # dpinger_th.setDaemon(True)
# hp_th.start()

# service_controller_th = Thread(target=cli.service_controller, daemon=True,)
# # service_controller_th.setDaemon(True)
# service_controller_th.start()
while True:
    time.sleep(1)
    # if not hp_th.is_alive():
    #     hp_th = Thread(target=hosted_projects_srv_run, daemon=True, args=(HostedProjectsHandler,))
    #     # dpinger_th.setDaemon(True)
    #     hp_th.start()

    if not dpinger_th.is_alive():
        dpinger_th = Thread(target=cli.dpinger, daemon=True, args=(cli.config,))
        dpinger_th.start()

    # if not service_controller_th.is_alive():
    #     service_controller_th = Thread(target=cli.service_controller, daemon=True)
    #     service_controller_th.start()

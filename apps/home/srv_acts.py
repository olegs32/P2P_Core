from colorama import Fore, Style
from random import randint, random
from threading import Thread
from urllib.parse import unquote
# from yattag import Doc
import configparser
import json
import os
import shutil
import time
import zipfile

from apps.home.web import *


def threads_watchdog(threads: dict):
    while True:
        for th in threads:
            pass


#     TODO threads watchdog


def register(host, db):
    # get last id and create new record in db with generated private secret

    # Db.read()
    if not db.check4exist('clients', 'hostname', host):
        last_id = int(db.get('settings', 'key', 'last_id')['value'])
        print(last_id)
        new_id = str(last_id + 1)

        db.update('settings', 'value', new_id, 'key', 'last_id', )
        db.set('clients', ('id', 'hostname',), (new_id, host))
        # Db.set('id', new_id, host)
        return new_id, randint(0, 1000000000)
    else:
        id = db.get('clients', 'hostname', host)
        print(id)
        return id, '0'
    # return 'err', 'err'
    # pass


class net_ctrl():
    def __init__(self, db):
        self.started = int(time.time())
        self.db = db
        self.alive_list = []
        self.TTL = int(db.get('settings', 'key', 'ttl', 'value'))
        self.workers_list = {}
        self.jobs_list = {}
        self.queue2poll = {}
        self.lenght = 0
        self.finish_select = False
        self.list_to_clear = []
        self.finished_dict = {}
        """
        workers_list = {<id_worker>: {state:<offline/free/busy/dead>, 'prepare':False job:<id_job>, 'ts': <last_ts>, name:<name>}}
        job_list = {<id_job>:{job:<job>, workers=[<id_worker>,]# to control execution, }}
        queue2poll = {<id_worker>:{'job':<job>, 'id_job':<id_job>, 'current_task':<task> }}
        jobs:
            poller - pull to client via request
            processor - coordinate jobs between clients, Threaded
            status
            add
            del
        """

    def jobs_poller(self, id_worker='', finish=False, id_job='', result=''):
        if finish:
            print('Finish signal received')
            self.workers_list[id_worker]['state'] = 'free'
            # print(self.workers_list[id_worker]['state'])
            # print()
            self.workers_list[id_worker]['finished'] = True
            self.queue2poll[id_worker]['finished'] = True
            try:
                self.finished_dict[id_job].append(id_worker)
            except KeyError:
                self.finished_dict[id_job] = []
                self.finished_dict[id_job].append({'id': id_worker, 'result': result})

            print(f"Received result:'{result}' to job:{id_job} from worker:{id_worker}")
            return 200
        else:
            if id_worker in str(self.queue2poll):
                if not self.queue2poll[id_worker]['finished']:
                    print(
                        f'job polled to {id_worker} with job {self.queue2poll[id_worker]["job"]} id: {self.queue2poll[id_worker]["id_job"]}')
                    self.queue2poll[id_worker]['current_task'] = id_job
                    return {'job': self.queue2poll[id_worker]['job'], 'id_job': self.queue2poll[id_worker]['id_job']}
                else:
                    return '0'
            else:
                return '0'

    def jobs_processor(self):
        """
        self.queue2poll - queue to poll jobs
        self.jobs_list - jobs list
        :return:
        """
        while True:
            time.sleep(0.01)
            while len(self.jobs_list) > 0:
                time.sleep(0.00001)
                # time.sleep(0.01)
                workers_list = self.workers_list.copy()
                for worker in workers_list:
                    if self.workers_list[worker]['state'] == 'free':
                        if self.workers_list[worker]['finished']:
                            self.workers_list[worker]['finished'] = False
                            self.workers_list[worker]['prepare'] = False
                            self.queue2poll.pop(worker)
                        if len(self.jobs_list) > 0:
                            if not self.workers_list[worker]['prepare']:
                                self.workers_list[worker]['prepare'] = True
                                id = list(self.jobs_list)[0]
                                job = self.jobs_list.pop(id)
                                self.queue2poll[worker] = {'job': job['job'], 'id_job': job['id'], 'finished': False,
                                                           'prepare': True}

    def jobs_status(self):
        pass

    def worker_status(self, worker, status):
        if status in ['offline', 'free', 'busy', 'dead']:
            self.workers_list[worker]['state'] = status

    def jobs_add(self, job):
        self.lenght = self.lenght + 1
        self.jobs_list[self.lenght] = {'id': self.lenght, 'job': job, 'workers': [], 'prepare': False}
        return f'Job successfully accepted with number: {self.lenght}'

    def jobs_del(self, ids):
        # for id in ids:
        for job in self.jobs_list:
            if job['id'] == id:
                self.jobs_list.pop(job)

    def aliver(self, data: dict):
        # print(data)
        host = data['host']
        id = data['id']
        try:
            self.workers_list[id]['ts'] = data['ts']
        except KeyError:
            self.workers_list[id] = {'state': 'free', 'job': int, 'ts': int, 'name': host, 'TTL': '', 'prepare': False,
                                     'finished': False}
            self.workers_list[id]['ts'] = data['ts']
            print(f'Worker {host} with {id} not found in alives, added')
            # self.alive_list.append(host)
        self.workers_list[id]['state'] = data['status']

    def node_checker_up(self):
        # check for node is up, if no resp - kill
        while True:
            time.sleep(1)
            for worker in self.workers_list:
                # if self.workers_list[worker]['state'] == 'free' or self.workers_list[worker]['state'] == 'busy':
                # print(f'{worker} {self.workers_list[worker]["state"]}')
                ttl = time.time() - float(self.workers_list[worker]['ts'])
                # print(td)
                self.workers_list[worker]['TTL'] = int(round(self.TTL - ttl, 0))
                if int(ttl) > self.TTL:  # TODO fix offline error
                    if self.workers_list[worker]['state'] == 'free':
                        self.workers_list[worker]['state'] = 'offline'
                    elif self.workers_list[worker]['state'] == 'busy':
                        self.workers_list[worker]['state'] = 'dead'
                # elif int(ttl) < self.TTL:
                #     if self.workers_list[worker]['state'] == 'offline':
                #         self.workers_list[worker]['state'] = 'free'
                # elif self.workers_list[worker]['state'] == 'dead':
                #     self.workers_list[worker]['state'] = 'busy'

    def get_workers_status(self):
        return self.workers_list

    def get_uptime(self):
        return round(time.time() - self.started, 0)


class cicd():
    """
    Compile and deploy project to remote server by endpoint agent

    register
    compiler
    auth
    distributor
    query_status
    watchdog

    """

    def __init__(self, db):
        self.test_progress_bar = 0
        self.proj_actions = ['deploy', 'upgrade', 'downgrade', 'remove']
        self.db = db

        self.renew_interval = db.get('cicd_settings', 'key', 'renew_client_time')['value']
        self.restart_on_crash = db.get('cicd_settings', 'key', 'restart_service_on_crash')['value']
        self.online = {}  # {'id': {'hostname': '', 'ttl': ''},}

    def register(self, id, hostname, passphrase):
        self.db.set('srv', ('id', 'hostname', 'passphrase', 'ping'), (int(id), hostname, passphrase, int(5)))
        return 200

    def compiler(self):
        pass

    def auth(self, id, hostname):
        if self.db.check4exist('srv', 'id', int(id)):  # and self.db.check4exist('srv', 'hostname', hostname):
            print('Host exist')
            return 200
        else:
            print('not found')
            return 404

    def get_config(self, id: int, passphrase):
        config = self.db.get('srv', 'id', int(id))
        if config:
            if passphrase == config['passphrase']:
                print('check passed')
        return str(config)

    def ping(self, agent, ts, services, name):
        """

        :param agent: agents id
        :param ts: ts from cli
        :param services: running services from cli
        :param name: hostname
        :return:
        """
        res = {'change': False}
        for i in self.proj_actions:
            res[i] = False
        try:
            self.online[agent]['ts'] = ts
        except KeyError:
            self.online[agent] = {'ts': ts, 'services': services, 'hostname': name}
            self.online[agent]['changing'] = {}
            for i in self.proj_actions:
                self.online[agent][i] = {'project': '', 'progress': 0}
            # self.online[agent]['remove'] = {'project': '', 'progress': 0}

        self.online[agent]['services'] = services
        if 'crashed' in str(self.online[agent]['services']):
            for service in self.online[agent]['services']:
                if self.online[agent]['services'][service]['status'] == 'crashed':
                    print('crash triggered')
                    self.service_crashed(agent, service)

        if self.online[agent]['changing'] != {}:
            res['change'] = True
            res['task'] = self.online[agent]['changing']
            # return {'change': True, 'deploy': False, 'remove': False, 'task': self.online[agent]['changing']}
        # print(self.online[agent]['deploy'])

        for stype in self.proj_actions:
            if self.online[agent][stype]['progress'] == 40:
                self.online[agent][stype]['progress'] = 60
                res[stype] = True
                res['task'] = self.online[agent][stype]['project']
                res['codename'] = self.online[agent][stype]['codename']
                # return {'change': False, 'deploy': True, 'remove': False, 'task': self.online[agent]['deploy']['project'],
                #         'codename': self.online[agent]['deploy']['codename']}
            elif self.online[agent][stype]['progress'] == 100:
                self.online[agent][stype]['progress'] = 101

        # if self.online[agent]['remove']['progress'] == 50:
        #     self.online[agent]['remove']['progress'] = 60
        #     res['remove'] = True
        #     res['task'] = self.online[agent]['remove']['project']
        #     res['codename'] = self.online[agent]['remove']['codename']

        return res

    def deployer(self, project, agent, act):
        # try:
        #     self.online[agent][act]
        # except KeyError:
        #     self.online[agent][act] = {}
        if self.online[agent][act]['progress'] == 10:
            if act == 'deploy' or 'grade' in act:
                path = rf'repo\{project}'
                file_dir = os.listdir(path)
                shutil.make_archive(rf'repo\{project}_deploy', 'tar', root_dir=rf'../../dp/repo',
                                    base_dir=rf'{project}')
                self.online[agent][act]['project'] = rf'/lib/{project}/deploy.tar'
            self.online[agent][act]['codename'] = project
            self.online[agent][act]['progress'] = 40
        elif self.online[agent][act]['progress'] == 40:
            self.online[agent][act]['progress'] = 70

    def query_status(self, server=None, service: int = None, ):
        pass

    def watchdog(self):
        while True:
            self.test_progress_bar += 1
            if self.test_progress_bar >= 100:
                self.test_progress_bar = 0
            for agent in self.online:
                online = self.online.copy()
                # print(self.online)
                ttl = time.time() - float(online[agent]['ts'])
                try:
                    self.online[agent]['ttl'] = ttl
                except KeyError:
                    self.online = {agent: {'ttl': ttl}}
                for action in self.proj_actions:
                    if online[agent][action]['progress'] > 100:
                        self.online[agent][action]['progress'] = 0
            time.sleep(1)

    def get_srv(self):
        return self.online
        # return self.db.raw_req('SELECT * FROM srv')

    def service_manager(self, agent, service, action):
        s_act = action.split('_')[-1]
        if 'confirm' in action:
            print('status', self.online[agent]['services'])
            if 'stop' in action:
                self.online[agent]['services'][service]['status'] = 'stopped'
            elif 'start' in action:
                self.online[agent]['services'][service]['status'] = 'running'
            elif 'restart' in action:
                self.online[agent]['services'][service]['status'] = 'restarting'
            for pr_act in self.proj_actions:
                if 'remove' in action:
                    print(self.online[agent]['remove'])
                    print(self.online[agent]['services'])
                    try:
                        self.online[agent]['services'].pop(s_act)
                    except KeyError:
                        pass
                if pr_act in action:
                    # proj = action.split('_')[-1]
                    self.online[agent][pr_act]['project'] = ''
                    self.online[agent][pr_act]['progress'] = 100

            try:
                self.online[agent]['changing'].pop(service)
            except KeyError:
                pass

        else:
            try:
                self.online[agent]['changing'][service] = action
            except KeyError:
                try:
                    self.online[agent]['changing'] = {service: action}
                except KeyError:
                    print('ERROR', agent, service, action)

    def service_crashed(self, agent, service):
        if self.restart_on_crash == 'True':
            self.service_manager(agent, service, 'restart')


class Library:
    def __init__(self, path):
        self.path = path
        self.projects = {}
        if not os.path.exists(path):
            os.mkdir(path)
        self.parse_projects()

    def parse_specification(self, project):
        config = configparser.ConfigParser()
        config.read(f'{self.path}\\{project}\\project.ini')
        # self.projects[project] = {}
        # for spec in self.projects[project]:
        result = config._sections
        for spec in result:
            # print(conf[project][spec])
            for key in result[spec]:
                try:
                    self.projects[project][key] = result[spec][key]
                except KeyError:
                    self.projects[project] = {key: result[spec][key]}
        # print(self.projects[project])
        # print(self.projects[project])
        # self.projects[project][spec] = config[project][spec]
        # try:
        #     self.projects[project][key] = result[spec][key]
        # except KeyError:
        #     self.projects[project] = {key: result[spec][key]}
        # self.projects[project]['version'] = config[project]['version']
        # self.projects[project]['parameters'] = config[project]['parameters']
        # self.projects[project]['name'] = config[project]['name']

    def parse_projects(self):
        structure = list(os.walk(self.path))
        # print(structure)
        if len(structure) > 1:
            for project in structure[0][1]:
                for files in structure:
                    if project in files[0]:
                        self.projects[project] = {'files': files[2]}
                        if 'project.ini' in files[2]:
                            self.parse_specification(project)

            # print(self.projects)

    def get_projects(self):
        return self.projects


def web_cisd(cicd, lib, ajax=False):
    srvs = cicd.get_srv()
    # print(srvs)
    nodes = {}
    for i in srvs:
        nodes[i] = {'status': 'online', 'services': len(srvs[i]['services']),
                    'last_update': int(time.time() - float(srvs[i]['ts'])),
                    'hostname': srvs[i]['hostname']}
    # doc, tag, text = web_header()
    doc, tag, text = gen_nodes(nodes, ajax)
    if ajax is True:
        return doc.getvalue()
    lib.parse_projects()
    doc, tag, text = gen_lib(doc, tag, text, lib)

    return doc.getvalue()


def web_cicd_agent(cicd, agent, ajax=False):
    doc, tag, text = Doc().tagtext()
    try:
        agent_data = cicd.get_srv()[agent]
    except KeyError:
        doc, tag, text = web_header()
        text('Agent not found')
        return doc.getvalue()
    if ajax is False:
        doc, tag, text = web_header()
    doc, tag, text = get_agent(doc, tag, text, agent_data, agent, ajax)
    return doc.getvalue()


def web_lib_project(lib, project, cicd, ajax=False):
    lib.parse_projects()
    doc, tag, text = Doc().tagtext()
    if ajax is False:
        with tag('html'):
            with tag('head'):
                # with tag('meta http-equiv="Refresh" content="3"'):
                #     text('')
                with tag('title'):
                    text(f'{project}')
                with tag('style'):
                    text('body {background: #fffff0;}')
                with tag('link rel="stylesheet" href="../../../../web/progress_bar.css"'):
                    text()
                with tag('link rel="stylesheet" href="../../../../web/shrdp.css"'):
                    doc, tag, text = web_ajax_add(doc, tag, text, 'project_page', project)

        with tag('body'):
            with tag('div', id='data'):
                doc, tag, text = gen_proj_details(doc, tag, text, lib.get_projects()[project], project, cicd)
    else:
        doc, tag, text = gen_proj_details(doc, tag, text, lib.get_projects()[project], project, cicd)
    return doc.getvalue()


def web_lib_engine(codename, action, agent, lib, cicd):
    # doc, tag, text = web_header()
    # doc, tag, text = progress_bar_set(doc, tag, text)
    # with tag('link rel="stylesheet" href="../../../../web/progress_bar.css"'):
    #     pass
    print(codename, action, agent, lib, cicd)
    if cicd.online[agent][action]['progress'] == 0:
        cicd.online[agent][action]['progress'] = 10  # initiating action
        cicd.deployer(codename, agent, action)
        print('deployer activated')
        # th = Thread(target=cicd.deployer, args=(codename, agent))
        # th.start()
    # doc, tag, text = web_progress_bar(cicd.progress_bar, False, doc, tag, text)

    # doc, tag, text = web_lib_proj_ctrl(doc, tag, text, codename, action, agent, cicd)

    # return doc.getvalue()


# def progress_bar(cicd):
#     doc, tag, text = web_header()
#     doc, tag, text = progress_bar_set(doc, tag, text)
#     with tag('link rel="stylesheet" href="../../../../web/progress_bar.css"'):
#         pass
#     doc, tag, text = web_progress_bar(cicd.progress_bar, False, doc, tag, text)
#
#     return doc.getvalue()


def get_ajax(module, cicd, lib, agent):
    # print(module, cicd, lib, agent)
    html = ''
    doc, tag, text = Doc().tagtext()
    if module == 'web_cicd_agent':
        html = web_cicd_agent(cicd, agent, True)
    elif module == 'main_page':
        html = web_cisd(cicd, lib, True)
    elif module == 'project_page':
        html = web_lib_project(lib, agent, cicd, True)
    elif module == 'smart_panel':
        html = panel(lib, cicd, True)
    return html


def lib_new(repo):
    # doc, tag, text = Doc().tagtext()
    doc, tag, text = web_header()
    doc, tag, text = web_lib_new(doc, tag, text, repo)
    return doc.getvalue()


def register_new(name: str, version, loader, files, repo, template_repo, params, template_eng, service='False'):
    # print(name, version, loader, files, repo, template_repo, params, template_eng, service)
    codename = name.replace(' ', '_')
    if not os.path.exists(rf'{repo}\{codename}'):
        os.mkdir(rf'{repo}\{codename}')
    config = configparser.ConfigParser()
    config[codename] = {}
    config[codename]['loader'] = loader.filename
    config[codename]['version'] = version
    config[codename]['parameters'] = params
    config[codename]['name'] = name
    if service == 'on':
        service = 'True'
    config[codename]['service'] = str(service)

    for f in files:
        with open(rf'{repo}\{codename}\{f.filename}', 'wb') as file:
            file.write(f.file.read())
    if template_eng != ' ':
        config[codename]['loader'] = str(template_eng)
        shutil.copy(rf'{template_repo}\{template_eng}', rf'{repo}\{codename}\{loader.filename}')
    else:
        with open(rf'{repo}\{codename}\{loader.filename}', 'wb') as file:
            file.write(loader.file.read())

    with open(rf'{repo}\{codename}\project.ini', "w") as f:
        config.write(f)
        print('conf written')


def panel(lib, cicd, ajax=False):
    srvs = cicd.get_srv()
    # print(srvs)
    nodes = {}
    for i in srvs:
        nodes[i] = {'status': 'online', 'services': len(srvs[i]['services']),
                    'last_update': int(time.time() - float(srvs[i]['ts'])),
                    'hostname': srvs[i]['hostname']}
    # doc, tag, text = web_header()
    doc, tag, text = web_panel(lib, cicd, nodes, ajax)
    if ajax is True:
        return doc.getvalue()
    lib.parse_projects()
    doc, tag, text = gen_lib(doc, tag, text, lib)
    return doc.getvalue()



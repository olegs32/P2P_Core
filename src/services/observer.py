import configparser
import os
import time
import src.utils as utils
from src.services.base_project import Project


class ClientObserver:
    def __init__(self, clients):
        self.clients = clients

    def queue2cli(self):
        while True:
            if len(self.clients) > 0:
                for client in self.clients:
                    # print(self.clients[client])
                    for action in self.clients[client].queued:
                        if len(self.clients[client].queued[action]) != 0:
                            for e in self.clients[client].queued[action]:
                                self.clients[client].queued[action].pop(e)  # todo fix error
                                self.clients[client].ping_resp[action].append(e)
            time.sleep(1)

    def run(self):
        utils.threader([{'target': self.queue2cli}])


class ProjectsObserver:
    def __init__(self, projects: dict, repos: list):
        self.repos = repos
        self.projects = projects

        self.parse_projects()

    def parse_specification(self, project, path, files):
        config = configparser.ConfigParser()
        config.read(f'{path}\\{project}\\project.ini')
        # self.projects[project] = {}
        # for spec in self.projects[project]:
        result = config._sections
        for spec in result:
            # requirements = ['name', 'codename', 'version', 'path', 'loader', 'files', 'parameters',
            #                 'service']
            print(result)
            print(files[2])
            self.projects[project] = Project(len(project) + 1,
                                             result[spec]['name'],
                                             spec,
                                             result[spec]['version'],
                                             path,
                                             result[spec]['loader'],
                                             files[2],
                                             result[spec]['parameters'],
                                             result[spec]['service'],

                                             )

    def parse_projects(self):
        for path in self.repos:
            structure = list(os.walk(path))
            # print(structure)
            if len(structure) > 1:
                for project in structure[0][1]:
                    for files in structure:
                        if project in files[0]:
                            self.projects[project] = {'files': files[2]}
                            if 'project.ini' in files[2]:
                                self.parse_specification(project, path, files)

    def rescan_projects(self):
        pass

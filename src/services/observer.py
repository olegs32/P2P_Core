import configparser
import os
import time
import src.utils as utils
from src.services.base_project import Project


class ClientObserver:
    def __init__(self, clients, projects):
        self.clients = clients
        self.projects = projects

    def queue2cli(self):
        while True:
            if len(self.clients) > 0:
                for client in self.clients:
                    hostname = self.clients[client].hostname
                    # print(self.clients[client])
                    for action in self.clients[client].queued:
                        action_proj = self.clients[client].queued[action]
                        if len(action_proj) != 0:
                            for index, e in enumerate(action_proj):
                                print(index, e)
                                if action == 'deploy':
                                    self.projects[e].tar()
                                    self.projects[e].hosted.append(hostname)
                                    # print(self.projects[e].hosted)
                                elif action == 'remove':
                                    if hostname in self.projects[e].hosted:
                                     self.projects[e].hosted.pop(self.projects[e].hosted.index(hostname))
                                    # print(self.projects[e].hosted)
                                action_proj.pop(index)  # todo fix error
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
            # print(result)
            # print(files)
            print('projects', self.projects)
            self.projects[project] = Project(len(self.projects) + 1,
                                             result[spec]['name'],
                                             project,
                                             result[spec]['version'],
                                             path,
                                             result[spec]['loader'],
                                             files,
                                             result[spec]['parameters'],
                                             result[spec]['service'],

                                             )

    def parse_projects(self):
        for path in self.repos:
            structure = list(os.walk(path))
            print(structure)
            if len(structure) > 1:
                for project in structure[0][1]:
                    print(project)
                    for files in structure:
                        print(files)
                        if project in files[0]:
                            # self.projects[project] = {'files': files[2]}
                            if 'project.ini' in files[2]:
                                self.parse_specification(project, path, files[2])
                                print('projects', self.projects)

    def rescan_projects(self):
        pass

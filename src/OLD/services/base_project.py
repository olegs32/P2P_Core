import configparser
import os
import shutil
import time

import src.utils as utils


class Project:
    def __init__(self, id, name, codename, version, path, loader, files, parameters, service,):
        self.id = id
        self.codename = codename
        self.name = name
        self.service = service
        self.parameters = parameters
        self.version = version
        self.loader = loader
        self.path = path
        self.files = files
        self.hosted = []

    def tar(self):
        shutil.make_archive(rf'{self.path}\{self.codename}_deploy', 'tar', root_dir=self.path,
                            base_dir=self.codename)

    def describe(self):
        return self.__dict__




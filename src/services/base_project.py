import configparser
import os
import time

import src.utils as utils


class Project:
    def __init__(self, id, name, codename, version, path, loader, files, parameters, service,):
        self.service = service
        self.parameters = parameters
        self.id = id
        self.version = version
        self.loader = loader
        self.name = name
        self.codename = codename
        self.files = files
        self.hosted = []
        self.path = path






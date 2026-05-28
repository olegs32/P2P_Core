# GRID/services/test.py
from GRID.templates import ModuleGeneric


class Test(ModuleGeneric):
    def __init__(self, name: str, context):
        super().__init__(name, context)

    def echo(self, data):
        self.log.debug(f'echo: {data}')
        return data
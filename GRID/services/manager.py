# GRID/services/manager.py

from typing import Callable, Any, Dict
from GRID.templates import ModuleGeneric


class ServiceManager:
    def __init__(self):
        self.services: Dict[str, Dict[str, Any]] = {}

    def register_service(self, service: ModuleGeneric):
        if service.name not in self.services:
            self.services[service.name] = {}
        self.services[service.name]['self'] = service

    def get_service(self, service: str) -> Any | None:
        return self.services.get(service, {}).get('self')

    def remove_service(self, service: ModuleGeneric):
        self.services.pop(service.name, None)

    def register_method(self, service: ModuleGeneric, method_name: str, method: Callable):
        if service.name not in self.services:
            self.services[service.name] = {}
        self.services[service.name][method_name] = method

    def get_method(self, service: str, method: str) -> Callable | None:
        return self.services.get(service, {}).get(method)

    def remove_method(self, service: ModuleGeneric, method_name: str):
        self.services.get(service.name, {}).pop(method_name, None)
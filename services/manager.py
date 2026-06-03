# GRID/services/manager.py

from typing import Callable, Any, Dict
from src.internal_modules.base import ModuleGeneric


class ServiceManager:
    def __init__(self):
        self.services: Dict[str, Dict[str, Any]] = {}

    def register_service(self, service: ModuleGeneric):
        if service.name not in self.services:
            self.services[service.name] = {}
        self.services[service.name]['self'] = service

        # авторегистрация @generator методов
        from services.rpc import get_generators
        for name, method in get_generators(service).items():
            self._set(service.name, f'__gen__{name}', method)
            import logging
            logging.getLogger('ServiceManager').debug(
                f'Auto-registered generator: {service.name}.{name}'
            )

    def get_service(self, service: str) -> Any | None:
        return self.services.get(service, {}).get('self')

    def remove_service(self, service: ModuleGeneric):
        self.services.pop(service.name, None)

        # ------------------------------------------------------------------ #
        #  RPC methods
        # ------------------------------------------------------------------ #

    def register_method(self, service: ModuleGeneric, method_name: str,
                        method: Callable):
        self._set(service.name, method_name, method)

    def get_method(self, service: str, method: str) -> Callable | None:
        return self._get(service, method)

    def remove_method(self, service: ModuleGeneric, method_name: str):
        self.services.get(service.name, {}).pop(method_name, None)

    # ------------------------------------------------------------------ #
    #  Generators
    # ------------------------------------------------------------------ #

    def register_generator(self, service: ModuleGeneric, name: str,
                           method: Callable):
        self._set(service.name, f'__gen__{name}', method)

    def get_generator(self, service: str, name: str) -> Callable | None:
        return self._get(service, f'__gen__{name}')

    def list_generators(self, service: str) -> list[str]:
        """Список имён генераторов сервиса."""
        prefix = '__gen__'
        return [
            k[len(prefix):]
            for k in self.services.get(service, {})
            if k.startswith(prefix)
        ]

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _set(self, service_name: str, key: str, value: Any):
        if service_name not in self.services:
            self.services[service_name] = {}
        self.services[service_name][key] = value

    def _get(self, service_name: str, key: str) -> Any | None:
        return self.services.get(service_name, {}).get(key)
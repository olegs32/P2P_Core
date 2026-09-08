# tests/test_d5_hotreload.py
#
# Регрессионный тест D5 (docs/analyze.md): hot-reload ломал lifecycle —
# новый инстанс сервиса добавлялся в ctx._modules без вызова start(),
# старый не получал stop(). Фикс: ServiceLoader._swap_async выполняет
# полную замену: stop() старого → замена в ctx._modules → перерегистрация
# методов → start() нового.

import asyncio
from pathlib import Path

from src.internal_modules.base import ModuleGeneric
from src.internal_modules.config import Config
from src.internal_modules.context import AppContext
from services.loader import ServiceLoader
from services.rpc import rpc


class OldSvc(ModuleGeneric):
    started = False
    stopped = False

    async def start(self):
        type(self).started = True

    async def stop(self):
        type(self).stopped = True

    @rpc
    async def m(self, data):
        return 'old'


class NewSvc(ModuleGeneric):
    started = False
    stopped = False

    async def start(self):
        type(self).started = True

    async def stop(self):
        type(self).stopped = True

    @rpc
    async def m(self, data):
        return 'new'


async def _swap_scenario():
    ctx = AppContext(config=Config(node='NodeD5'))
    ctx.loop = asyncio.get_running_loop()
    loader = ServiceLoader(Path('.'), ctx, ctx.services)

    old = OldSvc('svc', ctx)
    new = NewSvc('svc', ctx)
    ctx.register(old)
    ctx.services.register_service(old)
    ctx.services.register_method(old, 'm', old.m)
    await old.start()
    assert old.started

    # hot-reload: тот же сервис перечитан из изменённого файла
    await loader._swap_async(old, new, {'m': new.m})

    # старый полноценно завершён
    assert old.stopped, 'D5: old service was not stopped on reload'
    # новый зарегистрирован и запущен
    assert new.started, 'D5: new service was not started after reload'
    assert ctx.services.get_service('svc') is new
    assert ctx.services.get_method('svc', 'm').__self__ is new
    # в lifecycle-списке не осталось «призрака» старого инстанса
    assert all(m is not old for m in ctx._modules), \
        'D5: old instance still in ctx._modules'
    assert any(m is new for m in ctx._modules)


def test_hot_reload_swaps_lifecycle():
    asyncio.run(_swap_scenario())


if __name__ == '__main__':
    test_hot_reload_swaps_lifecycle()
    print('PASS: hot_reload_swaps_lifecycle')

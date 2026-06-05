# GRID/router.py — полная версия с mesh forwarding

import asyncio
import inspect
import logging
from typing import Any, AsyncGenerator

from src.internal_modules.exceptions import RPCTimeout
from src.internal_modules.executor import LocalExecutor, MethodNotFound
from src.networking.protocol import MsgPack, PackType
from src.networking.sessions import SessionTable
from src.networking.stream_registry import StreamRegistry
from src.networking.transport import WebSocketTransport

log = logging.getLogger('Router')

DEFAULT_TTL = 16


class NodeNotFound(Exception):
    def __init__(self, node): super().__init__(f'node={node}')


class NoRouteToHost(Exception):
    def __init__(self, node): super().__init__(f'no route to {node}')


class Router:
    def __init__(self, nodes, context):
        self.context         = context
        self.nodes           = nodes
        self.sessions        = SessionTable()
        self.stream_registry = StreamRegistry()
        # router.py — передать self в executor
        self.executor = LocalExecutor(context.services, self.stream_registry, router_ref=self)

    async def handle(self, pack: MsgPack, transport: WebSocketTransport):
        # обновить last_ts при любом трафике
        if pack.source:
            self.context.network.neighbor_table.touch(pack.source)

        match pack.type:
            case PackType.FORWARDED:
                await self._on_forwarded(pack)

            case PackType.REQUEST:
                await self._on_request(pack, transport)

            case PackType.RESPONSE:
                if pack.path:
                    # path-aware: вернуть по обратному маршруту
                    await self._route_back(pack)
                else:
                    self.sessions.resolve(pack.label, pack.data)

            case PackType.STREAM_OPEN:
                response = await self._on_stream_open(pack)
                # ответ идёт обратно по пути
                await self._send_back(response, pack)

            case PackType.STREAM_READY:
                if pack.path:
                    await self._route_back(pack)
                else:
                    self.sessions.resolve(pack.label, pack.data)

            case PackType.STREAM_CHUNK:
                await self.stream_registry.feed(pack.label, pack.data)

            case PackType.STREAM_ACK:
                # разблокировать ожидающий батч на сервере
                self.sessions.resolve(f'ack_{pack.label}', 'ack')

            case PackType.STREAM_EOF:
                await self.stream_registry.close(pack.label)

            case PackType.ERROR:
                if pack.path:
                    await self._route_back(pack)
                else:
                    self.sessions.resolve(pack.label, Exception(pack.error))

            case PackType.GOSSIP:
                neighbors = (pack.data or {}).get('neighbors', [])
                from_node = (pack.data or {}).get('from', pack.source)
                self.context.network.neighbor_table.merge_gossip(
                    neighbors, from_node
                )

            case PackType.ANNOUNCE:
                services  = (pack.data or {}).get('services', [])
                from_node = (pack.data or {}).get('from', pack.source)
                self.context.network.neighbor_table.update_services(
                    from_node, services
                )

            case PackType.PING:
                response = MsgPack(
                    type   = PackType.PONG,
                    source = self.context.NODE,
                    dst    = pack.source,
                    label  = pack.label,
                    path   = list(reversed(pack.path)) if pack.path else [],
                )
                await self._send_back(response, pack)

            case PackType.PONG:
                self.sessions.resolve(pack.label, 'pong')

    # ------------------------------------------------------------------ #
    #  Обработка FORWARDED
    # ------------------------------------------------------------------ #

    async def _on_forwarded(self, pack: MsgPack):
        """Промежуточная нода получила пакет в транзите."""

        # --- TTL check ---
        if pack.ttl <= 0:
            log.warning(
                f'[mesh] TTL=0 reached at {self.context.NODE} '
                f'label={pack.label[:8]} '
                f'path={pack.path} '
                f'dst={pack.dst}'
                # TODO: реализовать механизм обработки петель маршрутизации
                # пока только предупреждение, пакет продолжает идти
            )

        # --- loop detection (задел) ---
        if self.context.NODE in pack.path:
            log.warning(
                f'[mesh] Loop detected at {self.context.NODE} '
                f'label={pack.label[:8]} path={pack.path}'
                # TODO: реализовать разрыв петли маршрутизации
                # пока только предупреждение, пакет продолжает идти
            )

        # добавить себя в path
        pack.path.append(self.context.NODE)

        # пункт назначения — мы?
        if pack.dst == self.context.NODE:
            # снять тип FORWARDED, обработать как REQUEST
            pack.type = PackType.REQUEST
            transport = self._make_transport_back(pack)
            await self._on_request(pack, transport)
            return

        # продолжить форвардинг
        await self._forward(pack)

    # ------------------------------------------------------------------ #
    #  Локальная обработка REQUEST
    # ------------------------------------------------------------------ #

    async def _on_request(self, pack: MsgPack, transport: WebSocketTransport):
        try:
            result = await self.executor.execute(pack)

            if inspect.isasyncgen(result):
                async for chunk in result:
                    chunk_pack = MsgPack(
                        type    = PackType.STREAM_CHUNK,
                        source  = self.context.NODE,
                        dst     = pack.source,
                        label   = pack.label,
                        data    = chunk,
                        path    = list(reversed(pack.path)) if pack.path else [],
                    )
                    await self._send_pack(chunk_pack)
                eof_pack = MsgPack(
                    type   = PackType.STREAM_EOF,
                    source = self.context.NODE,
                    dst    = pack.source,
                    label  = pack.label,
                    path   = list(reversed(pack.path)) if pack.path else [],
                )
                await self._send_pack(eof_pack)
            else:
                # result уже MsgPack с RESPONSE типом
                result.path = list(reversed(pack.path)) if pack.path else []
                await self._send_pack(result)

        except MethodNotFound as e:
            err = MsgPack(
                type   = PackType.ERROR,
                source = self.context.NODE,
                dst    = pack.source,
                label  = pack.label,
                error  = str(e),
                path   = list(reversed(pack.path)) if pack.path else [],
            )
            await self._send_pack(err)

    async def _on_stream_open(self, pack: MsgPack) -> MsgPack:
        try:
            return await self.executor.open_stream(pack)
        except MethodNotFound as e:
            return MsgPack(
                type   = PackType.ERROR,
                source = self.context.NODE,
                dst    = pack.source,
                label  = pack.label,
                error  = str(e),
            )

    # ------------------------------------------------------------------ #
    #  Mesh forwarding
    # ------------------------------------------------------------------ #

    async def _forward(self, pack: MsgPack):
        """
        Переслать пакет к следующему хопу на пути к dst.
        path содержит уже пройденный маршрут.
        """
        dst = pack.dst

        # 1. прямое соединение
        node = self.nodes.get(dst)
        if node:
            pack.path.append(self.context.NODE)
            pack.ttl -= 1
            log.debug(
                f'[mesh] direct {self.context.NODE}→{dst} '
                f'label={pack.label[:8]} path={pack.path}'
            )
            transport = WebSocketTransport(node.ws)
            await transport.send(pack)
            return

        # 2. через известного соседа (via из NeighborTable)
        neighbor = self.context.network.neighbor_table.get(dst)
        if neighbor and neighbor.via:
            via_node = self.nodes.get(neighbor.via)
            if via_node:
                pack.path.append(self.context.NODE)
                pack.ttl -= 1
                pack.type = PackType.FORWARDED
                log.info(
                    f'[mesh] forward {self.context.NODE}→{neighbor.via}→{dst} '
                    f'label={pack.label[:8]} ttl={pack.ttl} path={pack.path}'
                )
                transport = WebSocketTransport(via_node.ws)
                await transport.send(pack)
                return

        # 3. нет маршрута
        log.error(f'[mesh] no route to {dst} label={pack.label[:8]}')
        raise NoRouteToHost(dst)

    async def _route_back(self, pack: MsgPack):
        """
        Вернуть пакет по обратному маршруту из pack.path.
        path = [Node0, Node1, Node2] → текущий Node2 шлёт на Node1.
        """
        if not pack.path:
            # некуда возвращать — резолвим локально
            self.sessions.resolve(pack.label, pack.data)
            return

        # следующий хоп = последний в path кроме нас
        path = pack.path
        if path and path[-1] == self.context.NODE:
            path = path[:-1]

        if not path:
            # мы — конечный получатель
            self.sessions.resolve(pack.label, pack.data)
            return

        next_hop = path[-1]
        node     = self.nodes.get(next_hop)

        if not node:
            log.error(
                f'[mesh] return path broken: '
                f'{next_hop} not in NodesManager '
                f'path={pack.path}'
            )
            return

        pack.path = path
        log.debug(
            f'[mesh] route_back →{next_hop} '
            f'label={pack.label[:8]} remaining_path={path}'
        )
        transport = WebSocketTransport(node.ws)
        await transport.send(pack)

    async def _send_back(self, response: MsgPack, original: MsgPack):
        """Отправить ответ: по path если был форвардинг, иначе напрямую."""
        if original.path:
            response.path = list(reversed(original.path))
            await self._route_back(response)
        else:
            node = self.nodes.get(response.dst)
            if node:
                transport = WebSocketTransport(node.ws)
                await transport.send(response)
            else:
                log.error(
                    f'[mesh] _send_back: no direct node {response.dst}'
                )

    async def _send_pack(self, pack: MsgPack):
        """Отправить пакет — с учётом маршрутизации."""
        if pack.path:
            await self._route_back(pack)
        else:
            node = self.nodes.get(pack.dst)
            if node:
                transport = WebSocketTransport(node.ws)
                await transport.send(pack)

    def _make_transport_back(self, pack: MsgPack) -> WebSocketTransport:
        """
        Создать transport для ответа на пакет пришедший через форвардинг.
        Используется в _on_request когда pack.path уже содержит маршрут.
        """
        if pack.path:
            # ответ пойдёт через _send_back → _route_back
            # транспорт не нужен напрямую — возвращаем заглушку
            return _PathAwareTransport(pack, self)
        node = self.nodes.get(pack.source)
        if node:
            return WebSocketTransport(node.ws)
        raise NoRouteToHost(pack.source)

    # ------------------------------------------------------------------ #
    #  Исходящие вызовы (публичный API)
    # ------------------------------------------------------------------ #

    async def call(self, dst: str, service: str, method: str,
                   data: Any = None, timeout: int = 10) -> Any:
        pack = MsgPack(
            type    = PackType.REQUEST,
            source  = self.context.NODE,
            dst     = dst,
            service = service,
            method  = method,
            data    = data,
            path    = [self.context.NODE],
            ttl     = DEFAULT_TTL,
        )

        # локальный shortcut
        if dst == self.context.NODE:
            response = await self.executor.execute(pack)
            return response.data

        future = self.sessions.register_single(pack.label, service, method)

        try:
            await self._forward(pack)
        except NoRouteToHost as e:
            self.sessions.cancel(pack.label)
            raise

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            if isinstance(result, Exception):
                raise result
            return result
        except asyncio.TimeoutError:
            self.sessions.cancel(pack.label)
            raise RPCTimeout(pack.label, timeout)

    async def stream(self, dst: str, service: str, method: str,
                     data: Any = None) -> AsyncGenerator:
        pack = MsgPack(
            type    = PackType.REQUEST,
            source  = self.context.NODE,
            dst     = dst,
            service = service,
            method  = method,
            data    = data,
            path    = [self.context.NODE],
            ttl     = DEFAULT_TTL,
        )

        node = self.nodes.get(dst)
        if not node:
            raise NodeNotFound(dst)

        queue = self.sessions.register_stream(pack.label)
        await self._forward(pack)

        async def _gen():
            while True:
                chunk = await queue.get()
                if chunk is None:
                    return
                yield chunk

        return _gen()


# ------------------------------------------------------------------ #
#  PathAwareTransport — заглушка для path-aware ответов
# ------------------------------------------------------------------ #

class _PathAwareTransport(WebSocketTransport):
    """
    Используется когда пакет пришёл через форвардинг.
    send() направляет ответ через _route_back вместо прямого WS.
    """
    def __init__(self, original_pack: MsgPack, router: Router):
        self._original = original_pack
        self._router   = router
        self.ws        = None  # нет прямого WS

    async def send(self, pack: MsgPack):
        pack.path = list(reversed(self._original.path))
        await self._router._route_back(pack)
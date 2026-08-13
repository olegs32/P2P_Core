# GRID/router.py — полная версия с mesh forwarding и mesh streaming

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from src.internal_modules.exceptions import RPCTimeout
from src.internal_modules.executor import LocalExecutor, MethodNotFound
from src.internal_modules.memory import Pipe, _SENTINEL
from src.networking.protocol import MsgPack, PackType
from src.networking.sessions import SessionTable
from src.networking.stream_registry import StreamRegistry
from src.networking.transport import WebSocketTransport

log = logging.getLogger('Router')

DEFAULT_TTL = 16

# TTL кэша маршрута стрима (секунды)
_STREAM_ROUTE_TTL = 300


class NodeNotFound(Exception):
    def __init__(self, node): super().__init__(f'node={node}')


class NoRouteToHost(Exception):
    def __init__(self, node): super().__init__(f'no route to {node}')


# ------------------------------------------------------------------ #
#  StreamRoute — кэшированный маршрут стрима
# ------------------------------------------------------------------ #

@dataclass
class StreamRoute:
    label: str
    source: str                            # узел-генератор
    dst: str                               # узел-consumer
    forward_path: list[str] = field(default_factory=list)   # source → dst
    backward_path: list[str] = field(default_factory=list)  # dst → source
    established_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.established_at) > _STREAM_ROUTE_TTL


# ------------------------------------------------------------------ #
#  Router
# ------------------------------------------------------------------ #

class Router:
    def __init__(self, nodes_manager, context):
        self.context         = context
        self._nodes_mgr      = nodes_manager
        self.sessions        = SessionTable()
        self.stream_registry = StreamRegistry()
        self.executor = LocalExecutor(context.services, self.stream_registry, router_ref=self)
        # WS transports для ответов удалённым WS-клиентам (webpanel и т.д.)
        self._ws_pending: dict[str, WebSocketTransport] = {}
        # Client-side WS маппинг: node_id → websocket (от NodeConnector)
        self._client_ws: dict[str, Any] = {}
        # Кэш маршрутов стримов: label → StreamRoute
        self._stream_routes: dict[str, StreamRoute] = {}

    def register_client_ws(self, node_id: str, ws):
        """Зарегистрировать client-side WS (от NodeConnector)."""
        self._client_ws[node_id] = ws

    def unregister_client_ws(self, node_id: str):
        """Убрать client-side WS при disconnect."""
        self._client_ws.pop(node_id, None)

    def get_transport_to(self, node_id: str) -> WebSocketTransport | None:
        """Получить транспорт к узлу (server-side или client-side)."""
        node = self._nodes_mgr.get(node_id)
        if node:
            return WebSocketTransport(node.ws)
        client_ws = self._client_ws.get(node_id)
        if client_ws:
            return WebSocketTransport(client_ws)
        return None

    # ------------------------------------------------------------------ #
    #  Диспетчеризация пакетов
    # ------------------------------------------------------------------ #

    async def handle(self, pack: MsgPack, transport: WebSocketTransport):
        # обновить last_ts при любом трафике
        if pack.source:
            self.context.network.neighbor_table.touch(pack.source)

        match pack.type:
            case PackType.FORWARDED:
                await self._on_forwarded(pack)

            case PackType.REQUEST:
                if pack.dst and pack.dst != self.context.NODE:
                    await self._on_remote_request(pack, transport)
                else:
                    await self._on_request(pack, transport)

            case PackType.RESPONSE:
                if pack.label in self._ws_pending:
                    ws_transport = self._ws_pending.pop(pack.label)
                    await ws_transport.send(pack)
                elif pack.path:
                    await self._route_back(pack)
                else:
                    self.sessions.resolve(pack.label, pack.data)

            # --- Stream packets: маршрутизация через mesh --- #

            case PackType.STREAM_OPEN:
                if pack.dst and pack.dst != self.context.NODE:
                    await self._forward_stream_open(pack)
                else:
                    response = await self._on_stream_open(pack)
                    # кэшировать маршрут при получении STREAM_OPEN на dst
                    self._cache_stream_route_on_open(pack)
                    await self._send_back(response, pack)

            case PackType.STREAM_READY:
                # кэшировать маршрут на генераторе при получении READY
                self._cache_stream_route_on_ready(pack)
                if pack.path:
                    await self._route_back(pack)
                else:
                    self.sessions.resolve(pack.label, pack.data)

            case PackType.STREAM_CHUNK:
                if pack.dst and pack.dst != self.context.NODE:
                    await self._forward_stream_data(pack)
                else:
                    await self.stream_registry.feed(pack.label, pack.data)

            case PackType.STREAM_ACK:
                if pack.dst and pack.dst != self.context.NODE:
                    await self._route_back(pack)
                else:
                    self.sessions.resolve(f'ack_{pack.label}', 'ack')

            case PackType.STREAM_EOF:
                if pack.dst and pack.dst != self.context.NODE:
                    await self._forward_stream_data(pack)
                else:
                    await self.stream_registry.close(pack.label)
                    self._stream_routes.pop(pack.label, None)

            # --- /Stream --- #

            case PackType.ERROR:
                if pack.label in self._ws_pending:
                    ws_transport = self._ws_pending.pop(pack.label)
                    await ws_transport.send(pack)
                elif pack.path:
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

            case PackType.CERT_SYNC:
                certs_digest = (pack.data or {}).get('certs', [])
                from_node = pack.source
                sync_version = (pack.data or {}).get('sync_version', 0)
                self.context.certs_index.merge_cert_sync(
                    from_node, certs_digest, sync_version
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
        if pack.ttl <= 0:
            log.warning(
                f'[mesh] TTL=0 reached at {self.context.NODE} '
                f'label={pack.label[:8]} dst={pack.dst} — packet dropped'
            )
            return

        if self.context.NODE in pack.path:
            log.warning(
                f'[mesh] Loop detected at {self.context.NODE} '
                f'label={pack.label[:8]} path={pack.path} — packet dropped'
            )
            return

        pack.path.append(self.context.NODE)

        if pack.dst == self.context.NODE:
            pack.type = PackType.REQUEST
            transport = self._make_transport_back(pack)
            await self._on_request(pack, transport)
            return

        await self._forward(pack)

    # ------------------------------------------------------------------ #
    #  Локальная обработка REQUEST
    # ------------------------------------------------------------------ #

    async def _on_remote_request(self, pack: MsgPack, transport: WebSocketTransport):
        """Маршрутизация REQUEST от WS-клиента к удалённому узлу через mesh."""
        self._ws_pending[pack.label] = transport
        try:
            pack.path.append(self.context.NODE)
            pack.ttl -= 1
            await self._forward(pack)
        except NoRouteToHost:
            self._ws_pending.pop(pack.label, None)
            err = MsgPack(
                type=PackType.ERROR,
                source=self.context.NODE,
                dst=pack.source,
                label=pack.label,
                error=f'No route to host: {pack.dst}',
            )
            await transport.send(err)

    async def _on_request(self, pack: MsgPack, transport):
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
    #  Stream route caching
    # ------------------------------------------------------------------ #

    def _cache_stream_route_on_open(self, pack: MsgPack):
        """На consumer-узле: кэшировать маршрут из STREAM_OPEN."""
        if not pack.path or not pack.source or not pack.dst:
            return
        route = StreamRoute(
            label=pack.label,
            source=pack.source,
            dst=pack.dst or self.context.NODE,
            forward_path=list(pack.path) + [self.context.NODE],
            backward_path=[self.context.NODE] + list(reversed(pack.path)),
        )
        self._stream_routes[pack.label] = route
        log.debug(
            f'[stream] route cached on consumer: {pack.label[:8]} '
            f'fwd={route.forward_path} bwd={route.backward_path}'
        )

    def _cache_stream_route_on_ready(self, pack: MsgPack):
        """На generator-узле: кэшировать маршрут из STREAM_READY."""
        if not pack.path or not pack.source or not pack.dst:
            return
        route = StreamRoute(
            label=pack.label,
            source=pack.dst,            # мы — генератор
            dst=pack.source,            # consumer
            forward_path=list(reversed(pack.path)),  # мы → consumer
            backward_path=list(pack.path),            # consumer → мы
        )
        self._stream_routes[pack.label] = route
        log.debug(
            f'[stream] route cached on generator: {pack.label[:8]} '
            f'fwd={route.forward_path} bwd={route.backward_path}'
        )

    def get_stream_route(self, label: str) -> StreamRoute | None:
        route = self._stream_routes.get(label)
        if route and route.expired:
            self._stream_routes.pop(label, None)
            return None
        return route

    # ------------------------------------------------------------------ #
    #  Mesh forwarding — stream packets
    # ------------------------------------------------------------------ #

    async def _forward_stream_open(self, pack: MsgPack):
        """Форвардинг STREAM_OPEN через mesh с кэшированием маршрута."""
        pack.path.append(self.context.NODE)
        pack.ttl -= 1
        # Кэшировать маршрут на промежуточном узле (для обратного ACK)
        if pack.source and pack.dst:
            existing = self._stream_routes.get(pack.label)
            if not existing:
                self._stream_routes[pack.label] = StreamRoute(
                    label=pack.label,
                    source=pack.source,
                    dst=pack.dst,
                    forward_path=list(pack.path),
                )
        await self._forward(pack)

    async def _forward_stream_data(self, pack: MsgPack):
        """Форвардинг STREAM_CHUNK / STREAM_EOF — предпочтительно через кэш маршрута."""
        route = self.get_stream_route(pack.label)
        if route and self.context.NODE in route.forward_path:
            idx = route.forward_path.index(self.context.NODE)
            if idx + 1 < len(route.forward_path):
                next_hop = route.forward_path[idx + 1]
                transport = self.get_transport_to(next_hop)
                if transport:
                    await transport.send(pack)
                    return
        # Fallback: обычная маршрутизация
        await self._forward(pack)

    # ------------------------------------------------------------------ #
    #  Mesh forwarding — general
    # ------------------------------------------------------------------ #

    async def _forward(self, pack: MsgPack):
        """Переслать пакет к следующему хопу на пути к dst."""
        dst = pack.dst

        # 1. server-side
        node = self._nodes_mgr.get(dst)
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

        # 1b. client-side
        client_ws = self._client_ws.get(dst)
        if client_ws:
            pack.path.append(self.context.NODE)
            pack.ttl -= 1
            log.debug(
                f'[mesh] client-direct {self.context.NODE}→{dst} '
                f'label={pack.label[:8]} path={pack.path}'
            )
            transport = WebSocketTransport(client_ws)
            await transport.send(pack)
            return

        # 2. через via из NeighborTable
        neighbor = self.context.network.neighbor_table.get(dst)
        if neighbor and neighbor.via:
            via_transport = self.get_transport_to(neighbor.via)
            if via_transport:
                pack.path.append(self.context.NODE)
                pack.ttl -= 1
                if pack.type == PackType.REQUEST:
                    pack.type = PackType.FORWARDED
                log.info(
                    f'[mesh] forward {self.context.NODE}→{neighbor.via}→{dst} '
                    f'label={pack.label[:8]} ttl={pack.ttl} path={pack.path}'
                )
                await via_transport.send(pack)
                return

        # 3. нет маршрута
        log.error(f'[mesh] no route to {dst} label={pack.label[:8]}')
        raise NoRouteToHost(dst)

    async def _route_back(self, pack: MsgPack):
        """Вернуть пакет по обратному маршруту из pack.path."""
        if not pack.path:
            self.sessions.resolve(pack.label, pack.data)
            return

        path = pack.path
        if path and path[-1] == self.context.NODE:
            path = path[:-1]

        if not path:
            self.sessions.resolve(pack.label, pack.data)
            return

        next_hop = path[-1]
        transport = self.get_transport_to(next_hop)

        if not transport:
            log.error(
                f'[mesh] return path broken: '
                f'{next_hop} not reachable path={pack.path}'
            )
            return

        pack.path = path
        log.debug(
            f'[mesh] route_back →{next_hop} '
            f'label={pack.label[:8]} remaining_path={path}'
        )
        await transport.send(pack)

    async def _send_back(self, response: MsgPack, original: MsgPack):
        """Отправить ответ: по path если был форвардинг, иначе напрямую."""
        if original.path:
            response.path = list(reversed(original.path))
            await self._route_back(response)
        else:
            transport = self.get_transport_to(response.dst)
            if transport:
                await transport.send(response)
            else:
                log.error(f'[mesh] _send_back: no transport to {response.dst}')

    async def _send_pack(self, pack: MsgPack):
        """Отправить пакет — с учётом маршрутизации."""
        if pack.path:
            await self._route_back(pack)
        else:
            transport = self.get_transport_to(pack.dst)
            if transport:
                await transport.send(pack)

    def _make_transport_back(self, pack: MsgPack):
        """Создать transport для ответа на пакет через форвардинг."""
        if pack.path:
            return _PathAwareTransport(pack, self)
        transport = self.get_transport_to(pack.source)
        if transport:
            return transport
        raise NoRouteToHost(pack.source)

    # ------------------------------------------------------------------ #
    #  Stream ACK — отправка через mesh (Вариант A)
    # ------------------------------------------------------------------ #

    async def send_stream_ack(self, label: str, buff: int):
        """Отправить STREAM_ACK генератору — через mesh если нужно."""
        route = self.get_stream_route(label)
        dst = route.source if route else None
        ack_pack = MsgPack(
            type=PackType.STREAM_ACK,
            source=self.context.NODE,
            dst=dst,
            label=label,
            data=buff,
            path=list(route.backward_path) if route else [],
        )
        if route and route.backward_path:
            await self._route_back(ack_pack)
        elif dst:
            transport = self.get_transport_to(dst)
            if transport:
                await transport.send(ack_pack)
            else:
                log.warning(f'[stream] ACK: no route to {dst} label={label[:8]}')
        else:
            log.warning(f'[stream] ACK: no cached route for label={label[:8]}')

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

        if dst == self.context.NODE:
            response = await self.executor.execute(pack)
            return response.data

        future = self.sessions.register_single(pack.label, service, method)

        try:
            await self._forward(pack)
        except NoRouteToHost:
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
                     data: Any = None, timeout: int = 30) -> AsyncGenerator:
        """Открыть mesh-стрим и вернуть async iterator."""
        label = str(uuid.uuid4())

        open_pack = MsgPack(
            type    = PackType.STREAM_OPEN,
            source  = self.context.NODE,
            dst     = dst,
            service = service,
            method  = method,
            data    = data,
            label   = label,
            path    = [self.context.NODE],
            ttl     = DEFAULT_TTL,
        )

        ready_future = self.sessions.register_single(label, service, method)

        try:
            await self._forward(open_pack)
        except NoRouteToHost:
            self.sessions.cancel(label)
            raise

        try:
            await asyncio.wait_for(ready_future, timeout=timeout)
        except asyncio.TimeoutError:
            self.sessions.cancel(label)
            raise RPCTimeout(label, timeout)

        pipe = Pipe(pipe_id=f'mesh_{label[:8]}', buff_len=10)
        self.stream_registry.register(label, pipe)

        return _MeshStreamIterator(self, label, pipe)


# ------------------------------------------------------------------ #
#  MeshStreamIterator — async iterator для mesh-стримов
# ------------------------------------------------------------------ #

class _MeshStreamIterator:
    """Итератор по чанкам mesh-стрима с автоматическим ACK."""

    def __init__(self, router: Router, label: str, pipe: Pipe):
        self.router = router
        self.label = label
        self._pipe = pipe

    def __aiter__(self):
        return self

    async def __anext__(self):
        chunk = await self._pipe.get()
        if chunk is _SENTINEL:
            raise StopAsyncIteration
        await self.router.send_stream_ack(self.label, self._pipe.buff_len)
        return chunk


# ------------------------------------------------------------------ #
#  PathAwareTransport — транспорт для path-aware ответов
# ------------------------------------------------------------------ #

class _PathAwareTransport:
    """
    Используется когда пакет пришёл через форвардинг.
    send() направляет ответ через _route_back вместо прямого WS.
    """
    def __init__(self, original_pack: MsgPack, router: Router):
        self._original = original_pack
        self._router   = router
        self.ws        = None

    async def send(self, pack: MsgPack):
        pack.path = list(reversed(self._original.path))
        await self._router._route_back(pack)

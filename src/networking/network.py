# GRID/network.py

import asyncio
import logging
import uuid

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict

from src.internal_modules.base import ModuleGeneric
from src.internal_modules.local_ip import LocalIPResolver
from src.networking.neighbor_table import PROTOCOL_VERSION, NeighborTable
from src.networking.protocol import (
    MAX_FRAME_SIZE,
    MsgPack,
    PackType,
    UnknownPackTypeError,
    decode_pack,
    hexdump_head,
)
from src.networking.router import Router
from src.networking.transport import WebSocketTransport

log = logging.getLogger('Network')

WS_CLOSE_PROTOCOL_ERROR = 1002


class FrameDecodeError(Exception):
    """Кадр нарушает протокол (text вместо binary / битый msgpack) —
    граница доверия: соединение закрывается."""


class Node:
    def __init__(self, node_id: str, ws: WebSocket):
        self.node_id = node_id
        self.ws = ws


class NodesManager:
    def __init__(self):
        self.nodes: Dict[str, Node] = {}

    def register(self, node_id: str, websocket: WebSocket) -> Node:
        node = Node(node_id=node_id, ws=websocket)
        self.nodes[node_id] = node
        log.info(f'Node {node_id} registered')
        return node

    def remove(self, node_id: str):
        self.nodes.pop(node_id, None)
        log.info(f'Node {node_id} removed')

    def get(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)


class NetworkModule(ModuleGeneric):
    def __init__(self, name: str, context, host: str = "0.0.0.0", port: int = 9000):
        super().__init__(name, context)
        self.host = host
        self.port = port
        self.app = FastAPI()
        self.nodes_manager: NodesManager = NodesManager()
        self.neighbor_table = NeighborTable(own_node_id=context.NODE)
        self.router = Router(self.nodes_manager, context)
        self.ip_resolver = LocalIPResolver(context)
        self._server        = None
        self._task          = None
        self._gossip_task   = None
        self._announce_task = None
        self._register_routes()

    def _register_routes(self):
        app = self.app

        @app.websocket("/ws/{node_id}")
        async def websocket_endpoint(websocket: WebSocket, node_id: str):
            await websocket.accept()
            transport = WebSocketTransport(websocket)

            try:
                # ждём HELLO первым кадром — строго binary msgpack
                first = await asyncio.wait_for(websocket.receive(), timeout=10)

                if first.get('bytes') is None:
                    # text-кадр = легаси JSON-клиент; протокол теперь msgpack-only
                    reason = (
                        'upgrade required: this node speaks msgpack '
                        f'binary frames (protocol {PROTOCOL_VERSION})'
                    )
                    self.log.info(
                        f'Handshake rejected from {node_id}: '
                        f'text frame (legacy JSON client)'
                    )
                    await transport.send(MsgPack(
                        type   = PackType.HELLO_REJECT,
                        source = self.ctx.NODE,
                        dst    = node_id,
                        data   = {'reason': reason},
                    ))
                    await websocket.close(reason='upgrade required')
                    return

                try:
                    pack = decode_pack(first['bytes'])
                except Exception as e:
                    self.log.warning(
                        f'Malformed HELLO frame from {node_id}: {e} '
                        f'head={hexdump_head(first["bytes"])}'
                    )
                    await websocket.close(code=WS_CLOSE_PROTOCOL_ERROR)
                    return

                if pack.type != PackType.HELLO:
                    reason = f'expected HELLO, got {pack.type.value}'
                    self.log.info(f'Handshake rejected from {node_id}: {reason}')
                    await transport.send(MsgPack(
                        type   = PackType.HELLO_REJECT,
                        source = self.ctx.NODE,
                        dst    = node_id,
                        data   = {'reason': reason},
                    ))
                    await websocket.close(reason='handshake rejected')
                    return

                if pack.dst != self.ctx.NODE:
                    reason = (
                        f'HELLO addressed to {pack.dst!r}, '
                        f'this node is {self.ctx.NODE!r} — check peer URI/node_id'
                    )
                    self.log.info(f'Handshake rejected from {node_id}: {reason}')
                    await transport.send(MsgPack(
                        type=PackType.HELLO_REJECT,
                        source=self.ctx.NODE,
                        dst=node_id,
                        data={
                            'reason':   reason,
                            'expected': self.ctx.NODE,
                            'got':      pack.dst,
                        },
                    ))
                    await websocket.close(reason='handshake rejected')
                    return

                # проверить дубликат — заменить старое подключение на новое (reconnect)
                old_node = self.nodes_manager.get(node_id)
                if old_node:
                    # Сначала регистрируем новое WS, чтобы избежать окна,
                    # когда узел отсутствует в nodes_manager и ответы теряются
                    self.nodes_manager.register(node_id, websocket)
                    self.log.info(f'Reconnect: replacing connection for {node_id}')
                    try:
                        await old_node.ws.close()
                    except Exception:
                        pass
                else:
                    self.nodes_manager.register(node_id, websocket)

                # принять
                hello_data = pack.data or {}
                session_id = str(uuid.uuid4())

                self.neighbor_table.register_connected(
                    node_id    = node_id,
                    host       = hello_data.get('host', ''),
                    port       = hello_data.get('port', 9000),
                    session_id = session_id,
                    version    = hello_data.get('version', PROTOCOL_VERSION),
                    services   = hello_data.get('services', []),
                )

                # ответить HELLO_ACK с текущей таблицей соседей и сервисами
                await transport.send(MsgPack(
                    type   = PackType.HELLO_ACK,
                    source = self.ctx.NODE,
                    dst    = node_id,
                    data   = {
                        'host':       self.local_ip(),
                        'port':       self.port,
                        'version':    PROTOCOL_VERSION,
                        'session_id': session_id,
                        'services':   list(self.ctx.services.services.keys()),
                        'neighbors':  self.neighbor_table.to_gossip(),
                    }
                ))
                self.log.info(
                    f'Node {node_id} accepted (session={session_id[:8]}, '
                    f'enc={hello_data.get("enc", "?")})'
                )

                # Запросить CERT_SYNC у нового узла (если у него есть certstool)
                hello_services = hello_data.get('services', [])
                if 'certstool' in hello_services:
                    asyncio.create_task(self._request_cert_sync(node_id))

                # основной цикл — только binary msgpack-кадры
                while True:
                    try:
                        pack = await self._recv_pack(websocket)
                    except UnknownPackTypeError as e:
                        # forward-compat: будущие PackType не рвут соединение
                        self.log.warning(
                            f'Unknown pack type {e.type_value!r} '
                            f'from {node_id} — dropped'
                        )
                        continue
                    except FrameDecodeError as e:
                        self.log.warning(f'{e} — closing connection')
                        await websocket.close(code=WS_CLOSE_PROTOCOL_ERROR)
                        break

                    # обновить last_ts при любом трафике
                    self.neighbor_table.touch(pack.source)

                    await self.router.handle(pack, transport)

            except asyncio.TimeoutError:
                self.log.warning(f'HELLO timeout from {node_id}')
            except WebSocketDisconnect:
                pass
            finally:
                # Удалить только если это текущее (активное) WS для node_id,
                # а не уже заменённое при reconnect
                current = self.nodes_manager.get(node_id)
                was_active = bool(current and current.ws is websocket)
                if was_active:
                    self.nodes_manager.remove(node_id)
                    self.neighbor_table.mark_unreachable(node_id)
                # Очистить pending-ответы для этого WS в любом случае
                self.router.cleanup_ws_pending(websocket)
                if was_active:
                    self.log.info(f'Node {node_id} disconnected')

    async def _recv_pack(self, websocket: WebSocket) -> MsgPack:
        """Принять один пакет: binary-кадр → decode_pack."""
        msg = await websocket.receive()
        raw = msg.get('bytes')
        if raw is None:
            text = msg.get('text')
            head = text[:80] if isinstance(text, str) else repr(text)
            raise FrameDecodeError(
                f'text frame in msgpack-only mode from peer: {head!r}'
            )
        try:
            return decode_pack(raw)
        except UnknownPackTypeError:
            raise
        except Exception as e:
            raise FrameDecodeError(
                f'malformed frame: {e} head={hexdump_head(raw)}'
            ) from e

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self):
        config = uvicorn.Config(
            self.app, host=self.host, port=self.port, log_level="warning",
            ws_max_size=MAX_FRAME_SIZE,
        )
        self._server = uvicorn.Server(config)
        self._task   = asyncio.create_task(self._server.serve())
        self._gossip_task   = asyncio.create_task(self._gossip_loop())
        self._announce_task = asyncio.create_task(self._announce_loop())
        self.log.info(f'Started on {self.host}:{self.port}')

    async def stop(self):
        for task in (self._gossip_task, self._announce_task):
            if task:
                task.cancel()
        if self._server:
            self._server.should_exit = True
        if self._task:
            await self._task
        self.log.info('Stopped')

    # ------------------------------------------------------------------ #
    #  Периодические рассылки
    # ------------------------------------------------------------------ #

    async def _gossip_loop(self):
        """Каждые 30с рассылать таблицу соседей всем connected нодам."""
        while True:
            await asyncio.sleep(30)
            neighbors = self.neighbor_table.to_gossip()
            if not neighbors:
                continue
            pack = MsgPack(
                type   = PackType.GOSSIP,
                source = self.ctx.NODE,
                data   = {'neighbors': neighbors, 'from': self.ctx.NODE},
            )
            for node in self.neighbor_table.connected():
                transport = self.router.get_transport_to(node.node_id)
                if transport:
                    try:
                        await transport.send(pack)
                    except Exception as e:
                        self.log.error(f'Gossip to {node.node_id} failed: {e}')

    async def _announce_loop(self):
        """Каждые 60с рассылать список сервисов всем connected нодам."""
        while True:
            await asyncio.sleep(60)
            services = list(self.ctx.services.services.keys())
            pack = MsgPack(
                type   = PackType.ANNOUNCE,
                source = self.ctx.NODE,
                data   = {'services': services, 'from': self.ctx.NODE},
            )
            for node in self.neighbor_table.connected():
                transport = self.router.get_transport_to(node.node_id)
                if transport:
                    try:
                        await transport.send(pack)
                    except Exception as e:
                        self.log.error(f'Announce to {node.node_id} failed: {e}')

    # ------------------------------------------------------------------ #
    #  CERT_SYNC on-connect
    # ------------------------------------------------------------------ #

    async def _request_cert_sync(self, node_id: str):
        """Запросить CERT_SYNC digest у нового узла и отправить свой."""
        await asyncio.sleep(1)  # дать время на завершение handshake

        try:
            # 1. Запросить digest у нового узла
            result = await self.router.call(
                dst=node_id,
                service='certstool',
                method='get_cert_sync_digest',
                data={},
                timeout=10,
            )
            if result and isinstance(result, dict):
                certs = result.get('certs', [])
                sync_version = result.get('sync_version', 0)
                self.ctx.certs_index.merge_cert_sync(node_id, certs, sync_version)
                self.log.info(f'On-connect CERT_SYNC from {node_id}: {len(certs)} certs')
        except Exception as e:
            self.log.warning(f'On-connect CERT_SYNC request to {node_id} failed: {e}')

        # 2. Отправить свой digest новому узлу
        try:
            digest = self.ctx.certs_index.get_digest_for_sync()
            pack = MsgPack(
                type=PackType.CERT_SYNC,
                source=self.ctx.NODE,
                data={'certs': digest, 'sync_version': 0},
            )
            transport = self.router.get_transport_to(node_id)
            if transport:
                await transport.send(pack)
        except Exception as e:
            self.log.warning(f'On-connect CERT_SYNC send to {node_id} failed: {e}')

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def local_ip(self) -> str:
        """Локальный IP интерфейса mesh (по запросу, с TTL-кэшем)."""
        return self.ip_resolver.get()

    async def connect_to(self, node_id: str, target_uri: str):
        """Динамически создать и запустить исходящее подключение к узлу.

        Проверяет, что узел ещё не подключен (по NeighborTable).
        """
        from src.networking.node_connector import NodeConnector

        existing = self.neighbor_table.get(node_id)
        if existing and existing.status.value == 'connected':
            raise ValueError(f'Узел {node_id} уже подключен')

        connector = NodeConnector(
            name=f'Connector_{node_id}',
            context=self.ctx,
            peer_node_id=node_id,
            target_uri=target_uri,
        )
        self.ctx.register(connector)
        await connector.start()
        self.log.info(f'Dynamic connection initiated → {node_id} ({target_uri})')

    async def call(self, dst: str, service: str, method: str, data=None, timeout: int = 10):
        """Single RPC вызов."""
        return await self.router.call(dst, service, method, data, timeout)

    async def stream(self, dst: str, service: str, method: str,
                     data=None, timeout: int = 30):
        """Открыть mesh-стрим и вернуть async iterator по чанкам."""
        return await self.router.stream(dst, service, method, data, timeout)

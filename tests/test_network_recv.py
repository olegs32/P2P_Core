# tests/test_network_recv.py
# Регрессия: disconnect-сообщение ASGI при остановке узла не должно
# классифицироваться как «text frame 'None'» (FrameDecodeError) и приводить
# к двойному websocket.close → RuntimeError в uvicorn.

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from src.networking.network import NetworkModule, FrameDecodeError


class FakeASGIWebSocket:
    """Минимальный стаб starlette-WebSocket для _recv_pack."""

    def __init__(self, messages):
        self._messages = list(messages)

    async def receive(self):
        if not self._messages:
            raise RuntimeError('no more messages')
        return self._messages.pop(0)


def make_module():
    class Ctx:
        NODE = 'NodeSelf'
        from services.manager import ServiceManager
        services = ServiceManager()

    return NetworkModule('network', Ctx())


def test_disconnect_message_raises_websocketdisconnect():
    """Остановка узла/уход пира: {'type': 'websocket.disconnect'} — это
    штатное закрытие, а НЕ нарушение протокола."""
    m = make_module()
    ws = FakeASGIWebSocket([{'type': 'websocket.disconnect', 'code': 1001}])

    with pytest.raises(WebSocketDisconnect) as ei:
        asyncio.run(m._recv_pack(ws))

    assert ei.value.code == 1001


def test_real_text_frame_still_protocol_error():
    """Настоящий text-кадр (легаси JSON после handshake) — по-прежнему ошибка."""
    m = make_module()
    ws = FakeASGIWebSocket([
        {'type': 'websocket.receive', 'text': '{"json": true}'}
    ])

    with pytest.raises(FrameDecodeError):
        asyncio.run(m._recv_pack(ws))


def test_binary_frame_decodes():
    import msgpack
    from src.networking.protocol import MsgPack, PackType, encode_pack

    m = make_module()
    pack = MsgPack(type=PackType.PING, source='peer')
    ws = FakeASGIWebSocket([
        {'type': 'websocket.receive', 'bytes': encode_pack(pack)}
    ])

    got = asyncio.run(m._recv_pack(ws))
    assert got.type == PackType.PING
    assert got.source == 'peer'

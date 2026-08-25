# tests/test_protocol.py — wire-формат: encode/decode round-trip

import pytest

from src.networking.protocol import (
    MAX_FRAME_SIZE,
    MsgPack,
    PackType,
    UnknownPackTypeError,
    decode_pack,
    encode_pack,
    hexdump_head,
)


def make_pack(**kwargs) -> MsgPack:
    defaults = dict(source='NodeA', dst='NodeB', label=str(kwargs.get('label', 'l1')))
    defaults.update(kwargs)
    return MsgPack(**defaults)


# ------------------------------------------------------------------ #
#  Round-trip
# ------------------------------------------------------------------ #

@pytest.mark.parametrize('data', [
    None,
    b'',
    b'\x00\x01\xff',
    b'x' * 1024,
    b'y' * (8 * 1024 * 1024),          # 8 МБ
    'hello ✓ юникод',
    {'nested': {'list': [1, 2.5, True, None, b'bytes']}},
    [1, 'two', 3.0, None, False],
    0,
    42,
    2**63,                              # большое int
    -2**63,
    3.141592653589793,
    True,
], ids=[
    'none', 'empty-bytes', 'small-bytes', '1kb-bytes', '8mb-bytes',
    'unicode-str', 'nested-dict', 'mixed-list',
    'int-zero', 'int-42', 'bigint-max', 'bigint-min', 'float-pi', 'bool-true',
])
def test_roundtrip_data(data):
    pack = make_pack(data=data)
    restored = decode_pack(encode_pack(pack))
    assert restored.data == data
    assert type(restored.data) is type(data) or data is None


def test_roundtrip_bytes_type_preserved():
    # bytes обязаны выжить как bytes (не str), это главный смысл миграции
    pack = make_pack(type=PackType.STREAM_CHUNK, data=b'\xde\xad\xbe\xef')
    restored = decode_pack(encode_pack(pack))
    assert isinstance(restored.data, bytes)
    assert restored.data == b'\xde\xad\xbe\xef'


@pytest.mark.parametrize('ptype', list(PackType))
def test_roundtrip_all_pack_types(ptype):
    pack = make_pack(type=ptype)
    restored = decode_pack(encode_pack(pack))
    assert restored.type is ptype


def test_roundtrip_full_model():
    pack = MsgPack(
        type=PackType.FORWARDED,
        source='N0',
        dst='N2',
        service='svc',
        method='m',
        data={'k': [1, b'b']},
        label='lbl-123',
        error=None,
        path=['N0', 'N1'],
        ttl=5,
    )
    restored = decode_pack(encode_pack(pack))
    assert restored == pack


def test_encode_returns_bytes_binary_payload():
    payload = encode_pack(make_pack())
    assert isinstance(payload, bytes)


# ------------------------------------------------------------------ #
#  Битые / невалидные кадры → граница доверия
# ------------------------------------------------------------------ #

@pytest.mark.parametrize('raw', [
    b'',                                # пустой кадр
    b'\xff\xff\xff\xff',                # мусор
    b'\xc1',                            # msgpack "undefined" — запрещён
    b'\xa3abc',                         # валидный msgpack, но не dict (str)
    b'\x91\x01',                        # валидный msgpack, но не dict (list)
])
def test_decode_invalid_frame_raises(raw):
    with pytest.raises(Exception):
        decode_pack(raw)


def test_decode_truncated_frame_raises():
    payload = encode_pack(make_pack(data=b'z' * 100))
    with pytest.raises(Exception):
        decode_pack(payload[:-10])


def test_decode_unknown_type_forward_compat():
    from src.networking.protocol import _KNOWN_TYPES
    import msgpack

    d = make_pack().model_dump()
    d['type'] = 'future_packet_kind'
    assert d['type'] not in _KNOWN_TYPES
    raw = msgpack.packb(d, use_bin_type=True)

    with pytest.raises(UnknownPackTypeError):
        decode_pack(raw)


def test_unknown_type_is_exception_not_valueerror():
    import msgpack

    d = make_pack().model_dump()
    d['type'] = 'whatever'
    raw = msgpack.packb(d, use_bin_type=True)
    with pytest.raises(UnknownPackTypeError):
        decode_pack(raw)


def test_decode_missing_required_field_raises():
    import msgpack

    raw = msgpack.packb({'type': 'ping'})  # нет source
    with pytest.raises(Exception):
        decode_pack(raw)


# ------------------------------------------------------------------ #
#  Утилиты и константы
# ------------------------------------------------------------------ #

def test_max_frame_size_sane():
    assert MAX_FRAME_SIZE == 32 * 1024 * 1024


def test_hexdump_head():
    raw = bytes(range(256))
    head = hexdump_head(raw, n=4)
    assert head == '00 01 02 03'
    assert len(hexdump_head(raw).split(' ')) == 64


def test_str_enum_packed_as_string():
    import msgpack

    payload = encode_pack(make_pack(type=PackType.PING))
    d = msgpack.unpackb(payload, raw=False)
    assert d['type'] == 'ping'

# tests/test_app_version.py

import pytest

from src.internal_modules.app_version import (
    compare_versions, is_newer, parse_version, read_version,
)


@pytest.mark.parametrize('raw, expected', [
    ('2.1.0', (2, 1, 0, 0)),
    ('v2.1.0-build42', (2, 1, 0, 42)),
    ('10.0.3-build7', (10, 0, 3, 7)),
    (' 1.2.3 ', (1, 2, 3, 0)),
    ('garbage', None),
    ('1.2', None),
    ('', None),
    (None, None),
])
def test_parse(raw, expected):
    assert parse_version(raw) == expected


@pytest.mark.parametrize('a, b, expected', [
    ('2.1.0', '2.1.0', 0),
    ('2.1.0-build1', '2.1.0', +1),
    ('2.1.0', '2.1.0-build1', -1),
    ('2.1.0', '2.2.0', -1),
    ('10.0.0', '9.99.99', +1),
])
def test_compare(a, b, expected):
    assert compare_versions(a, b) == expected


def test_is_newer():
    assert is_newer('2.1.0-build5', '2.1.0') is True
    assert is_newer('2.1.0', '2.1.0') is False
    assert is_newer('garbage', '1.0.0') is False
    # текущая неразбираемая (dev) — любой валидный кандидат новее
    assert is_newer('0.1.0', '0.0.0-dev') is True


def test_read_version_returns_string():
    v = read_version()
    assert isinstance(v, str)
    if v != '0.0.0-dev':
        assert parse_version(v) is not None

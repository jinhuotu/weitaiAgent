import asyncio
from asyncio.protocols import _feed_data_to_buffered_proto

import pytest
from asyncmy.connection import _MySQLProtocol

from db import asyncmy_compat


@pytest.fixture
def protocol(monkeypatch):
    original = _MySQLProtocol.get_buffer
    while hasattr(original, "__wrapped__"):
        original = original.__wrapped__
    monkeypatch.setattr(_MySQLProtocol, "get_buffer", original)
    monkeypatch.setattr(asyncmy_compat, "version", lambda _: "0.2.14")
    loop = asyncio.new_event_loop()
    try:
        yield _MySQLProtocol(loop)
    finally:
        loop.close()


@pytest.mark.parametrize("size", [1, 262144, 262145, 1048576, 5242880])
def test_proactor_receive_preserves_complete_large_result(protocol, size):
    assert asyncmy_compat.install_asyncmy_buffer_workaround(platform="win32")
    payload = (b"abcdefgh" * ((size + 7) // 8))[:size]
    _feed_data_to_buffered_proto(protocol, payload)
    assert protocol.length == size
    assert protocol.buffer[:protocol.length] == payload


def test_exported_old_buffer_stays_valid_during_growth(protocol):
    assert asyncmy_compat.install_asyncmy_buffer_workaround(platform="win32")
    old = protocol.get_buffer(-1)
    size = len(old)
    old[:] = b"x" * size
    protocol.buffer_updated(size)
    new = protocol.get_buffer(1)
    new[0] = ord("y")
    protocol.buffer_updated(1)
    assert old.tobytes() == b"x" * size
    assert protocol.buffer[:protocol.length] == b"x" * size + b"y"
    old.release()
    new.release()


def test_compaction_before_resize_failure_keeps_unconsumed_bytes(protocol):
    assert asyncmy_compat.install_asyncmy_buffer_workaround(platform="win32")
    old = protocol.get_buffer(-1)
    payload = (b"abcdefgh" * ((len(old) + 7) // 8))[:len(old)]
    old[:] = payload
    protocol.buffer_updated(len(old))
    protocol.pos = 1000
    new = protocol.get_buffer(len(old))
    assert protocol.pos == 0
    assert protocol.length == len(payload) - 1000
    assert protocol.buffer[:protocol.length] == payload[1000:]
    old.release()
    new.release()


def test_installer_is_idempotent_and_leaves_other_platforms_alone(protocol):
    original = _MySQLProtocol.get_buffer
    assert not asyncmy_compat.install_asyncmy_buffer_workaround(platform="linux")
    assert _MySQLProtocol.get_buffer is original
    assert asyncmy_compat.install_asyncmy_buffer_workaround(platform="win32")
    installed = _MySQLProtocol.get_buffer
    assert asyncmy_compat.install_asyncmy_buffer_workaround(platform="win32")
    assert _MySQLProtocol.get_buffer is installed


def test_unknown_driver_version_is_not_patched(protocol, monkeypatch):
    original = _MySQLProtocol.get_buffer
    monkeypatch.setattr(asyncmy_compat, "version", lambda _: "0.3.0")
    assert not asyncmy_compat.install_asyncmy_buffer_workaround(platform="win32")
    assert _MySQLProtocol.get_buffer is original


def test_unrelated_buffer_error_is_not_suppressed(protocol, monkeypatch):
    def unrelated(*args):
        raise BufferError("unrelated buffer failure")

    monkeypatch.setattr(_MySQLProtocol, "get_buffer", unrelated)
    assert asyncmy_compat.install_asyncmy_buffer_workaround(platform="win32")
    with pytest.raises(BufferError, match="unrelated buffer failure"):
        protocol.get_buffer(1)

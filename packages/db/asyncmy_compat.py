"""Compatibility for asyncmy 0.2.14's BufferedProtocol on Windows.

Proactor's feed helper can retain the previous memoryview while asking for the
next receive buffer. asyncmy then resizes the exported bytearray and raises
BufferError for large MySQL results. Copy on that specific resize failure;
the driver's normal buffer management and the event-loop policy stay intact.
"""

from functools import wraps
from importlib.metadata import PackageNotFoundError, version
import logging
import sys


def install_asyncmy_buffer_workaround(*, platform: str | None = None) -> bool:
    if (platform or sys.platform) != "win32":
        return False
    try:
        if version("asyncmy") != "0.2.14":
            return False
    except PackageNotFoundError:
        return False

    from asyncmy import connection

    protocol = getattr(connection, "_MySQLProtocol", None)
    original = getattr(protocol, "get_buffer", None)
    if original is None:
        return False
    if getattr(original, "_weitai_buffer_resize_safe", False):
        return True

    @wraps(original)
    def get_buffer(self, sizehint):
        try:
            return original(self, sizehint)
        except BufferError as exc:
            if "Existing exports of data" not in str(exc):
                raise
            # The first call may already have compacted consumed bytes; copy
            # that current state and retry without resizing the borrowed buffer.
            self.buffer = bytearray(self.buffer)
            return original(self, sizehint)

    get_buffer._weitai_buffer_resize_safe = True
    protocol.get_buffer = get_buffer
    logging.getLogger(__name__).warning("Applied asyncmy 0.2.14 Windows receive-buffer compatibility fix")
    return True

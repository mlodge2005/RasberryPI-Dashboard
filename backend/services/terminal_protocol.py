"""Length-prefixed frames for the terminal helper socket.

WebSockets have message boundaries. A Unix socket does not, so the dashboard
and the helper speak a small binary frame instead of raw PTY bytes.
"""

from __future__ import annotations

import struct

MAX_PAYLOAD = 65536
KIND_INPUT = 1
KIND_RESIZE = 2
KIND_OUTPUT = 3
KIND_EXIT = 4
KIND_ERROR = 5
_KINDS = {KIND_INPUT, KIND_RESIZE, KIND_OUTPUT, KIND_EXIT, KIND_ERROR}
_HEADER = struct.Struct("!IB")
_RESIZE = struct.Struct("!HH")
_MAX_DIMENSION = 500


def encode_frame(kind: int, payload: bytes = b"") -> bytes:
    if kind not in _KINDS:
        raise ValueError("invalid terminal frame")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    return _HEADER.pack(len(payload), kind) + payload


def encode_resize(rows: int, cols: int) -> bytes:
    safe_rows = max(1, min(int(rows), _MAX_DIMENSION))
    safe_cols = max(1, min(int(cols), _MAX_DIMENSION))
    return encode_frame(KIND_RESIZE, _RESIZE.pack(safe_rows, safe_cols))


def decode_resize(payload: bytes) -> tuple[int, int]:
    if len(payload) != _RESIZE.size:
        raise ValueError("bad resize")
    rows, cols = _RESIZE.unpack(payload)
    if not (1 <= rows <= _MAX_DIMENSION and 1 <= cols <= _MAX_DIMENSION):
        raise ValueError("bad resize")
    return rows, cols


class FrameReader:
    """Reassemble frames from an arbitrary byte stream."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        if not data:
            return []
        self._buf.extend(data)
        frames: list[tuple[int, bytes]] = []
        while len(self._buf) >= _HEADER.size:
            length, kind = _HEADER.unpack_from(self._buf, 0)
            if length > MAX_PAYLOAD or kind not in _KINDS:
                raise ValueError("invalid terminal frame")
            total = _HEADER.size + length
            if len(self._buf) < total:
                break
            payload = bytes(self._buf[_HEADER.size : total])
            del self._buf[:total]
            frames.append((kind, payload))
        return frames

"""Incremental console output assembler.

Child processes write two kinds of output into the same stream: finished lines
ending with `\\n`, and live frames that overwrite each other (download bars,
spinners). A pseudo console repaints those frames with `\\r` or with cursor
positioning escapes, so both are treated as frame boundaries here.

The result stays a line based log: complete lines are emitted as they arrive,
and overwriting frames are emitted as throttled progress updates instead of
thousands of log rows.
"""

from __future__ import annotations

from typing import Callable
import re
import time

from .ansi import strip_ansi
from .output_decode import decode_process_bytes


SPINNER_FRAME_CHARS = set("-\\|/ \t─│░▒▓█")

# `ESC [ <n> ; <m> H`, `ESC [ ... f` and `ESC [ 2 J` all repaint from the top of
# the frame, exactly like a carriage return does for a single line.
REPAINT_PATTERN = re.compile(rb"\x1b\[(?:\d+(?:;\d+)?)?[Hf]|\x1b\[\??\d*J")

EmitCallback = Callable[[str, bool], None]


def is_spinner_only(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and all(char in SPINNER_FRAME_CHARS for char in line)


def is_blank_output(text: str) -> bool:
    return not strip_ansi(text).strip()


def _escape_is_complete(data: bytes) -> bool:
    if len(data) < 2:
        return False
    kind = data[1:2]
    if kind == b"[":
        return any(0x40 <= byte <= 0x7E for byte in data[2:])
    if kind == b"]":
        return b"\x07" in data or b"\x1b\\" in data[2:]
    return True


class StreamAssembler:
    """Feed raw child bytes in, get decoded log lines out."""

    def __init__(
        self,
        emit: EmitCallback,
        *,
        progress_interval: float = 0.7,
        max_pending: int = 65536,
        keep_spinner_frames: bool = False,
    ) -> None:
        self._emit = emit
        self._progress_interval = max(0.0, float(progress_interval))
        self._max_pending = max(1024, int(max_pending))
        self._keep_spinner_frames = keep_spinner_frames
        self._raw = bytearray()
        self._buffer = bytearray()
        self._last_progress_at = 0.0
        self._last_progress_text = ""

    # -- public API ----------------------------------------------------------

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._raw.extend(chunk)
        self._normalize_raw(force=len(self._raw) > self._max_pending)

        while True:
            index = self._buffer.find(b"\n")
            if index < 0:
                break
            line = bytes(self._buffer[:index])
            del self._buffer[: index + 1]
            self._emit_completed_line(line)

        carriage = self._buffer.rfind(b"\r")
        if carriage >= 0:
            frames = bytes(self._buffer[:carriage]).split(b"\r")
            del self._buffer[: carriage + 1]
            self._emit_progress_frame(frames)

        if len(self._buffer) > self._max_pending:
            overflow = bytes(self._buffer)
            self._buffer.clear()
            self._emit_text(self._decode(overflow), False)

    def flush(self) -> None:
        self._normalize_raw(force=True)
        if not self._buffer:
            return
        remainder = bytes(self._buffer)
        self._buffer.clear()
        for part in remainder.split(b"\n"):
            self._emit_completed_line(part)

    # -- internals -----------------------------------------------------------

    def _normalize_raw(self, *, force: bool) -> None:
        """Move complete bytes into the line buffer, repaints turned into `\\r`."""
        if not self._raw:
            return
        safe_end = len(self._raw)
        if not force:
            escape_start = self._raw.rfind(b"\x1b")
            if escape_start >= 0 and not _escape_is_complete(bytes(self._raw[escape_start:])):
                safe_end = escape_start
        if safe_end <= 0:
            return
        data = bytes(self._raw[:safe_end])
        del self._raw[:safe_end]
        self._buffer.extend(REPAINT_PATTERN.sub(b"\r", data))

    def _decode(self, data: bytes) -> str:
        return decode_process_bytes(data).rstrip("\r\n").rstrip()

    def _emit_completed_line(self, line: bytes) -> None:
        for segment in reversed(line.split(b"\r")):
            text = self._decode(segment)
            if self._is_noise(text):
                continue
            self._emit_text(text, False)
            self._last_progress_text = ""
            return

    def _emit_progress_frame(self, frames: list[bytes]) -> None:
        now = time.monotonic()
        if self._progress_interval and now - self._last_progress_at < self._progress_interval:
            return
        for frame in reversed(frames):
            text = self._decode(frame)
            if self._is_noise(text):
                continue
            plain = strip_ansi(text).strip()
            if plain == self._last_progress_text:
                return
            self._last_progress_at = now
            self._last_progress_text = plain
            self._emit_text(text, True)
            return

    def _is_noise(self, text: str) -> bool:
        if is_blank_output(text):
            return True
        if not self._keep_spinner_frames and is_spinner_only(strip_ansi(text)):
            return True
        return False

    def _emit_text(self, text: str, is_progress: bool) -> None:
        if not text:
            return
        self._emit(text, is_progress)

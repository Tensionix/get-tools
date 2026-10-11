"""The one live line of a download, the same from every service:

    [DOWNLOAD] Vivaldi 8.2.4133.84: 88% · 132.0 / 148.9 MB · 8.6 MB/s

what is being fetched, how far it is, how much of how much, how fast. The window paints the parts
of the line (how much is there, how much there will be, the speed); a console reads it as it is.

A service that downloads by itself knows the bytes and says them (`download_line`). A service that
watches another program download - WinGet repaints `  ██████▒▒▒  91.0 MB / 328 MB` - reads the
frame (`frame_line`) and measures the speed itself (`Speed`).
"""
from __future__ import annotations

import re
import time
from collections import deque

UNITS = ("B", "KB", "MB", "GB", "TB")
SEPARATOR = " · "

# `91.0 MB / 328 MB`, `1.2 GiB / 3.4 GiB`: what a console program writes beside its bar.
SIZE_PAIR = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*([KMGT]?i?B)\s*/\s*(\d+(?:[.,]\d+)?)\s*([KMGT]?i?B)(?![\w/])",
    re.IGNORECASE,
)
PERCENT = re.compile(r"(?<![\d.,])(\d{1,3})\s*%")


def _unit_of(size: float) -> str:
    value = float(size)
    for unit in UNITS:
        if value < 1024 or unit == UNITS[-1]:
            return unit
        value /= 1024
    return UNITS[-1]


def _in_unit(size: float, unit: str) -> str:
    if unit == "B":
        return str(int(size))
    return f"{float(size) / 1024 ** UNITS.index(unit):.1f}"


def human_size(size: float) -> str:
    unit = _unit_of(size)
    return f"{_in_unit(size, unit)} {unit}"


def download_line(label: str, done: float, total: float = 0, speed: float = 0) -> str:
    """The live line of a download. `total` and `speed` of zero are "not known" and are left out."""
    head = f"[DOWNLOAD] {label}:" if label else "[DOWNLOAD]"
    parts = []
    if total > 0:
        unit = _unit_of(total)
        head += f" {max(0, min(100, int(done / total * 100)))}%"
        parts.append(f"{_in_unit(done, unit)} / {_in_unit(total, unit)} {unit}")
    else:
        parts.append(human_size(done))
    if speed > 0:
        parts.append(f"{human_size(speed)}/s")
    if total > 0:
        return SEPARATOR.join([head, *parts])
    return head + " " + SEPARATOR.join(parts)


def _bytes(number: str, unit: str) -> float:
    letter = unit.upper().replace("I", "")
    power = UNITS.index(letter) if letter in UNITS else 0
    return float(number.replace(",", ".")) * 1024 ** power


def sizes_of(frame: str) -> tuple[float, float] | None:
    """How much is there and how much there will be, in bytes, if the frame says it."""
    match = SIZE_PAIR.search(frame)
    if match is None:
        return None
    try:
        return _bytes(match.group(1), match.group(2)), _bytes(match.group(3), match.group(4))
    except ValueError:
        return None


class Speed:
    """The speed of a download nobody tells the speed of: by what arrived over the last seconds."""

    def __init__(self, window: float = 5.0) -> None:
        self._window = float(window)
        self._marks: deque[tuple[float, float]] = deque()

    def rate(self, done: float, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        # Another file began, or the counter went back: what was measured is of the file before.
        if self._marks and done < self._marks[-1][1]:
            self._marks.clear()
        self._marks.append((now, float(done)))
        while len(self._marks) > 2 and now - self._marks[0][0] > self._window:
            self._marks.popleft()
        began, first = self._marks[0]
        if now - began < 0.5:
            return 0.0
        return max(0.0, (done - first) / (now - began))


def frame_line(label: str, frame: str, speed: Speed | None = None, now: float | None = None) -> str:
    """
    The live line for a frame another program repainted. A frame with sizes becomes the line of a
    download; a frame with a share alone - the label and the share; any other frame stays as it is.
    """
    plain = frame.strip()
    sizes = sizes_of(plain)
    if sizes is not None:
        done, total = sizes
        return download_line(label, done, total, speed.rate(done, now) if speed is not None else 0)
    share = PERCENT.search(plain)
    if share is not None and label:
        return f"{label}: {min(100, int(share.group(1)))}%"
    return plain

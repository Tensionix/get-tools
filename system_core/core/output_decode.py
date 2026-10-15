from __future__ import annotations

from dataclasses import dataclass
import locale
import re


MOJIBAKE_MARKERS = {
    "\ufffd",
    "Ð",
    "Ñ",
    "Ћ",
    "Ў",
    "Є",
    "Ґ",
    "¤",
    "©",
    "«",
    "»",
    "╨",
    "╤",
    "╬",
    "├",
    "┬",
}
UTF8_CYRILLIC_AS_CP866_MARKERS = {"╨", "╤"}

MOJIBAKE_SEQUENCES = (
    "Р°",
    "Р±",
    "РІ",
    "Рі",
    "Рґ",
    "Рµ",
    "Рё",
    "Р№",
    "Рє",
    "Р»",
    "Рј",
    "РЅ",
    "Рѕ",
    "Рї",
    "СЂ",
    "СЃ",
    "С‚",
    "Сѓ",
    "С„",
    "С…",
    "С†",
    "С‡",
    "С€",
    "С‰",
    "С‹",
    "СЊ",
    "СЌ",
    "СЋ",
    "СЏ",
    "С‘",
    "Р”",
    "Рџ",
    "РЎ",
    "Рњ",
    "Рќ",
    "Рћ",
    "Р­",
    "â€¦",
    "â€",
    "â„",
    "â€“",
    "â€”",
    "тА",
    "тВ",
    "тГ",
    "тД",
    "тЕ",
    "тЖ",
    "тЗ",
    "тИ",
    "тК",
    "тЛ",
    "тМ",
    "тН",
    "тО",
    "тП",
    "тР",
    "тС",
    "тТ",
    "тУ",
    "тФ",
    "тХ",
    "тЦ",
    "тЧ",
    "тШ",
    "тЩ",
    "тЪ",
    "тЫ",
    "тЬ",
    "тЭ",
    "тЮ",
    "тЯ",
)

COMMON_RUSSIAN = set("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя")


@dataclass(frozen=True)
class DecodeCandidate:
    text: str
    encoding: str
    score: int


def _candidate_encodings(data: bytes) -> list[str]:
    encodings: list[str] = []

    def add(encoding: str | None) -> None:
        if not encoding:
            return
        normalized = encoding.lower().replace("_", "-")
        if normalized not in encodings:
            encodings.append(normalized)

    add("utf-8")
    if _looks_utf16(data):
        add("utf-16")
        add("utf-16-le")
        add("utf-16-be")
    add("cp866")
    add(locale.getpreferredencoding(False))
    add(locale.getencoding() if hasattr(locale, "getencoding") else None)
    add("mbcs")
    add("cp1251")
    return encodings


def _looks_utf16(data: bytes) -> bool:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    if len(data) < 4:
        return False
    sample = data[: min(len(data), 200)]
    even_nuls = sample[0::2].count(0)
    odd_nuls = sample[1::2].count(0)
    return max(even_nuls, odd_nuls) >= max(2, len(sample) // 5)


def _score_text(text: str, encoding: str) -> int:
    score = 0
    if encoding == "utf-8":
        score += 20
    for char in text:
        code = ord(char)
        if char in COMMON_RUSSIAN:
            score += 8
        elif char.isascii() and (char.isprintable() or char in "\r\n\t"):
            score += 2
        elif _is_terminal_graphic(char):
            score += 30 if char not in MOJIBAKE_MARKERS else 0
        elif char.isprintable():
            score += 1
        if char in MOJIBAKE_MARKERS:
            score -= 60 if char in UTF8_CYRILLIC_AS_CP866_MARKERS else 20
        if code < 32 and char not in "\r\n\t":
            score -= 30
    score -= text.count("\x00") * 40
    score -= text.count("����") * 80
    for sequence in MOJIBAKE_SEQUENCES:
        score -= text.count(sequence) * 120
    return score


def _is_terminal_graphic(char: str) -> bool:
    code = ord(char)
    return (
        0x2500 <= code <= 0x257F
        or 0x2580 <= code <= 0x259F
        or 0x2800 <= code <= 0x28FF
    )


# UTF-16 as Windows programs write it into a pipe - sfc, `cmd /u` - little-endian and most often
# without the mark. A run of it is told by the high bytes of its characters: 0x00 under ASCII, 0x04
# under Cyrillic - bytes no 8-bit console text holds. Cut into lines by the byte of the line feed,
# such a stream came apart: the second byte of the feed began the next line, and every character
# after it was read across two of its neighbours ("@825B, <8@" for "Привет, мир", 15.10.2026).
_UTF16_MARK = b"\xff\xfe"
_SURE_UNIT = rb"(?:[\x09\x0a\x0d\x20-\x7e]\x00|[\x00-\xff]\x04)"
# A run begins with two such characters, or with the mark and one.
_UTF16_START = re.compile(_UTF16_MARK + _SURE_UNIT + rb"|" + _SURE_UNIT + rb"{2}")
# A run goes on while the high byte is 0x00-0x05 (Latin, Greek, Cyrillic); 0x20-0x27 (dashes, №,
# arrows, box drawing) only between two such - a byte pair of ASCII text after the run must end it.
_UTF16_GOES_ON = re.compile(rb"(?:[\x00-\xff][\x00-\x05]|[\x00-\xff][\x20-\x27](?=[\x00-\xff][\x00-\x05]))*")
_SURE_PAIR = re.compile(_SURE_UNIT)


class Utf16Runs:
    """Turns the runs of UTF-16 in a stream of bytes into UTF-8, piece by piece; other bytes pass as they came.

    A piece may end inside a character, or in the first bytes of a run that cannot be told yet:
    those few bytes wait for the next piece. A line feed or a carriage return at the very end
    never waits - a finished line, a frame of progress shows at once.
    """

    def __init__(self) -> None:
        self._pending = bytearray()
        self._wide = False

    def feed(self, chunk: bytes, *, final: bool = False) -> bytes:
        if not self._wide and not self._pending and 0 not in chunk and 4 not in chunk:
            # The common case: no run here and none beginning - only the very last bytes may wait.
            hold = len(chunk) if final else self._tail_hold(chunk, 0)
            if hold == len(chunk):
                return bytes(chunk)
            self._pending.extend(chunk[hold:])
            return bytes(chunk[:hold])
        self._pending.extend(chunk)
        data = bytes(self._pending)
        out = bytearray()
        at, end = 0, len(data)
        while at < end:
            if self._wide:
                goes_to = _UTF16_GOES_ON.match(data, at).end()
                if goes_to > at:
                    out += data[at:goes_to].decode("utf-16-le", errors="replace").replace("\ufeff", "").encode("utf-8")
                    at = goes_to
                left = end - at
                if left == 0 or (not final and (left == 1 or (0x20 <= data[at + 1] <= 0x27 and left < 4))):
                    # Half a character, or a dash whose neighbour has not come: it waits.
                    if final:
                        at = end
                    break
                self._wide = False
                continue
            found = _UTF16_START.search(data, at)
            if found is None:
                hold = end if final else self._tail_hold(data, at)
                out += data[at:hold].replace(b"\x00", b"")
                at = hold
                break
            out += data[at:found.start()].replace(b"\x00", b"")
            at = found.start() + (2 if data.startswith(_UTF16_MARK, found.start()) else 0)
            self._wide = True
        del self._pending[:at]
        if final:
            self._pending.clear()
            self._wide = False
        return bytes(out)

    @staticmethod
    def _tail_hold(data: bytes, start: int) -> int:
        """Where the bytes that may begin a run start in the last three of data[start:]; len(data) - none."""
        end = len(data)
        for at in range(max(start, end - 3), end):
            left = end - at
            pair = data[at:at + 2]
            if left == 1 and data[at] not in (0x0A, 0x0D):
                return at
            if left >= 2 and (pair == _UTF16_MARK or _SURE_PAIR.fullmatch(pair)):
                return at
        return end


def utf16_runs_to_utf8(data: bytes) -> bytes:
    """A whole output with its runs of UTF-16 turned into UTF-8."""
    return Utf16Runs().feed(data, final=True)


def decode_process_bytes(data: bytes) -> str:
    if not data:
        return ""
    data = utf16_runs_to_utf8(data)

    candidates: list[DecodeCandidate] = []
    for encoding in _candidate_encodings(data):
        try:
            text = data.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError):
            continue
        candidates.append(DecodeCandidate(text, encoding, _score_text(text, encoding)))

    if candidates:
        return max(candidates, key=lambda candidate: candidate.score).text
    return data.decode("utf-8", errors="replace")


def decode_process_output(data: bytes) -> str:
    return decode_process_bytes(data)

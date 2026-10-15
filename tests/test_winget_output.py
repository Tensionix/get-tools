"""WinGet console output parsing and progress throttling."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from system_core.core.output_decode import decode_process_bytes  # noqa: E402
from system_core.core.stream_output import StreamAssembler  # noqa: E402
from system_core.services.winget_service import (  # noqa: E402
    _parse_winget_package_lines,
    _parse_winget_table_columns,
    _progress_total_mib,
    _progress_trace_interval,
)


RU_UPGRADE_TABLE = [
    "Имя                          ИД                 Версия      Доступно    Источник",
    "-------------------------------------------------------------------------------",
    "7-Zip 26.01 (x64 edition)    7zip.7zip          26.01.00.0  26.02       winget",
    "Mozilla Firefox (x64 en-US)  Mozilla.Firefox    151.0.4     153.0       winget",
    "Подсистема Windows для Linux Microsoft.WSL      2.7.10.0    2.7.11      winget",
    "Доступны обновления: 3.",
]

EN_UPGRADE_TABLE = [
    "Name                         Id                 Version     Available   Source",
    "-------------------------------------------------------------------------------",
    "7-Zip 26.01 (x64 edition)    7zip.7zip          26.01.00.0  26.02       winget",
    "Mozilla Firefox (x64 en-US)  Mozilla.Firefox    151.0.4     153.0       winget",
    "3 upgrades available.",
]


class WinGetTableParsingTests(unittest.TestCase):
    def test_english_header_is_read(self) -> None:
        packages = _parse_winget_package_lines(EN_UPGRADE_TABLE, strict_package_ids=True)
        self.assertEqual(
            [item["value"] for item in packages],
            ["7zip.7zip", "Mozilla.Firefox"],
        )
        self.assertEqual(packages[1]["version"], "151.0.4")
        self.assertEqual(packages[1]["available"], "153.0")

    def test_localized_header_is_read(self) -> None:
        """A translated header used to drop every row and report `no updates`."""
        packages = _parse_winget_package_lines(RU_UPGRADE_TABLE, strict_package_ids=True)
        self.assertEqual(
            [item["value"] for item in packages],
            ["7zip.7zip", "Mozilla.Firefox", "Microsoft.WSL"],
        )
        self.assertEqual(packages[0]["available"], "26.02")
        self.assertEqual(packages[2]["version"], "2.7.10.0")

    def test_localized_columns_match_english_layout(self) -> None:
        self.assertEqual(
            _parse_winget_table_columns(RU_UPGRADE_TABLE),
            _parse_winget_table_columns(EN_UPGRADE_TABLE),
        )

    def test_header_row_is_not_a_package(self) -> None:
        values = {item["value"] for item in _parse_winget_package_lines(RU_UPGRADE_TABLE)}
        self.assertNotIn("ИД", values)


class ProgressThrottleTests(unittest.TestCase):
    def test_total_size_is_read_from_a_frame(self) -> None:
        self.assertAlmostEqual(_progress_total_mib("  91.0 MB / 328 MB"), 328.0)
        self.assertAlmostEqual(_progress_total_mib("1.5 GiB / 2.0 GiB"), 2048.0)
        self.assertAlmostEqual(_progress_total_mib("8,5 MB / 9,7 MB"), 9.7)
        self.assertIsNone(_progress_total_mib("Installer hash verified"))

    def test_interval_follows_the_download_size(self) -> None:
        self.assertEqual(_progress_trace_interval(328.0), 10.0)
        self.assertEqual(_progress_trace_interval(85.5), 5.0)
        self.assertEqual(_progress_trace_interval(20.0), 1.0)
        self.assertEqual(_progress_trace_interval(4.0), 1.0)
        self.assertEqual(_progress_trace_interval(None), 1.0)


class StreamAssemblerTests(unittest.TestCase):
    def _collect(self, chunks: list[bytes]) -> tuple[list[str], list[str]]:
        lines: list[str] = []
        frames: list[str] = []

        def emit(text: str, is_progress: bool) -> None:
            (frames if is_progress else lines).append(text)

        assembler = StreamAssembler(emit, progress_interval=0.0)
        for chunk in chunks:
            assembler.feed(chunk)
        assembler.flush()
        return lines, frames

    def test_repainted_frames_are_progress_not_log_rows(self) -> None:
        chunks = [
            "Скачивание https://example/pkg.msi\n".encode(),
            "  1.0 MB / 85.5 MB\r".encode(),
            "  14.0 MB / 85.5 MB\r".encode(),
            "  62.0 MB / 85.5 MB\r".encode(),
            "  85.5 MB / 85.5 MB\n".encode(),
            "Хэш установщика успешно проверен\n".encode(),
        ]
        lines, frames = self._collect(chunks)
        self.assertEqual(len(frames), 3)
        self.assertIn("85.5 MB / 85.5 MB", lines[1])
        self.assertEqual(len(lines), 3)

    def test_cursor_repaint_escapes_are_frame_boundaries(self) -> None:
        """WinGet repaints with `ESC[H`, not only with a carriage return."""
        lines, frames = self._collect(
            [b"\x1b[H  10 MB / 20 MB", b"\x1b[H  20 MB / 20 MB", b"\x1b[H\n"]
        )
        self.assertEqual(len(frames), 1)
        self.assertIn("10 MB / 20 MB", frames[0])
        self.assertEqual(len(lines), 1)
        self.assertIn("20 MB / 20 MB", lines[0])

    def test_a_frame_superseded_inside_one_chunk_is_not_repeated(self) -> None:
        lines, frames = self._collect([b"\x1b[H  10 MB / 20 MB\x1b[H  20 MB / 20 MB\n"])
        self.assertEqual(frames, [])
        self.assertEqual(len(lines), 1)
        self.assertIn("20 MB / 20 MB", lines[0])


RU_LINES = ["Привет, мир: проверка 1", "Ёлки-палки, съешь ещё этих булок", "Строка №3 - конец"]


class EncodingTests(unittest.TestCase):
    """The terminal reads what a command writes in any of the encodings a Windows console meets."""

    @staticmethod
    def _lines(data: bytes, piece: int) -> list[str]:
        lines: list[str] = []
        assembler = StreamAssembler(lambda text, is_progress: lines.append(text), progress_interval=0.0)
        for start in range(0, len(data), piece):
            assembler.feed(data[start:start + piece])
        assembler.flush()
        return lines

    def test_every_encoding_in_pieces_of_every_size(self) -> None:
        text = "\r\n".join(RU_LINES) + "\r\n"
        for encoding in ("utf-8", "cp866", "cp1251", "utf-16-le", "utf-16"):
            for piece in (1, 2, 3, 7, 4096):
                with self.subTest(encoding=encoding, piece=piece):
                    self.assertEqual(self._lines(text.encode(encoding), piece), RU_LINES)

    def test_utf16_after_a_line_of_odd_length(self) -> None:
        """`echo one & sfc /?`: UTF-8 of cmd, then UTF-16 of sfc, then UTF-8 again, in one stream."""
        data = "one\r\n".encode() + "\r\r\nДля использования программы sfc\r\n".encode("utf-16-le") + "Готово\r\n".encode()
        for piece in (1, 5, 6, 4096):
            with self.subTest(piece=piece):
                self.assertEqual(self._lines(data, piece), ["one", "Для использования программы sfc", "Готово"])

    def test_utf16_keeps_dashes_and_the_number_sign(self) -> None:
        line = "Том «Система» — раздел №2 → готов"
        self.assertEqual(self._lines((line + "\r\n").encode("utf-16-le"), 3), [line])

    def test_utf16_progress_frames_are_frames(self) -> None:
        frames: list[str] = []
        lines: list[str] = []
        assembler = StreamAssembler(lambda text, is_progress: (frames if is_progress else lines).append(text), progress_interval=0.0)
        for chunk in ("Проверка 10%\r", "Проверка 60%\r", "Проверка 100% завершена.\r\n"):
            assembler.feed(chunk.encode("utf-16-le"))
        assembler.flush()
        self.assertEqual(frames, ["Проверка 10%", "Проверка 60%"])
        self.assertEqual(lines, ["Проверка 100% завершена."])

    def test_a_finished_line_shows_at_once(self) -> None:
        """Nothing waits for the next piece when the piece ends with a line feed."""
        for encoding in ("utf-8", "cp866", "utf-16-le"):
            with self.subTest(encoding=encoding):
                lines: list[str] = []
                assembler = StreamAssembler(lambda text, is_progress: lines.append(text), progress_interval=0.0)
                assembler.feed("Скачивание началось\r\n".encode(encoding))
                self.assertEqual(lines, ["Скачивание началось"])

    def test_a_whole_output_in_utf16_is_read(self) -> None:
        self.assertEqual(decode_process_bytes("Привет, мир\r\n".encode("utf-16-le")), "Привет, мир\r\n")
        self.assertEqual(decode_process_bytes("Привет, мир".encode("utf-16")), "Привет, мир")
        self.assertEqual(decode_process_bytes("Привет, мир".encode("cp866")), "Привет, мир")


if __name__ == "__main__":
    unittest.main()

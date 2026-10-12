"""Programs taken as files: the shelves of config\\programs.yaml, the list of a page, where a file comes from.

    runtime\\python.exe tests\\test_programs.py

Started as a file, like tests\\test_cli_door.py. Nothing here asks GitHub, WinGet or a maker's site:
the releases, the packages and the downloads are made up. The file of the program itself is read
as it is - every program in it must name its files in a way the provider knows.
"""
from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import yaml  # noqa: E402

from system_core.core.jobs import JobContext  # noqa: E402
from system_core.core.manifest import Operation  # noqa: E402
from system_core.services import vendor_service  # noqa: E402
from system_core.vendors import programs  # noqa: E402
from system_core.vendors.programs import ProgramsProvider  # noqa: E402
from test_intelchipset import zipped  # noqa: E402
from test_vendors import RecordingContext  # noqa: E402

SHELVES = r"""
shelves:
  carrier:
    blocks:
      - title: "Installation media"
        title_ru: "Установочный носитель"
        programs:
          - id: mct
            name: "Windows 11 Media Creation Tool"
            installer: {source: url, url: "https://go.microsoft.com/fwlink/?linkid=2156295", name: "MediaCreationTool.exe"}
          - id: rufus
            name: "Rufus"
            installer: {source: github, repo: "pbatard/rufus", asset: '^rufus-[\d.]+\.exe$'}
            portable: {source: github, repo: "pbatard/rufus", asset: '^rufus-[\d.]+p\.exe$'}
          - id: ventoy
            name: "Ventoy"
            portable: {source: github, repo: "ventoy/Ventoy", asset: '-windows\.zip$'}
  games:
    blocks:
      - title: "Launchers"
        title_ru: "Лаунчеры"
        programs:
          - id: steam
            name: "Steam"
            installer: {source: url, url: "https://cdn.cloudflare.steamstatic.com/client/installer/SteamSetup.exe"}
          - id: battlenet
            name: "Battle.net"
            installer: {source: url, url: "https://downloader.battle.net/download/getInstallerForGame?os=win&version=Live", name: "Battle.net-Setup.exe"}
      - title: "Minecraft"
        programs:
          - id: modrinth
            name: "Modrinth App"
            installer: {source: winget, id: "Modrinth.ModrinthApp"}
          - id: autoruns
            name: "Sysinternals Autoruns"
            portable: {source: winget, id: "Microsoft.Sysinternals.Autoruns", type: zip}
"""

RELEASES = {
    "pbatard/rufus": ("v4.15", [("rufus-4.15.exe", "https://github.com/pbatard/rufus/releases/download/v4.15/rufus-4.15.exe"),
                               ("rufus-4.15_arm64.exe", "https://github.com/pbatard/rufus/releases/download/v4.15/rufus-4.15_arm64.exe"),
                               ("rufus-4.15p.exe", "https://github.com/pbatard/rufus/releases/download/v4.15/rufus-4.15p.exe")]),
    "ventoy/Ventoy": ("v1.1.18", [("ventoy-1.1.18-linux.tar.gz", "https://github.com/ventoy/Ventoy/releases/download/v1.1.18/ventoy-1.1.18-linux.tar.gz"),
                                 ("ventoy-1.1.18-windows.zip", "https://github.com/ventoy/Ventoy/releases/download/v1.1.18/ventoy-1.1.18-windows.zip")]),
}

# The files of the release v0.7.0 of storytold/photocraft, as GitHub listed them on 12.10.2026.
PHOTOCRAFT_0_7_0 = """
photocraft-0.7.0-freebsd-x86_64.tar.gz photocraft-0.7.0-linux-aarch64.AppImage photocraft-0.7.0-linux-aarch64.AppImage.zsync
photocraft-0.7.0-linux-aarch64.deb photocraft-0.7.0-linux-aarch64.flatpak photocraft-0.7.0-linux-aarch64.rpm
photocraft-0.7.0-linux-aarch64.tar.gz photocraft-0.7.0-linux-riscv64.tar.gz photocraft-0.7.0-linux-x86_64.AppImage
photocraft-0.7.0-linux-x86_64.AppImage.zsync photocraft-0.7.0-linux-x86_64.deb photocraft-0.7.0-linux-x86_64.flatpak
photocraft-0.7.0-linux-x86_64.rpm photocraft-0.7.0-linux-x86_64.tar.gz photocraft-0.7.0-macos-universal.dmg
photocraft-0.7.0-windows-arm64-portable.zip photocraft-0.7.0-windows-arm64.msi photocraft-0.7.0-windows-x64-portable.zip
photocraft-0.7.0-windows-x64.msi photocraft-0.7.0-windows-x86-portable.zip photocraft-0.7.0-windows-x86.msi
photocraft-cli-0.7.0-macos-universal.zip photocraft-web-0.7.0.zip SHA256SUMS.txt
"""

# The files of the release 1.5.0 of rustdesk/rustdesk, as GitHub listed them on 12.10.2026.
RUSTDESK_1_5_0 = """
rustdesk-1.5.0-0-x86_64.pkg.tar.zst rustdesk-1.5.0-0.x86_64-suse.rpm rustdesk-1.5.0-0.x86_64.rpm rustdesk-1.5.0-aarch64.exe
rustdesk-1.5.0-aarch64.msi rustdesk-1.5.0-armv7-sciter.deb rustdesk-1.5.0-x86-sciter.exe rustdesk-1.5.0-x86_64-sciter.deb
rustdesk-1.5.0-x86_64-sciter.flatpak rustdesk-1.5.0-x86_64-signed.apk rustdesk-1.5.0-x86_64.AppImage rustdesk-1.5.0-x86_64.deb
rustdesk-1.5.0-x86_64.dmg rustdesk-1.5.0-x86_64.exe rustdesk-1.5.0-x86_64.flatpak rustdesk-1.5.0-x86_64.msi
rustdesk-unattended-wayland-1.5.0-x86_64.deb
"""

SHOWN = """Found Modrinth App [Modrinth.ModrinthApp]
Version: 0.21.9
Homepage: https://modrinth.com/app
License Url: https://github.com/modrinth/code/blob/main/LICENSE
Installer:
  Installer Type: nullsoft
  Installer Url: https://launcher-files.modrinth.com/versions/0.21.9/windows/Modrinth%20App_0.21.9_x64-setup.exe
  Installer SHA256: abc
"""


def provider(text: str = SHELVES, winget=None, releases=None, texts=None) -> tuple[ProgramsProvider, list[tuple[str, str, str]]]:
    path = Path(tempfile.mkdtemp()) / "programs.yaml"
    path.write_text(text, encoding="utf-8")
    asked: list[tuple[str, str, str]] = []

    def fake_winget(context: JobContext, package_id: str, installer_type: str, folder: Path) -> Path:
        asked.append((package_id, installer_type, str(folder)))
        target = folder / (package_id.rsplit(".", 1)[-1] + (".zip" if installer_type == "zip" else ".exe"))
        target.write_bytes(zipped(**{"Autoruns__Autoruns64.exe": b"exe", "Autoruns__Eula.txt": b"txt"}) if installer_type == "zip" else b"installer")
        return target

    return ProgramsProvider(path, releases=releases or (lambda repo: RELEASES[repo]), winget=winget or fake_winget,
                            show=lambda package: SHOWN, text=texts or (lambda url: (_ for _ in ()).throw(AssertionError(f"asked for {url}")))), asked


class TheFile(unittest.TestCase):
    """config\\programs.yaml as the program ships it."""

    def setUp(self) -> None:
        self.path = ROOT / "config" / "programs.yaml"
        self.shelves = programs.read_shelves(yaml.safe_load(self.path.read_text(encoding="utf-8")))

    def test_every_program_names_its_files_in_a_way_the_provider_knows(self) -> None:
        self.assertEqual(sorted(self.shelves), ["carrier", "craft", "games", "tests", "utilities"])
        listed = [program for shelf in self.shelves.values() for program in shelf]
        self.assertEqual(len(listed), 49)
        self.assertEqual(len({program.key for program in listed}), len(listed))
        for program in listed:
            self.assertTrue(program.block[0] and program.block[1], program.key)
            for kind in (programs.INSTALLER, programs.PORTABLE):
                source = program.source(kind)
                if source is None:
                    continue
                self.assertIn(source["source"], programs.WAYS, program.key)
                if source["source"] == "url":
                    self.assertTrue(source["url"].startswith("https://"), program.key)
                    # A file is named by its address or by the file: an address that ends in a query names nothing.
                    self.assertTrue(source.get("name") or re.search(r"/[^/?]+\.(exe|msi|zip|7z)$", source["url"]), program.key)
                if source["source"] == "winget":
                    self.assertRegex(source["id"], r"^[\w+-]+(\.[\w+-]+)+$", program.key)

    def test_the_pages_of_the_window_are_the_shelves_of_the_file(self) -> None:
        layout = yaml.safe_load((ROOT / "config" / "window_layout.yaml").read_text(encoding="utf-8"))
        fixed = [page["fixed"]["prg_kind"] for section in layout["sections"] for page in section["pages"] if (page.get("fixed") or {}).get("vendor") == "programs"]
        self.assertEqual(sorted(fixed), sorted(self.shelves))
        manifest = (ROOT / "config" / "tool_manifest.yaml").read_text(encoding="utf-8")
        for shelf in self.shelves:
            self.assertIn(f'- value: "{shelf}"', manifest)

    def test_rustdesk_names_its_two_x86_64_files_and_passes_the_neighbours_by(self) -> None:
        """The file names of the release 1.5.0 of rustdesk/rustdesk, as GitHub listed them on 12.10.2026."""
        utilities = self.shelves["utilities"]
        rustdesk = next(program for program in utilities if program.key == "rustdesk")
        self.assertEqual(rustdesk.block, ("Remote access", "Удалённый доступ"))
        self.assertTrue(rustdesk.about[0] and rustdesk.about[1])
        # The block stands after Security and before the next one.
        blocks: list[str] = []
        for program in utilities:
            if not blocks or blocks[-1] != program.block[0]:
                blocks.append(program.block[0])
        self.assertEqual(blocks[blocks.index("Security") + 1], "Remote access")
        self.assertEqual(len(utilities), 15)
        names = RUSTDESK_1_5_0.split()
        self.assertEqual(len(names), 17)

        def named(pattern: str) -> list[str]:
            return [name for name in names if re.search(pattern, name, re.IGNORECASE)]

        self.assertEqual(named(rustdesk.installer["asset"]), ["rustdesk-1.5.0-x86_64.msi"])  # type: ignore[index]
        self.assertEqual(named(rustdesk.portable["asset"]), ["rustdesk-1.5.0-x86_64.exe"])  # type: ignore[index]
        # Its release has no checksum file: none is asked for.
        self.assertNotIn("sums", rustdesk.installer)  # type: ignore[arg-type]
        self.assertNotIn("sums", rustdesk.portable)  # type: ignore[arg-type]

    def test_the_shelf_craft_is_fourteen_programs_in_six_blocks(self) -> None:
        craft = self.shelves["craft"]
        self.assertEqual([program.key for program in craft],
                         ["craft-launcher", "photocraft", "lightcraft", "vectorcraft", "designcraft", "filmcraft", "effectcraft", "soundcraft",
                          "pdfcraft", "wordcraft", "gridcraft", "deckcraft", "cadcraft", "artcraft"])
        blocks: list[tuple[str, str]] = []
        for program in craft:
            if not blocks or blocks[-1] != program.block:
                blocks.append(program.block)
        self.assertEqual(blocks, [("Launcher", "Запускатель"), ("Photo and graphics", "Фото и графика"), ("Video and sound", "Видео и звук"),
                                  ("Documents", "Документы"), ("Drafting", "Черчение"), ("Generation", "Генерация")])
        for program in craft:
            # Every card says what the program is, in both languages.
            self.assertTrue(program.about[0] and program.about[1] and program.about[0] != program.about[1], program.key)
            self.assertEqual(program.installer["repo"], f"storytold/{program.key}", program.key)  # type: ignore[index]
            if program.key == "artcraft":
                # Only an installer, and its release has no checksum file: nothing to check against.
                self.assertIsNone(program.portable)
                self.assertNotIn("sums", program.installer)  # type: ignore[arg-type]
                # A file that is taken unchecked says so on its card, in both languages
                # (found by the download of 12.10.2026: the installer is not signed either).
                self.assertIn("no checksum file", program.about[0])
                self.assertIn("no digital signature", program.about[0])
                self.assertIn("нет файла контрольных сумм", program.about[1])
                self.assertIn("без цифровой подписи", program.about[1])
                continue
            for kind in (programs.INSTALLER, programs.PORTABLE):
                self.assertEqual(program.source(kind)["sums"], r"^SHA256SUMS\.txt$", (program.key, kind))  # type: ignore[index]
        early = [program.key for program in craft if program.about[0].endswith("An early version.") and program.about[1].endswith("Ранняя версия.")]
        self.assertEqual(len(early), 12)
        self.assertNotIn("craft-launcher", early)
        self.assertNotIn("artcraft", early)

    def test_the_patterns_of_craft_name_the_x64_files_of_a_real_release_and_pass_the_rest_by(self) -> None:
        """The file names of the release 0.7.0 of PhotoCraft, of the launcher 0.3.2 and of ArtCraft 0.41.0, as GitHub listed them on 12.10.2026."""
        craft = {program.key: program for program in self.shelves["craft"]}

        def named(pattern: str, names: list[str]) -> list[str]:
            return [name for name in names if re.search(pattern, name, re.IGNORECASE)]

        photocraft = PHOTOCRAFT_0_7_0.split()
        self.assertEqual(len(photocraft), 24)
        self.assertEqual(named(craft["photocraft"].installer["asset"], photocraft), ["photocraft-0.7.0-windows-x64.msi"])  # type: ignore[index]
        self.assertEqual(named(craft["photocraft"].portable["asset"], photocraft), ["photocraft-0.7.0-windows-x64-portable.zip"])  # type: ignore[index]
        self.assertEqual(named(craft["photocraft"].installer["sums"], photocraft), ["SHA256SUMS.txt"])  # type: ignore[index]
        # The twelve apps name their files alike: the pattern of each is the one of PhotoCraft with its own name.
        for key in ("lightcraft", "vectorcraft", "designcraft", "filmcraft", "effectcraft", "soundcraft", "pdfcraft", "wordcraft", "gridcraft",
                    "deckcraft", "cadcraft"):
            own = [name.replace("photocraft", key) for name in photocraft]
            for kind, tail in ((programs.INSTALLER, ".msi"), (programs.PORTABLE, "-portable.zip")):
                self.assertEqual(craft[key].source(kind)["asset"], craft["photocraft"].source(kind)["asset"].replace("photocraft", key), key)  # type: ignore[index]
                self.assertEqual(named(craft[key].source(kind)["asset"], own), [f"{key}-0.7.0-windows-x64{tail}"], key)  # type: ignore[index]
        # The repository of the launcher is craft-launcher, its files are called artcraft-launcher.
        launcher = [f"artcraft-launcher-0.3.2-windows-{arch}{tail}" for arch in ("arm64", "x64", "x86") for tail in ("-portable.zip", ".msi")] + ["SHA256SUMS.txt"]
        self.assertEqual(named(craft["craft-launcher"].installer["asset"], launcher), ["artcraft-launcher-0.3.2-windows-x64.msi"])  # type: ignore[index]
        self.assertEqual(named(craft["craft-launcher"].portable["asset"], launcher), ["artcraft-launcher-0.3.2-windows-x64-portable.zip"])  # type: ignore[index]
        artcraft = ["ArtCraft_0.41.0_universal.dmg", "ArtCraft_0.41.0_x64-setup.exe", "ArtCraft_0.41.0_x64_en-US.msi", "ArtCraft_universal.app.tar.gz"]
        self.assertEqual(named(craft["artcraft"].installer["asset"], artcraft), ["ArtCraft_0.41.0_x64-setup.exe"])  # type: ignore[index]
        # Nothing of arm64, x86 or the web build is ever named, by any pattern of the shelf.
        every = photocraft + launcher + artcraft
        for program in craft.values():
            for kind in (programs.INSTALLER, programs.PORTABLE):
                source = program.source(kind)
                if source is not None:
                    for name in named(source["asset"], every):
                        self.assertNotRegex(name, r"arm64|x86(?!_64)|-web-|-cli-|linux|macos|freebsd", (program.key, kind))

    def test_the_page_craft_lists_fourteen_cards_and_thirteen_portable_ones(self) -> None:
        made = ProgramsProvider(self.path)
        options = vendor_service.program_options(made, "craft", False)
        cards = [option for option in options if option["value"]]
        self.assertEqual([len(cards), sum(1 for option in options if option.get("header"))], [14, 6])
        self.assertEqual(sum(1 for card in cards if card.get("portable_id")), 13)
        self.assertTrue(all(card.get("tooltip") and card.get("tooltip_ru") for card in cards))
        portable = [option for option in vendor_service.program_options(made, "craft", True) if option["value"]]
        self.assertEqual(len(portable), 13)
        self.assertNotIn("prg:artcraft:installer", [card["value"] for card in portable])
        self.assertTrue(all(card["value"].endswith(":portable") for card in portable))

    def test_a_program_that_names_nothing_or_names_it_oddly_is_refused_in_words(self) -> None:
        def shelves(program: str) -> dict:
            return yaml.safe_load("shelves:\n  one:\n    blocks:\n      - title: B\n        programs:\n          - " + program)

        for program, word in (("{id: a, name: A}", "neither an installer nor a portable"),
                              ("{id: a, name: A, installer: {source: ftp, url: x}}", "a source is one of"),
                              ("{id: a, name: A, installer: {source: github, repo: o/r}}", "a source is one of"),
                              ("{id: 'a:b', name: A, installer: {source: url, url: 'https://x/y.exe'}}", "has a colon"),
                              ("{name: A, installer: {source: url, url: 'https://x/y.exe'}}", "no id or no name")):
            with self.assertRaisesRegex(ValueError, word, msg=program):
                programs.read_shelves(shelves(program))
        with self.assertRaises(re.error):
            programs.read_shelves(shelves("{id: a, name: A, installer: {source: github, repo: o/r, asset: '(['}}"))


class TheList(unittest.TestCase):
    def test_a_shelf_is_its_blocks_and_a_card_says_which_files_a_program_has(self) -> None:
        made, _asked = provider()
        saved = ProgramsProvider._instance
        ProgramsProvider._instance = made
        try:
            options = vendor_service.vendor_build_options(values={"vendor": "programs", "prg_kind": "carrier"})
            portable = vendor_service.vendor_build_options(values={"vendor": "programs", "prg_kind": "carrier", "vendor_portable_only": True})
            games = vendor_service.vendor_build_options(values={"vendor": "programs", "prg_kind": "games"})
            none = vendor_service.vendor_build_options(values={"vendor": "programs", "prg_kind": "games", "vendor_portable_only": True})
            empty = vendor_service.vendor_build_options(values={"vendor": "programs", "prg_kind": "nothing"})
        finally:
            ProgramsProvider._instance = saved
        self.assertEqual([(option["value"], option["label"], option["label_ru"]) for option in options], [
            ("", "Installation media (3)", "Установочный носитель (3)"),
            ("prg:mct:installer", "Windows 11 Media Creation Tool - installer", "Windows 11 Media Creation Tool - установщик"),
            ("prg:rufus:installer", "Rufus - installer, portable", "Rufus - установщик, портативная"),
            # A program with a portable build alone: its tick is that build.
            ("prg:ventoy:portable", "Ventoy - portable", "Ventoy - портативная")])
        self.assertTrue(options[0]["header"])
        self.assertEqual((options[2]["portable_id"], options[2]["installer_id"]), ("prg:rufus:portable", "prg:rufus:installer"))
        self.assertEqual((options[3]["portable_id"], options[3]["installer_id"]), ("prg:ventoy:portable", ""))
        # The Portable switch: what has no portable build leaves, a tick is the portable build.
        self.assertEqual([(option["value"], option["label"]) for option in portable],
                         [("", "Installation media (2)"), ("prg:rufus:portable", "Rufus - portable"), ("prg:ventoy:portable", "Ventoy - portable")])
        # A block of the file without a Russian title keeps its own; a block left empty by the switch is not shown.
        self.assertEqual([option["label_ru"] for option in games if option.get("header")], ["Лаунчеры (2)", "Minecraft (2)"])
        self.assertEqual([option["label"] for option in none], ["Minecraft (1)", "Sysinternals Autoruns - portable"])
        self.assertEqual([option["value"] for option in empty], [""])

    def test_a_card_says_what_the_program_is_when_its_file_says_so(self) -> None:
        text = SHELVES.replace('''          - id: rufus
            name: "Rufus"
''', '''          - id: rufus
            name: "Rufus"
            about: "Writes a bootable stick."
            about_ru: "Пишет загрузочную флешку."
''').replace('''          - id: ventoy
            name: "Ventoy"
''', '''          - id: ventoy
            name: "Ventoy"
            about: "Many images on one stick."
''')
        self.assertNotEqual(text, SHELVES)
        made, _asked = provider(text)
        cards = {option["value"]: option for option in vendor_service.program_options(made, "carrier", False) if option["value"]}
        self.assertEqual((cards["prg:rufus:installer"]["tooltip"], cards["prg:rufus:installer"]["tooltip_ru"]),
                         ("Writes a bootable stick.", "Пишет загрузочную флешку."))
        # English alone serves both languages.
        self.assertEqual((cards["prg:ventoy:portable"]["tooltip"], cards["prg:ventoy:portable"]["tooltip_ru"]),
                         ("Many images on one stick.", "Many images on one stick."))
        # A program that says nothing of itself has no tooltip at all: the card is as it was.
        self.assertNotIn("tooltip", cards["prg:mct:installer"])
        self.assertNotIn("tooltip_ru", cards["prg:mct:installer"])
        # The card of the portable build, while the Portable switch is on, says the same.
        portable = {option["value"]: option for option in vendor_service.program_options(made, "carrier", True) if option["value"]}
        self.assertEqual(portable["prg:rufus:portable"]["tooltip_ru"], "Пишет загрузочную флешку.")
        # A heading is no program and tells nothing.
        self.assertTrue(all("tooltip" not in option for option in vendor_service.program_options(made, "carrier", False) if option.get("header")))

    def test_the_list_asks_nothing_of_the_network(self) -> None:
        made = ProgramsProvider(ROOT / "config" / "programs.yaml", releases=lambda repo: self.fail("a release was read"),
                                winget=lambda *args: self.fail("WinGet was called"), show=lambda package: self.fail("WinGet was asked"),
                                text=lambda url: self.fail("a checksum file was read"))
        self.assertEqual(made.products("Windows"), ["carrier", "utilities", "tests", "games", "craft"])
        self.assertEqual(sum(len(made.builds(shelf, "Windows")) for shelf in made.products("Windows")), 49)
        self.assertEqual(made.builds("carrier", "Mac OS X"), [])

    def test_the_file_is_read_again_when_it_changes(self) -> None:
        made, _asked = provider()
        self.assertEqual(len(made.builds("carrier", "Windows")), 3)
        path = made._path  # type: ignore[attr-defined]
        path.write_text(SHELVES.replace('          - id: ventoy\n            name: "Ventoy"\n            portable: {source: github, repo: "ventoy/Ventoy", asset: \'-windows\\.zip$\'}\n', ""), encoding="utf-8")
        import os

        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
        self.assertEqual(len(made.builds("carrier", "Windows")), 2)


class AFile(unittest.TestCase):
    def test_a_release_gives_the_first_file_its_pattern_finds(self) -> None:
        made, _asked = provider()
        self.assertEqual(made.release_file("pbatard/rufus", r"^rufus-[\d.]+\.exe$")[:1], ("rufus-4.15.exe",))
        self.assertEqual(made.release_file("pbatard/rufus", r"^RUFUS-[\d.]+p\.exe$")[0], "rufus-4.15p.exe")  # without regard to case
        with self.assertRaisesRegex(RuntimeError, r"no file matching \\\.msi\$; there are: rufus-4\.15\.exe, "):
            made.release_file("pbatard/rufus", r"\.msi$")

    def test_a_build_is_the_file_a_run_asks_for(self) -> None:
        made, _asked = provider()
        card = made.builds("carrier", "Windows")[1]
        self.assertEqual((card.download_id, card.portable_id, card.name, card.version), ("prg:rufus:installer", "prg:rufus:portable", "Rufus", "Rufus"))
        portable = made.build_by_id("prg:rufus:portable")
        self.assertEqual((portable.download_id, portable.name), ("prg:rufus:portable", "Rufus (portable)"))  # type: ignore[union-attr]
        for unknown in ("prg:rufus:source", "prg:nobody:installer", "prg:ventoy:installer", "prg:mct:portable", "icd:1.2.3", ""):
            self.assertIsNone(made.build_by_id(unknown), unknown)

    def test_where_a_file_comes_from_and_where_it_lands(self) -> None:
        made, asked = provider()
        rufus = made.sources(made.build_by_id("prg:rufus:portable"))[0]  # type: ignore[arg-type]
        self.assertEqual((rufus.host, rufus.name, rufus.folder), ("github.com", "", "Rufus/Portable"))
        self.assertEqual(rufus.link(), ("https://github.com/pbatard/rufus/releases/download/v4.15/rufus-4.15p.exe", ""))
        mct = made.sources(made.build_by_id("prg:mct:installer"))[0]  # type: ignore[arg-type]
        self.assertEqual((mct.host, mct.name, mct.folder), ("go.microsoft.com", "MediaCreationTool.exe", "Windows 11 Media Creation Tool"))
        steam = made.sources(made.build_by_id("prg:steam:installer"))[0]  # type: ignore[arg-type]
        self.assertEqual((steam.name, steam.link()[0].rsplit("/", 1)[-1]), ("", "SteamSetup.exe"))
        # A dot in a name is no extension of a folder.
        self.assertEqual(made.sources(made.build_by_id("prg:battlenet:installer"))[0].folder, "Battle.net")  # type: ignore[arg-type]
        modrinth = made.sources(made.build_by_id("prg:modrinth:installer"))[0]  # type: ignore[arg-type]
        self.assertEqual((modrinth.host, modrinth.folder), ("winget", "Modrinth App"))
        self.assertIsNotNone(modrinth.fetch)
        self.assertEqual(asked, [])
        self.assertEqual(made.sources(made.builds("carrier", "Windows")[0])[0].host, "go.microsoft.com")

    def test_the_link_of_a_file(self) -> None:
        made, _asked = provider()
        self.assertEqual(made.resolve_link(made.build_by_id("prg:ventoy:portable")),  # type: ignore[arg-type]
                         "https://github.com/ventoy/Ventoy/releases/download/v1.1.18/ventoy-1.1.18-windows.zip")
        self.assertEqual(made.resolve_link(made.build_by_id("prg:steam:installer")),  # type: ignore[arg-type]
                         "https://cdn.cloudflare.steamstatic.com/client/installer/SteamSetup.exe")
        # Of a WinGet package - the address WinGet itself names for the installer, not its licence or home page.
        self.assertEqual(made.resolve_link(made.build_by_id("prg:modrinth:installer")),  # type: ignore[arg-type]
                         "https://launcher-files.modrinth.com/versions/0.21.9/windows/Modrinth%20App_0.21.9_x64-setup.exe")


class Job(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = (ProgramsProvider._instance, vendor_service.download_with_resume)
        self.fetched: list[tuple[str, str]] = []
        self.files: dict[str, bytes] = {}

        def fake_download(context: JobContext, url: str, target: Path, label: str, **kwargs: object) -> Path:
            self.fetched.append((url, target.name))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(next(data for word, data in self.files.items() if word in url))
            return target

        vendor_service.download_with_resume = fake_download  # type: ignore[assignment]
        ProgramsProvider._instance, self.asked = provider()

    def tearDown(self) -> None:
        ProgramsProvider._instance, vendor_service.download_with_resume = self.saved  # type: ignore[assignment]

    def run_job(self, root: Path, *ids: str, **parameters: object) -> tuple[dict[str, object], RecordingContext]:
        context = RecordingContext(root)
        context.operation = Operation(id="vendor_download", title="t", description="", service="x:y", parameters={
            "vendor": "programs", "prg_kind": "carrier", "vendor_versions": list(ids), "output_path": str(root / "store"), **parameters})
        return vendor_service.download_vendor_builds(context), context

    def test_an_installer_lands_in_the_folder_of_its_program_under_the_name_of_its_address(self) -> None:
        self.files = {"rufus-4.15.exe": b"rufus", "fwlink": b"mct", "Battle": b"bnet", "getInstallerForGame": b"bnet"}
        with tempfile.TemporaryDirectory() as temp:
            result, context = self.run_job(Path(temp), "prg:rufus:installer", "prg:mct:installer", "prg:battlenet:installer")
            store = Path(temp) / "store" / "Vendors" / "Programs"
            self.assertEqual((store / "Rufus" / "rufus-4.15.exe").read_bytes(), b"rufus")
            # An address that names no file: the name its program gives.
            self.assertEqual((store / "Windows 11 Media Creation Tool" / "MediaCreationTool.exe").read_bytes(), b"mct")
            self.assertEqual((store / "Battle.net" / "Battle.net-Setup.exe").read_bytes(), b"bnet")
            self.assertEqual([entry["source"] for entry in result["results"]], ["github.com", "go.microsoft.com", "downloader.battle.net"])  # type: ignore[union-attr]
            self.assertIn("[SOURCE] github.com", context.lines)

    def test_a_portable_build_unpacks_into_portable_inside_the_folder_of_its_program(self) -> None:
        self.files = {"ventoy-1.1.18-windows.zip": zipped(**{"ventoy-1.1.18__Ventoy2Disk.exe": b"exe", "ventoy-1.1.18__boot__grub.cfg": b"cfg"}),
                      "rufus-4.15p.exe": b"portable rufus"}
        with tempfile.TemporaryDirectory() as temp:
            result, _context = self.run_job(Path(temp), "prg:ventoy:portable", "prg:rufus:portable")
            store = Path(temp) / "store" / "Vendors" / "Programs"
            # The lone root folder of the zip is dropped, the zip goes.
            self.assertEqual(sorted(str(item.relative_to(store / "Ventoy")) for item in (store / "Ventoy").rglob("*") if item.is_file()),
                             [str(Path("Portable") / "Ventoy2Disk.exe"), str(Path("Portable") / "boot" / "grub.cfg")])
            # One file that needs no unpacking stays as it is.
            self.assertEqual((store / "Rufus" / "Portable" / "rufus-4.15p.exe").read_bytes(), b"portable rufus")
            self.assertEqual([entry.get("extracted") for entry in result["results"]], [2, None])  # type: ignore[union-attr]

    def test_a_winget_package_is_brought_by_winget_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, context = self.run_job(Path(temp), "prg:modrinth:installer", "prg:autoruns:portable", prg_kind="games")
            store = Path(temp) / "store" / "Vendors" / "Programs"
            self.assertEqual([(package, kind) for package, kind, _folder in self.asked], [("Modrinth.ModrinthApp", ""), ("Microsoft.Sysinternals.Autoruns", "zip")])
            self.assertEqual((store / "Modrinth App" / "ModrinthApp.exe").read_bytes(), b"installer")
            # The zip WinGet brought is unpacked as any portable build is.
            self.assertEqual((store / "Sysinternals Autoruns" / "Portable" / "Autoruns64.exe").read_bytes(), b"exe")
            self.assertEqual(self.fetched, [])
            self.assertEqual([entry["source"] for entry in result["results"]], ["winget", "winget"])  # type: ignore[union-attr]
            self.assertEqual(len([line for line in context.lines if line == "[SOURCE] winget"]), 2)

    def test_a_release_without_the_file_fails_the_run_in_words(self) -> None:
        made, _asked = provider(SHELVES.replace(r"'^rufus-[\d.]+\.exe$'", r"'^rufus-[\d.]+\.msi$'"))
        ProgramsProvider._instance = made
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, "The latest release of pbatard/rufus \\(v4.15\\) has no file matching"):
                self.run_job(Path(temp), "prg:rufus:installer")


CRAFT = r"""
shelves:
  craft:
    blocks:
      - title: "Documents"
        title_ru: "Документы"
        programs:
          - id: pdfcraft
            name: "PdfCraft"
            installer: {source: github, repo: "storytold/pdfcraft", asset: '^pdfcraft-[\d.]+-windows-x64\.msi$', sums: '^SHA256SUMS\.txt$'}
            portable: {source: github, repo: "storytold/pdfcraft", asset: '^pdfcraft-[\d.]+-windows-x64-portable\.zip$', sums: '^SHA256SUMS\.txt$'}
"""
CRAFT_PAGE = "https://github.com/storytold/pdfcraft/releases/download/v0.7.0/"
CRAFT_MSI, CRAFT_ZIP = "pdfcraft-0.7.0-windows-x64.msi", "pdfcraft-0.7.0-windows-x64-portable.zip"


class Sums(unittest.TestCase):
    """A file of a release against the checksum file of that release."""

    def setUp(self) -> None:
        self.saved = (ProgramsProvider._instance, vendor_service.download_with_resume)
        self.files: dict[str, bytes] = {CRAFT_MSI: b"the installer of pdfcraft",
                                        CRAFT_ZIP: zipped(**{"pdfcraft__pdfcraft.exe": b"exe", "pdfcraft__data__app.dat": b"dat"})}
        self.assets = [CRAFT_MSI, "pdfcraft-0.7.0-windows-arm64.msi", CRAFT_ZIP, "SHA256SUMS.txt"]
        self.sums: str | None = None  # the checksum file as the release carries it; None - the true sums of the files
        self.read: list[str] = []
        self.releases = 0

        def fake_download(context: JobContext, url: str, target: Path, label: str, **kwargs: object) -> Path:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.files[url.rsplit("/", 1)[-1]])
            return target

        def release(repo: str) -> tuple[str, list[tuple[str, str]]]:
            self.releases += 1
            return "v0.7.0", [(name, CRAFT_PAGE + name) for name in self.assets]

        def text(url: str) -> str:
            self.read.append(url)
            if self.sums is not None:
                return self.sums
            return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in self.files.items())

        vendor_service.download_with_resume = fake_download  # type: ignore[assignment]
        ProgramsProvider._instance, _asked = provider(CRAFT, releases=release, texts=text)

    def tearDown(self) -> None:
        ProgramsProvider._instance, vendor_service.download_with_resume = self.saved  # type: ignore[assignment]

    def run_job(self, root: Path, *ids: str) -> tuple[dict[str, object], RecordingContext]:
        context = RecordingContext(root)
        context.operation = Operation(id="vendor_download", title="t", description="", service="x:y", parameters={
            "vendor": "programs", "prg_kind": "craft", "vendor_versions": list(ids), "output_path": str(root / "store")})
        self.context = context
        return vendor_service.download_vendor_builds(context), context

    def test_a_file_whose_sum_matches_stays_and_the_log_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result, context = self.run_job(Path(temp), "prg:pdfcraft:installer")
            folder = Path(temp) / "store" / "Vendors" / "Programs" / "PdfCraft"
            self.assertEqual((folder / CRAFT_MSI).read_bytes(), b"the installer of pdfcraft")
            self.assertIn(f"[OK] {CRAFT_MSI}: SHA256 matches the checksum file of the release", context.lines)
            self.assertEqual(result["results"][0]["sha256"], "matches")  # type: ignore[index]
            self.assertEqual(self.read, [CRAFT_PAGE + "SHA256SUMS.txt"])
            # The file, its address and its sum are named by one answer about the release.
            self.assertEqual(self.releases, 1)

    def test_a_portable_build_is_checked_before_it_is_unpacked_and_its_folder_is_not_emptied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            portable = Path(temp) / "store" / "Vendors" / "Programs" / "PdfCraft" / "Portable"
            (portable / "data").mkdir(parents=True)
            (portable / "data" / "settings.ini").write_text("mine", encoding="utf-8")
            (portable / "pdfcraft.exe").write_bytes(b"the former build")
            result, context = self.run_job(Path(temp), "prg:pdfcraft:portable")
            self.assertIn(f"[OK] {CRAFT_ZIP}: SHA256 matches the checksum file of the release", context.lines)
            # The new build lies over the former one; what the person keeps in the folder is still there.
            self.assertEqual((portable / "pdfcraft.exe").read_bytes(), b"exe")
            self.assertEqual((portable / "data" / "app.dat").read_bytes(), b"dat")
            self.assertEqual((portable / "data" / "settings.ini").read_text(encoding="utf-8"), "mine")
            self.assertFalse((portable / CRAFT_ZIP).exists())
            self.assertEqual(result["results"][0]["extracted"], 2)  # type: ignore[index]

    def refused(self, temp: str, download_id: str, name: str, words: str) -> None:
        with self.assertRaisesRegex(RuntimeError, "1 of 1 build\\(s\\) are not to be trusted: .*" + words):
            self.run_job(Path(temp), download_id)
        folder = Path(temp) / "store" / "Vendors" / "Programs" / "PdfCraft"
        place = folder / "Portable" if download_id.endswith("portable") else folder
        self.assertFalse((place / name).exists(), "the refused file kept its own name")
        self.assertTrue((place / (name + ".untrusted")).is_file())
        self.assertEqual([item.name for item in place.iterdir()], [name + ".untrusted"])  # nothing was unpacked

    def test_another_sum_sets_the_file_aside_and_fails_the_run(self) -> None:
        self.sums = f"{'0' * 64}  {CRAFT_MSI}\n{'0' * 64}  {CRAFT_ZIP}\n"
        with tempfile.TemporaryDirectory() as temp:
            self.refused(temp, "prg:pdfcraft:installer", CRAFT_MSI, "SHA256 differs from the one the checksum file of the release gives")
            self.assertTrue(any(line.startswith(f"[WARN] {CRAFT_MSI}: SHA256 differs") for line in self.context.lines))
        with tempfile.TemporaryDirectory() as temp:
            self.refused(temp, "prg:pdfcraft:portable", CRAFT_ZIP, "SHA256 differs")

    def test_a_release_without_the_checksum_file_is_not_trusted(self) -> None:
        self.assets.remove("SHA256SUMS.txt")
        with tempfile.TemporaryDirectory() as temp:
            self.refused(temp, "prg:pdfcraft:installer", CRAFT_MSI, "the release of storytold/pdfcraft \\(v0\\.7\\.0\\) has no checksum file")
            self.assertEqual(self.read, [])

    def test_a_checksum_file_without_a_line_for_the_file_is_not_trusted(self) -> None:
        self.sums = f"{hashlib.sha256(self.files[CRAFT_MSI]).hexdigest()}  pdfcraft-0.7.0-windows-arm64.msi\nnot a line of sums\n"
        with tempfile.TemporaryDirectory() as temp:
            self.refused(temp, "prg:pdfcraft:installer", CRAFT_MSI, f"SHA256SUMS\\.txt of storytold/pdfcraft \\(v0\\.7\\.0\\) names no sum for {re.escape(CRAFT_MSI)}")

    def test_a_checksum_file_that_does_not_come_is_not_trusted(self) -> None:
        def no_text(url: str) -> str:
            raise RuntimeError(f"the checksum file did not come from {url} (timed out)")

        ProgramsProvider._instance._text = no_text  # type: ignore[union-attr]
        with tempfile.TemporaryDirectory() as temp:
            self.refused(temp, "prg:pdfcraft:installer", CRAFT_MSI, "the checksum file did not come")

    def test_a_line_of_sums_is_read_as_sha256sum_writes_it(self) -> None:
        made = ProgramsProvider._instance
        assets = [("SHA256SUMS.txt", "u")]
        digest = "ab" * 32
        for line in (f"{digest}  a.msi", f"{digest} *a.msi", f"  {digest.upper()}  a.msi  ", f"{digest}  a.msi\r"):
            self.sums = f"{'0' * 64}  b.msi\n{line}\n"
            self.assertEqual(made.release_sum("o/r", "v1", assets, r"^SHA256SUMS\.txt$", "a.msi"), digest.upper(), line)  # type: ignore[union-attr]
        # A name is taken whole: a line for a longer name is not the line of this file.
        self.sums = f"{digest}  a.msi.sig\n{digest}  xa.msi\n"
        with self.assertRaisesRegex(RuntimeError, "names no sum for a.msi"):
            made.release_sum("o/r", "v1", assets, r"^SHA256SUMS\.txt$", "a.msi")  # type: ignore[union-attr]

    def test_the_list_and_the_link_ask_for_no_sums(self) -> None:
        made = ProgramsProvider._instance
        self.assertEqual(len(vendor_service.program_options(made, "craft", False)), 2)  # type: ignore[arg-type]
        self.assertEqual((self.releases, self.read), (0, []))
        self.assertEqual(made.resolve_link(made.build_by_id("prg:pdfcraft:installer")), CRAFT_PAGE + CRAFT_MSI)  # type: ignore[union-attr, arg-type]
        self.assertEqual(self.read, [])
        # A source without `sums` carries no check at all.
        plain, _asked = provider()
        self.assertIsNone(plain.sources(plain.build_by_id("prg:rufus:installer"))[0].checksum)  # type: ignore[arg-type]
        self.assertIsNotNone(made.sources(made.build_by_id("prg:pdfcraft:installer"))[0].checksum)  # type: ignore[union-attr, arg-type]

    def test_sums_belong_to_a_github_release_alone(self) -> None:
        for source in ('{source: url, url: "https://example.com/a.exe", sums: "^SHA256SUMS$"}',
                       '{source: winget, id: "A.B", sums: "^SHA256SUMS$"}',
                       '{source: github, repo: "o/r", asset: "a", sums: ""}'):
            text = f"shelves:\n  s:\n    blocks:\n      - title: T\n        programs:\n          - id: p\n            name: P\n            installer: {source}\n"
            with self.assertRaisesRegex(ValueError, "p.installer - `sums` is the checksum file of a github release"):
                programs.read_shelves(yaml.safe_load(text))


class Suite(unittest.TestCase):
    """New apps of Craft are followed by the make-up of the launcher: the probe reads the list in the launcher's source."""

    SOURCE = """
/// The Craft App Suite, in display order.
pub const SUITE: [CraftApp; 2] = [
    app(
        "photocraft",
        "PhotoCraft",
        "Image editing",
        AppGroup::Classic,
        [[0x2f, 0x7b, 0xf5], [0x1a, 0x5b, 0xc4], [0x7f, 0xb0, 0xff]],
        "storytold/photocraft",
    ),
    app(
        "filmcraft",
        "FilmCraft",
        "Video editing, color and sound",
        AppGroup::Classic,
        [[0x8b, 0x5c, 0xf6], [0x6a, 0x3f, 0xd6], [0xb6, 0x9c, 0xff]],
        "storytold/filmcraft",
    ),
];

pub const LAUNCHER: CraftApp = app(
    "artcraft-launcher",
    "ArtCraft Launcher",
    "Installs and updates the Craft App Suite",
    AppGroup::New,
    [[0x0b, 0x0b, 0x0c], [0x0b, 0x0b, 0x0c], [0xf2, 0xf1, 0xee]],
    "storytold/craft-launcher",
);
"""

    def test_the_suite_is_read_out_of_the_source_of_the_launcher(self) -> None:
        import probe_sources

        # Only the list of the suite is read: the launcher's own entry below it is not an app of the suite.
        self.assertEqual(probe_sources.suite_repos(self.SOURCE), ["storytold/photocraft", "storytold/filmcraft"])
        # A list that names another number of repositories than it declares, or one of another shape, is said in words.
        with self.assertRaisesRegex(ValueError, "declares 3 apps and names 2 repositories"):
            probe_sources.suite_repos(self.SOURCE.replace("[CraftApp; 2]", "[CraftApp; 3]"))
        with self.assertRaisesRegex(ValueError, "no `pub const SUITE"):
            probe_sources.suite_repos(self.SOURCE.replace("pub const SUITE", "pub const APPS"))
        with self.assertRaisesRegex(ValueError, "2 repositories \\(1 different\\)"):
            probe_sources.suite_repos(self.SOURCE.replace("storytold/filmcraft", "storytold/photocraft"))

    def test_the_shelf_names_its_two_programs_that_are_no_apps_of_the_suite(self) -> None:
        import probe_sources

        shipped = programs.read_shelves(yaml.safe_load((ROOT / "config" / "programs.yaml").read_text(encoding="utf-8")))["craft"]
        keys = [program.key for program in shipped]
        for key in probe_sources.NOT_OF_THE_SUITE:
            self.assertIn(key, keys)
        # The twelve others are the apps of the suite, each in the repository of its own name - as the launcher lists them.
        apps = [program for program in shipped if program.key not in probe_sources.NOT_OF_THE_SUITE]
        self.assertEqual(len(apps), 12)
        for program in apps:
            self.assertEqual(program.installer["repo"], f"storytold/{program.key}")  # type: ignore[index]


class WingetFile(unittest.TestCase):
    """What `winget download` leaves is sorted out: one installer, what it depends on beside it."""

    def setUp(self) -> None:
        from system_core.services import winget_service

        self.winget_service = winget_service
        self.saved = winget_service._download_package_to
        self.leaves: dict[str, bytes] = {}

        def fake(context: JobContext, package_id: str, target: Path, installer_type: str = "") -> dict[str, object]:
            context.progress(1.0)
            for name, data in self.leaves.items():
                (target / name).parent.mkdir(parents=True, exist_ok=True)
                (target / name).write_bytes(data)
            return {"files": []}

        winget_service._download_package_to = fake  # type: ignore[assignment]

    def tearDown(self) -> None:
        self.winget_service._download_package_to = self.saved  # type: ignore[assignment]

    def test_the_installer_moves_to_the_folder_and_the_job_keeps_its_own_progress(self) -> None:
        self.leaves = {"Revo Uninstaller_2.7.2.exe": b"exe", "Revo Uninstaller_2.7.2.yaml": b"manifest", "Dependencies/Microsoft.VCLibs.appx": b"dep"}
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "Revo Uninstaller"
            context = RecordingContext(Path(temp))
            told: list[float] = []
            context.progress = told.append  # type: ignore[method-assign]
            taken = programs.winget_file(context, "RevoUninstaller.RevoUninstaller", "", folder)
            self.assertEqual((taken, taken.read_bytes()), (folder / "Revo Uninstaller_2.7.2.exe", b"exe"))
            self.assertEqual((folder / "Dependencies" / "Microsoft.VCLibs.appx").read_bytes(), b"dep")
            self.assertFalse((folder / "_winget").exists())
            self.assertEqual(told, [])  # the download's own "all done" did not reach the job

    def test_several_installers_or_none_keep_nothing(self) -> None:
        for leaves, word in (({"a.exe": b"1", "b.msi": b"2"}, "2 installer files instead of one"), ({"only.yaml": b"m"}, "0 installer files instead of one")):
            self.leaves = leaves
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp) / "Program"
                with self.assertRaisesRegex(RuntimeError, word):
                    programs.winget_file(RecordingContext(Path(temp)), "Some.Package", "", folder)
                self.assertEqual([item.name for item in folder.iterdir()] if folder.exists() else [], [])


if __name__ == "__main__":
    unittest.main()

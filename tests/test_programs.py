"""Programs taken as files: the shelves of config\\programs.yaml, the list of a page, where a file comes from.

    runtime\\python.exe tests\\test_programs.py

Started as a file, like tests\\test_cli_door.py. Nothing here asks GitHub, WinGet or a maker's site:
the releases, the packages and the downloads are made up. The file of the program itself is read
as it is - every program in it must name its files in a way the provider knows.
"""
from __future__ import annotations

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

SHOWN = """Found Modrinth App [Modrinth.ModrinthApp]
Version: 0.21.9
Homepage: https://modrinth.com/app
License Url: https://github.com/modrinth/code/blob/main/LICENSE
Installer:
  Installer Type: nullsoft
  Installer Url: https://launcher-files.modrinth.com/versions/0.21.9/windows/Modrinth%20App_0.21.9_x64-setup.exe
  Installer SHA256: abc
"""


def provider(text: str = SHELVES, winget=None) -> tuple[ProgramsProvider, list[tuple[str, str, str]]]:
    path = Path(tempfile.mkdtemp()) / "programs.yaml"
    path.write_text(text, encoding="utf-8")
    asked: list[tuple[str, str, str]] = []

    def fake_winget(context: JobContext, package_id: str, installer_type: str, folder: Path) -> Path:
        asked.append((package_id, installer_type, str(folder)))
        target = folder / (package_id.rsplit(".", 1)[-1] + (".zip" if installer_type == "zip" else ".exe"))
        target.write_bytes(zipped(**{"Autoruns__Autoruns64.exe": b"exe", "Autoruns__Eula.txt": b"txt"}) if installer_type == "zip" else b"installer")
        return target

    return ProgramsProvider(path, releases=lambda repo: RELEASES[repo], winget=winget or fake_winget, show=lambda package: SHOWN), asked


class TheFile(unittest.TestCase):
    """config\\programs.yaml as the program ships it."""

    def setUp(self) -> None:
        self.path = ROOT / "config" / "programs.yaml"
        self.shelves = programs.read_shelves(yaml.safe_load(self.path.read_text(encoding="utf-8")))

    def test_every_program_names_its_files_in_a_way_the_provider_knows(self) -> None:
        self.assertEqual(sorted(self.shelves), ["carrier", "games", "tests", "utilities"])
        listed = [program for shelf in self.shelves.values() for program in shelf]
        self.assertEqual(len(listed), 34)
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

    def test_the_list_asks_nothing_of_the_network(self) -> None:
        made = ProgramsProvider(ROOT / "config" / "programs.yaml", releases=lambda repo: self.fail("a release was read"),
                                winget=lambda *args: self.fail("WinGet was called"), show=lambda package: self.fail("WinGet was asked"))
        self.assertEqual(made.products("Windows"), ["carrier", "utilities", "tests", "games"])
        self.assertEqual(sum(len(made.builds(shelf, "Windows")) for shelf in made.products("Windows")), 34)
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

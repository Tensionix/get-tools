"""The WinGet catalogue: the lists of config\\*.txt and the blocks of packages in the manifest.

    runtime\\python.exe tests\\test_catalog.py

Started as a file, like the other tests. Nothing here calls WinGet or the network: the lists and
the manifest are read as the program ships them. Whether an id exists in WinGet is asked by
tests\\probe_sources.py, by hand, before a release.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

CONFIG = ROOT / "config"

# What 3.1.0 added (E:\TOOLS\GET_TOOLS_NEW_PROGRAMS_SPEC.md, N5): block of the manifest, list, section of
# the list, and the packages in the order they were added - id, label, a console tool or not.
ADDED_3_1_0: list[tuple[str, str, str, list[tuple[str, str, bool]]]] = [
    ("packages_system", "system.txt", "", [("Microsoft.Sysinternals.ProcessMonitor", "Process Monitor", False),
                                           ("QL-Win.QuickLook", "QuickLook", False), ("Ditto.Ditto", "Ditto", False)]),
    ("packages_dev", "dev.txt", "", [("Microsoft.DotNet.SDK.10", ".NET SDK 10", True)]),
    ("packages_ai", "ai.txt", "", [("Comfy.ComfyUI-Desktop", "ComfyUI Desktop", False), ("ElementLabs.LMStudio", "LM Studio", False),
                                   ("Upscayl.Upscayl", "Upscayl", False)]),
    ("packages_office", "office.txt", "", [("geeksoftwareGmbH.PDF24Creator", "PDF24 Creator", False), ("Cyanfish.NAPS2", "NAPS2", False),
                                           ("JohnMacFarlane.Pandoc", "Pandoc", True), ("OSGeo.QGIS_LTR", "QGIS LTR", False)]),
    ("packages_media_images", "media.txt", "Images", [("darktable.darktable", "darktable", False), ("OliverBetz.ExifTool", "ExifTool", True),
                                                      ("BlenderFoundation.Blender", "Blender", False)]),
    ("packages_network", "browsers-vpn.txt", "", [("WinSCP.WinSCP", "WinSCP", False), ("LocalSend.LocalSend", "LocalSend", False)]),
]


def manifest_text() -> str:
    return (CONFIG / "tool_manifest.yaml").read_text(encoding="utf-8")


def blocks() -> dict[str, list[dict]]:
    """The blocks of packages of the manifest: the id of a field - its options."""
    fields = yaml.safe_load(manifest_text())["x-package-fields"]
    return {str(field["id"]): list(field.get("options") or []) for field in fields}


def list_ids(name: str, section: str = "") -> list[str]:
    """The ids of a list as they stand in it; of media.txt - of one of its sections (`# Images`)."""
    ids: list[str] = []
    inside = not section
    for line in (CONFIG / name).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#"):
            if section and line[1:].strip() in {"Images", "Audio", "Video"}:
                inside = line[1:].strip() == section
            continue
        if line and inside:
            ids.append(line)
    return ids


class Added(unittest.TestCase):
    """The sixteen packages of 3.1.0."""

    def test_there_are_sixteen_of_them(self) -> None:
        packages = [package for _block, _list, _section, rows in ADDED_3_1_0 for package, _label, _cli in rows]
        self.assertEqual((len(packages), len(set(packages))), (16, 16))

    def test_each_stands_at_the_end_of_its_block_with_its_label_and_two_tooltips(self) -> None:
        found = blocks()
        for block, _list, _section, rows in ADDED_3_1_0:
            tail = found[block][-len(rows):]
            self.assertEqual([option["value"] for option in tail], [package for package, _label, _cli in rows], block)
            for option, (package, label, cli) in zip(tail, rows):
                self.assertEqual((option["label"], option["label_ru"]), (label, label), package)
                self.assertTrue(option.get("tooltip") and option.get("tooltip_ru"), package)
                self.assertNotEqual(option["tooltip"], option["tooltip_ru"], package)
                # A console tool is marked as Rclone is; nothing else is.
                self.assertEqual(bool(option.get("cli")), cli, package)

    def test_each_stands_at_the_end_of_its_list(self) -> None:
        for _block, name, section, rows in ADDED_3_1_0:
            self.assertEqual(list_ids(name, section)[-len(rows):], [package for package, _label, _cli in rows], name)

    def test_none_is_part_of_a_profile_or_of_another_block(self) -> None:
        # The id of a new package stands in the manifest once - as its own option: no profile names it.
        text = manifest_text()
        for _block, _list, _section, rows in ADDED_3_1_0:
            for package, _label, _cli in rows:
                self.assertEqual(text.count(package), 1, package)

    def test_the_lists_keep_their_line_ends(self) -> None:
        for name in sorted({name for _block, name, _section, _rows in ADDED_3_1_0}):
            raw = (CONFIG / name).read_bytes()
            self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"), name)
            self.assertTrue(raw.endswith(b"\r\n"), name)
            self.assertTrue(all(byte < 128 for byte in raw), name)


# A package that stands in one of the two places alone, and why.
ONLY_IN_THE_LIST = {
    "Microsoft.AppInstaller": "WinGet itself: it is updated with the system list and is no card of the catalogue",
}
ONLY_IN_THE_MANIFEST = {
    "windows-feature:NetFx3": "a feature of Windows switched on through DISM, not a WinGet package: no list can carry it",
}


class Agreement(unittest.TestCase):
    """A package stands in two places - a list and a block of the manifest - and the two must name the same ones.

    Until 3.1.0 this was kept by attention alone. The order is not compared: in system.txt and dev.txt it
    already differs from the manifest.
    """

    def test_a_list_and_its_block_name_the_same_packages(self) -> None:
        from system_core.services.winget_service import CONFIG_LIST_TARGETS

        found = blocks()
        compared = 0
        for key, (name, block, section) in CONFIG_LIST_TARGETS.items():
            if block is None:
                continue  # the person's own list and the pins: no block of the manifest stands behind them
            listed = list_ids(name, section or "")
            offered = [str(option["value"]) for option in found[block]]
            self.assertEqual(len(listed), len(set(listed)), f"{name}: an id twice")
            self.assertEqual(len(offered), len(set(offered)), f"{block}: a value twice")
            self.assertEqual(sorted(set(listed) - set(ONLY_IN_THE_LIST)), sorted(set(offered) - set(ONLY_IN_THE_MANIFEST)), key)
            compared += 1
        self.assertEqual(compared, 10)

    def test_the_exceptions_are_where_they_are_said_to_be(self) -> None:
        found = blocks()
        system = [str(option["value"]) for option in found["packages_system"]]
        for package in ONLY_IN_THE_LIST:
            self.assertIn(package, list_ids("system.txt"))
            self.assertNotIn(package, system)
        for package in ONLY_IN_THE_MANIFEST:
            self.assertIn(package, system)
            self.assertNotIn(package, list_ids("system.txt"))
        self.assertEqual((len(ONLY_IN_THE_LIST), len(ONLY_IN_THE_MANIFEST)), (1, 1))

    def test_a_section_of_the_media_list_is_read_apart(self) -> None:
        images, audio, video = list_ids("media.txt", "Images"), list_ids("media.txt", "Audio"), list_ids("media.txt", "Video")
        self.assertTrue(images and audio and video)
        self.assertEqual(sorted(images + audio + video), sorted(list_ids("media.txt")))
        self.assertFalse(set(images) & set(audio) or set(audio) & set(video) or set(images) & set(video))


if __name__ == "__main__":
    unittest.main()

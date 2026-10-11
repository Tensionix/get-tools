"""Intel Chipset Device Software as a vendor: the list of packages, the one this PC wants, the sources of a package.

    runtime\\python.exe tests\\test_intelchipset.py

Started as a file, like tests\\test_cli_door.py. Nothing here asks a real site or reads the devices
of the machine: the pages, the devices and the signatures are made up.
"""
from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from system_core.core import pc_devices  # noqa: E402
from system_core.core.jobs import JobContext  # noqa: E402
from system_core.core.manifest import Operation  # noqa: E402
from system_core.services import vendor_service  # noqa: E402
from system_core.vendors import BuildSource, intelchipset  # noqa: E402
from system_core.vendors.intelchipset import IntelChipsetProvider  # noqa: E402
from test_vendors import RecordingContext  # noqa: E402


def zipped(**files: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name.replace("__", "/"), data)
    return buffer.getvalue()


OLD_ZIP = zipped(**{"SetupChipset.exe": b"old installer"})
OLD_SHA = hashlib.sha256(OLD_ZIP).hexdigest().upper()

PACKAGES = f"""﻿Name = Intel Chipset Device Software
INFVer = 28/07/2021,10.1.18836.8283
Variant = Consumer
Link = https://github.com/FirstEverTech/Universal-Intel-Chipset-Updater/releases/download/archive/intel_chipset_10.1.18836.8283.zip
Prefix = \\SetupChipset.exe
SHA256 = {OLD_SHA}
Backup = https://www.firstever.tech/download/backup/intel_chipset_10.1.18836.8283.zip

Name = Intel Chipset Device Software
INFVer = 14/08/2025,10.1.20314.8688S
Variant = Server
Link = https://github.com/FirstEverTech/Universal-Intel-Chipset-Updater/releases/download/archive/intel_chipset_10.1.20314.8688S.zip
Prefix = \\SetupChipset.exe
SHA256 = AA
Backup = https://www.firstever.tech/download/backup/intel_chipset_10.1.20314.8688S.zip

Name = Intel Chipset Device Software
INFVer = 17/10/2025,10.1.20378.8757
Variant = Consumer
Link = https://github.com/FirstEverTech/Universal-Intel-Chipset-Updater/releases/download/archive/intel_chipset_10.1.20378.8757.zip
Prefix = \\SetupChipset.msi
SHA256 = BB
Backup = https://www.firstever.tech/download/backup/intel_chipset_10.1.20378.8757.zip

Name = Intel Chipset Device Software
INFVer = 08/04/2026,10.1.20551.8850
Variant = Consumer
Link = https://github.com/FirstEverTech/Universal-Intel-Chipset-Updater/releases/download/archive/intel_chipset_10.1.20551.8850.zip
Prefix = \\SetupChipset.msi
SHA256 = CC
Backup = https://www.firstever.tech/download/backup/intel_chipset_10.1.20551.8850.zip

Name = Intel Chipset Device Software
INFVer = 24/07/2026,10.1.20658.8883
Variant = Consumer
Link = https://github.com/FirstEverTech/Universal-Intel-Chipset-Updater/releases/download/archive/intel_chipset_10.1.20658.8883.zip
Prefix = \\SetupChipset.msi
SHA256 = DD
Backup = https://www.firstever.tech/download/backup/intel_chipset_10.1.20658.8883.zip
"""

INF_BASE = """# Intel Chipset INF Files List

#### AlderLake
**Generation:** 12th Gen Core

| INF | Package | Version | Date | HWIDs |
|--------|---------|---------|------|--------|
| AlderLakeSystem.inf, AlderLakeSystemNorthpeak.inf | 10.1.20658.8883 | 10.1.45.10 | 20/01/2026* | 4601, 462F, 464D |

#### AlderLakepch-P_Extension-Dmasec

| INF | Package | Version | Date | HWIDs |
|--------|---------|---------|------|--------|
| AlderLakePCH-PDmaSecExtension.inf | 10.1.20658.8883 | 10.1.36.7 | 20/01/2026 | 5182 |

#### AlderLake EOL

| INF | Package | Version | Date | HWIDs |
|--------|---------|---------|------|--------|
| AlderLakeSystem.inf | 10.1.18836.8283 | 10.1.45.3 | 28/07/2021 | 464F |

#### Skylake

| INF | Package | Version | Date | HWIDs |
|--------|---------|---------|------|--------|
| Skylake.inf | 10.1.20314.8688S | 10.1.1.1 | 01/01/2020 | 1904, 190F |
"""


def station_page(*versions: str) -> str:
    folder = "/index.php/en-us/component/remository/Drivers/Intel/Chipset-Device-Software/Serie-10.0.x--and--10.1.1.x/"
    return "<html>" + "".join(
        f'<a href="{folder}Intel-Chipset-Device-Software-Version-{version}/lang,en-us/">x</a>'
        f'<a href="{folder}Intel-Chipset-Device-Software-Version-{version}/lang,en-us/">again</a>' for version in versions) + "</html>"


class FakeSites:
    """The three sites by their addresses: what is asked, in order, and the page each address answers with."""

    def __init__(self, station: dict[int, str] | None = None, broken: tuple[str, ...] = ()) -> None:
        self.station = station if station is not None else {1: station_page("10.1.20378.8757", "10.1.20658.8883")}
        self.broken = broken
        self.asked: list[str] = []

    def __call__(self, url: str) -> str:
        self.asked.append(url)
        if any(word in url for word in self.broken):
            raise RuntimeError("the site did not answer")
        if url.endswith(intelchipset.PACKAGES_FILE):
            return PACKAGES
        if url.endswith(intelchipset.INF_BASE_FILE):
            return INF_BASE
        if "Intel-Chipset-Device-Software-Version-" in url:
            return '<a href="/index.php/en-us/component/remository/func-startdown/6961/lang,en-us/">Download</a>'
        if url.startswith(intelchipset.STATION_FOLDER):
            return self.station.get(int(url.split("/page,", 1)[1].split("/", 1)[0]), "<html></html>")
        if "func-startdown/6961" in url:
            return '<a href="/index.php/en-us/component/remository/func-download/6961/chk,0123abcd/no_html,1/lang,en-us/">here</a>'
        return ""


def pci(device: str) -> pc_devices.Device:
    return pc_devices.Device(device_id=rf"PCI\VEN_8086&DEV_{device}&SUBSYS_22E717AA&REV_02\3&11583659&0&00", name="Intel device")


ALDER_LAKE = [pci("4601"), pci("5182"), pci("464F"), pc_devices.Device(r"PCI\VEN_10EC&DEV_4601&SUBSYS_1\1", "Realtek of the same number")]


def provider(devices: list[pc_devices.Device], sites: FakeSites | None = None, official: str | None = None) -> tuple[IntelChipsetProvider, FakeSites]:
    sites = sites or FakeSites()
    folder = Path(tempfile.mkdtemp())
    path = folder / "vendor_intel_chipset.yaml"
    path.write_text(official if official is not None else 'official:\n  "10.1.20658.8883": "https://downloadmirror.intel.com/925343/SetupChipset.exe"\n', encoding="utf-8")
    return IntelChipsetProvider(http=sites, devices=lambda: devices, official_path=path), sites


class Base(unittest.TestCase):
    def test_a_package_is_read_from_its_block(self) -> None:
        packages = intelchipset.parse_packages(PACKAGES)
        self.assertEqual([package.version for package in packages],
                         ["10.1.18836.8283", "10.1.20314.8688S", "10.1.20378.8757", "10.1.20551.8850", "10.1.20658.8883"])
        first = packages[0]
        self.assertEqual((first.date, first.variant, first.name, first.sha256), ("28/07/2021", "Consumer", "intel_chipset_10.1.18836.8283.zip", OLD_SHA))
        self.assertTrue(first.backup.startswith("https://www.firstever.tech/"))
        self.assertEqual(intelchipset.short_date("08/04/2026"), "8 Apr 2026")
        # A server's package carries a letter; its number still compares.
        self.assertFalse(intelchipset.is_msi_time("10.1.20314.8688S"))
        self.assertTrue(intelchipset.is_msi_time("10.1.20378.8757"))

    def test_a_record_whose_words_are_no_version_and_no_file_name_is_not_listed(self) -> None:
        block = ("Name = Intel Chipset Device Software\nINFVer = 01/01/2026,{version}\nVariant = Consumer\n"
                 "Link = https://github.com/x/releases/download/archive/{name}\nPrefix = \\SetupChipset.exe\nSHA256 = AA\n")
        text = "\n".join([block.format(version="10.1.99999.1", name="good.zip"),
                          block.format(version=r"10.1.1\..\up", name="by-version.zip"),
                          block.format(version="10.1.99998.1", name=r"a\..\..\evil.exe"),
                          block.format(version="10.1.99997.1", name="..%5C..%5Cevil.exe"),
                          block.format(version="latest", name="word.zip")])
        self.assertEqual([(package.version, package.name) for package in intelchipset.parse_packages(text)], [("10.1.99999.1", "good.zip")])

    def test_the_intel_devices_of_a_pc_are_looked_up_in_the_base(self) -> None:
        ids = intelchipset.intel_device_ids(ALDER_LAKE)
        self.assertEqual(ids, {"4601", "5182", "464F"})  # the Realtek of the same number is no Intel device
        matches = intelchipset.parse_matches(INF_BASE, ids)
        self.assertEqual([(match.platform, match.package, match.devices) for match in matches],
                         [("AlderLake", "10.1.20658.8883", ("4601",)), ("AlderLakepch-P_Extension-Dmasec", "10.1.20658.8883", ("5182",)),
                          ("AlderLake EOL", "10.1.18836.8283", ("464F",))])
        self.assertEqual([intelchipset.platform_name(match.platform) for match in matches], ["AlderLake", "AlderLakepch-P", "AlderLake"])
        self.assertEqual(intelchipset.parse_matches(INF_BASE, set()), [])

    def test_a_page_of_station_drivers_names_its_versions_once(self) -> None:
        found = intelchipset.parse_station(station_page("10.1.20378.8757", "10.1.20658.8883"))
        self.assertEqual(list(found), ["10.1.20378.8757", "10.1.20658.8883"])
        self.assertTrue(found["10.1.20378.8757"].startswith("https://www.station-drivers.com/index.php/"))


class TheList(unittest.TestCase):
    def test_the_package_this_pc_wants_is_ticked_and_named_over_the_list(self) -> None:
        made, _sites = provider(ALDER_LAKE)
        builds = made.builds(intelchipset.PRODUCT, "Windows")
        # Newest first. 10.1.20551 is of the MSI time and has neither Intel's address nor a page of station-drivers: not listed.
        self.assertEqual([build.version for build in builds], ["10.1.20658.8883", "10.1.20378.8757", "10.1.20314.8688S · Server", "10.1.18836.8283"])
        self.assertEqual([build.ticked for build in builds], [True, False, False, False])
        self.assertEqual([build.notes for build in builds], ["this PC", "", "", "this PC"])
        self.assertEqual(builds[0].date, "24 Jul 2026")
        self.assertEqual(made.note(intelchipset.PRODUCT)[0], "Intel Chipset Device Software · this PC: AlderLake, AlderLakepch-P · package 10.1.20658.8883, 10.1.18836.8283")
        self.assertIn("этот ПК", made.note(intelchipset.PRODUCT)[1])

    def test_without_an_intel_device_nothing_is_ticked_and_the_base_is_not_asked(self) -> None:
        made, sites = provider([pc_devices.Device(r"PCI\VEN_1022&DEV_1480&SUBSYS_1\1", "AMD Root Complex")])
        builds = made.builds(intelchipset.PRODUCT, "Windows")
        self.assertEqual(len(builds), 4)
        self.assertFalse(any(build.ticked for build in builds))
        self.assertIn("nothing ticked", made.note(intelchipset.PRODUCT)[0])
        self.assertFalse(any(url.endswith(intelchipset.INF_BASE_FILE) for url in sites.asked))

    def test_station_drivers_is_read_page_by_page_until_a_page_adds_nothing(self) -> None:
        sites = FakeSites(station={1: station_page("10.1.20378.8757"), 2: station_page("10.1.20551.8850"), 3: station_page("10.1.20551.8850"), 4: station_page("9.9.9.9")})
        made, _ = provider([], sites)
        self.assertEqual(list(made.station_versions()), ["10.1.20378.8757", "10.1.20551.8850"])
        self.assertEqual(len([url for url in sites.asked if "/page," in url]), 3)

    def test_the_list_stands_without_station_drivers(self) -> None:
        made, _sites = provider(ALDER_LAKE, FakeSites(broken=("station-drivers",)))
        builds = made.builds(intelchipset.PRODUCT, "Windows")
        # Of the MSI time only what Intel's own address is known for.
        self.assertEqual([build.version for build in builds], ["10.1.20658.8883", "10.1.20314.8688S · Server", "10.1.18836.8283"])
        self.assertTrue(builds[0].ticked)

    def test_the_form_gets_the_note_the_tick_and_one_product_without_a_field(self) -> None:
        made, _sites = provider(ALDER_LAKE)
        saved = IntelChipsetProvider._instance
        IntelChipsetProvider._instance = made
        try:
            options = vendor_service.vendor_build_options(values={"vendor": "intelchipset"})
        finally:
            IntelChipsetProvider._instance = saved
        self.assertTrue(options[0].get("note"))
        self.assertIn("this PC: AlderLake", options[0]["label"])
        self.assertEqual((options[1]["value"], options[1].get("default")), ("icd:10.1.20658.8883", True))
        self.assertEqual(options[1]["label"], "10.1.20658.8883 - 24 Jul 2026 - this PC - latest")
        self.assertEqual(len(options), 5)


class Sources(unittest.TestCase):
    def test_a_package_of_the_msi_time_comes_from_intel_then_the_archive_then_station_drivers(self) -> None:
        made, sites = provider(ALDER_LAKE)
        builds = made.builds(intelchipset.PRODUCT, "Windows")
        sources = made.sources(builds[0])
        self.assertEqual([source.host for source in sources], ["downloadmirror.intel.com", "web.archive.org", "www.station-drivers.com"])
        self.assertEqual([source.name for source in sources],
                         ["SetupChipset_10.1.20658.8883.exe", "SetupChipset_10.1.20658.8883.exe", "intel_chipset_10.1.20658.8883_station-drivers.zip"])
        self.assertEqual(sources[0].link(), ("https://downloadmirror.intel.com/925343/SetupChipset.exe", ""))
        self.assertEqual(sources[1].link()[0], "https://web.archive.org/web/2026id_/https://downloadmirror.intel.com/925343/SetupChipset.exe")
        # FirstEverTech's SHA256 is of its own zip, not of Intel's installer.
        self.assertEqual([source.sha256 for source in sources], ["", "", ""])
        self.assertEqual((sources[2].installer, sources[0].installer), (intelchipset.INSTALLER_IN_ZIP, ""))
        # The page of station-drivers is opened only when its turn comes.
        self.assertFalse(any("func-startdown" in url for url in sites.asked))
        url, referer = sources[2].link()
        self.assertTrue(url.endswith("func-download/6961/chk,0123abcd/no_html,1/lang,en-us/"))
        self.assertTrue(referer.endswith("func-startdown/6961/lang,en-us/"))
        # Without Intel's address - station-drivers alone.
        self.assertEqual([source.host for source in made.sources(builds[1])], ["www.station-drivers.com"])
        self.assertEqual(made.resolve_link(builds[1]), "https://www.station-drivers.com/index.php/en-us/component/remository/Drivers/Intel/"
                         "Chipset-Device-Software/Serie-10.0.x--and--10.1.1.x/Intel-Chipset-Device-Software-Version-10.1.20378.8757/lang,en-us/")

    def test_an_older_package_comes_from_the_archive_of_packages_with_its_sha256(self) -> None:
        made, _sites = provider(ALDER_LAKE)
        old = made.builds(intelchipset.PRODUCT, "Windows")[-1]
        sources = made.sources(old)
        self.assertEqual([source.host for source in sources], ["github.com", "www.firstever.tech"])
        self.assertEqual({source.name for source in sources}, {"intel_chipset_10.1.18836.8283.zip"})
        self.assertEqual({source.sha256 for source in sources}, {OLD_SHA})
        self.assertTrue(made.resolve_link(old).startswith("https://github.com/FirstEverTech/"))

    def test_a_build_is_found_by_its_id_in_another_process(self) -> None:
        made, _sites = provider(ALDER_LAKE)
        build = made.build_by_id("icd:10.1.18836.8283")
        self.assertEqual((build.name, build.version), ("intel_chipset_10.1.18836.8283.zip", "10.1.18836.8283"))  # type: ignore[union-attr]
        self.assertIsNone(made.build_by_id("icd:1.2.3.4"))
        self.assertIsNone(made.build_by_id("msc:x:y"))

    def test_the_file_of_addresses_may_be_absent_or_broken(self) -> None:
        for text in ("", "official: [1, 2]\n", "nothing: here\n", ": : :\n"):
            made, _sites = provider(ALDER_LAKE, official=text)
            self.assertEqual(made.official(), {}, text)
        self.assertEqual(IntelChipsetProvider(http=FakeSites(), devices=lambda: []).official(), {})


class Job(unittest.TestCase):
    """The download job with a provider of several sources: what is asked, checked, unpacked."""

    def setUp(self) -> None:
        self.saved = (IntelChipsetProvider._instance, vendor_service.download_with_resume, vendor_service.authenticode)
        self.fetched: list[tuple[str, dict[str, str]]] = []
        self.files: dict[str, bytes | None] = {}
        self.signed: dict[str, tuple[str, str]] = {}

        def fake_download(context: JobContext, url: str, target: Path, label: str, **kwargs: object) -> Path:
            self.fetched.append((url, dict(kwargs.get("headers") or {})))  # type: ignore[call-overload]
            data = next((data for word, data in self.files.items() if word in url), None)
            if data is None:
                raise RuntimeError(f"Download failed: {label} (HTTP Error 403: Forbidden)")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return target

        vendor_service.download_with_resume = fake_download  # type: ignore[assignment]
        vendor_service.authenticode = lambda path: self.signed.get(path.name, ("NotSigned", ""))  # type: ignore[assignment]
        IntelChipsetProvider._instance, _sites = provider(ALDER_LAKE)

    def tearDown(self) -> None:
        IntelChipsetProvider._instance, vendor_service.download_with_resume, vendor_service.authenticode = self.saved  # type: ignore[assignment]

    def run_job(self, root: Path, *versions: str) -> tuple[dict[str, object], RecordingContext]:
        context = RecordingContext(root)
        context.operation = Operation(id="vendor_download", title="t", description="", service="x:y", parameters={
            "vendor": "intelchipset", "vendor_versions": [f"icd:{version}" for version in versions], "output_path": str(root / "store"),
            # The switches of the form are off: a package whose installers are checked is unpacked all the same.
            "vendor_extract": False, "vendor_delete_zip": False})
        return vendor_service.download_vendor_builds(context), context

    INTEL = ("Valid", "CN=Intel Corporation, OU=Chipset, O=Intel Corporation, L=Santa Clara, S=California, C=US")

    def test_intels_own_server_gives_the_installer_and_its_signature_is_checked(self) -> None:
        self.files = {"downloadmirror.intel.com": b"intel installer"}
        self.signed = {"SetupChipset_10.1.20658.8883.exe": self.INTEL}
        with tempfile.TemporaryDirectory() as temp:
            result, context = self.run_job(Path(temp), "10.1.20658.8883")
            entry = result["results"][0]  # type: ignore[index]
            folder = Path(temp) / "store" / "Vendors" / "Intel" / "SetupChipset_10.1.20658.8883"
            self.assertEqual((entry["source"], entry["signature"], entry["archive"]),
                             ("downloadmirror.intel.com", "Valid", str(folder / "SetupChipset_10.1.20658.8883.exe")))
            self.assertEqual((folder / "SetupChipset_10.1.20658.8883.exe").read_bytes(), b"intel installer")
            self.assertEqual(len(self.fetched), 1)
            # Asked as a person's browser would, not by the program's own name.
            self.assertTrue(self.fetched[0][1]["User-Agent"].startswith("Mozilla/5.0"))
            self.assertIn("[SOURCE] downloadmirror.intel.com", context.lines)
            self.assertIn("[OK] SetupChipset_10.1.20658.8883.exe: signed by Intel Corporation", context.lines)

    def test_a_source_that_gives_nothing_hands_over_to_the_next_down_to_station_drivers(self) -> None:
        self.files = {"downloadmirror.intel.com": None, "web.archive.org": None,
                      "func-download": zipped(**{"Chipset_10.1.20658.8883__Chipset_10.1.20658.8883.exe": b"signed by intel",
                                                 "Chipset_10.1.20658.8883__INF__AlderLakeSystem.inf": b"inf", "readme.txt": b"txt"})}
        self.signed = {"Chipset_10.1.20658.8883.exe": self.INTEL}
        with tempfile.TemporaryDirectory() as temp:
            result, context = self.run_job(Path(temp), "10.1.20658.8883")
            entry = result["results"][0]  # type: ignore[index]
            folder = Path(temp) / "store" / "Vendors" / "Intel" / "SetupChipset_10.1.20658.8883"
            self.assertEqual((entry["source"], entry["signature"]), ("www.station-drivers.com", "Valid"))
            # Only Intel's installer is taken out of their zip; the zip goes.
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["Chipset_10.1.20658.8883.exe"])
            self.assertEqual(entry["archive"], str(folder / "Chipset_10.1.20658.8883.exe"))
            self.assertEqual([url.split("/")[2] for url, _headers in self.fetched], ["downloadmirror.intel.com", "web.archive.org", "www.station-drivers.com"])
            # The file of station-drivers is asked for with the start page as its Referer.
            self.assertTrue(self.fetched[2][1]["Referer"].endswith("func-startdown/6961/lang,en-us/"))
            self.assertNotIn("Referer", self.fetched[0][1])
            self.assertEqual(len([line for line in context.lines if "gave no file" in line]), 2)

    def test_an_older_package_is_compared_by_sha256_unpacked_and_checked(self) -> None:
        self.files = {"github.com": OLD_ZIP}
        self.signed = {"SetupChipset.exe": self.INTEL}
        with tempfile.TemporaryDirectory() as temp:
            result, context = self.run_job(Path(temp), "10.1.18836.8283")
            entry = result["results"][0]  # type: ignore[index]
            folder = Path(temp) / "store" / "Vendors" / "Intel" / "intel_chipset_10.1.18836.8283"
            self.assertEqual((entry["source"], entry["sha256"], entry["signature"], entry["archive"], entry["extracted"]),
                             ("github.com", "matches", "Valid", "", 1))
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["SetupChipset.exe"])
            self.assertIn("[OK] intel_chipset_10.1.18836.8283.zip: SHA256 matches the catalog of the packages", context.lines)

    def test_a_file_that_is_not_the_one_leaves_its_name_and_the_next_source_is_asked(self) -> None:
        self.files = {"github.com": zipped(**{"SetupChipset.exe": b"something else"})}
        with tempfile.TemporaryDirectory() as temp:
            # The mirror gives nothing here: what was wrong with each source is in the failure.
            with self.assertRaisesRegex(RuntimeError, r"1 of 1 build\(s\).*SHA256 differs.*www\.firstever\.tech gave no file"):
                self.run_job(Path(temp), "10.1.18836.8283")
            folder = Path(temp) / "store" / "Vendors" / "Intel" / "intel_chipset_10.1.18836.8283"
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["intel_chipset_10.1.18836.8283.zip.untrusted"])
            self.assertEqual([url.split("/")[2] for url, _headers in self.fetched], ["github.com", "www.firstever.tech"])

            # The mirror has the right file: it is taken, and nothing of the refused try is kept.
            self.files = {"github.com": zipped(**{"SetupChipset.exe": b"something else"}), "www.firstever.tech": OLD_ZIP}
            self.signed = {"SetupChipset.exe": self.INTEL}
            result, context = self.run_job(Path(temp), "10.1.18836.8283")
            entry = result["results"][0]  # type: ignore[index]
            self.assertEqual((entry["source"], entry["sha256"], entry["signature"]), ("www.firstever.tech", "matches", "Valid"))
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["SetupChipset.exe"])
            self.assertEqual(len([line for line in context.lines if "gave a file that is not to be trusted" in line]), 1)

    def test_an_installer_without_intels_signature_fails_the_run_and_says_not_to_run_it(self) -> None:
        self.files = {"github.com": OLD_ZIP, "downloadmirror.intel.com": b"intel installer"}
        self.signed = {"SetupChipset.exe": ("HashMismatch", self.INTEL[1]),
                       "SetupChipset_10.1.20658.8883.exe": ("Valid", "CN=Intel Corporation Fan Club, O=Somebody")}
        with tempfile.TemporaryDirectory() as temp:
            context = RecordingContext(Path(temp))
            context.operation = Operation(id="vendor_download", title="t", description="", service="x:y", parameters={
                "vendor": "intelchipset", "vendor_versions": ["icd:10.1.18836.8283", "icd:10.1.20658.8883"], "output_path": str(Path(temp) / "store")})
            with self.assertRaisesRegex(RuntimeError, r"2 of 2 build\(s\)"):
                vendor_service.download_vendor_builds(context)
            warned = [line for line in context.lines if "the signature of Intel Corporation is not confirmed" in line]
            self.assertIn("(HashMismatch)", warned[0])
            store = Path(temp) / "store" / "Vendors" / "Intel"
            # The package was unpacked apart and stays apart; the folder of the build holds nothing to run.
            old = store / "intel_chipset_10.1.18836.8283"
            self.assertEqual(sorted(item.name for item in old.iterdir()), ["_untrusted"])
            self.assertEqual([item.name for item in (old / "_untrusted").iterdir()], ["SetupChipset.exe"])
            # A lone installer whose signature is somebody else's leaves its name; every source was asked.
            new = store / "SetupChipset_10.1.20658.8883"
            self.assertEqual(sorted(item.name for item in new.iterdir()), ["SetupChipset_10.1.20658.8883.exe.untrusted"])
            self.assertEqual([url.split("/")[2] for url, _headers in self.fetched][2:], ["downloadmirror.intel.com", "web.archive.org", "www.station-drivers.com"])

    def test_a_zip_of_station_drivers_without_the_installer_is_left_and_the_run_fails(self) -> None:
        self.files = {"func-download": zipped(**{"INF__AlderLakeSystem.inf": b"inf", "Setup.exe": b"not it"})}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, "no installer of the vendor inside"):
                self.run_job(Path(temp), "10.1.20378.8757")
            folder = Path(temp) / "store" / "Vendors" / "Intel" / "SetupChipset_10.1.20378.8757"
            self.assertEqual(sorted(item.name for item in folder.iterdir()), ["intel_chipset_10.1.20378.8757_station-drivers.zip.untrusted"])


class Pieces(unittest.TestCase):
    def test_the_last_source_fails_the_download_with_its_own_words(self) -> None:
        def broken() -> tuple[str, str]:
            raise RuntimeError("the page has no file link")

        saved = vendor_service.download_with_resume
        vendor_service.download_with_resume = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Download failed: HTTP 403"))  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as temp:
                context = RecordingContext(Path(temp))
                sources = [BuildSource("one.example", "a.exe", lambda: ("https://one.example/a.exe", "")), BuildSource("two.example", "a.zip", broken)]
                with self.assertRaisesRegex(RuntimeError, "no file link"):
                    vendor_service.download_from_sources(context, sources, Path(temp), "a")
                self.assertEqual([line for line in context.lines if line.startswith("[WARN]")], ["[WARN] one.example gave no file (Download failed: HTTP 403); the next source"])
                with self.assertRaisesRegex(RuntimeError, "No source"):
                    vendor_service.download_from_sources(context, [], Path(temp), "a")
        finally:
            vendor_service.download_with_resume = saved  # type: ignore[assignment]

    def test_a_cancelled_run_tries_no_other_source(self) -> None:
        asked: list[str] = []

        def cancelled_download(context: JobContext, url: str, *args: object, **kwargs: object) -> Path:
            asked.append(url)
            raise RuntimeError("Cancelled: a (partial file kept for resume)")

        saved = vendor_service.download_with_resume
        vendor_service.download_with_resume = cancelled_download  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as temp:
                context = RecordingContext(Path(temp), cancel_after=0)
                sources = [BuildSource(f"{name}.example", "a.exe", lambda name=name: (f"https://{name}.example/a.exe", "")) for name in ("one", "two")]
                with self.assertRaisesRegex(RuntimeError, "Cancelled"):
                    vendor_service.download_from_sources(context, sources, Path(temp), "a")
                self.assertEqual(asked, ["https://one.example/a.exe"])
        finally:
            vendor_service.download_with_resume = saved  # type: ignore[assignment]

    def test_only_the_installer_is_taken_out_of_a_zip_under_its_own_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            archive = folder / "station.zip"
            archive.write_bytes(zipped(**{"deep__er__Chipset_10.1.2.3.exe": b"exe", "deep__Other.exe": b"other", "..__..__SetupChipset.msi": b"msi"}))
            taken = vendor_service.take_installer(RecordingContext(folder), archive, folder, intelchipset.INSTALLER_IN_ZIP)
            # The first that fits, by its own name: the folders of the zip are not followed anywhere.
            self.assertEqual((taken, taken.read_bytes()), (folder / "Chipset_10.1.2.3.exe", b"exe"))
            self.assertEqual(sorted(item.name for item in folder.iterdir() if item.is_file() and item.suffix != ".txt"), ["Chipset_10.1.2.3.exe"])

    def test_every_installer_of_a_folder_is_asked_for_the_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "package"
            (folder / "sub").mkdir(parents=True)
            for name in ("Setup.exe", "sub/Driver.msi", "sub/data.cab", "readme.txt", "sub/driver.inf"):
                (folder / name).write_bytes(b"x")
            asked: list[str] = []

            def check(path: Path) -> tuple[str, str]:
                asked.append(path.name)
                return ("Valid", "CN=Intel Corporation, O=Intel Corporation") if path.suffix != ".cab" else ("NotSigned", "")

            context = RecordingContext(Path(temp))
            problems = vendor_service.check_installers(context, folder, "Intel Corporation", check)
            self.assertEqual(sorted(asked), ["Driver.msi", "Setup.exe", "data.cab"])
            self.assertEqual(problems, ["data.cab: signature not confirmed (NotSigned)"])
            # A package with nothing to run is no package.
            (Path(temp) / "empty").mkdir()
            self.assertEqual(vendor_service.check_installers(context, Path(temp) / "empty", "Intel Corporation", check), ["empty: no installer in the package"])
            self.assertEqual(vendor_service.check_installers(context, folder / "readme.txt", "Intel Corporation", check), ["readme.txt: no installer in the package"])


if __name__ == "__main__":
    unittest.main()

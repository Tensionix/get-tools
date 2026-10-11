"""The Microsoft Update Catalog as a vendor: the list of a kind of driver, the driver this PC wants, the package.

    runtime\\python.exe tests\\test_mscatalog.py

Started as a file, like tests\\test_cli_door.py. Nothing here asks the real catalog or reads the
devices of the machine: the pages and the devices are made up.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system_core.core import pc_devices  # noqa: E402
from system_core.services import vendor_service  # noqa: E402
from system_core.vendors import mscatalog  # noqa: E402
from system_core.vendors.mscatalog import MsCatalogProvider  # noqa: E402


def row(number: int, title: str, products: str, date: str, version: str, size: str = "1.1 MB") -> str:
    update_id = f"{number:08d}-0000-0000-0000-000000000000"
    return (f'<tr id="{update_id}_R{number}"><td class="resultsbottomBorder"></td><td><a href="#"> {title} </a></td><td>{products}</td>'
            f"<td>Drivers (Networking)</td><td> {date} </td><td>{version}</td><td><span>{size}</span> <span>1153433</span></td><td><input/></td></tr>")


def page(*rows: str, of: str = "") -> str:
    return "<html><table>" + "".join(rows) + f"</table>{of}</html>"


class FakeCatalog:
    """The catalog by its queries: what is asked, in order, and the page each query answers with."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, method: str, url: str, form: dict[str, str] | None) -> str:
        if method == "POST":
            self.asked.append("dialog:" + (form or {}).get("updateIDs", ""))
            return "var x; downloadInformation[0].files[0].url = 'https://catalog.s.download.windowsupdate.com/d/x/abc_123.cab';"
        query = unquote(url.split("q=", 1)[1])
        self.asked.append(query)
        return self.answers.get(query, page())


REALTEK_LAN = pc_devices.Device(
    device_id=r"PCI\VEN_10EC&DEV_8125&SUBSYS_E0001458&REV_05\01000000684CE00000",
    name="Realtek Gaming 2.5GbE Family Controller",
    compatible=(r"PCI\VEN_10EC&DEV_8125&REV_05", r"PCI\VEN_10EC&CC_020000", r"PCI\CC_0200"),
    driver_version="1125.21.903.2024",
)


def provider(answers: dict[str, str], devices: list[pc_devices.Device], windows: str = "24H2") -> tuple[MsCatalogProvider, FakeCatalog]:
    catalog = FakeCatalog(answers)
    return MsCatalogProvider(http=catalog, devices=lambda: devices, windows=lambda: windows), catalog


class Catalog(unittest.TestCase):
    def test_a_row_is_read_with_its_version_and_size(self) -> None:
        rows = mscatalog.parse_rows(page(
            row(1, "Realtek - Net - 1125.21.903.2024", "Windows 11 Client, version 24H2 and later, Servicing Drivers", "6/12/2026", "1125.21.903.2024"),
            row(2, "Realtek Semiconductor Corp. - MEDIA - 6.0.9499.1", "Windows 10", "3/27/2023", "n/a", "11.9 MB"),
        ))
        self.assertEqual((rows[0].version, rows[0].date, rows[0].size), ("1125.21.903.2024", "6/12/2026", "1.1 MB"))
        self.assertEqual(rows[0].update_id, "00000001-0000-0000-0000-000000000000")
        # A row that says n/a in the version column carries the version at the end of its title.
        self.assertEqual((rows[1].version, rows[1].size), ("6.0.9499.1", "11.9 MB"))

    def test_the_words_of_the_catalog_for_this_windows(self) -> None:
        self.assertEqual([mscatalog.windows_token(build) for build in (26200, 26100, 22631, 22000, 19045)],
                         ["25H2", "24H2", "22H2", "Windows 11", "Windows 10"])

    def test_the_device_of_this_pc_chooses_the_list_and_its_newest_driver_is_ticked(self) -> None:
        exact, chip = r"PCI\VEN_10EC&DEV_8125&SUBSYS_E0001458", r"PCI\VEN_10EC&DEV_8125"
        made, catalog = provider({
            exact: page(row(1, "Realtek - Net - 1125.22.110.2026", "Windows 11 Client, version 24H2 and later", "2/3/2026", "1125.22.110.2026"),
                        row(2, "Realtek - Net - 1125.22.110.2026", "Windows 10, version 1809 and later", "2/3/2026", "1125.22.110.2026"),
                        row(3, "Realtek - Net - 1125.21.903.2024", "Windows 11 Client, version 24H2 and later", "9/20/2024", "1125.21.903.2024")),
            chip: page(row(4, "Realtek - Net - 1125.20.1.2023", "Windows 10, version 1809 and later", "1/9/2023", "1125.20.1.2023")),
        }, [REALTEK_LAN])
        builds = made.builds("Realtek LAN", "Windows")
        # Asked by the ids of the device - without its revision - and never by the words of the kind.
        self.assertEqual(sorted(catalog.asked), sorted([exact, chip]))
        self.assertEqual([build.version for build in builds], ["1125.22.110.2026", "1125.21.903.2024", "1125.20.1.2023"])
        # One row a version: the one for this Windows.
        self.assertEqual(builds[0].download_id, "msc:Realtek LAN:00000001-0000-0000-0000-000000000000")
        self.assertEqual(builds[0].label, "1125.22.110.2026 - 3 Feb 2026 - 1.1 MB - latest")
        self.assertEqual(builds[0].name, "Realtek_LAN_Driver_1125.22.110.2026.cab")
        self.assertEqual([build.ticked for build in builds], [True, False, False])
        self.assertIn("installed", builds[1].label)
        self.assertIn("this PC: Realtek Gaming 2.5GbE Family Controller · 1125.21.903.2024", made.note("Realtek LAN")[0])

    def test_the_newest_driver_is_not_ticked_when_the_device_runs_it(self) -> None:
        current = pc_devices.Device(REALTEK_LAN.device_id, REALTEK_LAN.name, REALTEK_LAN.compatible, "1125.22.110.2026")
        made, _ = provider({r"PCI\VEN_10EC&DEV_8125": page(
            row(1, "Realtek - Net - 1125.22.110.2026", "Windows 11", "2/3/2026", "1125.22.110.2026"))}, [current])
        self.assertEqual([build.ticked for build in made.builds("Realtek LAN", "Windows")], [False])

    def test_without_the_device_the_general_list_is_shown_and_nothing_is_ticked(self) -> None:
        made, catalog = provider({
            "Realtek - Net": page(row(1, "Realtek - Net - 1125.22.110.2026", "Windows 11", "2/3/2026", "1125.22.110.2026"),
                                  # A Wi-Fi line of the same query: it belongs to the Wi-Fi driver.
                                  row(2, "Realtek - Net - 6001.16.127.0", "Windows 11", "3/1/2026", "6001.16.127.0")),
            "Realtek USB GbE": page(row(3, "Realtek - Net - 1153.21.20.1215", "Windows 11", "1/5/2026", "1153.21.20.1215"),
                                    # Only the line this query was asked for is taken from it.
                                    row(4, "Realtek - Net - 1168.21.20.1215", "Windows 11", "1/6/2026", "1168.21.20.1215")),
        }, [])
        builds = made.builds("Realtek LAN", "Windows")
        self.assertEqual(sorted(catalog.asked), sorted(["Realtek - Net", "Realtek Net Driver Update", "Realtek USB GbE", "Realtek USB 2.5GbE"]))
        self.assertEqual([build.version for build in builds], ["1125.22.110.2026", "1153.21.20.1215"])
        self.assertFalse(any(build.ticked for build in builds))
        self.assertIn("No Realtek network card on this PC", made.note("Realtek LAN")[0])

    def test_the_list_is_ordered_by_the_date_of_the_catalog_not_by_the_number(self) -> None:
        made, _ = provider({"Realtek Wireless LAN": page(
            row(1, "Realtek - Net - 5001.0.10.110", "Windows 11", "5/1/2025", "5001.0.10.110"),
            row(2, "Realtek - Net - 1030.44.1202.2025", "Windows 11", "2/2/2026", "1030.44.1202.2025"))}, [])
        self.assertEqual([build.version for build in made.builds("Realtek Wi-Fi", "Windows")], ["1030.44.1202.2025", "5001.0.10.110"])

    def test_a_search_goes_on_to_the_next_page_while_pages_are_full(self) -> None:
        full = page(*[row(n, f"Killer - Net - 1.0.{n}.0", "Windows 11", "1/1/2026", f"1.0.{n}.0") for n in range(25)], of="page 1 of 2")
        asked: list[str] = []

        def http(method: str, url: str, form: dict[str, str] | None) -> str:
            asked.append(url.split("?", 1)[1])
            return full if "&p=" not in url else page(row(99, "Killer - Net - 1.0.99.0", "Windows 11", "1/1/2025", "1.0.99.0"), of="page 2 of 2")

        rows = MsCatalogProvider(http=http, devices=lambda: [], windows=lambda: "24H2").search("Killer Ethernet", 4)
        self.assertEqual((len(rows), asked), (26, ["q=Killer%20Ethernet", "q=Killer%20Ethernet&p=1"]))

    def test_a_build_gives_the_file_the_catalog_names_for_its_update(self) -> None:
        made, catalog = provider({"Marvell AQtion": page(row(7, "Marvell - Net - 3.1.10.0", "Windows 11", "4/4/2026", "3.1.10.0"))}, [])
        build = made.builds("Marvell AQtion", "Windows")[0]
        self.assertEqual(made.resolve_link(build), "https://catalog.s.download.windowsupdate.com/d/x/abc_123.cab")
        self.assertIn('"updateID":"00000007-0000-0000-0000-000000000000"', catalog.asked[-1])
        # Another process ticked the row: the build is found again by its id.
        again = MsCatalogProvider(http=catalog, devices=lambda: [], windows=lambda: "24H2")
        self.assertEqual(again.build_by_id(build.download_id).name, "Marvell_AQtion_LAN_Driver_3.1.10.0.cab")


class Form(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = MsCatalogProvider._instance

    def tearDown(self) -> None:
        MsCatalogProvider._instance = self.saved

    def test_the_form_gets_a_note_the_lines_of_a_vendor_as_blocks_and_the_tick(self) -> None:
        MsCatalogProvider._instance, _ = provider({r"PCI\VEN_10EC&DEV_8125": page(
            row(1, "Realtek - Net - 1125.22.110.2026", "Windows 11", "2/3/2026", "1125.22.110.2026"),
            row(2, "Realtek - Net - 1168.21.20.1215", "Windows 11", "1/6/2026", "1168.21.20.1215"),
            row(3, "Realtek - Net - 1125.21.903.2024", "Windows 11", "9/20/2024", "1125.21.903.2024"))}, [REALTEK_LAN])
        options = vendor_service.vendor_build_options(None, {"vendor": "mscatalog", "msc_kind": "lan", "msc_lan": "Realtek LAN"})
        self.assertTrue(options[0]["note"] and options[0]["header"])
        self.assertIn("этот ПК", options[0]["label_ru"])
        self.assertEqual([option["label"] for option in options[1:] if option.get("header")], ["RTL8125 · 2.5G (2)", "RTL8111/8168 · 1G (1)"])
        self.assertEqual([bool(option.get("default")) for option in options if not option.get("header")], [True, False, False])

    def test_a_kind_without_lines_is_one_list(self) -> None:
        MsCatalogProvider._instance, _ = provider({"Marvell AQtion": page(row(7, "Marvell - Net - 3.1.10.0", "Windows 11", "4/4/2026", "3.1.10.0"))}, [])
        options = vendor_service.vendor_build_options(None, {"vendor": "mscatalog", "msc_kind": "lan", "msc_lan": "Marvell AQtion"})
        self.assertEqual([bool(option.get("header")) for option in options], [True, False])


class MachineDefaults(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = vendor_service._this_pc

    def tearDown(self) -> None:
        vendor_service._this_pc = self.saved

    def test_the_page_opens_on_a_driver_this_pc_has_a_device_for(self) -> None:
        codec = pc_devices.Device(r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0287&SUBSYS_17AA22E7&REV_1000\5&1", "Realtek(R) Audio")
        killer = pc_devices.Device(r"PCI\VEN_1969&DEV_E0B1&SUBSYS_E0001458&REV_10\4&1", "Killer E2500", (r"PCI\CC_0200",))
        vendor_service._this_pc = pc_devices.Snapshot(lambda: [codec])
        self.assertEqual(vendor_service.default_catalog_kind({}), "audio")
        self.assertEqual(vendor_service.default_catalog_driver({"id": "msc_audio"}), "Realtek Audio")
        # The kinds are asked in the order of the switches; a kind the PC has nothing of keeps the default of the manifest.
        vendor_service._this_pc = pc_devices.Snapshot(lambda: [codec, killer])
        self.assertEqual(vendor_service.default_catalog_kind({}), "lan")
        self.assertEqual(vendor_service.default_catalog_driver({"id": "msc_lan"}), "Killer Ethernet")
        self.assertEqual(vendor_service.default_catalog_driver({"id": "msc_wifi"}), "")
        vendor_service._this_pc = pc_devices.Snapshot(lambda: [])
        self.assertEqual(vendor_service.default_catalog_kind({}), "")

    def test_the_case_of_this_machine_reads(self) -> None:
        # Whatever the firmware says, it is read without an error, and a kind of case is a small number.
        for kind in pc_devices.chassis_types():
            self.assertTrue(0 < kind < 64, kind)


class Devices(unittest.TestCase):
    def test_a_device_is_matched_by_its_id_its_class_and_its_name(self) -> None:
        radio = pc_devices.Device(r"USB\VID_13D3&PID_3568\00E04C000001", "Realtek Bluetooth Adapter", (r"USB\Class_E0&SubClass_01&Prot_01",), "1.9.1051.3")
        devices = [REALTEK_LAN, radio]
        self.assertEqual(pc_devices.find([{"id": r"^PCI\\VEN_10EC&", "compatible": "CC_0200"}], devices), [REALTEK_LAN])
        self.assertEqual(pc_devices.find([{"id": r"^PCI\\VEN_10EC&", "compatible": "CC_0280"}], devices), [])
        # Realtek's radio under a module maker's own USB id: found by its name.
        self.assertEqual(pc_devices.find([{"id": r"^USB\\VID_0BDA&", "name": "bluetooth"}, {"id": r"^USB\\VID_", "name": "realtek.*bluetooth"}], devices), [radio])

    def test_the_catalog_is_asked_by_the_exact_id_then_by_the_chip(self) -> None:
        self.assertEqual(REALTEK_LAN.queries, (r"PCI\VEN_10EC&DEV_8125&SUBSYS_E0001458", r"PCI\VEN_10EC&DEV_8125"))
        codec = pc_devices.Device(r"INTELAUDIO\FUNC_01&VEN_10EC&DEV_0287&SUBSYS_17AA22E7&REV_1000\5&2A1B&0&0001", "Realtek(R) Audio")
        # Audio on Intel's own bus is the same codec on the HD Audio bus in the catalog.
        self.assertEqual(codec.queries, (r"INTELAUDIO\FUNC_01&VEN_10EC&DEV_0287&SUBSYS_17AA22E7", r"INTELAUDIO\FUNC_01&VEN_10EC&DEV_0287",
                                         r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0287&SUBSYS_17AA22E7", r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0287"))

    def test_this_machine_answers(self) -> None:
        # The real registry: whatever it holds, it reads without an error, and what is here has an id.
        for device in pc_devices.present_devices():
            self.assertRegex(device.device_id, r"^[A-Z]+\\")


class Package(unittest.TestCase):
    def test_a_signature_is_the_publishers_only_by_its_whole_name(self) -> None:
        signer = "Microsoft Windows Hardware Compatibility Publisher"
        def check(subject: str, status: str = "Valid"):
            return vendor_service.signed_by(Path("x.cab"), signer, lambda _path: (status, subject))[0]
        self.assertTrue(check(f"CN={signer}, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"))
        self.assertFalse(check(f"CN={signer} Clone, O=Somebody"))
        self.assertFalse(check(f"CN={signer}, O=Microsoft Corporation", status="HashMismatch"))
        self.assertFalse(check(""))

    def test_a_file_is_compared_by_its_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "a.bin"
            file.write_bytes(b"abc")
            self.assertEqual(vendor_service.file_sha256(file), "BA7816BF8F01CFEA414140DE5DAE2223B00361A396177A9CB410FF61F20015AD")

    def test_the_id_of_an_update_is_a_guid_and_nothing_else(self) -> None:
        made, catalog = provider({}, [])
        good = made.build_by_id("msc:Realtek LAN:00000001-0000-0000-0000-000000000000")
        self.assertEqual(good.name, "Realtek_LAN_Driver_00000001.cab")  # type: ignore[union-attr]
        for tail in (r"..\..\up", "00000001-0000-0000-0000-00000000000", "00000001-0000-0000-0000-000000000000/x", "not-a-guid", ""):
            self.assertIsNone(made.build_by_id("msc:Realtek LAN:" + tail), tail)

    def test_a_package_whose_signature_is_not_confirmed_is_not_unpacked_and_leaves_its_name(self) -> None:
        class Context:
            lines: list[str] = []

            def log(self, line: str) -> None:
                self.lines.append(line)

        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "Realtek_LAN_Driver_1.cab"
            archive.write_bytes(b"not a cabinet")
            done = vendor_service.unpack_driver_cab(Context(), archive, Path(folder), "Microsoft Windows Hardware Compatibility Publisher",
                                                    check=lambda _path: ("NotSigned", ""))
            self.assertIn("signature not confirmed (NotSigned)", done["problem"])
            # Nothing is unpacked, and the next run finds no finished file to take "from the cache".
            self.assertFalse(archive.exists())
            self.assertEqual([item.name for item in Path(folder).iterdir()], [archive.name + ".untrusted"])


if __name__ == "__main__":
    unittest.main()

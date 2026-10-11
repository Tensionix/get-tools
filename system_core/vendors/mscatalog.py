"""The Microsoft Update Catalog: the drivers Windows Update gives, taken by hand.

What Windows Update installs by itself lies in the open at catalog.update.microsoft.com: bare INF
drivers signed by Microsoft (WHQL), without the vendor's programs around them. That is the source
for the kinds of hardware nobody mirrors with a history - Realtek, MediaTek, Qualcomm, Broadcom,
Killer, Marvell - and for the Intel pieces intel.com does not hand out by a plain link.

The catalog is searched by the hardware id of the device the driver is for: the devices of this PC
(`core.pc_devices`) choose the list, and the newest driver for a device that is here is ticked,
unless the device runs it already. With no such device on the PC the general list of the kind is
shown and nothing is ticked.

One driver version is listed once per release of Windows; the row for this Windows is taken. A
vendor keeps several lines of drivers whose numbers do not compare (Realtek Wi-Fi 1030.x, 5001.x,
6001.x are different chips): the first number of a version is its line, a kind of driver names its
lines where it knows them, and the list is ordered by the catalog's date, not by the number.

Ported from Audion Setup Tools (Engine\\Downloads.ps1, source `mscatalog`; Manifests\\Downloads.psd1),
where this was worked out against the live catalog on 27-28.09.2026.
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from system_core.core import pc_devices
from system_core.vendors import PLATFORM_WINDOWS, VendorBuild

CATALOG_URL = "https://www.catalog.update.microsoft.com/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AudionGetTools"
PREFIX = "msc:"
ROWS_PER_PAGE = 25
LIST_LIMIT = 40
CACHE_TTL_SECONDS = 1800.0

# Whose name a driver of the catalog is signed with: checked on every package before it is unpacked.
SIGNER = "Microsoft Windows Hardware Compatibility Publisher"

KIND_LAN = "lan"
KIND_WIFI = "wifi"
KIND_BLUETOOTH = "bluetooth"
KIND_AUDIO = "audio"
KIND_CHIPSET = "chipset"


@dataclass(frozen=True)
class Driver:
    """A kind of driver: how the catalog is asked for it and which devices of a PC it is for."""

    name: str
    query: str  # the general search, when the PC has no such device
    match: tuple[dict[str, str], ...]  # the devices it is for (core.pc_devices.matches)
    none: tuple[str, str]  # what is said when the PC has no such device: English, Russian
    also: tuple[dict[str, Any], ...] = ()  # more searches for the general list; `only` - the lines taken from it
    exclude: tuple[str, ...] = ()  # lines of the search that are another kind of hardware
    families: dict[str, str] = field(default_factory=dict)  # a line -> the chip it is for


def _none(what_en: str, what_ru: str) -> tuple[str, str]:
    return (f"No {what_en} on this PC - the general list, nothing ticked",
            f"{what_ru} на этом ПК нет - общий список, ничего не отмечено")


# Kind -> the drivers of it, in the order of the switches. Intel's network, Wi-Fi and Bluetooth
# drivers are not here: TechPowerUp mirrors Intel's own packages with their history.
KINDS: dict[str, dict[str, Driver]] = {
    KIND_LAN: {
        "Realtek LAN": Driver(
            "Realtek LAN", "Realtek - Net",
            ({"id": r"^PCI\\VEN_10EC&", "compatible": "CC_0200"},),
            _none("Realtek network card", "Сетевой карты Realtek"),
            # "Net" is Wi-Fi too: those lines go to the Wi-Fi driver. The catalog's first pages of one
            # query miss lines now and then (USB RTL8156, the new RTL8127): the newest updates and the
            # USB adapters are asked for as well.
            also=({"query": "Realtek Net Driver Update"}, {"query": "Realtek USB GbE", "only": ("1153",)},
                  {"query": "Realtek USB 2.5GbE", "only": ("1156",)}),
            exclude=("6102", "6001", "5001", "1030", "3008", "2023", "2024"),
            families={"1168": "RTL8111/8168 · 1G", "1166": "RTL8111/8168 · 1G (DEV_8166)", "1125": "RTL8125 · 2.5G",
                      "1126": "RTL8126 · 5G", "1127": "RTL8127 · 10G", "10": "NDIS · Windows 10",
                      "1153": "USB RTL8153 · 1G", "1156": "USB RTL8156 · 2.5G"},
        ),
        "Killer Ethernet": Driver(
            "Killer Ethernet", "Killer Ethernet",
            ({"id": r"^PCI\\VEN_1969&", "compatible": "CC_0200"}, {"id": r"^PCI\\VEN_10EC&", "compatible": "CC_0200", "name": "killer"}),
            _none("Killer network card", "Сетевой карты Killer"),
        ),
        "Broadcom NetXtreme": Driver(
            "Broadcom LAN", "Broadcom NetXtreme",
            ({"id": r"^PCI\\VEN_14E4&", "compatible": "CC_0200"},),
            _none("Broadcom network card", "Сетевой карты Broadcom"),
        ),
        "Marvell AQtion": Driver(
            "Marvell AQtion LAN", "Marvell AQtion",
            ({"id": r"^PCI\\VEN_1D6A&", "compatible": "CC_0200"},),
            _none("Marvell AQtion (Aquantia) network card", "Сетевой карты Marvell AQtion (Aquantia)"),
        ),
    },
    KIND_WIFI: {
        "Realtek Wi-Fi": Driver(
            "Realtek Wi-Fi", "Realtek Wireless LAN",
            ({"id": r"^PCI\\VEN_10EC&", "compatible": "CC_0280"}, {"id": r"^USB\\VID_0BDA&", "name": r"wireless|wi-?fi|802\.11|wlan"}),
            _none("Realtek Wi-Fi adapter", "Wi-Fi адаптера Realtek"),
            # The newest PCIe Wi-Fi 6/7 lines are filed as "Net" only: taken from that query, those lines alone.
            also=({"query": "Realtek - Net", "only": ("6102", "6001", "2024")}, {"query": "Realtek Net Driver Update", "only": ("6102", "6001")}),
            exclude=("1168", "1166", "1125", "1126", "1127", "10", "1153", "1156"),
            families={"6102": "RTL8922AE · Wi-Fi 7 PCIe", "6001": "RTL8852BE · Wi-Fi 6 PCIe", "5001": "USB · Wi-Fi 6 (RTL8832/8852)",
                      "1030": "USB · Wi-Fi 4/5 (RTL8188/8192/8812/8821)", "2024": "PCIe · Wi-Fi 5 (RTL8821C/8822B/8822C/8723D)",
                      "2023": "PCIe · Wi-Fi 4/5 (RTL8723/8821/8822)", "3008": "SDIO · Wi-Fi 4/5"},
        ),
        "MediaTek Wi-Fi": Driver(
            "MediaTek Wi-Fi", "MediaTek Wi-Fi",
            ({"id": r"^PCI\\VEN_14C3&", "compatible": "CC_0280"},),
            _none("MediaTek Wi-Fi adapter", "Wi-Fi адаптера MediaTek"),
        ),
        "Qualcomm Wi-Fi": Driver(
            "Qualcomm Wi-Fi", "Qualcomm Wi-Fi",
            ({"id": r"^PCI\\VEN_(17CB|168C)&", "compatible": "CC_0280"},),
            _none("Qualcomm Wi-Fi adapter", "Wi-Fi адаптера Qualcomm"),
        ),
        "Broadcom Wi-Fi": Driver(
            "Broadcom Wi-Fi", "Broadcom Wireless",
            ({"id": r"^PCI\\VEN_14E4&", "compatible": "CC_0280"},),
            _none("Broadcom Wi-Fi adapter", "Wi-Fi адаптера Broadcom"),
        ),
        "Killer Wi-Fi": Driver(
            "Killer Wi-Fi", "Killer Wi-Fi",
            ({"id": r"^PCI\\VEN_(8086|168C|17CB)&", "compatible": "CC_0280", "name": "killer"},),
            _none("Killer Wi-Fi adapter", "Wi-Fi адаптера Killer"),
        ),
    },
    KIND_BLUETOOTH: {
        "Realtek Bluetooth": Driver(
            "Realtek Bluetooth", "Realtek Bluetooth",
            # Realtek's radio under a module maker's own USB id (AzureWave 13D3, Foxconn 0489, Lite-On 04CA): found by its name.
            ({"id": r"^USB\\VID_0BDA&", "name": "bluetooth"}, {"id": r"^USB\\VID_", "name": "realtek.*bluetooth"}),
            ("No Realtek Bluetooth on this PC - the general list, nothing ticked. Every build is made for one or two "
             "particular adapters: without the adapter the list cannot tell which",
             "Bluetooth Realtek на этом ПК нет - общий список, ничего не отмечено. Каждая сборка сделана под одно-два "
             "конкретных устройства: без адаптера выбрать по списку нельзя"),
            # 10.x is a fingerprint reader's Bluetooth service (AtansBleFP), not a driver of the radio.
            exclude=("10",),
        ),
        "MediaTek Bluetooth": Driver(
            "MediaTek Bluetooth", "MediaTek Bluetooth",
            ({"id": r"^USB\\VID_(0E8D|13D3|0489)&", "name": "bluetooth"},),
            _none("MediaTek Bluetooth", "Bluetooth MediaTek"),
        ),
        "Qualcomm Bluetooth": Driver(
            "Qualcomm Bluetooth", "Qualcomm Bluetooth",
            ({"id": r"^USB\\VID_(0CF3|04CA|0489)&", "name": "bluetooth"}, {"id": r"^(BTH|ACPI)\\.*QCOM", "name": "bluetooth"}),
            _none("Qualcomm Bluetooth", "Bluetooth Qualcomm"),
        ),
        "Broadcom Bluetooth": Driver(
            "Broadcom Bluetooth", "Broadcom Bluetooth",
            ({"id": r"^USB\\VID_0A5C&", "name": "bluetooth"},),
            _none("Broadcom Bluetooth", "Bluetooth Broadcom"),
        ),
    },
    KIND_AUDIO: {
        "Realtek Audio": Driver(
            "Realtek Audio", "Realtek High Definition Audio",
            ({"id": r"^(HDAUDIO|INTELAUDIO)\\FUNC_01&VEN_10EC&"},),
            _none("Realtek audio", "Звука Realtek"),
        ),
    },
    KIND_CHIPSET: {
        "Intel Management Engine": Driver(
            "Intel Management Engine", "Intel Management Engine Interface",
            ({"id": r"^PCI\\VEN_8086&", "compatible": "CC_0780"},),
            _none("Intel ME device", "Устройства Intel ME"),
        ),
        "Intel RST VMD": Driver(
            "Intel RST VMD", "Intel RST VMD Controller",
            ({"id": r"^PCI\\VEN_8086&", "compatible": "CC_0104"},),
            ("No Intel RAID or VMD controller on this PC: the disks run on the Windows driver, RST is not needed. Below, "
             "the general list - to give the VMD driver to Windows setup on another machine, say",
             "Контроллера Intel RAID или VMD на этом ПК нет: диски работают через драйвер Windows, RST не нужен. Ниже общий "
             "список - например, чтобы подложить драйвер VMD установщику Windows на другой машине"),
        ),
    },
}

# Every driver of every kind, product name -> Driver.
PRODUCTS: dict[str, Driver] = {name: driver for items in KINDS.values() for name, driver in items.items()}


@dataclass(frozen=True)
class Row:
    """A row of the catalog's search."""

    update_id: str
    title: str
    products: str
    date: str  # M/d/yyyy, as the catalog writes it
    version: str
    size: str


VERSION = re.compile(r"^\d+(\.\d+)+$")
ROW = re.compile(r'<tr id="([0-9a-f-]{36})_R\d+".*?</tr>', re.DOTALL)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
PAGE_OF = re.compile(r"page (\d+) of (\d+)")
UPDATE_ID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
FILE_URL = re.compile(r"downloadInformation\[\d+\]\.files\[\d+\]\.url\s*=\s*'([^']+)'")

HttpCall = Callable[[str, str, dict[str, str] | None], str]


def _default_http(method: str, url: str, form: dict[str, str] | None) -> str:
    request = Request(url, data=urlencode(form).encode("ascii") if form is not None else None, method=method)
    request.add_header("User-Agent", USER_AGENT)
    # The catalog stalls now and then; one retry, as the NVIDIA service gets.
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            with urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8", "replace")
        except HTTPError as exc:
            raise RuntimeError(f"The Microsoft catalog answered HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
    reason = getattr(last_error, "reason", None) or last_error
    raise RuntimeError(f"The Microsoft catalog did not answer: {reason}. Refresh the list to try again.") from last_error


def _text(html: str) -> str:
    import html as entities

    return entities.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()


def parse_rows(page: str) -> list[Row]:
    rows: list[Row] = []
    for match in ROW.finditer(page):
        cells = [cell for cell in (_text(item) for item in CELL.findall(match.group(0))) if cell]
        if len(cells) < 6:
            continue
        # Some rows say n/a in the version column; their title ends with it ("... - MEDIA - 6.0.9499.1").
        version = cells[4]
        if not VERSION.match(version):
            tail = re.search(r"(\d+(?:\.\d+){2,})\)?\s*$", cells[0])
            version = tail.group(1) if tail else version
        rows.append(Row(match.group(1), cells[0], cells[1], cells[3], version, re.sub(r"\s+\d+$", "", cells[5])))
    return rows


def windows_token(build: int | None = None) -> str:
    """The words of the catalog's product column that name this Windows."""
    if build is None:
        build = sys.getwindowsversion().build if sys.platform == "win32" else 0
    if build >= 26200:
        return "25H2"
    if build >= 26100:
        return "24H2"
    if build >= 22621:
        return "22H2"
    return "Windows 11" if build >= 22000 else "Windows 10"


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(version or "")))


def _date(row: Row) -> datetime:
    try:
        return datetime.strptime(row.date, "%m/%d/%Y")
    except ValueError:
        return datetime.min


def short_date(row: Row) -> str:
    when = _date(row)
    return when.strftime("%d %b %Y").lstrip("0") if when != datetime.min else row.date


def line_of(version: str) -> str:
    """The line of drivers a version belongs to: its first number."""
    return str(version or "").split(".", 1)[0]


class MsCatalogProvider:
    id = "mscatalog"
    name = "Microsoft Update Catalog"
    platforms = (PLATFORM_WINDOWS,)
    # The catalog's own file names are a guid and a hash: a package is named by its driver and version.
    names_its_files = True
    signer = SIGNER

    _instance: "MsCatalogProvider | None" = None

    def __init__(self, http: HttpCall | None = None, devices: Callable[[], list[pc_devices.Device]] | None = None,
                 windows: Callable[[], str] | None = None) -> None:
        self._http: HttpCall = http or _default_http
        self._snapshot = pc_devices.Snapshot(devices or pc_devices.present_devices)
        self._windows = windows or windows_token
        self._cache: dict[str, tuple[float, list[VendorBuild]]] = {}
        self._builds: dict[str, VendorBuild] = {}
        self._notes: dict[str, tuple[str, str]] = {}
        self._running: dict[str, list[str]] = {}

    @classmethod
    def instance(cls) -> "MsCatalogProvider":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ----- the catalog -----

    def search(self, query: str, pages: int = 1) -> list[Row]:
        """The catalog answers 25 rows a page, newest first. A broad query (Realtek Wireless LAN: a
        thousand rows) needs several pages, or the list stops at a few versions of last year."""
        rows: list[Row] = []
        for page in range(max(1, pages)):
            url = CATALOG_URL + "Search.aspx?q=" + quote(query) + (f"&p={page}" if page else "")
            html = self._http("GET", url, None)
            found = parse_rows(html)
            rows.extend(found)
            count = PAGE_OF.search(html)
            if len(ROW.findall(html)) < ROWS_PER_PAGE or (count and int(count.group(1)) >= int(count.group(2))):
                break
        return rows

    def search_all(self, asks: list[tuple[str, int]]) -> list[list[Row]]:
        """Several searches side by side, their answers in the order asked: each is seconds of the catalog's own time."""
        if len(asks) < 2:
            return [self.search(query, pages) for query, pages in asks]
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(4, len(asks))) as pool:
            return list(pool.map(lambda ask: self.search(ask[0], ask[1]), asks))

    def file_url(self, update_id: str) -> str:
        ids = json.dumps([{"size": 0, "languages": "", "uidInfo": update_id, "updateID": update_id}], separators=(",", ":"))
        dialog = self._http("POST", CATALOG_URL + "DownloadDialog.aspx", {"updateIDs": ids})
        found = FILE_URL.search(dialog)
        if not found:
            raise RuntimeError(f"The Microsoft catalog gave no link for update {update_id}.")
        return found.group(1)

    # ----- the list of a kind of driver -----

    def devices_of(self, product: str) -> list[pc_devices.Device]:
        driver = PRODUCTS.get(product)
        return pc_devices.find(list(driver.match), self._snapshot.devices()) if driver else []

    def _load(self, product: str) -> list[VendorBuild]:
        driver = PRODUCTS[product]
        devices = self.devices_of(product)
        queries: list[str] = []
        for device in devices:
            queries += [query for query in device.queries if query not in queries]
        # A row without a version tells nothing to choose by.
        found = [row for rows in self.search_all([(query, 2) for query in queries]) for row in rows if VERSION.match(row.version)]
        for_machine = bool(found)
        if not found:
            # The general list of the kind, and more queries beside it - each for some lines only when it says so.
            answers = self.search_all([(driver.query, 4)] + [(str(also["query"]), 2) for also in driver.also])
            found = [row for row in answers[0] if VERSION.match(row.version)]
            for also, rows in zip(driver.also, answers[1:]):
                only = tuple(also.get("only") or ())
                found += [row for row in rows if VERSION.match(row.version) and (not only or line_of(row.version) in only)]
        if driver.exclude:
            found = [row for row in found if line_of(row.version) not in driver.exclude]

        # One row per version: the entry for this Windows, else any.
        token = self._windows()
        by_version: dict[str, list[Row]] = {}
        for row in found:
            by_version.setdefault(row.version, []).append(row)
        versions = [next((row for row in rows if token in row.products), rows[0]) for rows in by_version.values()]
        # Newest first by the catalog's date, not by the number: 5001 of 2025 is not newer than 1030 of 2026.
        versions.sort(key=lambda row: (_date(row), version_key(row.version)), reverse=True)
        versions = versions[:LIST_LIMIT]

        installed = [device.driver_version for device in devices if device.driver_version]
        newest = versions[0].version if versions else ""
        have = max((version_key(item) for item in installed), default=())
        # Ticked: the newest driver for this PC's device, unless the device runs it already.
        tick = for_machine and bool(newest) and newest not in installed and (not have or version_key(newest) > have)

        what = "; ".join(sorted({device.name + (f" · {device.driver_version}" if device.driver_version else "") for device in devices}))
        # For the badge of versions: the drivers this PC's devices run - when the list is the one for them.
        self._running[product] = sorted(set(installed)) if for_machine else []
        if for_machine:
            self._notes[product] = (f"Microsoft Update Catalog · WHQL drivers without the vendor's programs · this PC: {what}",
                                    f"Каталог обновлений Microsoft · драйверы WHQL без программ производителя · этот ПК: {what}")
        elif devices:
            self._notes[product] = (f"Microsoft Update Catalog · no catalog driver for this PC's devices ({what}) - the general list, nothing ticked",
                                    f"Каталог обновлений Microsoft · для устройств этого ПК ({what}) драйверов в каталоге нет - общий список, ничего не отмечено")
        else:
            self._notes[product] = (f"Microsoft Update Catalog · {driver.none[0]}", f"Каталог обновлений Microsoft · {driver.none[1]}")

        builds: list[VendorBuild] = []
        file_stem = re.sub(r'[\\/:*?"<>|\s]+', "_", driver.name + " Driver")
        for row in versions:
            marks = [row.size]
            if row.version in installed:
                marks.append("installed")
            if row.version == newest:
                marks.append("latest")
            build = VendorBuild(
                vendor=self.id,
                product=product,
                version=row.version,
                platform=PLATFORM_WINDOWS,
                date=short_date(row),
                download_id=f"{PREFIX}{product}:{row.update_id}",
                name=f"{file_stem}_{row.version}.cab",
                notes=" - ".join(mark for mark in marks if mark),
                ticked=tick and row.version == newest,
            )
            self._builds[build.download_id] = build
            builds.append(build)
        return builds

    def note(self, product: str) -> tuple[str, str]:
        """What stands over the list, English and Russian: where it comes from and which device of this PC it is for."""
        return self._notes.get(product, ("", ""))

    # The numbers of the catalog are the numbers Windows shows: a driver the list does not hold is still compared.
    numbers_of_windows = True

    def running(self, product: str) -> list[str]:
        """The versions of the driver this PC's devices run, when the list of the kind is the one for them."""
        return list(self._running.get(product, []))

    def line(self, product: str, version: str) -> str:
        """The line of drivers a version belongs to, where the kind has lines; '' where it has none."""
        return line_of(version) if product in PRODUCTS and PRODUCTS[product].families else ""

    def lines(self, product: str) -> dict[str, str]:
        """The lines of drivers the kind names: the first number of a version -> the chip it is for."""
        return dict(PRODUCTS[product].families) if product in PRODUCTS else {}

    # ----- VendorProvider -----

    def products(self, platform: str) -> list[str]:
        return list(PRODUCTS) if platform in self.platforms else []

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        del selection  # the kind switch only picks which product field is read
        if platform not in self.platforms or product not in PRODUCTS:
            return []
        now = time.monotonic()
        cached = self._cache.get(product)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        builds = self._load(product)
        self._cache[product] = (now, builds)
        return builds

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        wanted = str(download_id or "").strip()
        if wanted in self._builds:
            return self._builds[wanted]
        if not wanted.startswith(PREFIX):
            return None
        product, _, update_id = wanted[len(PREFIX):].rpartition(":")
        # The id of an update is a GUID and nothing else: it goes into a request and into the name of a file.
        if product not in PRODUCTS or not UPDATE_ID.match(update_id):
            return None
        # A run asks for a build the list of another process ticked: the list is read again, and a
        # row that has left it since still downloads by its id - the id is all the catalog needs.
        self.builds(product, PLATFORM_WINDOWS)
        if wanted in self._builds:
            return self._builds[wanted]
        stem = re.sub(r'[\\/:*?"<>|\s]+', "_", PRODUCTS[product].name + " Driver")
        return VendorBuild(vendor=self.id, product=product, version=update_id[:8], platform=PLATFORM_WINDOWS, date="",
                           download_id=wanted, name=f"{stem}_{update_id[:8]}.cab")

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        del registration  # the catalog never asks for a form
        return self.file_url(build.download_id.rsplit(":", 1)[-1])

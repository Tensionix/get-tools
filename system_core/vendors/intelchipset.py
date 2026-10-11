"""Intel Chipset Device Software - the chipset INF: the package that names the devices of an Intel board to Windows.

intel.com hands it out from a page that answers 403 to many addresses and keeps the newest version
only. Every package from 10.0.13 on, each with its SHA256 and a mirror, is kept on GitHub by
FirstEverTech's Universal Intel Chipset Updater (MIT). Its base of INF files says which package
last carries the INF of each Intel device: the Intel devices of this PC (`core.pc_devices`) choose
the package, and it is ticked.

From 10.1.20378 on Intel ships the package as an MSI, and the MSI inside FirstEverTech's zips does
not pass Intel's signature (HashMismatch, seen in Audion Setup Tools on 27.09.2026). Those
versions come from Intel's own server where its address is known (`config\\vendor_intel_chipset.yaml`),
then from the web archive's copy of that address, then from station-drivers.com - Intel's signed
Chipset_<version>.exe out of their zip. A version of that time with none of the three is not listed.

Whatever the source, the download job checks every installer for the signature of Intel Corporation.

Ported from Audion Setup Tools (Engine\\Downloads.ps1, source `intelchipset`; Manifests\\Downloads.psd1).
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from system_core.core import pc_devices
from system_core.core.config import load_yaml_or_json
from system_core.vendors import PLATFORM_WINDOWS, BuildSource, VendorBuild

DATA_URL = "https://raw.githubusercontent.com/FirstEverTech/Universal-Intel-Chipset-Updater/main/data/"
PACKAGES_FILE = "intel-chipset-infs-download.txt"
INF_BASE_FILE = "intel-chipset-infs-latest.md"

STATION_SITE = "https://www.station-drivers.com/"
STATION_REMOSITORY = STATION_SITE + "index.php/en-us/component/remository/"
STATION_FOLDER = STATION_REMOSITORY + "Drivers/Intel/Chipset-Device-Software/Serie-10.0.x--and--10.1.1.x/"
STATION_PAGES = 6
STATION_PAGE_LINK = re.compile(r'href="([^"]*Intel-Chipset-Device-Software-Version-([\d.]+)/[^"]*)"')
# Out of a station-drivers zip only Intel's installer is taken: the INF and CAT files beside it they unpacked themselves.
INSTALLER_IN_ZIP = r"(Chipset_.*|SetupChipset.*)\.(exe|msi)"

# The web archive's copy of an address, as the file itself ("id_"), the capture nearest to 2026.
ARCHIVE_COPY = "https://web.archive.org/web/2026id_/"

# From this version on the package is an MSI, and FirstEverTech's copy of it does not pass Intel's signature.
MSI_FROM = (10, 1, 20378, 0)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AudionGetTools"
PRODUCT = "Intel Chipset Device Software"
PREFIX = "icd:"
SIGNER = "Intel Corporation"
CACHE_TTL_SECONDS = 1800.0

HttpCall = Callable[[str], str]


@dataclass(frozen=True)
class Package:
    """One package of the chipset INF and where it can be taken from."""

    version: str  # 10.1.20658.8883, a server's 10.1.20314.8688S
    date: str  # 24/07/2026
    variant: str  # Consumer, Server
    url: str = ""
    backup: str = ""
    sha256: str = ""
    name: str = ""  # the file it is saved as
    page: str = ""  # its page on station-drivers


@dataclass(frozen=True)
class Match:
    """A row of the base of INF files that names a device of this PC."""

    platform: str
    inf: str
    package: str  # the package that last carries the INF
    inf_version: str
    devices: tuple[str, ...]


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(version or "")))


def is_msi_time(version: str) -> bool:
    return version_key(version) >= MSI_FROM


# A version of a package as the base writes it: 10.0.13.0, 10.1.20658.8883, a server's 10.1.20314.8688S.
VERSION = re.compile(r"^\d+(\.\d+){2,4}[A-Za-z]?$")


def parse_packages(text: str) -> list[Package]:
    """`intel-chipset-infs-download.txt`: blocks of `Key = value` lines, a blank line between two packages.

    The file is kept by somebody else, and its words go into file names (`SetupChipset_<version>.exe`,
    the last piece of the address): a record whose version is no version, or whose file name is no
    plain name, is not listed.
    """
    from system_core.services.vendor_service import safe_file_name

    packages: list[Package] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.lstrip("﻿")):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            found = re.match(r"^\s*(\w+)\s*=\s*(.*?)\s*$", line)
            if found:
                fields[found.group(1)] = found.group(2)
        if not fields.get("Link") or "," not in fields.get("INFVer", ""):
            continue
        date, version = fields["INFVer"].split(",", 1)
        name = fields["Link"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        if not VERSION.match(version.strip()):
            continue
        try:
            safe_file_name(name)
        except RuntimeError:
            continue
        packages.append(Package(
            version=version.strip(), date=date.strip(), variant=fields.get("Variant", ""),
            url=fields["Link"], backup=fields.get("Backup", ""), sha256=fields.get("SHA256", "").upper(),
            name=name,
        ))
    return packages


def intel_device_ids(devices: list[pc_devices.Device]) -> set[str]:
    """The Intel PCI devices of a PC as the base names them: the four digits of DEV_."""
    found: set[str] = set()
    for device in devices:
        match = re.match(r"PCI\\VEN_8086&DEV_([0-9A-F]{4})", device.device_id, re.IGNORECASE)
        if match:
            found.add(match.group(1).upper())
    return found


def parse_matches(text: str, ids: set[str]) -> list[Match]:
    """`intel-chipset-infs-latest.md`: under each `#### Platform` a table `| INF | Package | Version | Date | HWIDs |`."""
    matches: list[Match] = []
    platform = ""
    for line in text.splitlines():
        heading = re.match(r"^####\s+(.+?)\s*$", line)
        if heading:
            platform = heading.group(1)
            continue
        if not line.startswith("|") or re.match(r"^\|\s*(INF|-)", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        hits = tuple(item for item in re.split(r",\s*", cells[4]) if item.upper() in ids)
        if hits:
            matches.append(Match(platform, cells[0], cells[1], cells[2], hits))
    return matches


def platform_name(name: str) -> str:
    """"AlderLake EOL" and "AlderLake_Extension-Dmasec" are AlderLake too."""
    return re.sub(r"(?i)([ _-]+(EOL|Extension-Dmasec|Extension))+$", "", name)


def parse_station(html: str) -> dict[str, str]:
    """A page of station-drivers' folder: version -> the page of its file."""
    from html import unescape

    found: dict[str, str] = {}
    for match in STATION_PAGE_LINK.finditer(html):
        found.setdefault(match.group(2), urljoin(STATION_SITE, unescape(match.group(1))))
    return found


def short_date(date: str) -> str:
    try:
        return datetime.strptime(date, "%d/%m/%Y").strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return date


def _default_http(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            with urlopen(request, timeout=45) as response:
                return response.read().decode("utf-8-sig", "replace")
        except HTTPError as exc:
            raise RuntimeError(f"{request.host} answered HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
    reason = getattr(last_error, "reason", None) or last_error
    raise RuntimeError(f"{request.host} did not answer: {reason}. Refresh the list to try again.") from last_error


class IntelChipsetProvider:
    id = "intelchipset"
    name = "Intel"
    platforms = (PLATFORM_WINDOWS,)
    # One address serves several versions (SetupChipset.exe): a package is named by its version.
    names_its_files = True
    signer = SIGNER

    _instance: "IntelChipsetProvider | None" = None

    def __init__(self, http: HttpCall | None = None, devices: Callable[[], list[pc_devices.Device]] | None = None,
                 official_path: Path | None = None) -> None:
        self._http: HttpCall = http or _default_http
        self._snapshot = pc_devices.Snapshot(devices or pc_devices.present_devices)
        self._official_path = official_path
        self._cache: tuple[float, list[VendorBuild]] | None = None
        self._packages: dict[str, Package] = {}
        self._builds: dict[str, VendorBuild] = {}
        self._note: tuple[str, str] = ("", "")

    @classmethod
    def instance(cls) -> "IntelChipsetProvider":
        if cls._instance is None:
            root = Path(__file__).resolve().parents[2]
            cls._instance = cls(official_path=root / "config" / "vendor_intel_chipset.yaml")
        return cls._instance

    # ----- the sources -----

    def official(self) -> dict[str, str]:
        """Intel's own addresses by version, from the file the owner keeps; nothing when it does not read."""
        if not self._official_path:
            return {}
        try:
            data = load_yaml_or_json(self._official_path)
        except Exception:  # noqa: BLE001 - without the file the other sources still stand
            return {}
        listed = data.get("official") if isinstance(data, dict) else None
        if not isinstance(listed, dict):
            return {}
        return {str(version).strip(): str(url).strip() for version, url in listed.items() if str(url or "").strip()}

    def station_versions(self) -> dict[str, str]:
        """What station-drivers has: its folder page by page until a page adds nothing."""
        found: dict[str, str] = {}
        for page in range(1, STATION_PAGES + 1):
            new = {version: url for version, url in parse_station(self._http(f"{STATION_FOLDER}orderby,2/page,{page}/lang,en-us/")).items()
                   if version not in found}
            if not new:
                break
            found.update(new)
        return found

    def station_link(self, page: str) -> tuple[str, str]:
        """The file of a station-drivers page and the Referer it is given to: the file comes only through a start page."""
        file_id = re.search(r"func-startdown/(\d+)", self._http(page))
        if not file_id:
            raise RuntimeError(f"The station-drivers page has no file link: {page}")
        start = f"{STATION_REMOSITORY}func-startdown/{file_id.group(1)}/lang,en-us/"
        link = re.search(r"func-download/\d+/chk,[0-9a-f]+/no_html,1/lang,en-us/", self._http(start))
        if not link:
            raise RuntimeError(f"station-drivers gave no file link: {page}")
        return STATION_REMOSITORY + link.group(0), start

    # ----- the list -----

    def _load(self) -> list[VendorBuild]:
        from concurrent.futures import ThreadPoolExecutor

        ids = intel_device_ids(self._snapshot.devices())

        def station() -> dict[str, str]:
            try:
                return self.station_versions()
            except Exception:  # noqa: BLE001 - one source of three; the list stands without it
                return {}

        # Three sites, each its own seconds: asked side by side.
        with ThreadPoolExecutor(max_workers=3) as pool:
            packages_text = pool.submit(self._http, DATA_URL + PACKAGES_FILE)
            base_text = pool.submit(self._http, DATA_URL + INF_BASE_FILE) if ids else None
            station_pages = pool.submit(station)
            catalog = parse_packages(packages_text.result())
            matches = parse_matches(base_text.result(), ids) if base_text is not None else []
            on_station = station_pages.result()
        official = self.official()

        packages: list[Package] = []
        for package in catalog:
            if not is_msi_time(package.version):
                packages.append(package)
            elif package.version in official or package.version in on_station:
                intel = official.get(package.version, "")
                packages.append(replace(package, url=intel, backup=ARCHIVE_COPY + intel if intel else "", sha256="",
                                        name=f"SetupChipset_{package.version}.exe", page=on_station.get(package.version, "")))
        packages.sort(key=lambda package: (version_key(package.version), package.variant), reverse=True)

        for_machine = sorted({match.package for match in matches}, key=version_key, reverse=True)
        pick = next((package for package in packages if package.version in for_machine), None)
        platforms: list[str] = []
        for match in matches:
            name = platform_name(match.platform)
            if name.lower() not in [item.lower() for item in platforms]:
                platforms.append(name)
        if platforms:
            where, wanted = ", ".join(sorted(platforms, key=str.lower)), ", ".join(for_machine)
            self._note = (f"Intel Chipset Device Software · this PC: {where} · package {wanted}",
                          f"Intel Chipset Device Software · этот ПК: {where} · нужен пакет {wanted}")
        else:
            self._note = ("Intel Chipset Device Software · no Intel device the INF base knows on this PC - nothing ticked",
                          "Intel Chipset Device Software · устройств Intel, которые знает база INF, на этом ПК нет - ничего не отмечено")

        self._packages, self._builds = {}, {}
        builds: list[VendorBuild] = []
        for package in packages:
            build = VendorBuild(
                vendor=self.id,
                product=PRODUCT,
                version=package.version + (f" · {package.variant}" if package.variant and package.variant != "Consumer" else ""),
                platform=PLATFORM_WINDOWS,
                date=short_date(package.date),
                download_id=PREFIX + package.version,
                name=package.name,
                notes="this PC" if package.version in for_machine else "",
                ticked=package is pick,
            )
            self._packages[build.download_id] = package
            self._builds[build.download_id] = build
            builds.append(build)
        return builds

    def note(self, product: str) -> tuple[str, str]:
        """What stands over the list, English and Russian: the platform of this PC and the package it wants."""
        return self._note if product == PRODUCT else ("", "")

    # ----- VendorProvider -----

    def products(self, platform: str) -> list[str]:
        return [PRODUCT] if platform in self.platforms else []

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        del selection
        if platform not in self.platforms or product != PRODUCT:
            return []
        now = time.monotonic()
        if self._cache and now - self._cache[0] < CACHE_TTL_SECONDS:
            return self._cache[1]
        builds = self._load()
        self._cache = (now, builds)
        return builds

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        wanted = str(download_id or "").strip()
        if wanted not in self._builds and wanted.startswith(PREFIX):
            # A run asks for a build the list of another process ticked: the list is read again.
            self.builds(PRODUCT, PLATFORM_WINDOWS)
        return self._builds.get(wanted)

    def sources(self, build: VendorBuild) -> list[BuildSource]:
        """Where the package comes from, in the order to try: its address, the mirror or the archive's copy, station-drivers."""
        package = self._packages.get(build.download_id)
        if package is None:
            return []
        found: list[BuildSource] = []
        for url in (package.url, package.backup):
            if url:
                host = Request(url).host or url
                found.append(BuildSource(host=host, name=package.name, link=lambda url=url: (url, ""), sha256=package.sha256))
        if package.page:
            found.append(BuildSource(host=Request(package.page).host or "station-drivers", name=f"intel_chipset_{package.version}_station-drivers.zip",
                                     link=lambda page=package.page: self.station_link(page), installer=INSTALLER_IN_ZIP))
        return found

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        """The first address a person can open: the file itself, else the page of station-drivers it is behind."""
        del registration
        package = self._packages.get(build.download_id)
        if package is None:
            raise RuntimeError(f"Unknown build id {build.download_id}; refresh the version list.")
        return package.url or package.backup or package.page

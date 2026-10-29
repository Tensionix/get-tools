"""TechPowerUp's download catalog: drivers and hardware utilities with every version they keep.

TechPowerUp mirrors the vendors' own packages and keeps the history: each
product page lists every version with its date, size and checksum, and a file
is handed out through the site's own signed link (three-step flow in
`techpowerup_service`). The catalog is grouped into kinds the way the site's
sections go: drivers, tuning utilities, monitoring, benchmarks and video BIOS
flashers. NVIDIA drivers and the DLSS libraries also live there, but they stay
on the NVIDIA tab where their marks are.

A version is one card even when the site has two files for it: the installer
is the card's own id, the portable build (or zip) rides along as
`portable_id`, so the card shows one arrow for each and the list keeps one
height.
"""
from __future__ import annotations

import dataclasses
import re
import time
from collections.abc import Callable
from typing import Any

from system_core.core import pc_devices
from system_core.services import techpowerup_service
from system_core.vendors import PLATFORM_WINDOWS, VendorBuild

TPU_PREFIX = "tpu:"
CACHE_TTL_SECONDS = 1800.0

KIND_DRIVERS = "drivers"
KIND_TOOLS = "tools"
KIND_MONITOR = "monitor"
KIND_BENCH = "bench"
KIND_BIOS = "bios"

# Kind -> (product name -> TechPowerUp download slug). Order is the order of the switches.
KINDS: dict[str, dict[str, str]] = {
    KIND_DRIVERS: {
        "AMD Radeon Graphics": "amd-radeon-graphics-drivers",
        "AMD Ryzen Chipset": "amd-ryzen-chipset-drivers",
        "Intel Graphics (Arc, iGPU)": "intel-graphics-drivers",
        "Intel Wi-Fi": "intel-wireless-networking-wifi-adapter-drivers",
        "Intel Bluetooth": "intel-wireless-bluetooth-drivers",
        "Intel Ethernet": "intel-ethernet-networking-drivers",
        "Intel NPU": "intel-npu-drivers",
        "Qualcomm Snapdragon X Graphics": "qualcomm-snapdragon-x-graphics-drivers",
    },
    KIND_TOOLS: {
        "Display Driver Uninstaller": "display-driver-uninstaller-ddu",
        "NVCleanstall": "techpowerup-nvcleanstall",
        "NVIDIA Profile Inspector": "nvidia-profile-inspector",
        "ThrottleStop": "techpowerup-throttlestop",
        "DRAM Calculator for Ryzen": "ryzen-dram-calculator",
        "Samsung Magician": "samsung-magician-ssd-management-utility",
        "Visual C++ Runtimes AIO": "visual-c-redistributable-runtime-package-all-in-one",
        "MemTest64": "techpowerup-memtest64",
    },
    KIND_MONITOR: {
        "GPU-Z": "techpowerup-gpu-z",
        "CPU-Z": "cpu-z",
        "AIDA64 Extreme": "aida64-extreme",
        "ZenTimings": "amd-ryzen-zen-timings",
        "Real Temp": "techpowerup-real-temp",
    },
    KIND_BENCH: {
        "3DMark": "futuremark-3dmark-timespy-raytracing",
        "PCMark 10": "futuremark-pcmark-10",
        "Cinebench": "maxon-cinebench",
        "FurMark": "furmark",
        "Prime95": "prime95",
        "Unigine Superposition": "unigine-superposition",
        "Unigine Heaven": "unigine-heaven-dx11-benchmark",
        "GravityMark": "gravitymark-gpu-benchmark",
        "Linpack Xtreme": "linpack-xtreme",
        "ATTO Disk Benchmark": "atto-disk-benchmark",
    },
    KIND_BIOS: {
        "NVIDIA NVFlash": "nvidia-nvflash",
        "AMDVBFlash": "ati-atiflash",
    },
}

# Which devices of a PC a driver is for (core.pc_devices.matches), as Audion Setup Tools tells them
# (Manifests\Downloads.psd1, Detect): its newest release is ticked only on a PC that has one. A
# product not named here is for any PC.
DETECT: dict[str, tuple[dict[str, str], ...]] = {
    "AMD Radeon Graphics": ({"id": r"^PCI\\VEN_1002&", "compatible": "CC_03"},),
    "AMD Ryzen Chipset": ({"id": r"^PCI\\VEN_1022&", "compatible": "CC_0600"},),
    "Intel Graphics (Arc, iGPU)": ({"id": r"^PCI\\VEN_8086&", "compatible": "CC_03"},),
    "Intel Wi-Fi": ({"id": r"^PCI\\VEN_8086&", "compatible": "CC_0280"},),
    "Intel Bluetooth": ({"id": r"^USB\\VID_8087&", "name": "bluetooth"},),
    "Qualcomm Snapdragon X Graphics": ({"id": r"^ACPI\\(VEN_)?QCOM", "name": "adreno|graphics|display"},),
}

NOT_A_RELEASE = re.compile(r"\b(beta|preview|alpha|rc\d*)\b", re.IGNORECASE)


def same_version(installed: str, listed: str) -> bool:
    """Whether the driver Windows names `installed` is the release the page names `listed`.

    Windows' number is longer than the page's: Intel graphics 32.0.101.7085 is TechPowerUp's
    101.7085 (its tail), Intel Wi-Fi 23.160.0.4 is 23.160.0 (its head). Numbers of another
    scheme (AMD's driver 32.x against Adrenalin 25.x) are never the same.
    """
    found = re.match(r"\s*v?(\d+(?:\.\d+)*)", str(listed or ""))
    have = [part for part in str(installed or "").split(".") if part]
    want = found.group(1).split(".") if found else []
    if len(want) < 2 or len(have) < len(want):
        return False
    return have[:len(want)] == want or have[-len(want):] == want


# Intel's graphics drivers go in lines since Intel split them: Arc and Core Ultra on 101.8xxx and up,
# the iGPUs of Core 11th-14th gen (Iris Xe, UHD 7xx) on 101.7xxx, Core 7th-10th gen on 101.2xxx; the 6th
# gen (HD 5xx) is on none of them. The newest of the page may not run the GPU of this PC at all: it is
# offered the newest of its own line.
INTEL_GRAPHICS = "Intel Graphics (Arc, iGPU)"


def intel_graphics_line(version: str) -> str:
    """The line of an Intel graphics driver by its number - the page's 101.7092 or Windows' 32.0.101.7092;
    '' for the drivers from before the split, which were for every GPU."""
    found = re.search(r"\b101\.(\d{4})\b", str(version or ""))
    if not found:
        return ""
    build = int(found.group(1))
    if build >= 8000:
        return "arc"
    if build >= 7000:
        return "core11-14"
    if 2000 <= build < 3000:
        return "core7-10"
    return ""


def intel_graphics_device_line(name: str, driver_version: str) -> str:
    """The line an Intel GPU of this PC is on, by the name Windows gives it. 101.70xx came out before the
    split for every GPU too, so the driver it runs decides only where it says Arc or Core 7th-10th gen;
    '' - unknown, measured against the whole list as before."""
    text = str(name or "").lower()
    if re.search(r"\barc\b", text):
        return "arc"
    if re.search(r"iris(\(r\))? xe|uhd graphics 7\d\d", text):
        return "core11-14"
    if re.search(r"\b(uhd|hd) graphics 6\d\d|iris(\(r\))? plus", text):
        return "core7-10"
    by_driver = intel_graphics_line(driver_version)
    return by_driver if by_driver in {"arc", "core7-10"} else ""


# AMD keeps its Radeon drivers in lines too. Since Adrenalin 25.10.2 (October 2025) RDNA 3 and 4 (RX 7000,
# RX 9000, Radeon 7xxM / 8xxM) take every release, RDNA 1 and 2 (RX 5000, RX 6000, Radeon 6xxM) only the
# combined package of a release: TechPowerUp keeps both files of such a version and says "For older GPUs"
# over the combined one (-c, about 1.6 GB; -b, about 0.9 GB, is for RDNA 3 and 4 only). Polaris and Vega
# (RX 400 / 500, RX Vega, Radeon VII, the graphics of Ryzen up to 5000) have had a legacy driver of their own
# since November 2023, and it is not on this page at all.
AMD_GRAPHICS = "AMD Radeon Graphics"
AMD_LINES = ("rdna1-2", "rdna3-4", "polaris-vega")
# The graphics of Ryzen that Windows names "AMD Radeon(TM) Graphics" and nothing more, by the chip.
AMD_IGPU_LINES: dict[str, str] = {
    # Raven Ridge, Picasso, Renoir, Cezanne, Lucienne, Barcelo
    "15DD": "polaris-vega", "15D8": "polaris-vega", "1636": "polaris-vega", "1638": "polaris-vega", "164C": "polaris-vega", "15E7": "polaris-vega",
    # Raphael (Ryzen 7000 / 9000 desktop), Rembrandt, Mendocino, Van Gogh
    "164E": "rdna1-2", "1681": "rdna1-2", "1506": "rdna1-2", "163F": "rdna1-2",
    # Phoenix, Phoenix 2, Strix Point, Strix Halo
    "15BF": "rdna3-4", "15C8": "rdna3-4", "150E": "rdna3-4", "1586": "rdna3-4",
}


def amd_graphics_device_line(name: str, device_id: str) -> str:
    """The line a Radeon of this PC is on, by the name Windows gives it, and by the chip for the graphics of a
    Ryzen named "AMD Radeon(TM) Graphics" alone; '' - unknown, measured against the whole list as before."""
    text = str(name or "").lower()
    if re.search(r"\brx\s*(7[6-9]\d\d|9\d{3})\b|\b(740|760|780|8[2-9]0)m\b|\b80[4-6]0s\b", text):
        return "rdna3-4"
    if re.search(r"\brx\s*(5[3-7]\d\d|6[3-9]\d\d)\b|\b(610|660|680)m\b", text):
        return "rdna1-2"
    if re.search(r"\brx\s*(4[6-8]0|5[4-9]0)x?\b|\bvega\b|radeon(\s*\(tm\))?\s*vii\b", text):
        return "polaris-vega"
    chip = re.search(r"DEV_([0-9A-F]{4})", str(device_id or ""), re.IGNORECASE)
    return AMD_IGPU_LINES.get(chip.group(1).upper(), "") if chip else ""


def amd_package_line(file_name: str, kind: str) -> str:
    """Which Radeons a package of the page installs on: 'rdna3-4' - RDNA 3 and 4 only; 'all' - every RDNA
    (the combined package, and the packages from before the lines)."""
    if "older gpus" in str(kind or "").lower():
        return "all"
    return "rdna3-4" if re.search(r"rdna3|-b\.exe$", str(file_name or ""), re.IGNORECASE) else "all"


# Every product of every kind, product name -> slug.
PRODUCTS: dict[str, str] = {name: slug for items in KINDS.values() for name, slug in items.items()}

# Products that ship as one self-contained exe and never install anything:
# TechPowerUp does not label their files, so the product says it for them.
PORTABLE_ONLY: frozenset[str] = frozenset({"GPU-Z", "NVCleanstall", "MemTest64"})
PORTABLE_KIND = re.compile(r"\b(portable|standalone|zip)\b", re.IGNORECASE)
# File kinds that only say installer-or-portable; anything else is a flavour worth showing.
PLAIN_KINDS: frozenset[str] = frozenset({"installer", "portable", "zip archive", "zip package", "exe package"})


def is_portable_file(product: str, kind: str, file_name: str) -> bool:
    """A file that runs from its folder: TechPowerUp says 'Portable' or 'ZIP', or the product is portable by nature.

    The site names the kind only where a product has several files
    ('Installer' / 'Portable', 'EXE Package' / 'ZIP Package'); a lone zip or a
    single self-contained exe comes with no kind at all.
    """
    if PORTABLE_KIND.search(str(kind or "")):
        return True
    return product in PORTABLE_ONLY or str(file_name or "").lower().endswith(".zip")


def flavour(kind: str) -> str:
    """The part of a file kind that tells builds of one version apart ('asus rog themed'), if any."""
    text = str(kind or "").strip().lower()
    return "" if not text or text in PLAIN_KINDS else text


# A file for another system: the page of a benchmark keeps the Mac and Linux builds beside the Windows ones
# (Cinebench2026_win_x86_64.zip, Cinebench2026_win_arm64.zip, Cinebench2026_macOS.dmg) and names no kind; a
# .dmg taken for the installer of a Windows card downloaded three gigabytes of macOS.
OTHER_SYSTEM = re.compile(r"\.(dmg|pkg|deb|rpm|run|appimage)$|\.tar\.(gz|xz|bz2)$|macos|mac_os|\bosx\b|linux", re.IGNORECASE)
ARM_BUILD = re.compile(r"arm64|aarch64", re.IGNORECASE)


def for_windows(file_name: str) -> bool:
    """Whether a file of the page is for Windows: whatever names no other system is."""
    return not OTHER_SYSTEM.search(str(file_name or ""))


def file_flavour(item: techpowerup_service.TechPowerUpFile) -> str:
    """What tells a file apart from the others of its version: its kind, and 'arm64' for a build for ARM."""
    return " - ".join(part for part in (flavour(item.kind), "arm64" if ARM_BUILD.search(item.file_name) else "") if part)


TpuListCall = Callable[[str], list[techpowerup_service.TechPowerUpFile]]
TpuResolveCall = Callable[[str, str], str]


def _default_tpu_resolve(slug: str, file_id: str) -> str:
    url, _name, _id = techpowerup_service.resolve_signed_url(None, slug, file_id)
    return url


class TechPowerUpProvider:
    id = "techpowerup"
    name = "TechPowerUp"
    platforms = (PLATFORM_WINDOWS,)

    _instance: "TechPowerUpProvider | None" = None

    def __init__(self, tpu_list: TpuListCall | None = None, tpu_resolve: TpuResolveCall | None = None,
                 devices: Callable[[], list[pc_devices.Device]] | None = None) -> None:
        self._tpu_list: TpuListCall = tpu_list or techpowerup_service.list_versions
        self._tpu_resolve: TpuResolveCall = tpu_resolve or _default_tpu_resolve
        self._snapshot = pc_devices.Snapshot(devices or pc_devices.present_devices)
        # what stands over the list of a driver: which device of this PC it is for
        self._notes: dict[str, tuple[str, str]] = {}
        self._running: dict[str, list[str]] = {}
        # the line of drivers this PC's device is on, where the product has lines
        self._lines: dict[str, str] = {}
        self._cache: dict[str, tuple[float, list[VendorBuild]]] = {}
        # every single file by id, the cards' own ids and the portable companions alike
        self._files: dict[str, VendorBuild] = {}
        # the SHA256 the page gives for each file: a download is checked against it
        self._sha256: dict[str, str] = {}
        # which Radeons each package of AMD's page installs on (amd_package_line)
        self._packages: dict[str, str] = {}

    @classmethod
    def instance(cls) -> "TechPowerUpProvider":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ----- helpers -----

    def _cached(self, key: str, loader: Callable[[], list[VendorBuild]]) -> list[VendorBuild]:
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        builds = loader()
        self._cache[key] = (now, builds)
        return builds

    def _build(self, product: str, version: str, download_id: str, name: str, *, date: str = "", notes: str = "", portable_id: str = "") -> VendorBuild:
        return VendorBuild(
            vendor=self.id,
            product=product,
            version=version,
            platform=PLATFORM_WINDOWS,
            date=date,
            download_id=download_id,
            name=name,
            notes=notes,
            portable_id=portable_id,
        )

    def _file_build(self, product: str, slug: str, item: techpowerup_service.TechPowerUpFile) -> VendorBuild:
        """One file as its own build: what a download id resolves to, with the file's own name and size."""
        download_id = f"{TPU_PREFIX}{slug}:{item.file_id}"
        portable = is_portable_file(product, item.kind, item.file_name)
        remarks = " - ".join(part for part in (file_flavour(item), f"portable {item.size}" if portable else item.size) if part)
        build = self._build(product, item.version, download_id, item.file_name, date=item.date, notes=remarks, portable_id=download_id if portable else "")
        self._files[download_id] = build
        if item.sha256:
            self._sha256[download_id] = item.sha256.upper()
        if product == AMD_GRAPHICS:
            self._packages[download_id] = amd_package_line(item.file_name, item.kind)
        return build

    def product_builds(self, product: str) -> list[VendorBuild]:
        slug = PRODUCTS[product]

        def load() -> list[VendorBuild]:
            # Files come newest first; a version's files sit together.
            groups: dict[tuple[str, str], list[tuple[techpowerup_service.TechPowerUpFile, VendorBuild]]] = {}
            for item in self._tpu_list(slug):
                if not for_windows(item.file_name):
                    continue
                groups.setdefault((item.version, item.date), []).append((item, self._file_build(product, slug, item)))
            cards: list[VendorBuild] = []
            for (version, date), files in groups.items():
                installers = [(item, build) for item, build in files if not build.portable_id]
                portables = [(item, build) for item, build in files if build.portable_id]
                # An installer and a portable build of one version share a card; the odd ones out keep their own.
                for index, (item, build) in enumerate(installers):
                    companion = portables[index] if index < len(portables) else None
                    notes = " - ".join(part for part in (file_flavour(item), item.size) if part)
                    portable_id = ""
                    if companion is not None:
                        portable_id = companion[1].download_id
                        notes = f"{notes}, portable {companion[0].size}" if companion[0].size else notes
                    cards.append(self._build(product, version, build.download_id, build.name, date=date, notes=notes, portable_id=portable_id))
                for _item, build in portables[len(installers):]:
                    cards.append(build)
            return self._for_this_pc(product, cards)

        return self._cached(f"tpu:{slug}", load)

    def _for_this_pc(self, product: str, cards: list[VendorBuild]) -> list[VendorBuild]:
        """The newest release ticked - for a driver, only when the PC has its device and does not run it already;
        the release the device runs marked as installed."""
        detect = DETECT.get(product)
        devices = pc_devices.find(list(detect), self._snapshot.devices()) if detect else None
        installed = [device.driver_version for device in devices or [] if device.driver_version]
        # For the badge of versions. Windows writes these numbers its own way (Intel graphics 32.0.101.7085
        # is the page's 101.7085), so the badge is shown only for a driver the list holds - marked below.
        self._running[product] = sorted(set(installed))
        if devices is None:
            self._notes.pop(product, None)
        elif devices:
            what = "; ".join(sorted({device.name + (f" · {device.driver_version}" if device.driver_version else "") for device in devices}))
            self._notes[product] = (f"TechPowerUp · this PC: {what}", f"TechPowerUp · этот ПК: {what}")
        else:
            self._notes[product] = ("TechPowerUp · no such device on this PC - nothing ticked",
                                    "TechPowerUp · подходящего устройства на этом ПК нет - ничего не отмечено")
        own = ""
        if product == INTEL_GRAPHICS and devices:
            lines = {intel_graphics_device_line(device.name, device.driver_version) for device in devices} - {""}
            # An Arc card beside an iGPU of another line: the card's driver is the one worth ticking.
            own = "arc" if "arc" in lines else min(lines, default="")
        amd = ""
        if product == AMD_GRAPHICS and devices:
            lines = {amd_graphics_device_line(device.name, device.device_id) for device in devices} - {""}
            # The combined package of RDNA 1-2 installs RDNA 3-4 as well: with both on the PC, it is the one.
            amd = next((line for line in AMD_LINES if line in lines), "")
            if amd in {"rdna1-2", "rdna3-4"}:
                # Only the packages that install on this PC's Radeon stand in its list.
                cards = [card for card in cards if self._packages.get(card.download_id, "all") in {"all", amd}]
                label = "RDNA 1-2 (RX 5000, RX 6000)" if amd == "rdna1-2" else "RDNA 3-4 (RX 7000, RX 9000)"
                en, ru = self._notes[product]
                self._notes[product] = (f"{en} · the packages of {label}", f"{ru} · пакеты для {label}")
            elif amd == "polaris-vega":
                # No card is of their line: nothing is marked the latest either (line() says '' for AMD).
                own = amd
                en, ru = self._notes[product]
                self._notes[product] = (f"{en} - Polaris / Vega: their legacy driver is on AMD's site only, nothing ticked",
                                        f"{ru} - Polaris / Vega: их драйвер legacy есть только на сайте AMD, ничего не отмечено")
        self._lines[product] = own
        finals = [card for card in cards if not NOT_A_RELEASE.search(card.version)]
        # Of its own line or nothing: a driver of another line may not run this GPU at all.
        newest = next((card for card in finals if not own or self.line(product, card.version) == own), None)
        marked: list[VendorBuild] = []
        for card in cards:
            here = any(same_version(version, card.version) for version in installed)
            tick = card is newest and devices != [] and not here
            if here or tick:
                card = dataclasses.replace(card, notes=" - ".join(part for part in (card.notes, "installed" if here else "") if part), ticked=tick)
            marked.append(card)
        return marked

    def note(self, product: str) -> tuple[str, str]:
        """What stands over the list of a driver, English and Russian; ('', '') for a product that is for any PC."""
        return self._notes.get(product, ("", ""))

    def running(self, product: str) -> list[str]:
        """The versions of the driver this PC's device runs, as Windows writes them."""
        return list(self._running.get(product, []))

    def line(self, product: str, version: str) -> str:
        """The line of drivers a version belongs to, where the product has lines; '' where it has none."""
        return intel_graphics_line(version) if product == INTEL_GRAPHICS else ""

    def machine_line(self, product: str) -> str:
        """The line of drivers this PC's device is on; '' - the product has no lines, or the device is unknown."""
        return self._lines.get(product, "")

    # ----- VendorProvider -----

    def products(self, platform: str) -> list[str]:
        return list(PRODUCTS) if platform in self.platforms else []

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        del selection  # the kind switch only picks which product field is read; the product names the page
        if platform not in self.platforms or product not in PRODUCTS:
            return []
        return self.product_builds(product)

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        wanted = str(download_id or "").strip()
        if wanted in self._files:
            return self._files[wanted]
        if wanted.startswith(TPU_PREFIX):
            _prefix, slug, _file_id = wanted.split(":", 2)
            product = next((name for name, item in PRODUCTS.items() if item == slug), "")
            if product:
                self.product_builds(product)
                return self._files.get(wanted)
        return None

    def expected_sha256(self, build: VendorBuild) -> str:
        """The SHA256 TechPowerUp's page gives for the file of this build; '' when the page gives none."""
        return self._sha256.get(build.download_id, "")

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        del registration  # TechPowerUp never asks for a form
        if build.download_id.startswith(TPU_PREFIX):
            _prefix, slug, file_id = build.download_id.split(":", 2)
            return self._tpu_resolve(slug, file_id)
        return build.download_id

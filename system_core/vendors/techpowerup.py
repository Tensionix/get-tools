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
        self._cache: dict[str, tuple[float, list[VendorBuild]]] = {}
        # every single file by id, the cards' own ids and the portable companions alike
        self._files: dict[str, VendorBuild] = {}
        # the SHA256 the page gives for each file: a download is checked against it
        self._sha256: dict[str, str] = {}

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
        remarks = " - ".join(part for part in (flavour(item.kind), f"portable {item.size}" if portable else item.size) if part)
        build = self._build(product, item.version, download_id, item.file_name, date=item.date, notes=remarks, portable_id=download_id if portable else "")
        self._files[download_id] = build
        if item.sha256:
            self._sha256[download_id] = item.sha256.upper()
        return build

    def product_builds(self, product: str) -> list[VendorBuild]:
        slug = PRODUCTS[product]

        def load() -> list[VendorBuild]:
            # Files come newest first; a version's files sit together.
            groups: dict[tuple[str, str], list[tuple[techpowerup_service.TechPowerUpFile, VendorBuild]]] = {}
            for item in self._tpu_list(slug):
                groups.setdefault((item.version, item.date), []).append((item, self._file_build(product, slug, item)))
            cards: list[VendorBuild] = []
            for (version, date), files in groups.items():
                installers = [(item, build) for item, build in files if not build.portable_id]
                portables = [(item, build) for item, build in files if build.portable_id]
                # An installer and a portable build of one version share a card; the odd ones out keep their own.
                for index, (item, build) in enumerate(installers):
                    companion = portables[index] if index < len(portables) else None
                    notes = " - ".join(part for part in (flavour(item.kind), item.size) if part)
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
        newest = next((card for card in cards if not NOT_A_RELEASE.search(card.version)), None)
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

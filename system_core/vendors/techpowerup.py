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

import re
import time
from collections.abc import Callable
from typing import Any

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

    def __init__(self, tpu_list: TpuListCall | None = None, tpu_resolve: TpuResolveCall | None = None) -> None:
        self._tpu_list: TpuListCall = tpu_list or techpowerup_service.list_versions
        self._tpu_resolve: TpuResolveCall = tpu_resolve or _default_tpu_resolve
        self._cache: dict[str, tuple[float, list[VendorBuild]]] = {}
        # every single file by id, the cards' own ids and the portable companions alike
        self._files: dict[str, VendorBuild] = {}

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
            return cards

        return self._cached(f"tpu:{slug}", load)

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

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        del registration  # TechPowerUp never asks for a form
        if build.download_id.startswith(TPU_PREFIX):
            _prefix, slug, file_id = build.download_id.split(":", 2)
            return self._tpu_resolve(slug, file_id)
        return build.download_id

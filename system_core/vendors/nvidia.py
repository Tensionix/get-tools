"""NVIDIA: GeForce drivers by chip generation, the apps, CUDA, and the two cleanup tools.

The pain this answers: NVIDIA's own site makes you name the exact card, and
never says which generations a driver version covers, or which one a person
should keep. Here the choice goes the other way round - pick a generation and
a driver kind, and every version published for it is listed with its date,
size, the NVENC SDK generation it satisfies (so a given FFmpeg build is known
to work) and a star on the versions the community treats as keepers. The
marks live in `config\\vendor_nvidia.yaml` and are the owner's to edit.

Sources, all without an account:

- Drivers: the driver search service behind nvidia.com. One request per
  series and driver kind returns every version with a permanent download URL.
  Studio drivers only appear when the WHQL filter is off; that is how the
  service behaves, not a choice made here.
- NVIDIA App and NVIDIA Broadcast: the current installer link is on the
  product page; the version is in the file name.
- CUDA Toolkit: the archive page lists every release, each release page
  carries the local installer link.
- DDU and NVCleanstall used to be here; they live on the TechPowerUp tab now
  with the rest of the site's utilities.
"""

from __future__ import annotations

import dataclasses
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen
import json

from system_core.core import pc_devices
from system_core.core.config import load_yaml_or_json
from system_core.services import techpowerup_service

from . import PLATFORM_WINDOWS, VendorBuild


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36"
)
PLATFORMS: tuple[str, ...] = (PLATFORM_WINDOWS,)
CACHE_TTL_SECONDS = 1800.0

DRIVER_SERVICE = (
    "https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php"
    "?func=DriverManualLookup&psid={psid}&pfid=&osID={os}&languageCode=1033"
    "&beta=0&isWHQL={whql}&dltype=-1&dch={dch}&upCRD={crd}&qnf=0&sort1=0&numberOfResults=200"
)
# Fermi never got a DCH driver and NVIDIA files its last release (391.35) under
# Windows 10 only (osID 57), so those two series are asked for as Windows 10, non-DCH;
# the driver installs on Windows 11 all the same. Everything newer is Windows 11 (135), DCH.
LEGACY_SERIES: frozenset[str] = frozenset({"GTX 500", "GTX 400"})
OS_WINDOWS_11 = 135
OS_WINDOWS_10 = 57

PRODUCT_GAME_READY = "Game Ready Driver"
PRODUCT_STUDIO = "Studio Driver"
PRODUCT_APP = "NVIDIA App"
PRODUCT_BROADCAST = "NVIDIA Broadcast"
PRODUCT_CUDA = "CUDA Toolkit"
PRODUCT_DLSS = "DLSS DLL"
PRODUCT_DLSS_FG = "DLSS Frame Generation DLL"
PRODUCT_DLSS_RR = "DLSS Ray Reconstruction DLL"
DRIVER_PRODUCTS = (PRODUCT_GAME_READY, PRODUCT_STUDIO)
PRODUCTS: tuple[str, ...] = (
    PRODUCT_GAME_READY,
    PRODUCT_STUDIO,
    PRODUCT_APP,
    PRODUCT_BROADCAST,
    PRODUCT_CUDA,
    PRODUCT_DLSS,
    PRODUCT_DLSS_FG,
    PRODUCT_DLSS_RR,
)

# Chip generation -> product series id in NVIDIA's lookup (desktop, notebook).
SERIES: dict[str, tuple[int, int]] = {
    "RTX 50": (131, 133),
    "RTX 40": (127, 129),
    "RTX 30": (120, 123),
    "RTX 20": (107, 111),
    "GTX 16": (112, 115),
    "GTX 10": (101, 102),
    "GTX 900": (98, 99),
    # Kepler and Fermi: NVIDIA still lists their last drivers (GTX 700/600 up to 47x, GTX 500/400 up to 391.35).
    "GTX 700": (95, 92),
    "GTX 600": (85, 84),
    "GTX 500": (76, 78),
    "GTX 400": (71, 72),
}
FORM_DESKTOP = "desktop"
FORM_NOTEBOOK = "notebook"
MY_CARDS = "*"  # the series value that means "every generation in my_generations"

# The NVIDIA card of a PC: a display adapter of NVIDIA's vendor id.
PC_CARD = ({"id": r"^PCI\\VEN_10DE&", "compatible": "CC_03"},)


def pc_card(devices: list[pc_devices.Device]) -> pc_devices.Device | None:
    found = pc_devices.find(list(PC_CARD), devices)
    return found[0] if found else None


def series_of(card_name: str) -> str:
    """The series a card is of, by its name ("NVIDIA GeForce RTX 4070 Laptop GPU" -> "RTX 40"); '' when the name tells none
    (Audion Setup Tools, Get-NvidiaSeries)."""
    name = str(card_name or "")
    found = re.search(r"(RTX|GTX)\s*(\d{2})(\d{2})", name, re.IGNORECASE)
    if found:
        key = f"{found.group(1).upper()} {found.group(2)}"
        return key if key in SERIES else ""
    if re.search(r"GTX\s*9\d0", name, re.IGNORECASE):
        return "GTX 900"
    found = re.search(r"GTX\s*([4-7])\d0", name, re.IGNORECASE)
    return f"GTX {found.group(1)}00" if found else ""


def is_notebook_card(card_name: str) -> bool:
    return bool(re.search(r"laptop|notebook|\bmax-q\b", str(card_name or ""), re.IGNORECASE))


def driver_number(windows_version: str) -> str:
    """The driver as NVIDIA numbers it: Windows' 32.0.15.6614 is 566.14 - the last five digits of the last two parts."""
    digits = "".join(re.findall(r"\d", "".join(str(windows_version or "").split(".")[-2:])))
    if len(digits) < 5:
        return ""
    return f"{digits[-5:-2]}.{digits[-2:]}"

APP_PAGE = "https://www.nvidia.com/en-us/software/nvidia-app/"
BROADCAST_PAGE = "https://www.nvidia.com/en-us/geforce/broadcasting/broadcast-app/"
CUDA_ARCHIVE = "https://developer.nvidia.com/cuda-toolkit-archive"
CUDA_SITE = "https://developer.nvidia.com"
CUDA_PAGE_QUERY = "?target_os=Windows&target_arch=x86_64&target_version=11&target_type=exe_local"
# DLSS is not a program: the upscaler is a DLL games carry (nvngx_dlss.dll) and
# swap for a newer one; TechPowerUp keeps every version of the three libraries.
TPU_SLUGS: dict[str, str] = {
    PRODUCT_DLSS: "nvidia-dlss-dll",
    PRODUCT_DLSS_FG: "nvidia-dlss-3-frame-generation-dll",
    PRODUCT_DLSS_RR: "nvidia-dlss-3-ray-reconstruction-dll",
}
TPU_PREFIX = "tpu:"
CUDA_PREFIX = "cuda:"

# Where the list of Game Ready drivers comes from: NVIDIA's own service - the list a page opens on - or
# TechPowerUp's page of them, the other choice of the form's switch for when the service stalls: every release
# since 2017 in one answer, each file with its SHA256. That page does not say which cards a version is for,
# so its list knows no generations.
SOURCE_NVIDIA = "nvidia"
SOURCE_TPU = "techpowerup"
GEFORCE_SLUG = "nvidia-geforce-graphics-drivers"
# Whose name a driver is signed with: asked of a file that came from a mirror.
SIGNER = "NVIDIA Corporation"


def geforce_file_rank(file_name: str) -> int | None:
    """How well a file of TechPowerUp's page is the driver of a 64-bit Windows 10 or 11; None - it is not one.

    A version has up to five files there: Windows 7 and 8, 32 bits, the Standard and the DCH driver.
    The DCH driver for Windows 10 and 11 is the one; a file the site keeps "for research" under a
    name that says DANGEROUS is never offered.
    """
    name = str(file_name or "").lower()
    if "dangerous" in name or "32bit" in name or "win10" not in name:
        return None
    return (2 if "dch" in name else 0) + (1 if "win11" in name else 0)


def tpu_date(text: str) -> str:
    """'October 6th, 2026' -> '6 Oct 2026', as the dates of NVIDIA's own list read; anything else stays."""
    match = re.match(r"^(\w{3})\w*\s+(\d+)(?:st|nd|rd|th)?,\s*(\d{4})$", str(text or "").strip())
    return f"{int(match.group(2))} {match.group(1)} {match.group(3)}" if match else str(text or "").strip()

EXE_LINK_PATTERN = re.compile(r'https?://[^"\'\s<>]+\.exe')
CUDA_ARCHIVE_PATTERN = re.compile(r'href="(/cuda-[\d-]+-download-archive)"[^>]*>\s*CUDA Toolkit\s+([\d.]+)([^<]*)<', re.IGNORECASE)
CUDA_LOCAL_PATTERN = re.compile(r'https?://developer\.download\.nvidia\.com/compute/cuda/[^"\'\s<>]+/local_installers/[^"\'\s<>]+\.exe')

HttpCall = Callable[[str, str, str | None, dict[str, str]], tuple[int, str]]
TpuListCall = Callable[[str], list[techpowerup_service.TechPowerUpFile]]
TpuResolveCall = Callable[[str, str], str]


def _default_http(method: str, url: str, body: str | None, headers: dict[str, str]) -> tuple[int, str]:
    request = Request(url, data=body.encode("utf-8") if body is not None else None, method=method)
    request.add_header("User-Agent", USER_AGENT)
    for key, value in headers.items():
        request.add_header(key, value)
    # The driver service (AjaxDriverService) answers in seconds most of the
    # day and stalls past a minute now and then; 'My cards' asks it three
    # times in a row, so one stall used to empty the whole list. One retry.
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            with urlopen(request, timeout=60) as response:
                return int(response.status), response.read().decode("utf-8", "replace")
        except HTTPError as exc:
            return int(exc.code), ""
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc
    reason = getattr(last_error, "reason", None) or last_error
    raise RuntimeError(f"NVIDIA request failed twice: {reason}. Refresh the list to try again.") from last_error


def _default_tpu_resolve(slug: str, file_id: str) -> str:
    url, _name, _id = techpowerup_service.resolve_signed_url(None, slug, file_id)
    return url


# ----- marks: golden versions and NVENC SDK generations -----


def driver_key(version: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", str(version or ""))]
    while len(parts) < 2:
        parts.append(0)
    return tuple(parts[:3])


Thresholds = list[tuple[str, str]]  # (generation, minimum driver), newest first


class DriverMarks:
    """What `config\\vendor_nvidia.yaml` says about driver versions."""

    def __init__(
        self,
        golden: set[str],
        nvenc: Thresholds,
        cuda: Thresholds,
        mine: tuple[str, ...] = (),
        ffmpeg: Thresholds | None = None,
    ) -> None:
        self.golden = golden
        self.nvenc = nvenc
        self.cuda = cuda
        self.mine = mine
        self.ffmpeg = ffmpeg or []

    @staticmethod
    def _thresholds(raw: Any) -> Thresholds:
        if not isinstance(raw, dict):
            return []
        return sorted(
            ((str(key).strip(), str(value).strip()) for key, value in raw.items()),
            key=lambda pair: driver_key(pair[1]),
            reverse=True,
        )

    @staticmethod
    def save_my_generations(path: Path, names: list[str] | tuple[str, ...]) -> bool:
        """Rewrite the `my_generations` list of the marks file in place, comments and the rest untouched."""
        if not path.exists():
            return False
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        start = next((i for i, line in enumerate(lines) if line.startswith("my_generations:")), None)
        if start is None:
            return False
        end = start + 1
        while end < len(lines) and (lines[end].startswith("  ") or not lines[end].strip()):
            if not lines[end].strip() and end + 1 < len(lines) and not lines[end + 1].startswith("  "):
                break
            end += 1
        newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
        block = ["my_generations:" + newline] + [f'  - "{name}"{newline}' for name in names if name in SERIES]
        if not names:
            block = ["my_generations: []" + newline]
        updated = "".join(lines[:start] + block + lines[end:])
        if updated == text:
            return False
        path.write_text(updated, encoding="utf-8")
        return True

    @classmethod
    def load(cls, path: Path | None) -> "DriverMarks":
        if path is None or not path.exists():
            return cls(set(), [], [])
        try:
            data = load_yaml_or_json(path)
        except Exception:  # noqa: BLE001 - marks are decoration, never a reason to fail the list
            return cls(set(), [], [])
        if not isinstance(data, dict):
            return cls(set(), [], [])
        golden = {str(item).strip() for item in (data.get("golden") or [])}
        mine = tuple(str(item).strip() for item in (data.get("my_generations") or []) if str(item).strip() in SERIES)
        return cls(
            golden,
            cls._thresholds(data.get("nvenc_sdk")),
            cls._thresholds(data.get("cuda_min_driver")),
            mine,
            cls._thresholds(data.get("ffmpeg_builds")),
        )

    def notes(self, version: str, kind: str) -> list[str]:
        notes: list[str] = []
        if "security" in kind.lower():
            notes.append("security update")
        sdk = newest_satisfied(version, self.nvenc)
        if sdk:
            notes.append(f"NVENC SDK {sdk}")
        ffmpeg = newest_satisfied(version, self.ffmpeg)
        if ffmpeg:
            notes.append(f"FFmpeg <= {ffmpeg}")
        cuda = newest_satisfied(version, self.cuda)
        if cuda:
            notes.append(f"CUDA <= {cuda}")
        if version in self.golden:
            notes.append("★ golden")
        return notes


def newest_satisfied(version: str, thresholds: Thresholds) -> str:
    """The newest generation whose minimum driver the version meets."""
    key = driver_key(version)
    for generation, minimum in thresholds:
        if key >= driver_key(minimum):
            return generation
    return ""


def short_date(text: str) -> str:
    """'Wed Aug 26, 2026' -> '26 Aug 2026'; anything else stays."""
    match = re.match(r"^\w+\s+(\w+)\s+(\d+),\s*(\d{4})$", str(text or "").strip())
    if match:
        return f"{int(match.group(2))} {match.group(1)} {match.group(3)}"
    return str(text or "").strip()


# ----- the provider -----


class NvidiaProvider:
    id = "nvidia"
    name = "NVIDIA"
    platforms = PLATFORMS
    # The only hashes this vendor knows are of the files TechPowerUp keeps: the log names their page.
    sha256_from = "TechPowerUp"

    _instance: "NvidiaProvider | None" = None

    def __init__(
        self,
        http: HttpCall | None = None,
        tpu_list: TpuListCall | None = None,
        tpu_resolve: TpuResolveCall | None = None,
        marks_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        devices: Callable[[], list[pc_devices.Device]] | None = None,
    ) -> None:
        self._snapshot = pc_devices.Snapshot(devices or pc_devices.present_devices)
        # what stands over the list of drivers: the card of this PC
        self._note: tuple[str, str] = ("", "")
        # ... and the driver it runs, for the badge of versions - when the list is one for this card
        self._running: list[str] = []
        self._http: HttpCall = http or _default_http
        self._tpu_list: TpuListCall = tpu_list or techpowerup_service.list_versions
        self._tpu_resolve: TpuResolveCall = tpu_resolve or _default_tpu_resolve
        self._marks_path = marks_path
        self._clock = clock
        self._cache: dict[str, tuple[float, list[VendorBuild]]] = {}
        self._sha256: dict[str, str] = {}

    @classmethod
    def instance(cls) -> "NvidiaProvider":
        if cls._instance is None:
            root = Path(__file__).resolve().parents[2]
            cls._instance = cls(marks_path=root / "config" / "vendor_nvidia.yaml")
        return cls._instance

    # ----- helpers -----

    def _cached(self, key: str, loader: Callable[[], list[VendorBuild]]) -> list[VendorBuild]:
        now = self._clock()
        cached = self._cache.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        builds = loader()
        self._cache[key] = (now, builds)
        return builds

    def _get(self, url: str) -> str:
        status, text = self._http("GET", url, None, {"Accept": "*/*"})
        if status != 200:
            raise RuntimeError(f"NVIDIA source answered HTTP {status}: {url.split('?')[0]}")
        return text

    def _build(self, product: str, version: str, download_id: str, name: str, *, date: str = "", notes: str = "") -> VendorBuild:
        return VendorBuild(
            vendor=self.id,
            product=product,
            version=version,
            platform=PLATFORM_WINDOWS,
            date=date,
            download_id=download_id,
            name=name,
            notes=notes,
        )

    # ----- drivers -----

    def my_cards_builds(self, product: str, form: str, mine: tuple[str, ...] | None = None) -> list[VendorBuild]:
        """Versions published for every generation in `mine` (the window's ticks, else `my_generations`): one driver for all cards."""
        if mine is None:
            mine = DriverMarks.load(self._marks_path).mine
        mine = tuple(name for name in mine if name in SERIES)
        if not mine:
            return []
        # The service answers each generation in its own seconds, a minute when it stalls: asked side by
        # side, the wait is the slowest answer, not the sum of them. The order of the answers is the order asked.
        if len(mine) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(4, len(mine))) as pool:
                lists = list(pool.map(lambda series: self.driver_builds(product, series, form), mine))
        else:
            lists = [self.driver_builds(product, series, form) for series in mine]
        common = set.intersection(*(set(build.version for build in builds) for builds in lists))
        return [build for build in lists[0] if build.version in common]

    def driver_builds(self, product: str, series: str, form: str, mine: tuple[str, ...] | None = None) -> list[VendorBuild]:
        if series == MY_CARDS:
            return self.my_cards_builds(product, form, mine)
        if series not in SERIES:
            return []
        psid = SERIES[series][1 if form == FORM_NOTEBOOK else 0]
        studio = product == PRODUCT_STUDIO
        legacy = series in LEGACY_SERIES
        url = DRIVER_SERVICE.format(psid=psid, whql=0 if studio else 1, crd=1 if studio else 0, dch=0 if legacy else 1, os=OS_WINDOWS_10 if legacy else OS_WINDOWS_11)

        def load() -> list[VendorBuild]:
            marks = DriverMarks.load(self._marks_path)
            try:
                data = json.loads(self._get(url))
            except json.JSONDecodeError as exc:
                raise RuntimeError("NVIDIA driver service returned no JSON.") from exc
            builds: list[VendorBuild] = []
            seen: set[str] = set()
            for entry in data.get("IDS") or []:
                info = entry.get("downloadInfo") if isinstance(entry, dict) else None
                if not isinstance(info, dict):
                    continue
                version = str(info.get("Version") or "").strip()
                link = str(info.get("DownloadURL") or "").strip()
                if not version or not link or link in seen:
                    continue
                seen.add(link)
                kind = unquote(str(info.get("Name") or ""))
                notes = marks.notes(version, kind)
                size = str(info.get("DownloadURLFileSize") or "").strip()
                remarks = " - ".join(part for part in (size, *notes) if part)
                builds.append(
                    self._build(
                        product,
                        version,
                        link,
                        link.rsplit("/", 1)[-1],
                        date=short_date(str(info.get("ReleaseDateTime") or "")),
                        notes=remarks,
                    )
                )
            return sorted(builds, key=lambda build: driver_key(build.version), reverse=True)

        return self._cached(f"driver:{psid}:{studio}", load)

    # ----- apps -----

    def _page_installer(self, product: str, page: str) -> list[VendorBuild]:
        def load() -> list[VendorBuild]:
            html = self._get(page)
            links = [link for link in EXE_LINK_PATTERN.findall(html) if "nvidia.com" in link]
            if not links:
                raise RuntimeError(f"No installer link found on {page}")
            link = links[0]
            name = link.rsplit("/", 1)[-1]
            match = re.search(r"v?(\d+(?:\.\d+)+)", name)
            version = match.group(1) if match else "latest"
            return [self._build(product, version, link, name)]

        return self._cached(f"page:{product}", load)

    def cuda_builds(self) -> list[VendorBuild]:
        def load() -> list[VendorBuild]:
            html = self._get(CUDA_ARCHIVE)
            builds: list[VendorBuild] = []
            for path, version, tail in CUDA_ARCHIVE_PATTERN.findall(html):
                note = re.sub(r"\s+", " ", tail).strip()
                builds.append(
                    self._build(
                        PRODUCT_CUDA,
                        version,
                        f"{CUDA_PREFIX}{version}:{path}",
                        f"cuda_{version}_windows.exe",
                        notes=note,
                    )
                )
            return builds

        return self._cached("cuda", load)

    def tpu_builds(self, product: str) -> list[VendorBuild]:
        slug = TPU_SLUGS[product]

        def load() -> list[VendorBuild]:
            builds: list[VendorBuild] = []
            for item in self._tpu_list(slug):
                remarks = " - ".join(part for part in (item.kind.lower(), item.size) if part)
                builds.append(
                    self._build(
                        product,
                        item.version,
                        f"{TPU_PREFIX}{slug}:{item.file_id}",
                        item.file_name,
                        date=item.date,
                        notes=remarks,
                    )
                )
                if item.sha256:
                    self._sha256[builds[-1].download_id] = item.sha256.upper()
            return builds

        return self._cached(f"tpu:{slug}", load)

    def tpu_driver_builds(self) -> list[VendorBuild]:
        """Game Ready drivers as TechPowerUp keeps them: one page, every release since 2017, newest first.

        One card a version - its DCH driver for a 64-bit Windows 10 or 11. A beta and a hotfix keep
        their word and are no release. The marks of the owner's file go by the number of a version,
        as in NVIDIA's own list.
        """
        def load() -> list[VendorBuild]:
            marks = DriverMarks.load(self._marks_path)
            best: dict[str, tuple[int, techpowerup_service.TechPowerUpFile]] = {}
            for item in self._tpu_list(GEFORCE_SLUG):
                rank = geforce_file_rank(item.file_name)
                if rank is None:
                    continue
                number, _, word = item.version.strip().partition(" ")
                shown = number if word.strip().upper() in ("", "WHQL") else f"{number} {word.strip()}"
                if shown not in best or rank > best[shown][0]:
                    best[shown] = (rank, item)
            builds: list[VendorBuild] = []
            for shown, (_rank, item) in best.items():
                number = shown.split(" ", 1)[0]
                # "DCH Driver" and "Standard Driver" only tell the files of one version apart: the DCH one is taken.
                kind = "" if item.kind in ("", "DCH Driver", "Standard Driver") else item.kind
                remarks = " - ".join(part for part in (item.size, kind, *marks.notes(number, item.kind)) if part)
                build = dataclasses.replace(
                    self._build(PRODUCT_GAME_READY, shown, f"{TPU_PREFIX}{GEFORCE_SLUG}:{item.file_id}", item.file_name,
                                date=tpu_date(item.date), notes=remarks),
                    preview=shown != number)
                if item.sha256:
                    self._sha256[build.download_id] = item.sha256.upper()
                builds.append(build)
            return builds

        return self._cached(f"tpu:{GEFORCE_SLUG}", load)

    def _tpu_for_this_pc(self, builds: list[VendorBuild]) -> list[VendorBuild]:
        """TechPowerUp's list on this PC: the driver the card runs is marked as installed. Nothing is ticked -
        the page does not say which cards a version is for, and a driver is not offered to a card by guess."""
        card = pc_card(self._snapshot.devices())
        self._running = []
        if card is None:
            self._note = ("TechPowerUp · every Game Ready release in one list · no NVIDIA card on this PC",
                          "TechPowerUp · все выпуски Game Ready одним списком · видеокарты NVIDIA на этом ПК нет")
            return builds
        installed = driver_number(card.driver_version)
        self._running = [installed] if installed else []
        what = card.name + (f" · {installed}" if installed else "")
        self._note = (f"TechPowerUp · every Game Ready release in one list; the site does not say which cards a version is for - nothing ticked · this PC: {what}",
                      f"TechPowerUp · все выпуски Game Ready одним списком; для каких карт версия, сайт не говорит - ничего не отмечено · этот ПК: {what}")
        if not installed:
            return builds
        return [dataclasses.replace(build, notes=" - ".join(part for part in (build.notes, "installed") if part))
                if build.version == installed else build for build in builds]

    # ----- VendorProvider -----

    def products(self, platform: str) -> list[str]:
        return list(PRODUCTS) if platform in self.platforms else []

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        if platform not in self.platforms:
            return []
        if product == PRODUCT_GAME_READY and str(selection.get("source") or SOURCE_NVIDIA) == SOURCE_TPU:
            return self._tpu_for_this_pc(self.tpu_driver_builds())
        if product in DRIVER_PRODUCTS:
            series = str(selection.get("series") or next(iter(SERIES)))
            form = str(selection.get("form") or FORM_DESKTOP)
            raw_mine = selection.get("mine")
            mine = tuple(str(item).strip() for item in raw_mine) if isinstance(raw_mine, (list, tuple)) else None
            return self._for_this_pc(self.driver_builds(product, series, form, mine), series, mine)
        self._note = ("", "")
        self._running = []
        if product == PRODUCT_APP:
            return self._page_installer(product, APP_PAGE)
        if product == PRODUCT_BROADCAST:
            return self._page_installer(product, BROADCAST_PAGE)
        if product == PRODUCT_CUDA:
            return self.cuda_builds()
        if product in TPU_SLUGS:
            return self.tpu_builds(product)
        return []

    def _for_this_pc(self, builds: list[VendorBuild], series: str, mine: tuple[str, ...] | None) -> list[VendorBuild]:
        """The driver the card of this PC runs marked as installed; the newest golden driver ticked - the newest
        one when the list has no golden - when the list is of the card's own generation and the card does not
        run it already. No NVIDIA card on the PC, or a list of another generation: nothing is ticked."""
        card = pc_card(self._snapshot.devices())
        self._running = []
        if card is None:
            self._note = ("NVIDIA · no NVIDIA card on this PC - nothing ticked", "NVIDIA · видеокарты NVIDIA на этом ПК нет - ничего не отмечено")
            return builds
        own = series_of(card.name)
        installed = driver_number(card.driver_version)
        marks = DriverMarks.load(self._marks_path)
        listed = bool(own) and (series == own or (series == MY_CARDS and own in (mine if mine is not None else marks.mine)))
        what = card.name + (f" · {installed}" if installed else "")
        # A list of another generation is not about this card: its driver is not measured against it.
        self._running = [installed] if listed and installed else []
        self._note = ((f"NVIDIA · this PC: {what}", f"NVIDIA · этот ПК: {what}") if listed else
                      (f"NVIDIA · this PC's card ({what}) is of another generation - nothing ticked",
                       f"NVIDIA · видеокарта этого ПК ({what}) - другого поколения, ничего не отмечено"))
        golden = next((build for build in builds if build.version in marks.golden), None)
        pick = (golden or (builds[0] if builds else None)) if listed else None
        marked: list[VendorBuild] = []
        for build in builds:
            here = bool(installed) and build.version == installed
            tick = build is pick and not here
            if here or tick:
                build = dataclasses.replace(build, notes=" - ".join(part for part in (build.notes, "installed" if here else "") if part), ticked=tick)
            marked.append(build)
        return marked

    def note(self, product: str) -> tuple[str, str]:
        """What stands over the list of drivers, English and Russian; ('', '') for what is no driver."""
        return self._note if product in DRIVER_PRODUCTS else ("", "")

    # The number of a driver is read off the version Windows shows (32.0.15.9597 is 595.97): one scheme.
    numbers_of_windows = True

    def running(self, product: str) -> list[str]:
        """The driver the card of this PC runs, when the list is one for this card."""
        return list(self._running) if product in DRIVER_PRODUCTS else []

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        wanted = str(download_id or "").strip()
        for _key, (_stamp, builds) in self._cache.items():
            for build in builds:
                if build.download_id == wanted:
                    return build
        # Not listed in this session: rebuild what the id itself says.
        if wanted.startswith(TPU_PREFIX):
            _prefix, slug, file_id = wanted.split(":", 2)
            if slug == GEFORCE_SLUG:
                return next((build for build in self.tpu_driver_builds() if build.download_id == wanted), None)
            product = next((name for name, item in TPU_SLUGS.items() if item == slug), "")
            if not product:
                return None
            for build in self.tpu_builds(product):
                if build.download_id == wanted:
                    return build
            return None
        if wanted.startswith(CUDA_PREFIX):
            version = wanted.split(":", 2)[1]
            return self._build(PRODUCT_CUDA, version, wanted, f"cuda_{version}_windows.exe")
        if "download.nvidia.com/Windows/" in wanted:
            name = wanted.rsplit("/", 1)[-1]
            version = wanted.split("/Windows/", 1)[1].split("/", 1)[0]
            product = PRODUCT_STUDIO if "-nsd-" in name else PRODUCT_GAME_READY
            return self._build(product, version, wanted, name)
        if "nvidia.com" in wanted and wanted.endswith(".exe"):
            name = wanted.rsplit("/", 1)[-1]
            match = re.search(r"v?(\d+(?:\.\d+)+)", name)
            product = PRODUCT_BROADCAST if "broadcast" in wanted.lower() else PRODUCT_APP
            return self._build(product, match.group(1) if match else "latest", wanted, name)
        return None

    def expected_sha256(self, build: VendorBuild) -> str:
        """The SHA256 TechPowerUp's page gives for a file that comes from it; '' for a file of NVIDIA's own servers."""
        return self._sha256.get(build.download_id, "")

    def signer_for(self, build: VendorBuild) -> str:
        """Whose signature the file must carry: a driver that came from a mirror is checked for NVIDIA's."""
        return SIGNER if build.download_id.startswith(f"{TPU_PREFIX}{GEFORCE_SLUG}:") else ""

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        del registration  # NVIDIA never asks for a form
        download_id = build.download_id
        if download_id.startswith(TPU_PREFIX):
            _prefix, slug, file_id = download_id.split(":", 2)
            return self._tpu_resolve(slug, file_id)
        if download_id.startswith(CUDA_PREFIX):
            _prefix, version, path = download_id.split(":", 2)
            html = self._get(f"{CUDA_SITE}{path}{CUDA_PAGE_QUERY}")
            links = CUDA_LOCAL_PATTERN.findall(html)
            if not links:
                raise RuntimeError(f"No local installer link on the CUDA {version} page.")
            return links[0]
        return download_id

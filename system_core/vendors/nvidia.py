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

import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen
import json

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

    _instance: "NvidiaProvider | None" = None

    def __init__(
        self,
        http: HttpCall | None = None,
        tpu_list: TpuListCall | None = None,
        tpu_resolve: TpuResolveCall | None = None,
        marks_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http: HttpCall = http or _default_http
        self._tpu_list: TpuListCall = tpu_list or techpowerup_service.list_versions
        self._tpu_resolve: TpuResolveCall = tpu_resolve or _default_tpu_resolve
        self._marks_path = marks_path
        self._clock = clock
        self._cache: dict[str, tuple[float, list[VendorBuild]]] = {}

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
            return builds

        return self._cached(f"tpu:{slug}", load)

    # ----- VendorProvider -----

    def products(self, platform: str) -> list[str]:
        return list(PRODUCTS) if platform in self.platforms else []

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        if platform not in self.platforms:
            return []
        if product in DRIVER_PRODUCTS:
            series = str(selection.get("series") or next(iter(SERIES)))
            form = str(selection.get("form") or FORM_DESKTOP)
            raw_mine = selection.get("mine")
            mine = tuple(str(item).strip() for item in raw_mine) if isinstance(raw_mine, (list, tuple)) else None
            return self.driver_builds(product, series, form, mine)
        if product == PRODUCT_APP:
            return self._page_installer(product, APP_PAGE)
        if product == PRODUCT_BROADCAST:
            return self._page_installer(product, BROADCAST_PAGE)
        if product == PRODUCT_CUDA:
            return self.cuda_builds()
        if product in TPU_SLUGS:
            return self.tpu_builds(product)
        return []

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        wanted = str(download_id or "").strip()
        for _key, (_stamp, builds) in self._cache.items():
            for build in builds:
                if build.download_id == wanted:
                    return build
        # Not listed in this session: rebuild what the id itself says.
        if wanted.startswith(TPU_PREFIX):
            _prefix, slug, file_id = wanted.split(":", 2)
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

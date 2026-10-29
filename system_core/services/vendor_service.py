"""Vendor builds in the GUI: the version list for the form and the download jobs.

One form serves every vendor. A `vendor` switch picks the provider, each
vendor has its own platform and product toggles, and the shared versions
list comes from the provider. The download job resolves a link per ticked
version, fetches it with resume into its own folder under
`Vendors\\<Vendor>\\<archive name>` and unpacks a zip on request, leaving
exactly what a person would have made by hand.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen
import zipfile

from system_core.core import pc_devices
from system_core.core.jobs import JobContext, hidden_subprocess_kwargs
from system_core.core.rights import is_elevated
from system_core.services.external_tools_service import start_on_its_own
from system_core.core.live_line import download_line
from system_core.vendors import VENDOR_IDS, BuildSource, RegistrationRequired, VendorBuild, VendorProvider, get_provider
from system_core.vendors.blackmagic import REGISTRATION_FIELDS


VENDORS_DIRECTORY_NAME = "Vendors"
USER_AGENT = "Audion-Get-Vendors"
# What a build with several sources is asked for by: sites that hand a file to a person refuse a program's name.
SOURCE_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AudionGetTools"
CHUNK_BYTES = 1024 * 1024
WHOLE_CATALOG = "*"
DEFAULT_VENDOR = VENDOR_IDS[0]

# Which form fields carry each vendor's choices: the platform field (empty when
# the vendor has one platform), the product field, and extra switches handed to
# the provider by name.
VENDOR_FIELDS: dict[str, tuple[str, str, dict[str, str]]] = {
    "blackmagic": ("bmd_platform", "bmd_product", {}),
    "affinity": ("aff_platform", "aff_product", {}),
    "nvidia": ("", "nv_product", {"series": "nv_series", "form": "nv_form", "mine": "nv_my_generations", "source": "nv_source"}),
    # TechPowerUp: no single product field; `kind` names which of the per-kind product fields is read.
    "techpowerup": ("", "", {"kind": "tpu_kind", "drivers": "tpu_driver", "tools": "tpu_tool", "monitor": "tpu_monitor", "bench": "tpu_bench", "bios": "tpu_bios"}),
    "uupdump": ("uup_platform", "uup_product", {"lang": "uup_lang", "edition": "uup_edition", "virtual": "uup_virtual"}),
    # The Microsoft catalog: the kind of device names which of the per-kind driver fields is read, as with TechPowerUp.
    "mscatalog": ("", "", {"kind": "msc_kind", "lan": "msc_lan", "wifi": "msc_wifi", "bluetooth": "msc_bluetooth",
                           "audio": "msc_audio", "chipset": "msc_chipset"}),
    # One product, nothing to choose but the version.
    "intelchipset": ("", "", {}),
    # Programs as files: the shelf is the product, its programs are the list.
    "programs": ("", "prg_kind", {}),
}

UrlOpener = Callable[..., Any]


# ----- options for the form -----


def _option(value: str, label: str, label_ru: str | None = None) -> dict[str, str]:
    return {"value": value, "label": label, "label_ru": label_ru or label}


def selected_vendor(values: dict[str, Any] | None) -> str:
    vendor = str((values or {}).get("vendor") or "").strip().lower()
    return vendor if vendor in VENDOR_IDS else DEFAULT_VENDOR


def resolve_uup_language(values: dict[str, Any]) -> str:
    """`uup_lang` is two buttons and 'other'; 'other' hands over to the `uup_lang_other` list."""
    lang = str(values.get("uup_lang") or "").strip()
    if lang == "other":
        lang = str(values.get("uup_lang_other") or "").strip() or "en-us"
    return lang


def selection(values: dict[str, Any] | None, vendor: str) -> tuple[str, str, dict[str, Any]]:
    """`(platform, product, extra switches)` from the vendor's own form fields, with sane fallbacks."""
    platform_key, product_key, extra_keys = VENDOR_FIELDS[vendor]
    provider = get_provider(vendor)
    platform = str((values or {}).get(platform_key) or "").strip() if platform_key else ""
    if platform not in provider.platforms:
        platform = provider.platforms[0]
    product = str((values or {}).get(product_key) or "").strip()
    extra: dict[str, Any] = {}
    for name, key in extra_keys.items():
        raw = (values or {}).get(key)
        # A checkbox group hands over a list; everything else is a single switch.
        extra[name] = [str(item).strip() for item in raw] if isinstance(raw, (list, tuple)) else str(raw or "").strip()
    if "lang" in extra:
        extra["lang"] = resolve_uup_language({**(values or {}), "uup_lang": extra["lang"]})
    if not product and str(extra.get("kind") or "") in extra:
        # A kind switch (drivers / tools / ...) with one product field per kind: the switch says which field counts.
        product = str(extra[str(extra["kind"])] or "").strip()
    if not product and not product_key and "kind" not in extra_keys:
        # A vendor of one product has no field for it: that product is the choice.
        names = provider.products(platform)
        product = names[0] if len(names) == 1 else ""
    return platform, product, extra


def nvidia_my_generation_options(root: Path | str | None = None, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The generation cards of 'My generations': every series NVIDIA knows, the ones from the marks file ticked."""
    del values
    from system_core.vendors.nvidia import SERIES, DriverMarks, NvidiaProvider

    provider = get_provider("nvidia")
    marks_path = getattr(provider, "_marks_path", None)
    mine = set(DriverMarks.load(marks_path).mine) if marks_path else set()
    return [{"value": name, "label": name, "label_ru": name, "default": name in mine} for name in SERIES]


def remember_my_generations(values: dict[str, Any]) -> None:
    """The ticks of 'My generations' are the memory: they go back into config\\vendor_nvidia.yaml when they change."""
    raw = values.get("nv_my_generations")
    if not isinstance(raw, list):
        return
    from system_core.vendors.nvidia import DriverMarks

    provider = get_provider("nvidia")
    marks_path = getattr(provider, "_marks_path", None)
    if not marks_path:
        return
    chosen = [str(item).strip() for item in raw if str(item).strip()]
    # An empty list is the field before its cards have shown (or a cleared row):
    # neither is a choice worth writing over the person's file.
    if chosen and set(chosen) != set(DriverMarks.load(marks_path).mine):
        DriverMarks.save_my_generations(marks_path, chosen)


PREVIEW_WORDS = re.compile(r"\b(beta|preview|insider|rs_prerelease)\b", re.IGNORECASE)
GENERATION_NAME = re.compile(r"^(\d{2})H([12])$", re.IGNORECASE)


# Generations that are never gold. The owner, 10.10.2026: "26H1 - off the chosen ones".
PLAIN_GENERATIONS = {"26H1"}


def _generation_rank(name: str) -> tuple[int, int, int]:
    """Newest generation first: 26H2, 26H1, 25H2... What is no generation by its name stands after them, in the order of the catalogue."""
    match = GENERATION_NAME.match(name)
    return (0, -int(match.group(1)), -int(match.group(2))) if match else (1, 0, 0)


def windows_build_options(builds: list[VendorBuild]) -> list[dict[str, Any]]:
    """The builds of Windows by generation: each generation a block under its heading, the newest generation on top.

    A generation (22H2, 24H2, 25H2...) gets new cumulative builds for years. The heading names the
    product and the generation once, so a card under it says only what tells it from its
    neighbours: the build, the date, the language and the edition. Inside a generation the order
    is the catalogue's - newest first; the newest final build of each generation is gold (but for
    the generations of PLAIN_GENERATIONS), and the first of them, at the top of the list, is the
    latest. A preview is marked so that "the latest stable" passes it by whatever its card says.
    """
    groups: dict[str, list[VendorBuild]] = {}
    for build in builds:
        groups.setdefault(str(build.version or "").split(" ", 1)[0].strip().upper(), []).append(build)
    options: list[dict[str, Any]] = []
    latest_given = False
    for generation in sorted(groups, key=_generation_rank):
        members = groups[generation]
        title = f"{members[0].product} {generation}".strip() + f" ({len(members)})"
        options.append({"value": "", "label": title, "label_ru": title, "header": True})
        gold_given = False
        for build in members:
            label = build.label
            if generation and label.upper().startswith(generation + " "):
                label = label[len(generation) + 1:]
            option: dict[str, Any] = _option(build.download_id, label)
            if build.beta or PREVIEW_WORDS.search(f"{build.product} {build.label}"):
                option["beta"] = True
            if generation and generation not in PLAIN_GENERATIONS and not gold_given and not build.beta and not PREVIEW_WORDS.search(build.label):
                gold_given = True
                if not latest_given:
                    latest_given = True
                    label = f"{label} - latest"
                option["label"] = option["label_ru"] = f"★ {label}"
            options.append(option)
    return options


# ----- defaults that follow the machine (`default_from` of the manifest) -----

# The devices of this PC, read once a process: every default asks the same list.
_this_pc = pc_devices.Snapshot()


def default_nvidia_form(field: dict[str, Any]) -> str:
    """A notebook's list of NVIDIA drivers on a notebook that has an NVIDIA card: by the name of the card, else by
    the kind of the case (Audion Setup Tools, Get-NvidiaSeries). Without such a card - the default of the manifest."""
    del field
    from system_core.vendors import nvidia

    card = nvidia.pc_card(_this_pc.devices())
    if card is None:
        return ""
    return nvidia.FORM_NOTEBOOK if nvidia.is_notebook_card(card.name) or pc_devices.is_notebook() else nvidia.FORM_DESKTOP


def default_my_generations(field: dict[str, Any]) -> list[str] | None:
    """'My generations' as config\\vendor_nvidia.yaml has them: the list 'My cards' is read by.

    The window ticks them when the cards of the field arrive; the console command has no cards to
    wait for, and with nothing ticked 'My cards' is an empty list.
    """
    del field
    from system_core.vendors.nvidia import DriverMarks

    marks_path = getattr(get_provider("nvidia"), "_marks_path", None)
    mine = list(DriverMarks.load(marks_path).mine) if marks_path else []
    return mine or None


def choice_for_this_pc(choices: list[dict[str, Any]]) -> str:
    """The first maker of a page of drivers this PC has a device of; '' - none of them, the page opens on its first.

    What a page of the layout names in `choose`. A choice says its source and its product by the
    values it fixes: NVIDIA, a driver of TechPowerUp, a driver of the Microsoft catalog, the
    chipset INF of Intel.
    """
    from system_core.vendors import intelchipset, mscatalog, nvidia, techpowerup

    devices = _this_pc.devices()
    for choice in choices:
        values = dict(choice.get("values") or {})
        vendor = str(values.get("vendor") or "")
        here = False
        if vendor == "nvidia":
            here = nvidia.pc_card(devices) is not None
        elif vendor == "techpowerup":
            detect = techpowerup.DETECT.get(str(values.get("tpu_driver") or ""))
            here = bool(detect) and bool(pc_devices.find(list(detect), devices))
        elif vendor == "mscatalog":
            kind = str(values.get("msc_kind") or "")
            driver = mscatalog.KINDS.get(kind, {}).get(str(values.get(f"msc_{kind}") or ""))
            here = driver is not None and bool(pc_devices.find(list(driver.match), devices))
        elif vendor == "intelchipset":
            here = bool(intelchipset.intel_device_ids(devices))
        if here:
            return str(choice.get("id") or "")
    return ""


def default_catalog_kind(field: dict[str, Any]) -> str:
    """The first kind of device of the Microsoft catalog this PC has a device of: the page opens on a driver for this PC."""
    del field
    from system_core.vendors import mscatalog

    devices = _this_pc.devices()
    for kind, drivers in mscatalog.KINDS.items():
        if any(pc_devices.find(list(driver.match), devices) for driver in drivers.values()):
            return kind
    return ""


def default_catalog_driver(field: dict[str, Any]) -> str:
    """The driver of a kind (the field `msc_<kind>`) this PC has a device for."""
    from system_core.vendors import mscatalog

    kind = str(field.get("id") or "").removeprefix("msc_")
    devices = _this_pc.devices()
    for name, driver in mscatalog.KINDS.get(kind, {}).items():
        if pc_devices.find(list(driver.match), devices):
            return name
    return ""


def _number_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(version or "")))


def machine_standing(provider: VendorProvider, product: str, builds: list[VendorBuild]) -> dict[str, Any] | None:
    """The driver this PC's device runs against the newest of the list: for the badge of versions.

    `running` is the driver in the numbers of the list, `newest` the newest final release - of
    its own line where the kind has lines (Realtek: a line is a chip, and 1168 is not behind
    1127) - and `behind` says whether the PC is behind it. The driver is found in the list by the
    mark the provider gave it ("installed"). One the list does not hold is compared by its
    number only where the numbers of the source are the numbers Windows shows
    (`numbers_of_windows`); elsewhere it is not judged at all: AMD's driver 32.x says nothing
    about Adrenalin 25.x. None - no badge.
    """
    running = getattr(provider, "running", None)
    have = [str(version).strip() for version in (running(product) if callable(running) else []) if str(version).strip()]
    if not have or not builds:
        return None
    at = next((index for index, build in enumerate(builds) if re.search(r"\binstalled\b", build.notes or "")), None)
    if at is not None:
        shown = builds[at].version
    elif getattr(provider, "numbers_of_windows", False):
        shown = max(have, key=_number_key)
    else:
        return None
    line = getattr(provider, "line", None)

    def line_of(version: str) -> str:
        return str(line(product, version) or "") if callable(line) else ""

    # The line of the device itself goes first where the provider knows it: Intel's 101.70xx came out
    # before the split for every GPU, and an Arc card that runs it is not on the line of Iris Xe.
    machine_line = getattr(provider, "machine_line", None)
    own = (str(machine_line(product) or "") if callable(machine_line) else "") or line_of(shown)
    finals = [build for build in builds if not (build.beta or build.preview or PREVIEW_WORDS.search(build.label))]
    pool = [build for build in finals if not own or line_of(build.version) == own]
    if not pool:
        return None
    newest = pool[0]
    # The list stands newest first: a newest release above the driver of the PC means the PC is behind.
    behind = builds.index(newest) < at if at is not None else _number_key(shown) < _number_key(newest.version)
    return {"running": shown, "newest": newest.version, "behind": behind, "said": have}


def note_option(provider: VendorProvider, product: str, builds: list[VendorBuild]) -> dict[str, Any] | None:
    """The line over a list of versions - where it comes from, which device of this PC it is for - with the badge of the driver the device runs."""
    note = getattr(provider, "note", None)
    note_en, note_ru = note(product) if callable(note) else ("", "")
    if not note_en:
        return None
    option: dict[str, Any] = {"value": "", "label": note_en, "label_ru": note_ru, "header": True, "note": True}
    standing = machine_standing(provider, product, builds)
    if standing:
        # The badge says the version: the line does not say it once more after the name of the device.
        for said in standing.pop("said"):
            option["label"] = option["label"].replace(f" · {said}", "")
            option["label_ru"] = option["label_ru"].replace(f" · {said}", "")
        option.update(standing)
    return option


def catalog_build_options(provider: VendorProvider, product: str, builds: list[VendorBuild]) -> list[dict[str, Any]]:
    """A kind of driver from the Microsoft catalog.

    Over the list - a note of where it comes from and which device of this PC it is for. Where the
    kind names its lines of drivers (Realtek: a line is a chip), each line is a block under the
    chip's name, in the order the newest driver of each came out. The driver the PC's own device
    wants is ticked.
    """
    def card(build: VendorBuild) -> dict[str, Any]:
        option: dict[str, Any] = _option(build.download_id, build.label)
        if build.ticked:
            option["default"] = True
        return option

    options: list[dict[str, Any]] = []
    over = note_option(provider, product, builds)
    if over:
        options.append(over)
    lines = provider.lines(product)  # type: ignore[attr-defined]
    if not lines:
        return options + [card(build) for build in builds]
    groups: dict[str, list[VendorBuild]] = {}
    for build in builds:
        groups.setdefault(str(build.version).split(".", 1)[0], []).append(build)
    for line, members in groups.items():
        title = f"{lines.get(line) or line + '.x'} ({len(members)})"
        options.append({"value": "", "label": title, "label_ru": title, "header": True})
        options.extend(card(build) for build in members)
    return options


def program_options(provider: VendorProvider, shelf: str, portable_only: bool) -> list[dict[str, Any]]:
    """A shelf of programs taken as files: its blocks under their headings, a card for a program.

    A card says which files the program has. A tick means its installer - or its portable build
    when it has no installer, and for every card while the Portable switch is on: then the
    programs without a portable build leave the list. What the program is, where its file says
    so, is the tooltip of the card.
    """
    options: list[dict[str, Any]] = []
    for (title, title_ru), builds in provider.blocks(shelf):  # type: ignore[attr-defined]
        if portable_only:
            builds = [build for build in builds if build.portable_id]
        if not builds:
            continue
        options.append({"value": "", "label": f"{title} ({len(builds)})", "label_ru": f"{title_ru} ({len(builds)})", "header": True})
        for build in builds:
            both = bool(build.portable_id) and build.download_id != build.portable_id
            if portable_only or (build.portable_id and not both):
                what, what_ru = "portable", "портативная"
            elif both:
                what, what_ru = "installer, portable", "установщик, портативная"
            else:
                what, what_ru = "installer", "установщик"
            option = _option(build.portable_id if portable_only else build.download_id, f"{build.version} - {what}", f"{build.version} - {what_ru}")
            if build.portable_id:
                option["portable_id"] = build.portable_id
                option["installer_id"] = build.download_id if both else ""
            if build.about:
                option["tooltip"], option["tooltip_ru"] = build.about, build.about_ru or build.about
            options.append(option)
    if not options:
        return [_option("", "No portable builds on this page" if portable_only else "No programs on this page",
                        "Портативных сборок на этой странице нет" if portable_only else "Программ на этой странице нет")]
    return options


def vendor_build_options(root: Path | str | None = None, values: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Versions of one product, or the whole catalog for the platform with the product in front."""
    del root
    vendor = selected_vendor(values)
    provider = get_provider(vendor)
    platform, product, extra = selection(values, vendor)
    if vendor == "nvidia":
        remember_my_generations(values or {})
    if vendor == "programs":
        return program_options(provider, product, bool((values or {}).get("vendor_portable_only")))  # type: ignore[return-value]
    if not product or product == WHOLE_CATALOG:
        options: list[dict[str, str]] = []
        for name in provider.products(platform):
            options.extend(_option(build.download_id, f"{name} {build.label}") for build in provider.builds(name, platform, **extra))
        if not options:
            return [_option("", f"No {provider.name} builds for {platform}", f"Сборок {provider.name} для {platform} нет")]
        return options
    builds = provider.builds(product, platform, **extra)
    portable_only = bool((values or {}).get("vendor_portable_only"))
    if portable_only:
        # The Portable switch: cards without a portable build leave, the rest tick their portable file.
        builds = [build for build in builds if build.portable_id]
    if not builds:
        return [_option("", f"No {product} builds for {platform}", f"Сборок {product} для {platform} нет")]
    # The builds of Windows are a list of hundreds: by generation, under headings that name the
    # product - 10, 11 and whatever comes next must not read alike.
    if vendor == "uupdump":
        return windows_build_options(builds)
    if vendor == "mscatalog":
        return catalog_build_options(provider, product, builds)
    options = [_option(build.portable_id if portable_only else build.download_id, build.label) for build in builds]
    # A card with two files says which id is which, so its two arrows and the
    # Portable switch can tell the installer from the portable build.
    for option, build in zip(options, builds):
        if build.portable_id:
            option["portable_id"] = build.portable_id
            option["installer_id"] = build.download_id if build.download_id != build.portable_id else ""
    # What is no release says so: "the latest stable" passes it by whatever its label reads.
    for option, build in zip(options, builds):
        if build.preview:
            option["beta"] = True  # type: ignore[assignment]
    # The newest final release is marked so its card stands out from the rest of the list - of the line
    # this PC's device is on, where the product has lines (Intel graphics: Iris Xe is not on Arc's line).
    # "The latest stable" of the window takes the card marked so.
    machine_line = getattr(provider, "machine_line", None)
    line = getattr(provider, "line", None)
    own = str(machine_line(product) or "") if callable(machine_line) and callable(line) else ""
    for option, build in zip(options, builds):
        if build.beta or build.preview or PREVIEW_WORDS.search(build.label):
            continue
        if own and line(product, build.version) != own:  # type: ignore[misc]
            continue
        if not re.search(r"\blatest\b", build.label):
            option["label"] = option["label_ru"] = f"{option['label']} - latest"
        option["latest"] = True  # type: ignore[assignment]
        break
    # What the provider ticks by itself - the driver this PC's device wants - and its line over the list.
    for option, build in zip(options, builds):
        if build.ticked:
            option["default"] = True  # type: ignore[assignment]
    over = note_option(provider, product, builds)
    if over:
        options.insert(0, over)  # type: ignore[arg-type]
    return options


# ----- shared download with resume -----


def _progress_between(context: JobContext, start: float, end: float, fraction: float) -> None:
    fraction = max(0.0, min(1.0, fraction))
    context.progress(start + (end - start) * fraction)


def _modified_time(headers: Any) -> float | None:
    """`Last-Modified` of an answer as a time of this machine's clock; None when the answer names none."""
    value = str(headers.get("Last-Modified") or "").strip()
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _remote_size(url: str, open_url: UrlOpener, headers: dict[str, str] | None = None) -> tuple[int, float | None]:
    """What the server says of the file without sending it: its size and when it was changed; (0, None) when it does not say."""
    request = Request(url, method="HEAD", headers=headers or {"User-Agent": USER_AGENT})
    try:
        with open_url(request, timeout=60) as response:
            return int(response.headers.get("Content-Length") or 0), _modified_time(response.headers)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return 0, None


def _is_the_file(target: Path, size: int, modified: float | None) -> bool:
    """Whether the file in the folder is the one the server has: the same size, and not older than the server's.

    A maker puts a new build under the old name, often of the very size (a web installer of two
    megabytes): a file fetched before that is older than it and is fetched again. A file a person
    already has is newer than its version on the server. A server that names no time is believed
    by the size alone.
    """
    if size <= 0 or not target.is_file():
        return False
    found = target.stat()
    return found.st_size == size and (modified is None or found.st_mtime >= modified)


def _strong_etag(headers: Any) -> str:
    """The ETag of an answer when it names these very bytes; a weak one ("W/...") names any equal-looking file."""
    etag = str(headers.get("ETag") or "").strip()
    return "" if not etag or etag.upper().startswith("W/") else etag


def _content_range(headers: Any) -> tuple[int, int, int] | None:
    """`Content-Range: bytes 1000-4999/5000` as (first, last, the whole length); None when the answer names no range."""
    found = re.match(r"bytes\s+(\d+)-(\d+)/(\d+)$", str(headers.get("Content-Range") or "").strip())
    return (int(found.group(1)), int(found.group(2)), int(found.group(3))) if found else None


def _read_note(part: Path, note_file: Path) -> dict[str, Any] | None:
    """What was written beside a `.part` when its download began; None when there is nothing to resume by."""
    if not part.exists() or not note_file.exists():
        return None
    try:
        note = json.loads(note_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(note, dict) or not (str(note.get("etag") or "") or str(note.get("last_modified") or "")):
        return None
    return note


def download_with_resume(
    context: JobContext,
    url: str,
    target: Path,
    label: str,
    *,
    progress_start: float = 0.0,
    progress_end: float = 1.0,
    open_url: UrlOpener = urlopen,
    expected_size: int = 0,
    headers: dict[str, str] | None = None,
) -> Path:
    """Fetch `url` into `target`; a download that broke off goes on from where it stopped - when it is the same file.

    `headers` go with every request over the program's own: the Referer a site hands its file to,
    the name it wants to be asked by.

    A finished file is not fetched again when the server says the same of it: the same size, and a
    time not later than the file's (`_is_the_file`). A server that names no size on HEAD is asked
    for the file, and the first lines of its answer decide before a byte of the body is read; when
    it names no size there either, the file is fetched anew and replaces the one in the folder.
    Cancelling keeps the `.part`, and the next run resumes it.

    The same file, as Audion Setup Tools decides it (Engine\\Downloads.ps1, Save-DownloadFile): the
    `.part` keeps a note of what the first answer said of the file - a strong ETag or Last-Modified,
    and the full size. The resume asks with If-Range, and the piece that comes back must start at
    the bytes already here, end inside the file, be as long as it says, and name the same full
    size and the same version; anything else starts over from zero, once. Without a note there is
    no resume: a fixed address may hold a new version by now, and its bytes glued after the old
    ones make a file that is neither. A piece that stops short of the end is followed by a request
    for the rest; the file is published only at its full size.

    One thing is not as there: the address is noted but does not decide. A signed link
    (TechPowerUp) is another address at every try and a mirror is another host, while the file is
    the same - If-Range and the size tell that, and a drivers package of 900 MB resumes.
    """
    # The name of a file is sometimes known only once its address is: asked here once more, before the folder is made.
    reason = too_long(target, label)
    if reason:
        raise RuntimeError(reason)
    target.parent.mkdir(parents=True, exist_ok=True)
    asked_with = {"User-Agent": USER_AGENT, **(headers or {})}
    total, changed = (expected_size, None) if expected_size else _remote_size(url, open_url, asked_with)
    if _is_the_file(target, total, changed):
        context.log(f"[CACHE] {label}: {target} ({target.stat().st_size} bytes)")
        _progress_between(context, progress_start, progress_end, 1.0)
        return target
    # A file is here and HEAD named no size (a signed address answers GET alone): the answer to the GET decides.
    undecided = total <= 0 and target.is_file() and target.stat().st_size > 0

    part = target.with_name(target.name + ".part")
    note_file = part.with_name(part.name + ".json")
    note = _read_note(part, note_file)
    if note is None:
        part.unlink(missing_ok=True)
        note_file.unlink(missing_ok=True)

    context.log(f"[DOWNLOAD] {label}")
    context.log(f"[URL] {url.split('?')[0]}")
    # One start over at most; a server that sends the rest in pieces is asked again while each piece adds bytes.
    restarts = pieces = 0
    try:
        while True:
            have = part.stat().st_size if note is not None and part.exists() else 0
            if have > 0 and int(note.get("total") or 0) > 0 and have >= int(note["total"]):  # type: ignore[union-attr]
                # As long as the file already: nothing to ask the rest of. Fetched anew rather than trusted unseen.
                part.unlink()
                note_file.unlink(missing_ok=True)
                note, have = None, 0
            request_headers = dict(asked_with)
            if have > 0:
                request_headers["Range"] = f"bytes={have}-"
                request_headers["If-Range"] = str(note.get("etag") or note.get("last_modified"))  # type: ignore[union-attr]
            with open_url(Request(url, headers=request_headers), timeout=120) as response:
                status = int(getattr(response, "status", 200) or 200)
                length = int(response.headers.get("Content-Length") or 0)
                modified = str(response.headers.get("Last-Modified") or "").strip()
                resumed = have > 0 and status == 206
                span = _content_range(response.headers) if resumed else None
                if resumed:
                    # The piece must continue these very bytes of this very file.
                    same = (span is not None and span[0] == have and span[0] <= span[1] < span[2]
                            and (int(note.get("total") or 0) <= 0 or span[2] == int(note["total"]))  # type: ignore[union-attr]
                            and (not length or length == span[1] - span[0] + 1))
                    # A server that ignores If-Range still names the version it sends: another one is another file.
                    etag_now = str(response.headers.get("ETag") or "").strip()
                    if same and note.get("etag") and etag_now and etag_now != note["etag"]:  # type: ignore[union-attr]
                        same = False
                    if same and not note.get("etag") and note.get("last_modified") and modified and modified != note["last_modified"]:  # type: ignore[union-attr]
                        same = False
                    if not same:
                        if restarts >= 1:
                            raise RuntimeError(f"Download failed: {label} (the server does not give the whole file)")
                        restarts += 1
                        context.log("[RESUME] the server gave no continuation of the same file, starting over")
                        part.unlink(missing_ok=True)
                        note_file.unlink(missing_ok=True)
                        note = None
                        continue
                    context.log(f"[RESUME] {label}: from {have} bytes")
                else:
                    if undecided and have == 0 and status == 200 and _is_the_file(target, length, _modified_time(response.headers)):
                        context.log(f"[CACHE] {label}: {target} ({target.stat().st_size} bytes)")
                        _progress_between(context, progress_start, progress_end, 1.0)
                        return target
                    # A whole answer: a fresh start - the range was ignored, or the file is another one by now.
                    if have > 0:
                        context.log("[RESUME] the server sent the whole file, starting over")
                    have = 0
                    etag = _strong_etag(response.headers)
                    if etag or modified:
                        note = {"url": url.split("?")[0], "etag": etag, "last_modified": modified, "total": length}
                        note_file.write_text(json.dumps(note), encoding="utf-8")
                    else:
                        note_file.unlink(missing_ok=True)
                        note = None
                # The whole file: a piece names it in Content-Range, a whole answer in its length.
                whole = span[2] if span is not None else length
                shown_total = whole or total
                downloaded = have
                started = time.monotonic()
                last_log = int(downloaded / shown_total * 100) // 10 * 10 if shown_total > 0 else -10
                with part.open("ab" if resumed else "wb") as handle:
                    while True:
                        if context.cancelled():
                            raise RuntimeError(f"Cancelled: {label} (partial file kept for resume)")
                        chunk = response.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        speed = (downloaded - have) / max(0.001, time.monotonic() - started)
                        if shown_total > 0:
                            fraction = downloaded / shown_total
                            _progress_between(context, progress_start, progress_end, fraction)
                            percent = int(fraction * 100)
                            context.activity(download_line(label, downloaded, shown_total, speed))
                            if percent >= last_log + 10:
                                context.log(f"[DOWNLOAD] {label}: {percent}% ({downloaded}/{shown_total} bytes)")
                                last_log = percent
                        else:
                            context.activity(download_line(label, downloaded, 0, speed))
            got = part.stat().st_size
            again = "run again to resume" if note is not None else "run again"
            # The piece itself broke off: the `.part` and its note stay for the next try.
            piece_end = span[1] + 1 if span is not None else whole
            if piece_end > 0 and got != piece_end:
                raise RuntimeError(f"Download incomplete: {label} ({got} of {whole} bytes); {again}")
            # A whole piece short of the end: the rest is asked for; the file is never published short.
            if whole > 0 and got < whole:
                pieces += 1
                if got <= have or pieces > 64:
                    raise RuntimeError(f"Download incomplete: {label} ({got} of {whole} bytes); {again}")
                context.log(f"[RESUME] the server gave a part: {got} of {whole} bytes, asking for the rest")
                continue
            break
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Download failed: {label} ({exc})") from exc
    finally:
        context.activity("")

    size = part.stat().st_size
    if total > 0 and size != total:
        raise RuntimeError(f"Download incomplete: {label} ({size} of {total} bytes); run again to resume")
    part.replace(target)
    note_file.unlink(missing_ok=True)
    context.log(f"[OK] {target} ({size} bytes)")
    _progress_between(context, progress_start, progress_end, 1.0)
    return target


def safe_file_name(name: str) -> str:
    """A file name that came from foreign data - an address, a catalog somebody else keeps - as it may be written.

    Read with its %XX decoded, it has no `/`, `\\` or `:` and no control character, is not empty,
    not `.` or `..`, and does not end with a dot or a space: a name is a name, never a path.
    Anything else is refused before a byte is written. The unpacking of an archive refuses such
    names by `zip_destination`; this is the same guard for a download.
    """
    text = str(name or "")
    plain = unquote(text)
    if (not plain or plain in {".", ".."} or plain[-1] in ". " or any(mark in plain for mark in "/\\:")
            or any(ord(char) < 32 for char in plain)):
        raise RuntimeError(f"unsafe file name: {text!r}")
    return text


def safe_folder(root: Path, relative: str) -> Path:
    """The folder a source names for its build, under the vendor's own: a relative path that stays inside it."""
    try:
        return zip_destination(root, str(relative).replace("\\", "/").rstrip("/"))
    except ValueError:
        raise RuntimeError(f"unsafe folder name: {relative!r}") from None


# The longest path Windows opens for a program that did not ask for long ones: 260 with its closing zero.
MAX_PATH = 259
# What a download writes beside its file while it goes on: the part and the note of what the server said of it.
PART_NOTE_SUFFIX = ".part.json"


def too_long(file: Path, name: str) -> str:
    """Why the file of a build cannot be fetched there - its path is longer than Windows opens - or ''.

    The longest thing a download writes is the note beside the part (`<file>.part.json`). Past 259
    characters Windows answers "file not found" for a folder that is there, and the run ended with
    that (the audit of 10.10.2026). The words name the build, the count and what to do; where the
    folders of a build lie is not changed.
    """
    longest = str(file) + PART_NOTE_SUFFIX
    if len(longest) <= MAX_PATH:
        return ""
    return (f"{name}: the path of its file is {len(longest)} characters with what a download writes beside it, and Windows opens {MAX_PATH} at most - "
            f"choose a downloads folder nearer the root of the drive ({file})")


UNTRUSTED_SUFFIX = ".untrusted"
CHECKING_FOLDER = "_checking"
UNTRUSTED_FOLDER = "_untrusted"


class NotTrusted(RuntimeError):
    """Every source of a build gave a file the checks refused; `problems` says what was wrong with each."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def set_aside(path: Path) -> Path:
    """What did not pass a check leaves its own name: `<name>.untrusted` beside it, a former one replaced.

    A person has something to look at; the program has nothing to take for a finished file - the
    next run downloads anew instead of finding the refused file "in the cache".
    """
    aside = path.with_name(path.name + UNTRUSTED_SUFFIX)
    if aside.is_dir():
        shutil.rmtree(aside, ignore_errors=True)
    else:
        aside.unlink(missing_ok=True)
    path.replace(aside)
    return aside


def forget_untrusted(folder: Path) -> None:
    """A build that passed its checks keeps nothing of the tries that did not: its `.untrusted` files and `_untrusted`."""
    shutil.rmtree(folder / UNTRUSTED_FOLDER, ignore_errors=True)
    if folder.is_dir():
        for item in folder.glob("*" + UNTRUSTED_SUFFIX):
            if item.is_file():
                item.unlink(missing_ok=True)


def download_from_sources(context: JobContext, sources: list[BuildSource], folder: Path, label: str, *,
                          progress_start: float = 0.0, progress_end: float = 1.0,
                          accept: Callable[[Path, BuildSource], list[str]] | None = None) -> tuple[Path, BuildSource]:
    """A build with several sources: they are tried in the order its vendor names them, the first that gives the file is it.

    A source that gives nothing says why and the next one is tried; what the last one says is the
    failure. A run that was cancelled tries nothing more (Audion Setup Tools, Save-FeedDownloads).

    `accept` is the checks of the file - its hash, the signature of its installers. They belong to
    the walk: a source whose file is not to be trusted has refused as much as one that gave nothing,
    and the next one is tried. When the last has refused too, NotTrusted carries what was wrong
    with each.
    """
    if not sources:
        raise RuntimeError(f"No source for {label}; refresh the version list.")
    refused: list[str] = []
    for index, source in enumerate(sources):
        last = index == len(sources) - 1
        try:
            if source.fetch is not None:
                context.log(f"[SOURCE] {source.host}")
                folder.mkdir(parents=True, exist_ok=True)
                target = Path(source.fetch(context, folder))
                context.log(f"[OK] {target} ({target.stat().st_size} bytes)")
                _progress_between(context, progress_start, progress_end, 1.0)
            else:
                url, referer = source.link()
                context.log(f"[SOURCE] {source.host}")
                headers = {"User-Agent": SOURCE_AGENT, **({"Referer": referer} if referer else {})}
                # A file its source does not name is named by its address; either way the name is foreign data.
                name = safe_file_name(source.name or unquote(_archive_name(url)))
                target = download_with_resume(context, url, folder / name, label, progress_start=progress_start,
                                              progress_end=progress_end, headers=headers)
        except RuntimeError as exc:
            if context.cancelled() or (last and not refused):
                raise
            if last:
                raise NotTrusted(refused + [f"{source.host} gave no file ({exc})"]) from exc
            context.log(f"[WARN] {source.host} gave no file ({exc}); the next source")
            continue
        problems = accept(target, source) if accept is not None else []
        if not problems:
            return target, source
        refused += problems
        if last:
            raise NotTrusted(refused)
        context.log(f"[WARN] {source.host} gave a file that is not to be trusted ({'; '.join(problems)}); the next source")
    raise AssertionError("unreachable")


def take_installer(context: JobContext, archive: Path, folder: Path, pattern: str) -> Path:
    """Only the vendor's installer out of a zip a third party made: the rest of it is not taken.

    The installer lands beside the zip under its own name, the zip goes (Audion Setup Tools,
    Expand-StationInstaller).
    """
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/")).name
            if info.is_dir() or not re.fullmatch(pattern, name, re.IGNORECASE):
                continue
            try:
                target = zip_destination(folder, name)
            except ValueError:
                continue
            with bundle.open(info) as source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle, CHUNK_BYTES)
            break
        else:
            raise RuntimeError(f"{archive.name}: no installer of the vendor inside")
    archive.unlink()
    context.log(f"[EXTRACT] {target.name} taken out of {archive.name}; the rest of the archive is not kept")
    return target


INSTALLER_SUFFIXES = (".exe", ".msi", ".cab")


def check_installers(context: JobContext, path: Path, signer: str,
                     check: Callable[[Path], tuple[str, str]] | None = None) -> list[str]:
    """The installers of a downloaded package carry the publisher's signature, or the run says: do not run them.

    `path` is the installer or the folder its package was unpacked into; every .exe, .msi and .cab
    there is asked. What is wrong comes back in words (Audion Setup Tools, Test-DownloadSigner).
    """
    if path.is_dir():
        files = sorted(item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in INSTALLER_SUFFIXES)
    else:
        files = [path] if path.suffix.lower() in INSTALLER_SUFFIXES else []
    if not files:
        context.log(f"[WARN] {path.name}: no installer in the package - nothing to check the signature of {signer} on")
        return [f"{path.name}: no installer in the package"]
    problems: list[str] = []
    for item in files:
        confirmed, status = signed_by(item, signer, check or authenticode)
        if confirmed:
            context.log(f"[OK] {item.name}: signed by {signer}")
        else:
            context.log(f"[WARN] {item.name}: the signature of {signer} is not confirmed ({status}) - do not run it")
            problems.append(f"{item.name}: signature not confirmed ({status})")
    return problems


def zip_single_root(names: list[str]) -> str:
    """The one folder every entry of the archive sits in, or '' when the archive has none.

    ThrottleStop_9.7.3.zip holds ThrottleStop/ThrottleStop.exe: unpacked as is
    it lands two levels deep, folder in folder of the same name. That root is
    dropped, so the program is at the top of its download folder.
    """
    roots: set[str] = set()
    for name in names:
        unified = name.replace("\\", "/")
        clean = unified.strip("/")
        if not clean:
            continue
        if "/" not in clean and not unified.endswith("/"):
            return ""  # a file at the top level: there is no single root
        roots.add(clean.split("/", 1)[0])
        if len(roots) > 1:
            return ""
    return next(iter(roots), "")


def zip_destination(target_dir: Path, relative: str) -> Path:
    """Where an archive entry lands, or ValueError when it would land outside the target.

    Refused: a drive, an absolute or UNC path, `..` anywhere, a colon (an NTFS
    stream), and a path that leaves the target once links on the way are resolved.
    """
    windows = PureWindowsPath(relative)
    parts = PurePosixPath(relative.replace("\\", "/")).parts
    if windows.drive or windows.root or relative.startswith(("/", "\\")) or ":" in relative or ".." in parts:
        raise ValueError(relative)
    base = target_dir.resolve()
    destination = (base / relative).resolve()
    if destination != base and base not in destination.parents:
        raise ValueError(relative)
    return destination


def extract_zip(context: JobContext, archive: Path, target_dir: Path) -> int:
    """Unpack a zip next to itself; returns the number of entries written.

    An archive whose entries all sit in one root folder is unpacked without
    that folder: the files land straight in the target. Every entry is checked
    before anything is written: one that would land outside the target refuses
    the whole archive.
    """
    context.log(f"[EXTRACT] {archive.name} -> {target_dir}")
    count = 0
    with zipfile.ZipFile(archive) as bundle:
        root = zip_single_root(bundle.namelist())
        if root:
            context.log(f"[EXTRACT] single root folder '{root}' dropped")
        plan: list[tuple[zipfile.ZipInfo, Path]] = []
        for info in bundle.infolist():
            relative = info.filename.replace("\\", "/").strip("/")
            if root:
                relative = relative[len(root):].lstrip("/")
                if not relative:
                    continue  # the root folder entry itself
            try:
                plan.append((info, zip_destination(target_dir, relative)))
            except ValueError:
                raise RuntimeError(f"Unsafe path in {archive.name}: {info.filename!r} - nothing was extracted") from None
        for info, destination in plan:
            if context.cancelled():
                raise RuntimeError("Cancelled during extraction")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, destination.open("wb") as handle:
                shutil.copyfileobj(source, handle, CHUNK_BYTES)
            count += 1
    context.log(f"[OK] extracted {count} files")
    return count


def tar_program() -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"


def extract_7z(context: JobContext, archive: Path, target_dir: Path) -> int:
    """Unpack a 7z next to itself; returns the number of files written, 0 when it could not be read.

    The tar of Windows 11 is libarchive: it reads 7z, and by itself refuses an entry that would
    leave the folder. The promises of a zip are kept: a lone root folder is dropped, and the
    names are checked before anything is written. Anything wrong with the archive or with tar -
    the archive stays as it came (Audion Setup Tools, Expand-PortableDownload).
    """
    tar = tar_program()
    if not tar.is_file():
        context.log(f"[WARN] {archive.name}: Windows has no tar.exe to read a 7z with; the archive is left as it came")
        return 0
    listed = subprocess.run([str(tar), "-tf", str(archive)], capture_output=True, text=True, errors="replace", check=False,
                            **hidden_subprocess_kwargs())
    names = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if listed.returncode != 0 or not names:
        context.log(f"[WARN] {archive.name}: tar.exe could not read it (code {listed.returncode}); the archive is left as it came")
        return 0
    context.log(f"[EXTRACT] {archive.name} -> {target_dir}")
    root = zip_single_root(names)
    if root:
        context.log(f"[EXTRACT] single root folder '{root}' dropped")
    files = 0
    for name in names:
        relative = name.replace("\\", "/").strip("/")
        if root:
            relative = relative[len(root):].lstrip("/")
            if not relative:
                continue  # the root folder entry itself
        try:
            zip_destination(target_dir, relative)
        except ValueError:
            raise RuntimeError(f"Unsafe path in {archive.name}: {name!r} - nothing was extracted") from None
        files += 0 if name.replace("\\", "/").endswith("/") else 1
    if context.cancelled():
        raise RuntimeError("Cancelled during extraction")
    target_dir.mkdir(parents=True, exist_ok=True)
    command = [str(tar), "-xf", str(archive), "-C", str(target_dir)] + (["--strip-components", "1"] if root else [])
    result = subprocess.run(command, capture_output=True, text=True, errors="replace", check=False, **hidden_subprocess_kwargs())
    if result.returncode != 0:
        context.log(f"[WARN] {archive.name}: tar.exe ended with code {result.returncode}; the archive is left as it came")
        return 0
    context.log(f"[OK] extracted {files} files")
    return files


# ----- jobs -----


def _param_text(context: JobContext, key: str, default: str = "") -> str:
    return str(context.operation.parameters.get(key, default) or "").strip()


def _param_bool(context: JobContext, key: str, default: bool = False) -> bool:
    value = context.operation.parameters.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if value is None:
        return default
    return bool(value)


def _param_list(context: JobContext, key: str) -> list[str]:
    value = context.operation.parameters.get(key, [])
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _registration(context: JobContext) -> dict[str, str]:
    return {key: _param_text(context, f"bmd_reg_{key}") for key in REGISTRATION_FIELDS}


def _output_base(context: JobContext) -> Path:
    raw = _param_text(context, "output_path")
    return Path(raw) if raw else context.paths.output


def _input_base(context: JobContext) -> Path:
    raw = _param_text(context, "input_path")
    return Path(raw) if raw else context.paths.input


DRIVERS_DIRECTORY_NAME = "Drivers"


def _vendor_root(context: JobContext, vendor_name: str) -> Path:
    if _param_bool(context, "vendor_flat", False):
        return _output_base(context)
    return _output_base(context) / VENDORS_DIRECTORY_NAME / vendor_name


UUP_WORK_DIRECTORY_NAME = "UUP"


def uup_work_root(context: JobContext) -> Path:
    """`<drive of the program>:\\UUP`: where Windows builds are unpacked and assembled.

    The UUP dump script refuses a path with spaces and DISM inside the converter
    chokes on a long one, so the build never lives under the destination the
    person chose. It gets a short fixed folder at the root of the drive the
    program runs from; only the finished image travels to the destination.
    A test may point it elsewhere through the `uup_work_root` parameter.
    """
    override = _param_text(context, "uup_work_root")
    if override:
        return Path(override)
    drive = context.paths.root.resolve().drive or Path.cwd().resolve().drive
    return Path(f"{drive}\\") / UUP_WORK_DIRECTORY_NAME


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def authenticode(path: Path) -> tuple[str, str]:
    """The Authenticode signature of a file as Windows tells it: its status ("Valid") and the subject of the signer.

    Asked of Windows PowerShell 5.1, which every Windows has. The path goes by the environment,
    not into the command; PSModulePath of the caller is not handed over - under PowerShell 7 it
    makes 5.1 load the wrong security module.
    """
    env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
    env["AUDION_SIGNED_FILE"] = str(path)
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:AUDION_SIGNED_FILE; [string]$s.Status; "
              "if ($s.SignerCertificate) { [string]$s.SignerCertificate.Subject }")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    try:
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                                capture_output=True, text=True, errors="replace", timeout=180, env=env, check=False,
                                **hidden_subprocess_kwargs())
    except (OSError, subprocess.SubprocessError) as exc:
        return f"not checked ({exc})", ""
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return (lines[0] if lines else "not checked", lines[1] if len(lines) > 1 else "")


def signed_by(path: Path, signer: str, check: Callable[[Path], tuple[str, str]] = authenticode) -> tuple[bool, str]:
    """Whether the file carries a valid signature of this publisher, and the status Windows gave."""
    status, subject = check(path)
    return status == "Valid" and bool(re.search(r"(^|,\s*)CN=" + re.escape(signer) + r"(,|$)", subject)), status


def unpack_driver_cab(context: JobContext, archive: Path, folder: Path, signer: str,
                      check: Callable[[Path], tuple[str, str]] = authenticode) -> dict[str, Any]:
    """A driver package of the Microsoft catalog: its signature checked, then unpacked beside itself.

    A package whose signature is not confirmed is not unpacked - what would be installed from it
    is a driver of the kernel - and leaves its own name (`set_aside`).
    """
    confirmed, status = signed_by(archive, signer, check)
    if not confirmed:
        aside = set_aside(archive)
        context.log(f"[WARN] {archive.name}: the signature of {signer} is not confirmed ({status}); the package is kept as {aside.name} - do not install it")
        return {"signature": status, "archive": str(aside), "problem": f"{archive.name}: signature not confirmed ({status})"}
    context.log(f"[OK] {archive.name}: signed by {signer}")
    expand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "expand.exe"
    result = subprocess.run([str(expand), "-F:*", str(archive), str(folder)], capture_output=True, text=True, errors="replace",
                            check=False, **hidden_subprocess_kwargs())
    if result.returncode != 0:
        context.log(f"[WARN] {archive.name}: expand.exe ended with code {result.returncode}; the package is left as it came")
        return {"signature": status, "problem": f"{archive.name}: not unpacked (expand.exe code {result.returncode})"}
    infs = sorted(item.name for item in folder.glob("*.inf"))
    archive.unlink()
    context.log(f"[OK] {folder}: {', '.join(infs) if infs else 'no .inf at the top of the package'}")
    return {"signature": status, "extracted": len(list(folder.iterdir())), "archive": "", "inf": infs}


def _archive_name(url: str) -> str:
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1].replace("%20", " ")


def _provider(context: JobContext) -> VendorProvider:
    return get_provider(selected_vendor(context.operation.parameters))


def _selected_builds(context: JobContext, provider: VendorProvider) -> list[VendorBuild]:
    ids = _param_list(context, "vendor_versions")
    if not ids:
        raise RuntimeError("Tick at least one version.")
    builds: list[VendorBuild] = []
    for download_id in ids:
        build = provider.build_by_id(download_id)
        if build is None:
            raise RuntimeError(f"Unknown build id {download_id}; refresh the version list.")
        builds.append(build)
    return builds


def _resolve(context: JobContext, provider: VendorProvider, build: VendorBuild) -> str:
    try:
        return provider.resolve_link(build, _registration(context))
    except RegistrationRequired as exc:
        raise RuntimeError(
            f"{exc} Fill the Blackmagic form fields (first name, last name, e-mail, phone, "
            "country code, city, street, state) and run again."
        ) from exc


def link_vendor_builds(context: JobContext) -> dict[str, object]:
    """Resolve links for the ticked versions and write them to the log and the report."""
    provider = _provider(context)
    builds = _selected_builds(context, provider)
    links: list[dict[str, str]] = []
    for index, build in enumerate(builds, start=1):
        context.activity(f"{build.name} [{build.platform}]")
        url = _resolve(context, provider, build)
        context.log(f"[LINK] {provider.name}: {build.name} [{build.platform}]")
        context.log(url)
        links.append({"vendor": provider.id, "name": build.name, "platform": build.platform, "file": _archive_name(url), "url": url})
        context.progress(index / len(builds))
    report = context.report_dir / "links.txt"
    report.write_text("\n".join(f"{item['file']}\n{item['url']}\n" for item in links), encoding="utf-8")
    context.log(f"[REPORT] {report}")
    context.log("Signed links expire within hours; plain ones stay.")
    return {"links": links, "report": str(report)}


UUP_SCRIPT = "uup_download_windows.cmd"
UUP_PATH_SOFT_LIMIT = 48


UUP_DOWNLOAD_ONLY_SCRIPT = "uup_download_only.cmd"
HOTPATCH_ORIGINALS = "_hotpatch_original"  # under UUPs: the msu as Microsoft ships it, back in place for aria2
HOTPATCH_STRIPPED = "_hotpatch_stripped"  # under UUPs: the same msu without its hotpatch part, for the converter


def download_only_script(folder: Path) -> Path:
    """A copy of the UUP dump script that stops after the downloads instead of calling the converter.

    The build then runs in two consoles: first the downloads, then the
    converter. In between, the program can look at what came down, which the
    one-piece script never allows: it starts converting the moment aria2 ends.
    """
    source = folder / UUP_SCRIPT
    text = source.read_bytes().decode("utf-8", errors="replace")
    marker = "if EXIST convert-UUP.cmd goto :START_CONVERT"
    if marker not in text:
        raise RuntimeError(f"{UUP_SCRIPT} has no converter hand-off line; the script layout changed.")
    text = text.replace(marker, "goto :EOF", 1)
    target = folder / UUP_DOWNLOAD_ONLY_SCRIPT
    target.write_bytes(text.encode("utf-8"))
    return target


def hotpatch_members(names: list[str]) -> list[str]:
    """The entries of a cumulative-update msu that belong to its hotpatch part."""
    return [name for name in names if "-hotpatch-" in name.lower()]


def wimlib_entries(wimlib: Path, archive: Path) -> list[str]:
    completed = subprocess.run([str(wimlib), "dir", str(archive), "1"], capture_output=True, text=True, errors="replace", check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"wimlib could not read {archive.name}: {(completed.stderr or completed.stdout).strip()[:200]}")
    return [line.strip().lstrip("\\/") for line in completed.stdout.splitlines() if line.strip() and line.strip() not in ("\\", "/")]


def strip_hotpatch_from_msu(context: JobContext, msu: Path, wimlib: Path) -> bool:
    """Rewrite a cumulative-update msu without its hotpatch part, so the UUP dump converter accepts it.

    Since September 2026 the monthly update for Windows 11 25H2 carries a
    hotpatch component (`*-Hotpatch-*.cab/.psf` and a HotpatchCompDB in the
    aggregated metadata). The converter, v126, looks at that metadata and
    skips the whole package as "Not Supported: HotPatchUpdate", in the console
    only, and then writes an ISO of the bare base build. Without the hotpatch
    part the msu is an ordinary cumulative update again and integrates as
    before; the hotpatch enrolment is something Windows Update does later on
    the installed system.

    The msu is a plain uncompressed WIM, so wimlib edits it in place: the
    hotpatch entries go, the metadata cab is rebuilt with makecab from its
    other members. The original is kept next to it for the downloader.
    """
    names = wimlib_entries(wimlib, msu)
    hotpatch = hotpatch_members(names)
    if not hotpatch:
        return False
    metadata_name = next((name for name in names if name.lower().endswith("aggregatedmetadata.cab")), "")
    work = msu.parent / f"{msu.stem}_hotpatch_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    commands = [f'delete "/{name}"' for name in hotpatch]
    if metadata_name:
        subprocess.run([str(wimlib), "extract", str(msu), "1", f"/{metadata_name}", f"--dest-dir={work}", "--no-acls", "--no-attributes"], capture_output=True, check=False)
        cab = work / Path(metadata_name).name
        members = work / "members"
        members.mkdir()
        system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        subprocess.run([str(system32 / "expand.exe"), "-F:*", str(cab), str(members)], capture_output=True, check=False)
        kept = sorted(item for item in members.iterdir() if item.is_file() and "hotpatchcompdb" not in item.name.lower())
        if not kept:
            raise RuntimeError(f"{msu.name}: the aggregated metadata cab could not be unpacked")
        ddf = work / "metadata.ddf"
        rebuilt_dir = work / "rebuilt"
        rebuilt_dir.mkdir()
        ddf.write_text(
            ".OPTION EXPLICIT\n.Set CabinetNameTemplate=" + Path(metadata_name).name + "\n"
            f".Set DiskDirectoryTemplate={rebuilt_dir}\n.Set Cabinet=on\n.Set Compress=on\n.Set CompressionType=MSZIP\n"
            ".Set MaxDiskSize=0\n.Set MaxDiskFileCount=0\n.Set FolderSizeThreshold=0\n.Set RptFileName=nul\n.Set InfFileName=nul\n"
            + "".join(f'"{item}"\n' for item in kept),
            encoding="utf-8",
        )
        completed = subprocess.run([str(system32 / "makecab.exe"), "/F", str(ddf)], capture_output=True, text=True, errors="replace", check=False, cwd=str(work))
        rebuilt = rebuilt_dir / Path(metadata_name).name
        if completed.returncode != 0 or not rebuilt.exists():
            raise RuntimeError(f"{msu.name}: makecab could not rebuild the metadata cab: {(completed.stderr or completed.stdout).strip()[:200]}")
        commands.append(f'delete "/{metadata_name}"')
        commands.append(f'add "{rebuilt}" "/{metadata_name}"')
    script = work / "update.txt"
    script.write_text("\n".join(commands) + "\n", encoding="utf-8")
    # wimlib takes several commands on standard input only (--command works for one)
    completed = subprocess.run([str(wimlib), "update", str(msu), "1"], input="\n".join(commands) + "\n", capture_output=True, text=True, errors="replace", check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{msu.name}: wimlib could not rewrite the package: {(completed.stderr or completed.stdout).strip()[:300]}")
    shutil.rmtree(work, ignore_errors=True)
    context.log(f"[ISO] {msu.name}: hotpatch part removed ({', '.join(Path(name).name for name in hotpatch)}); the converter takes the update now")
    return True


def restore_original_msus(folder: Path) -> int:
    """Before a download: put Microsoft's original msu files back so aria2 sees them complete; keep the stripped copies aside."""
    uups = folder / "UUPs"
    originals = uups / HOTPATCH_ORIGINALS
    stripped = uups / HOTPATCH_STRIPPED
    count = 0
    if originals.is_dir():
        stripped.mkdir(exist_ok=True)
        for original in sorted(originals.glob("*.msu")):
            current = uups / original.name
            if current.exists():
                shutil.move(str(current), str(stripped / original.name))
            shutil.move(str(original), str(current))
            count += 1
    return count


def prepare_msus_for_converter(context: JobContext, folder: Path, stripper: Callable[[JobContext, Path, Path], bool] | None = None) -> int:
    """After the downloads: give the converter msu files without hotpatch parts; originals go to `_hotpatch_original`."""
    uups = folder / "UUPs"
    if not uups.is_dir():
        return 0
    originals = uups / HOTPATCH_ORIGINALS
    stripped = uups / HOTPATCH_STRIPPED
    wimlib = folder / "bin" / "wimlib-imagex.exe"
    strip = stripper or strip_hotpatch_from_msu
    count = 0
    for duplicate in sorted(uups.glob("*-KB*.msu")):
        # aria2 keeps a file it does not recognise and downloads the original as name.1.msu:
        # a second copy of the same update, which the converter would try as well
        match = re.fullmatch(r"(.+)\.(\d+)\.msu", duplicate.name)
        if match and (uups / f"{match.group(1)}.msu").exists():
            duplicate.unlink()
            context.log(f"[ISO] {duplicate.name}: duplicate download removed")
    for msu in sorted(uups.glob("*-KB*.msu")):
        ready = stripped / msu.name
        if ready.exists():
            # stripped once already: swap the files, no rework
            originals.mkdir(exist_ok=True)
            shutil.move(str(msu), str(originals / msu.name))
            shutil.move(str(ready), str(msu))
            count += 1
            continue
        if stripper is None and not wimlib.exists():
            context.log("[WARN] wimlib-imagex.exe not found in the build folder; cumulative updates are left as they are")
            return count
        originals.mkdir(exist_ok=True)
        backup = originals / msu.name
        shutil.copy2(msu, backup)
        if strip(context, msu, wimlib):
            count += 1
        else:
            backup.unlink()
    return count


def run_uup_script(context: JobContext, folder: Path) -> str:
    """Start the UUP dump script in its own console and wait; the ISO lands next to it.

    The script refuses a path with spaces and asks for administrator rights
    itself, so both are stated here before anything starts.
    """
    script = folder / UUP_SCRIPT
    if not script.exists():
        raise RuntimeError(f"{UUP_SCRIPT} not found in {folder}; unpack the package first.")
    if " " in str(folder):
        raise RuntimeError(
            f"The UUP dump script refuses a folder path with spaces: {folder}. "
            "Pick a destination such as D:\\UUP and run again."
        )
    if len(str(folder)) > UUP_PATH_SOFT_LIMIT:
        # Seen on a real build: DISM inside the converter fails with error 87 and
        # "file name too long" from a 58-character folder, and every update is
        # discarded while the ISO still gets written - a base image with nothing in it.
        raise RuntimeError(
            f"The build folder path is {len(str(folder))} characters; DISM inside the UUP dump converter "
            f"needs it short (about {UUP_PATH_SOFT_LIMIT} or less). Pick a destination such as D:\\UUP and run again."
        )
    # The ticks of the driver list are the whole truth: this machine's drivers are
    # exported straight into the build, the ticked packages from input are copied.
    machine_ticks, input_ticks = split_driver_ticks(_param_list(context, "driver_packages"))
    exported = export_driver_packages(context, machine_ticks, folder / DRIVERS_DIRECTORY_NAME / "OS") if machine_ticks else 0
    copied = embed_input_packages(folder, _input_base(context), input_ticks) if input_ticks else 0
    add_drivers = bool(exported or copied)
    if exported:
        context.log(f"[ISO] {exported} driver package(s) of this machine exported into the build")
    if copied:
        context.log(f"[ISO] {copied} package(s) from input copied into the build")
    if add_drivers:
        context.log(f"[ISO] {exported + copied} driver package(s) go into install.wim (Drivers\\OS, AddDrivers=1)")
    else:
        context.log("[ISO] no driver ticked: the image keeps Microsoft's in-box drivers only")
    apps_mode = _param_text(context, "uup_apps_mode", "stock") or "stock"
    apps = _param_list(context, "uup_apps")
    skip_edge = converter_skips_edge(apps_mode, apps)
    enable_converter_autoexit(folder, skip_edge=skip_edge, add_drivers=add_drivers)
    context.log("[ISO] Edge browser left out (SkipEdge=1); WebView2 stays" if skip_edge else "[ISO] Edge browser stays in the image")
    chosen = apply_converter_apps(folder, apps_mode, apps)
    if apps_mode == "custom":
        context.log(f"[ISO] Store apps limited to {len(chosen)} chosen entries via CustomAppsList.txt")
    elif apps_mode == "none":
        context.log("[ISO] Store apps skipped entirely (SkipApps=1)")
    adk = adk_dism_path()
    if not adk:
        raise RuntimeError(
            "Windows ADK Deployment Tools not found. Without it the converter falls back to the system DISM, "
            "which on a Windows 11 25H2 host cannot service the image (error 87 while mounting) and the ISO "
            "comes out without updates or extra editions. Use 'Install Windows ADK Deployment Tools' first."
        )
    context.log(f"[ISO] Windows ADK Deployment Tools found; the converter will service the image with {adk}")
    # Two consoles: the downloads first, then the converter. In between the
    # cumulative updates are made digestible for the converter (see
    # strip_hotpatch_from_msu); the one-piece script gives no such moment.
    restored = restore_original_msus(folder)
    if restored:
        context.log(f"[ISO] {restored} original update package(s) put back for the downloader")
    downloader = download_only_script(folder)
    context.log(f"[ISO] step 1: {downloader.name} in a console window downloads Microsoft's files with aria2")
    # Both consoles leave the job of whoever started this program, as WinUtil does: a window that
    # fell does not take aria2 and DISM with it, and the console that stays is in plain sight.
    # STOP reaches them all the same - by their process tree, below.
    download = start_on_its_own(context, ["cmd.exe", "/c", str(downloader)], cwd=folder, console=True)
    while download.poll() is None:
        if context.cancelled():
            _kill_tree(download.pid)
            raise RuntimeError("Cancelled: the download console was closed; run again to continue")
        context.activity(f"downloading the UUP set: {folder.name}")
        time.sleep(2)
    context.activity("")
    leftovers = sorted((folder / "UUPs").glob("*.aria2")) if (folder / "UUPs").is_dir() else []
    if download.returncode != 0 or leftovers or not (folder / "UUPs").is_dir():
        raise RuntimeError(
            f"The download step ended with code {download.returncode}"
            + (f" and {len(leftovers)} file(s) still incomplete" if leftovers else "")
            + "; see the console output. Run again to continue the download."
        )
    stripped = prepare_msus_for_converter(context, folder)
    if stripped:
        context.log(f"[ISO] {stripped} cumulative update package(s) prepared for the converter")
    # Step 2 is the converter itself, not the whole script again: a second aria2
    # pass would see the rewritten msu, take it for a foreign file, download the
    # original once more next to it (2.5 GB, an hour on a slow line) and hand the
    # converter both. convert-UUP.cmd finds the UUPs folder next to it by itself.
    converter = folder / "convert-UUP.cmd"
    launcher = converter if converter.exists() else script
    context.log(f"[ISO] step 2: {launcher.name} in a console window builds the ISO")
    # One console, the converter's own: a `cmd /c start /wait` wrapper would sit
    # next to it as an empty black window for the whole build.
    process = start_on_its_own(context, ["cmd.exe", "/c", str(launcher)], cwd=folder, console=True)
    settled_since: float | None = None
    last_size = -1
    while process.poll() is None:
        if context.cancelled():
            _kill_tree(process.pid)
            raise RuntimeError("Cancelled: the ISO build console was closed; run again to continue")
        # Fallback for a converter that still waits for a key: once the ISO exists
        # and has not grown for a while, the build is over and the console is closed here.
        isos = _isos_in(folder)
        if isos:
            size = isos[0].stat().st_size
            if size == last_size:
                if settled_since is None:
                    settled_since = time.monotonic()
                elif time.monotonic() - settled_since > ISO_SETTLE_SECONDS:
                    context.log("[ISO] image stopped growing; closing the converter console")
                    _kill_tree(process.pid)
                    break
            else:
                last_size = size
                settled_since = None
        context.activity(f"ISO build running in the console: {folder.name}")
        time.sleep(2)
    context.activity("")
    isos = _isos_in(folder)
    if not isos:
        raise RuntimeError(f"The script finished (code {process.returncode}) but no ISO appeared in {folder}; see the console output.")
    errors = converter_errors(folder)
    if errors:
        # The converter still writes an ISO after DISM fails, but it is the bare
        # base image: no updates, no extra editions. Say so instead of "done".
        raise RuntimeError(
            f"The converter wrote {isos[0].name} but reported errors, so the image is the base build "
            f"without updates or extra editions: {'; '.join(errors)}. "
            "DISM on this machine must be at least as new as the build being converted; "
            "update Windows first or pick a build no newer than the host, then run again."
        )
    context.log(f"[ISO] {isos[0].name} ({isos[0].stat().st_size} bytes)")
    return str(isos[0])


BUILD_NUMBER = re.compile(r"\b(\d{5})\.(\d+)\b")


def wim_image_builds(path: Path) -> list[str]:
    """`BUILD.SPBUILD` of every image in a WIM, read from the XML resource the header points at; no DISM involved."""
    with path.open("rb") as handle:
        header = handle.read(208)
        if header[:8] != b"MSWIM\x00\x00\x00":
            raise RuntimeError(f"{path} is not a WIM file")
        packed = int.from_bytes(header[0x48:0x50], "little")
        size = packed & ((1 << 56) - 1)
        offset = int.from_bytes(header[0x50:0x58], "little", signed=True)
        handle.seek(offset)
        text = handle.read(size).decode("utf-16", errors="replace")
    builds: list[str] = []
    for body in re.findall(r"<IMAGE INDEX=\"\d+\">(.*?)</IMAGE>", text, re.S):
        major = re.search(r"<BUILD>(\d+)</BUILD>", body)
        minor = re.search(r"<SPBUILD>(\d+)</SPBUILD>", body)
        if major:
            builds.append(f"{major.group(1)}.{minor.group(1) if minor else '0'}")
    return builds


def verify_iso_build(context: JobContext, iso: Path, folder: Path, expected_version: str) -> None:
    """Refuse an image whose build is not the one that was asked for.

    The converter writes an ISO even when its DISM step fails: it is then the
    bare base image (24H2 RTM 26100.1742 for a 25H2 request), a third smaller and
    without the cumulative update, the enablement package, the app list or the
    drivers. Twice such an image passed as a finished build. The converter names
    the ISO after the build it really produced, and install.wim says the same, so
    both are compared with the requested build before the image is handed over.
    """
    wanted = BUILD_NUMBER.search(expected_version or "")
    if not wanted:
        return
    expected = f"{wanted.group(1)}.{wanted.group(2)}"
    seen: list[str] = []
    named = BUILD_NUMBER.search(iso.name)
    if named:
        seen.append(f"{named.group(1)}.{named.group(2)}")
    for wim in (folder / "ISOFOLDER" / "sources" / "install.wim", folder / "ISOFOLDER" / "sources" / "install.esd"):
        if wim.exists():
            try:
                seen.extend(wim_image_builds(wim))
            except Exception as exc:  # noqa: BLE001 - the name check still stands
                context.log(f"[WARN] could not read {wim.name}: {exc}")
            break
    wrong_build = sorted({item for item in seen if item.split(".")[0] != wanted.group(1)})
    if wrong_build:
        raise RuntimeError(
            f"The converter produced build {', '.join(wrong_build)} instead of the requested {expected}: the base image "
            f"without the cumulative update, so no 25H2 update, app list or embedded drivers went in; the update was "
            f"skipped or DISM failed inside the console. The image and the cache stay in {folder} for a look. Run again "
            f"with 'Delete cache after ISO' off and watch the converter window."
        )
    lower = sorted({item for item in seen if item != expected})
    if lower:
        # 26200.9445 for a 26200.9448 request: the cumulative update went in, the three
        # revisions on top are the hotpatch part the converter cannot take; Windows
        # Update adds it on the installed system.
        context.log(f"[ISO] image build {', '.join(lower)}, requested {expected}: the update is in, the missing revisions are the hotpatch part, Windows Update adds it later")
    else:
        context.log(f"[ISO] image build verified: {expected}")


def _unique_path(target: Path) -> Path:
    """The destination itself, or a dated subfolder when an image of that name is already there.

    Two builds of the same Windows build get the same file name; the earlier
    image is never overwritten, the newer one lands in `<date time>\name.iso`.
    """
    if not target.exists():
        return target
    stamp = time.strftime("%Y-%m-%d %H-%M")
    dated = target.parent / stamp
    dated.mkdir(parents=True, exist_ok=True)
    return dated / target.name


def _remove_tree(context: JobContext, folder: Path, attempts: int = 5) -> bool:
    """rmtree with a few retries: the converter's console may still be letting go of its files."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(folder)
            return True
        except OSError as exc:
            if attempt == attempts - 1:
                context.log(f"[WARN] build cache not removed ({exc}); delete {folder} by hand")
                return False
            time.sleep(3)
    return False


def hand_over_iso(context: JobContext, iso: Path, folder: Path, root: Path, *, clean_cache: bool) -> str:
    """Move the finished image from the build folder into the destination; drop the cache when asked.

    The build folder under `<drive>:\\UUP` holds the converter's scripts and the
    9 GB of Microsoft's files (`UUPs`), useful only for a rebuild of that very
    build. The image is what the person came for, so it goes to the destination
    folder the program was given, like every other vendor download.
    """
    root.mkdir(parents=True, exist_ok=True)
    final = _unique_path(root / iso.name)
    if final.parent != iso.parent:
        if final.drive.lower() != iso.drive.lower():
            context.log(f"[ISO] copying {iso.stat().st_size / 1e9:.1f} GB to another drive: {final}")
        shutil.move(str(iso), str(final))
        context.log(f"[ISO] moved to {final}")
    if clean_cache:
        size = sum(item.stat().st_size for item in folder.rglob("*") if item.is_file())
        if _remove_tree(context, folder):
            context.log(f"[CLEAN] build cache removed: {folder} ({size / 1e9:.1f} GB)")
    return str(final)


def file_build_number(path: Path) -> int:
    """The build field of a Windows file version (10.0.26100.2454 -> 26100), 0 when unreadable."""
    try:
        import ctypes
        from ctypes import wintypes

        version = ctypes.windll.version  # type: ignore[attr-defined]
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return 0
        buffer = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
            return 0
        pointer = ctypes.c_void_p()
        length = wintypes.UINT()
        if not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            return 0
        fixed = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        # dwFileVersionMS is field 2 (major.minor), dwFileVersionLS field 3 (build.revision)
        return int(fixed[3] >> 16)
    except Exception:  # noqa: BLE001 - a version we cannot read is treated as unknown
        return 0


def adk_dism_path() -> Path | None:
    """dism.exe from the Windows ADK Deployment Tools, and only a kit new enough for a 26100 image.

    The 22621 kit installs into the same folder and services nothing newer than
    itself, so it counts as absent: the gate stays closed and the install
    button fetches the 26100 kit on top of it.
    """
    roots = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")]
    for root in roots:
        if not root:
            continue
        candidate = Path(root) / "Windows Kits" / "10" / "Assessment and Deployment Kit" / "Deployment Tools" / "amd64" / "DISM" / "dism.exe"
        if candidate.exists() and file_build_number(candidate) >= ADK_MIN_DISM_BUILD:
            return candidate
    return None


# Microsoft's permanent link to the ADK 10.1.26100 setup. The other permanent link,
# linkid=2196127, still hands out the 22621 kit whose DISM is older than a 26100 image.
ADK_SETUP_URL = "https://go.microsoft.com/fwlink/?linkid=2289980"
ADK_MIN_DISM_BUILD = 26100
ADK_SETUP_ARGS = ("/features", "OptionId.DeploymentTools", "/quiet", "/norestart", "/ceip", "off")


def download_gate(values: dict[str, Any] | None = None) -> str:
    """Label key that explains why 'Download' is off for the current form, or ''.

    Only the Windows (UUP dump) vendor is gated: its ISO build needs the ADK's
    DISM on this machine, so the button waits until the ADK is installed - and
    administrator rights: the console of the build asks Windows for them itself and
    goes on as another process, the one started here ends at once, and the run is
    told as a download that failed. The rights are what the build needs: with "Build
    the ISO right away" off the package of scripts is only downloaded and unpacked,
    and that goes without them.
    """
    if selected_vendor(values) != "uupdump":
        return ""
    if adk_dism_path() is None:
        return "gate_adk_missing"
    if builds_the_iso(values) and not is_elevated():
        return "gate_admin_missing"
    return ""


def builds_the_iso(values: dict[str, Any] | None) -> bool:
    """`uup_run` of the form as the run will read it; a form that does not say is the default - on."""
    value = (values or {}).get("uup_run")
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def adk_version_build(version: str) -> int:
    """10.1.22621.5337 -> 22621; anything unreadable -> 0."""
    parts = str(version or "").split(".")
    try:
        return int(parts[2]) if len(parts) >= 3 else 0
    except ValueError:
        return 0


def older_adk_bundles() -> list[tuple[str, str]]:
    """Installed ADK bundles older than the kit we need: (version, quiet uninstall command).

    The ADK refuses to install over another version ("MSI replacement servicing
    is not supported"), so the old bundle has to go first. Its own cached
    adksetup.exe knows how to remove it; the command comes from the Uninstall key.
    """
    try:
        import winreg
    except ImportError:
        return []
    found: list[tuple[str, str]] = []
    roots = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for root in roots:
        try:
            base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
        except OSError:
            continue
        with base:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(base, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(base, name) as key:
                        def read(value: str) -> str:
                            try:
                                return str(winreg.QueryValueEx(key, value)[0])
                            except OSError:
                                return ""
                        provider = read("BundleProviderKey")
                        version = read("DisplayVersion")
                        command = read("QuietUninstallString")
                        display = read("DisplayName")
                except OSError:
                    continue
                if not provider or not command or not version.startswith("10.1."):
                    continue
                if "Deployment Kit" not in display and "развертывания и оценки" not in display and "Windows Kits" not in command:
                    if "adksetup" not in command.lower():
                        continue
                if adk_version_build(version) < ADK_MIN_DISM_BUILD:
                    found.append((version, command))
    return found


def _wait_process(context: JobContext, process: subprocess.Popen, activity: str, what: str) -> int:
    while process.poll() is None:
        if context.cancelled():
            process.terminate()
            raise RuntimeError(f"Cancelled: {what} was stopped; run again to finish")
        context.activity(activity)
        time.sleep(2)
    context.activity("")
    return int(process.returncode or 0)


def install_adk_deployment_tools(context: JobContext) -> dict[str, object]:
    """Fetch adksetup.exe from Microsoft and install only the Deployment Tools feature.

    The web installer downloads the feature itself (about 100 MB) and asks
    for administrator rights on its own; the job waits for it and then checks
    that the ADK's dism.exe is in place.
    """
    already = adk_dism_path()
    if already:
        context.log(f"[ADK] already installed: {already}")
        return {"installed": True, "dism": str(already), "changed": False}
    removed: list[str] = []
    for version, command in older_adk_bundles():
        context.log(f"[ADK] an older kit {version} is installed and the ADK does not upgrade in place; removing it first (administrator rights are asked)")
        process = subprocess.Popen(f"{command} /norestart", shell=True)
        code = _wait_process(context, process, f"Windows ADK {version} uninstalling...", "the ADK uninstaller")
        if code not in (0, 3010):
            raise RuntimeError(f"Removing the older ADK {version} failed with code {code}; uninstall it from Apps & features and run again")
        context.log(f"[ADK] kit {version} removed")
        removed.append(version)
    target = _output_base(context) / VENDORS_DIRECTORY_NAME / get_provider("uupdump").name / "ADK" / "adksetup.exe"
    download_with_resume(context, ADK_SETUP_URL, target, "Windows ADK setup", progress_start=0.0, progress_end=0.1)
    context.log("[ADK] installing Deployment Tools; the installer asks for administrator rights and downloads about 100 MB")
    process = subprocess.Popen([str(target), *ADK_SETUP_ARGS], cwd=str(target.parent))
    _wait_process(context, process, "Windows ADK Deployment Tools installing...", "the ADK installer")
    dism = adk_dism_path()
    if process.returncode not in (0, 3010) or dism is None:
        raise RuntimeError(
            f"The ADK installer finished with code {process.returncode} but the Deployment Tools DISM was not found; "
            "run adksetup.exe by hand and tick 'Deployment Tools'."
        )
    context.log(f"[ADK] installed: {dism}")
    if process.returncode == 3010:
        context.log("[ADK] the installer asks for a reboot before the tools are fully usable")
    return {"installed": True, "dism": str(dism), "changed": True, "reboot": process.returncode == 3010, "removed": removed}


EDGE_APP = "Microsoft.Edge"  # the Edge card of the app list; not a Store family, it drives the converter's SkipEdge


def converter_skips_edge(mode: str, apps: list[str]) -> bool:
    """SkipEdge from the app choice: 'stock' keeps Edge as Microsoft does, 'none' drops it, 'custom' follows the Edge card."""
    if mode == "none":
        return True
    if mode == "custom":
        return EDGE_APP.lower() not in {str(item).strip().lower() for item in apps}
    return False


def apply_converter_apps(folder: Path, mode: str, apps: list[str]) -> list[str]:
    """Set which Store apps the converter puts into the image; returns the entries kept.

    `stock` leaves the package as it came. `none` sets `SkipApps=1`. `custom`
    turns `CustomList=1` on and rewrites CustomAppsList.txt so that only the
    chosen package families are uncommented - everything else stays behind a
    `#` and is never installed, so there is nothing to strip afterwards.
    """
    config = folder / "ConvertConfig.ini"
    if mode not in ("none", "custom") or not config.exists():
        return []
    lines = config.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip().lower()
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        if key == "skipapps":
            lines[index] = line.split("=", 1)[0] + "=" + ("1" if mode == "none" else "0") + newline
        elif key == "customlist":
            lines[index] = line.split("=", 1)[0] + "=" + ("1" if mode == "custom" else "0") + newline
    config.write_text("".join(lines), encoding="utf-8")
    if mode != "custom":
        return []
    wanted = {item.strip().lower() for item in apps if item.strip()}
    listing = folder / "CustomAppsList.txt"
    if not listing.exists():
        raise RuntimeError(f"CustomAppsList.txt not found in {folder}; the package predates app selection.")
    kept: list[str] = []
    rewritten: list[str] = []
    for raw in listing.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("###"):
            rewritten.append(raw)
            continue
        name = stripped.lstrip("#").strip()
        if name.lower() in wanted:
            rewritten.append(name)
            kept.append(name)
        else:
            rewritten.append(f"# {name}")
    listing.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return kept


def converter_errors(folder: Path) -> list[str]:
    """Lines from the converter's ErrorLog_*.txt files, empty when it reported none."""
    errors: list[str] = []
    for path in sorted(folder.glob("ErrorLog_*.txt")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip() and line.strip() not in errors:
                errors.append(line.strip())
    return errors


ISO_SETTLE_SECONDS = 45.0


def _isos_in(folder: Path) -> list[Path]:
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".iso")


def _kill_tree(pid: int) -> None:
    """The console is `start`ed by the cmd we own, so only a tree kill reaches it."""
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)


def enable_converter_autoexit(folder: Path, *, skip_edge: bool = False, add_drivers: bool = False) -> bool:
    """Tune ConvertConfig.ini before the converter starts; True when something changed.

    `AutoExit=1` makes the converter close instead of asking for a key.
    `StartVirtual=1` makes it actually build the extra editions listed in
    `vAutoEditions`: the package from the site lists them but leaves the switch
    off, and without it the ISO ends up with the base edition alone.
    `SkipEdge=1` leaves the Edge browser out when asked; the WebView2 runtime is
    a separate feature package and stays. `AddDrivers=1` makes the converter
    run DISM /Add-Driver over the `Drivers` folder next to its script.
    """
    config = folder / "ConvertConfig.ini"
    if not config.exists():
        return False
    lines = config.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    wants_virtual = any(
        line.split("=", 1)[0].strip().lower() == "vautoeditions" and line.split("=", 1)[1].strip()
        for line in lines
        if "=" in line
    )
    changed = False
    for index, line in enumerate(lines):
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip().lower()
        value = line.split("=", 1)[1].strip()
        target = None
        if key == "autoexit":
            target = "1"
        elif key == "startvirtual" and wants_virtual:
            target = "1"
        elif key == "skipedge" and skip_edge:
            target = "1"
        elif key == "adddrivers" and add_drivers:
            target = "1"
        if target is not None and value != target:
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            lines[index] = line.split("=", 1)[0] + "=" + target + newline
            changed = True
    if changed:
        config.write_text("".join(lines), encoding="utf-8")
    return changed


def find_driver_packages(source: Path) -> list[Path]:
    """Folders under `source` that hold an .inf, whatever they are called and however deep.

    A person drops the drivers into the input folder as they like: the export's
    own subfolders, a folder named after the laptop, or the files themselves at
    the top. Every folder with an .inf in it counts as one package.
    """
    if not source.is_dir():
        return []
    packages: dict[Path, None] = {}
    for inf in sorted(source.rglob("*.inf")):
        if inf.is_file():
            packages.setdefault(inf.parent, None)
    return list(packages)


def _default_input_root() -> Path:
    return Path(__file__).resolve().parents[2] / "input"


INPUT_PACKAGE_PREFIX = "input:"  # a card of the driver list that stands for a package found under input


def input_package_id(source: Path, package: Path) -> str:
    relative = "." if package == source else package.relative_to(source).as_posix()
    return f"{INPUT_PACKAGE_PREFIX}{relative}"


def split_driver_ticks(ticks: list[str]) -> tuple[list[str], list[str]]:
    """`(published inf names of this machine, relative paths of packages under input)` from one tick list."""
    machine = [item for item in ticks if not str(item).startswith(INPUT_PACKAGE_PREFIX)]
    inputs = [str(item)[len(INPUT_PACKAGE_PREFIX):] for item in ticks if str(item).startswith(INPUT_PACKAGE_PREFIX)]
    return machine, inputs


def input_driver_options(source: Path) -> list[dict[str, Any]]:
    """Cards for the packages found under input: one per folder with an .inf, ticked, under their own header."""
    packages = find_driver_packages(source)
    if not packages:
        return []
    options: list[dict[str, Any]] = [{
        "value": "", "header": True,
        "label": f"Packages under input ({len(packages)})", "label_ru": f"Пакеты из input ({len(packages)})",
    }]
    for package in packages:
        infs = sorted(p.name for p in package.glob("*.inf"))
        shown = ", ".join(infs[:3]) + (f" +{len(infs) - 3}" if len(infs) > 3 else "")
        name = package.name if package != source else source.name
        options.append({
            "value": input_package_id(source, package),
            "label": f"{name} · {shown}", "label_ru": f"{name} · {shown}",
            "default": True, "tags": ["Input"],
        })
    return options


def copy_driver_packages(folder: Path, source: Path, packages: list[Path]) -> int:
    """Copy the given INF packages from under `source` into the build's `Drivers\\OS`; returns their count."""
    target = folder / DRIVERS_DIRECTORY_NAME / "OS"
    target.mkdir(parents=True, exist_ok=True)
    for package in packages:
        destination = target if package == source else target / package.name
        if destination != target and destination.exists():
            # two packages with the same folder name in different places: keep both
            destination = target / f"{package.name}_{abs(hash(str(package))) % 10000}"
        destination.mkdir(parents=True, exist_ok=True)
        for item in package.iterdir():
            if item.is_file():
                shutil.copy2(item, destination / item.name)
            else:
                shutil.copytree(item, destination / item.name, dirs_exist_ok=True)
    return len(packages)


def embed_input_packages(folder: Path, source: Path, relatives: list[str]) -> int:
    """Copy the ticked input packages (by their relative paths) into the build; a vanished package is skipped."""
    wanted = {relative.strip().strip("/") or "." for relative in relatives}
    present = {input_package_id(source, package)[len(INPUT_PACKAGE_PREFIX):]: package for package in find_driver_packages(source)}
    chosen = [present[relative] for relative in wanted if relative in present]
    return copy_driver_packages(folder, source, chosen) if chosen else 0


def apply_converter_drivers(folder: Path, source: Path, required: bool = True) -> int:
    """Copy every INF driver package found under `source` into the build's `Drivers\\OS`; returns their count.

    The converter feeds that folder to DISM /Add-Driver /Recurse for
    install.wim (`OS`); `ALL` and `WinPE` would also touch the boot images and
    are left alone: a machine's full driver set (GPU, audio) has no place in
    WinPE. An .exe installer is not a driver package and is not accepted.
    """
    if not source.is_dir():
        if not required:
            return 0
        raise RuntimeError(f"Input folder not found: {source}. Tick this machine's drivers, or put driver packages (folders with .inf, .sys, .cat) anywhere inside it.")
    packages = find_driver_packages(source)
    if not packages:
        if not required:
            return 0
        raise RuntimeError(f"No .inf driver packages anywhere under {source} and no driver ticked. Only unpacked INF packages are embedded; a vendor's .exe installer must be unpacked, or the driver ticked in 'This machine's drivers'.")
    return copy_driver_packages(folder, source, packages)



NETWORK_CLASS_GUIDS = {
    "{4d36e972-e325-11ce-bfc1-08002be10318}": "Net",
    "{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}": "Bluetooth",
}


def parse_pnputil_drivers(text: str) -> list[dict[str, str]]:
    """Blocks of `pnputil /enum-drivers` -> published name, original inf, provider, class, class GUID.

    Labels are localized, values are not: the block is read by position and by
    the shapes of its values (oemNN.inf, a GUID in braces), never by the label text.
    """
    drivers: list[dict[str, str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        published = next((m.group(1) for line in lines if (m := re.search(r"\b(oem\d+\.inf)\b", line, re.IGNORECASE))), "")
        if not published:
            continue
        values = [line.split(":", 1)[1].strip() if ":" in line else line for line in lines]
        guid = next((m.group(0).lower() for line in lines if (m := re.search(r"\{[0-9a-f-]{36}\}", line, re.IGNORECASE))), "")
        infs = [value for value in values if value.lower().endswith(".inf")]
        original = next((value for value in infs if value.lower() != published.lower()), published)
        guid_index = next((index for index, line in enumerate(lines) if guid and guid in line.lower()), -1)
        class_name = values[guid_index - 1] if guid_index >= 1 else ""
        provider = values[guid_index - 2] if guid_index >= 2 else ""
        # The version follows the class GUID - after the Extension ID in an extension package, so the
        # first value after the GUID shaped as a version is taken, not simply the next one.
        after = values[guid_index + 1:] if guid_index >= 0 else []
        version = next((value for value in after if re.search(r"\d+\.\d+", value) and not re.fullmatch(r"\{[0-9a-f-]{36}\}", value, re.IGNORECASE)), "")
        drivers.append({"published": published, "original": original, "provider": provider, "class": class_name, "guid": guid, "version": version})
    return drivers


def list_machine_drivers() -> list[dict[str, str]]:
    pnputil = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "pnputil.exe"
    if not pnputil.exists():
        raise RuntimeError("pnputil.exe not found; driver export works on Windows only.")
    completed = subprocess.run([str(pnputil), "/enum-drivers"], capture_output=True, check=False)
    return parse_pnputil_drivers(pnputil_text(completed.stdout or b""))


def pnputil_text(data: bytes, ansi: str | None = None) -> str:
    """What pnputil wrote into a pipe: the ANSI code page of Windows (cp1251 on a Russian one), whatever `chcp` says.

    The console door runs Python in UTF-8 mode, and read as UTF-8 the Russian names of devices
    came out as replacement signs (23.10.2026). UTF-8 is taken when the bytes are valid UTF-8 -
    a later pnputil may switch - otherwise the code page of Windows, not a guess: on a few
    Russian words among English ones the engine's general decoder took cp1251 for cp866.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(ansi or locale.getencoding(), errors="replace")


# The value that opens the record of a device in `pnputil /enum-devices`, its instance id:
# "USB\VID_8087&PID_0033\5&158e3aec&0&10", "{8e7bd593-...}\...". No other value of a record has this shape.
_DEVICE_ID = re.compile(r"^(?:[A-Z][A-Z0-9_]*|\{[0-9A-Fa-f-]{36}\})\\\S+$")


def parse_pnputil_devices(text: str) -> list[dict[str, Any]]:
    """`pnputil /enum-devices` -> a record per device: its instance id, its name and the packages it works on.

    Read by the shapes of the values, as the listing of the drivers: the labels are localized. A record
    opens with the instance id, the name of the device follows it, and every oemNN.inf in it - the
    driver and the extension drivers - is a package the device works on.
    """
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        value = line.split(":", 1)[1].strip() if ":" in line else line
        if _DEVICE_ID.match(value):
            current = {"id": value, "name": "", "drivers": []}
            devices.append(current)
        elif current is not None:
            if not current["name"]:
                current["name"] = value
            current["drivers"] += [found.lower() for found in re.findall(r"\boem\d+\.inf\b", value, re.IGNORECASE)]
    return devices


def machine_devices() -> tuple[list[dict[str, Any]], set[str]]:
    """Every device Windows knows on this machine, plugged in now or before, and the instance ids of those present now.

    Both listings go at once (about a second each). ([], set()) when Windows does not say:
    pnputil learned /enum-devices in Windows 10 2004. The bytes are read by `pnputil_text`.
    """
    pnputil = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "pnputil.exe"
    if not pnputil.exists():
        return [], set()
    runs: list[subprocess.Popen] = []
    try:
        for extra in ([], ["/connected"]):
            runs.append(subprocess.Popen([str(pnputil), "/enum-devices", *extra], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL))
        outputs = [pnputil_text(run.communicate(timeout=60)[0] or b"") for run in runs]
    except (OSError, subprocess.TimeoutExpired):
        for run in runs:
            run.kill()
        return [], set()
    if any(run.returncode != 0 for run in runs):
        return [], set()
    return parse_pnputil_devices(outputs[0]), {device["id"] for device in parse_pnputil_devices(outputs[1])}


def driver_hardware_id(published: str) -> str:
    """The first device a package is written for, from its copy in %SystemRoot%\\INF ("USB\\VID_8087&PID_0026").

    That is what tells apart the packages of one name and one version: Intel puts ibtusb.inf into
    the driver store once per Bluetooth chip.
    """
    inf = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "INF" / published
    try:
        raw = inf.read_bytes()
    except OSError:
        return ""
    text = raw.decode("utf-16", errors="replace") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", errors="replace")
    # A line of a models section: "%Desc% = InstallSection, USB\VID_8087&PID_0026[, more ids]".
    match = re.search(r"^[^;\[\r\n]*=[^,\r\n]*,\s*([A-Za-z0-9_]+\\[^\s,;]+)", text, re.MULTILINE)
    return match.group(1) if match else ""


DRIVER_CLASS_NAMES = {
    "{4d36e972-e325-11ce-bfc1-08002be10318}": ("Network", "Сеть"),
    "{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}": ("Bluetooth", "Bluetooth"),
    "{4d36e97d-e325-11ce-bfc1-08002be10318}": ("Chipset / system", "Чипсет / система"),
    "{4d36e97b-e325-11ce-bfc1-08002be10318}": ("Storage / RAID", "Диски / RAID"),
    "{4d36e96a-e325-11ce-bfc1-08002be10318}": ("Storage controller", "Контроллер дисков"),
    "{4d36e96c-e325-11ce-bfc1-08002be10318}": ("Audio", "Звук"),
    "{5989fce8-9cd0-467d-8a6a-5419e31529d4}": ("Audio effects", "Звуковые эффекты"),
    "{4d36e968-e325-11ce-bfc1-08002be10318}": ("Display", "Видео"),
    "{4d36e96b-e325-11ce-bfc1-08002be10318}": ("Keyboard", "Клавиатура"),
    "{4d36e96f-e325-11ce-bfc1-08002be10318}": ("Mouse", "Мышь"),
    "{745a17a0-74d3-11d0-b6fe-00a0c90f57da}": ("HID", "HID"),
    "{4d36e979-e325-11ce-bfc1-08002be10318}": ("Printer", "Принтер"),
    "{6bdd1fc6-810f-11d0-bec7-08002be2092f}": ("Camera", "Камера"),
    "{ca3e7ab9-b4c3-4ae6-8251-579ef933890f}": ("Camera", "Камера"),
    "{88bae032-5a81-49f0-bc3d-a4ff138216d6}": ("USB device", "USB-устройство"),
    "{36fc9e60-c465-11cf-8056-444553540000}": ("USB", "USB"),
    "{e2f84ce7-8efa-411c-aa69-97454ca4cb57}": ("Extension", "Расширение"),
    "{5c4c3332-344d-483c-8739-259e934c9cc8}": ("Software component", "Программный компонент"),
    "{f2e7dd72-6468-4e36-b6f1-6488f42c1b52}": ("Firmware", "Прошивка"),
    "{53d29ef7-377c-4d14-864b-eb3a85769359}": ("Biometric", "Биометрия"),
    "{4d36e96e-e325-11ce-bfc1-08002be10318}": ("Monitor", "Монитор"),
}


def driver_version_key(version: str) -> tuple[int, ...]:
    """'07/02/2026 24.60.0.4' -> (24, 60, 0, 4): the numeric version, the date ignored."""
    tail = version.strip().split()[-1] if version.strip() else ""
    parts = []
    for piece in tail.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def machine_driver_options(root: Path, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Cards for the 'This machine's drivers' window: one per package a device of this machine works on.

    Each card is named by its device, then the original inf, the provider and the version; the
    network classes come first and are ticked by default. The tooltip names the published package
    and every device on it with its instance id.
    """
    source = Path(str((values or {}).get("input_path") or "").strip() or (Path(root) / "input" if root else _default_input_root()))
    del values
    drivers = list_machine_drivers()
    # The driver store keeps every version Windows ever put in and, for Intel's Bluetooth, a variant
    # per chip of one version: the owner (23.10.2026) took eight equal cards of ibtusb.inf for a
    # glitch and could not tell which to take - «только рабочие и с именами устройств». Shown are
    # the packages a device of this machine works on, plugged in now or before (a USB network card in
    # a drawer is just what a fresh system needs to get online), each named by its device. When
    # Windows does not say which (pnputil older than 2004), every package, as before.
    devices, present = machine_devices() if drivers else ([], set())
    owners: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        for published in device["drivers"]:
            owners.setdefault(published, []).append(device)
    if owners:
        drivers = [driver for driver in drivers if driver["published"].lower() in owners]
    families: dict[str, list[dict[str, str]]] = {}
    for driver in drivers:
        families.setdefault(driver["original"].lower(), []).append(driver)

    def users(driver: dict[str, str]) -> list[dict[str, Any]]:
        return owners.get(driver["published"].lower(), [])

    def plugged(driver: dict[str, str]) -> bool:
        return any(device["id"] in present for device in users(driver))

    def caption(driver: dict[str, str]) -> str:
        names = list(dict.fromkeys(device["name"] for device in users(driver) if device["name"]))
        return (names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "")) if names else ""

    # Of each network driver the package of a device plugged in now is ticked by default; with none
    # plugged in (an adapter in a drawer) the newest version.
    default_names: set[str] = set()
    for family in families.values():
        network = [driver for driver in family if driver["guid"] in NETWORK_CLASS_GUIDS]
        if not network:
            continue
        used = [driver for driver in network if plugged(driver)]
        default_names.update(driver["published"] for driver in (used or [max(network, key=lambda d: driver_version_key(d.get("version", "")))]))

    class_order = list(DRIVER_CLASS_NAMES)  # network first, then the way the table reads; unknown classes last

    def class_names(driver: dict[str, str]) -> tuple[str, str]:
        return DRIVER_CLASS_NAMES.get(driver["guid"], (driver["class"] or "Other", driver["class"] or "Прочее"))

    def rank(driver: dict[str, str]) -> tuple[int, str, str, bool, tuple[int, ...]]:
        position = class_order.index(driver["guid"]) if driver["guid"] in class_order else len(class_order)
        newer_first = tuple(-part for part in driver_version_key(driver.get("version", "")))
        return (position, class_names(driver)[0].lower(), (caption(driver) or driver["original"]).lower(), not plugged(driver), newer_first)

    # The cards sit under a header per class: a header is an option without a
    # value, which the grid draws as a caption spanning the row.
    options: list[dict[str, Any]] = []
    current_class: tuple[str, str] | None = None
    for driver in sorted(drivers, key=rank):
        names = class_names(driver)
        if names != current_class:
            current_class = names
            count = sum(1 for item in drivers if class_names(item) == names)
            options.append({"value": "", "label": f"{names[0]} ({count})", "label_ru": f"{names[1]} ({count})", "header": True})
        provider = driver["provider"] or "?"
        version = driver.get("version", "")
        label = f"{driver['original']} · {provider}" + (f" {version}" if version else "")
        if owners:
            name = caption(driver)
            head = f"{name} · " if name else ""
            away, away_ru = ("", "") if plugged(driver) else (" · not connected", " · не подключено")
            label, label_ru = head + label + away, head + label + away_ru
            on = users(driver)[:6]
            more = len(users(driver)) - len(on)
            tooltip = driver["published"] + ": " + "; ".join(f"{d['name']} ({d['id']})" + ("" if d["id"] in present else ", not connected") for d in on) + (f"; and {more} more" if more else "")
            tooltip_ru = driver["published"] + ": " + "; ".join(f"{d['name']} ({d['id']})" + ("" if d["id"] in present else ", не подключено") for d in on) + (f"; и ещё {more}" if more else "")
        else:
            # Windows did not say which devices work on what: every package, and two of one name tell
            # the device they are written for (the revision tells nothing apart and is cut).
            device = driver_hardware_id(driver["published"])
            tail = ""
            if len(families[driver["original"].lower()]) > 1:
                tail = " · " + (re.sub(r"&REV_[0-9A-F]+", "", device, flags=re.IGNORECASE) if device else driver["published"])
            label = label_ru = label + tail
            tooltip = driver["published"] + (f", for {device}" if device else "")
            tooltip_ru = driver["published"] + (f", для {device}" if device else "")
        options.append({
            "value": driver["published"],
            "label": label,
            "label_ru": label_ru,
            "default": driver["published"] in default_names,
            "tags": [names[0]],
            "tooltip": tooltip,
            "tooltip_ru": tooltip_ru,
        })
    if not options:
        options.append(_option("", "No third-party drivers found on this machine", "Сторонних драйверов на этой машине не найдено"))
    return input_driver_options(source) + options


def export_selected_drivers(context: JobContext) -> dict[str, object]:
    """`pnputil /export-driver` of the ticked drivers into <output>\\Drivers.

    Each driver lands in its own folder with the .inf, .sys and .cat Windows
    already has for it, ready for `Drivers\\OS` of a UUP dump build or for
    `pnputil /add-driver` on a fresh system. Nothing on the machine changes.
    """
    chosen, input_ticks = split_driver_ticks(_param_list(context, "driver_packages"))
    if input_ticks:
        context.log(f"[DRIVERS] {len(input_ticks)} package(s) from input are files already and are not exported")
    if not chosen:
        raise RuntimeError("Tick at least one driver of this machine.")
    destination = _output_base(context) / DRIVERS_DIRECTORY_NAME
    exported = export_driver_packages(context, chosen, destination)
    size = sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())
    context.log(f"[DONE] {exported} driver package(s), {size / 1e6:.0f} MB in {destination}")
    context.log("[NEXT] a Windows build of this machine embeds the ticked drivers on its own; the folder is for other machines: put it anywhere under their input, it shows up in their list as ticked cards.")
    return {"packages": exported, "bytes": size, "folder": str(destination)}


PnputilRunner = Callable[[list[str]], subprocess.CompletedProcess]


def _run_pnputil(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, errors="replace", check=False)


def export_driver_packages(context: JobContext, chosen: list[str], destination: Path, runner: PnputilRunner | None = None) -> int:
    """`pnputil /export-driver` of the given published names into `destination`, one folder per package; returns how many made it."""
    destination.mkdir(parents=True, exist_ok=True)
    pnputil = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "pnputil.exe"
    if runner is None and not pnputil.exists():
        raise RuntimeError("pnputil.exe not found; driver export works on Windows only.")
    run = runner or _run_pnputil
    known = {driver["published"].lower(): driver for driver in list_machine_drivers()} if runner is None else {}
    context.log(f"[DRIVERS] exporting {len(chosen)} driver package(s) -> {destination}")
    exported = 0
    for index, published in enumerate(chosen, start=1):
        driver = known.get(str(published).lower(), {"published": str(published), "original": str(published), "provider": "", "guid": "", "class": ""})
        context.activity(f"pnputil /export-driver {driver['published']} ({index}/{len(chosen)})")
        # one folder per package: pnputil drops a single driver's files flat into the target
        package_dir = destination / f"{Path(driver['original']).stem}_{Path(driver['published']).stem}"
        package_dir.mkdir(parents=True, exist_ok=True)
        completed = run([str(pnputil), "/export-driver", driver["published"], str(package_dir)])
        names = DRIVER_CLASS_NAMES.get(driver["guid"], (driver["class"] or "Other", ""))
        if completed.returncode in (0, 259):
            exported += 1
            context.log(f"[OK] {names[0]}: {driver['original']} ({driver['provider']})")
        else:
            context.log(f"[WARN] {driver['published']} ({driver['original']}) not exported: {(completed.stderr or completed.stdout).strip()[:160]}")
        context.progress(index / len(chosen))
    context.activity("")
    return exported


def download_vendor_builds(context: JobContext) -> dict[str, object]:
    """Fetch the ticked versions into `Vendors\\<Vendor>\\<archive name>`.

    A file that did not pass its checks does not stay under its own name (`set_aside`), so the next
    run downloads anew; the run goes on to the next build and ends as failed.
    """
    provider = _provider(context)
    builds = _selected_builds(context, provider)
    # A Windows package is a script bundle: it is always unpacked and the zip dropped,
    # the zip switches are for the other vendors.
    extract = _param_bool(context, "vendor_extract", True) or provider.id == "uupdump"
    delete_zip = _param_bool(context, "vendor_delete_zip", True) or provider.id == "uupdump"
    build_iso = _param_bool(context, "uup_run", False) and provider.id == "uupdump"
    clean_cache = _param_bool(context, "uup_clean_cache", False)
    root = _vendor_root(context, provider.name)
    results: list[dict[str, Any]] = []
    # The builds that were fetched and are not to be trusted, each with what was wrong.
    failed: list[list[str]] = []
    total = len(builds)
    fetch = getattr(provider, "fetch", None)
    sources_of = getattr(provider, "sources", None)
    expected_sha256 = getattr(provider, "expected_sha256", None)
    vendor_signer = str(getattr(provider, "signer", "") or "")
    signer_for = getattr(provider, "signer_for", None)

    # Before the first request: every build by the name it goes under - its folder, its file, the note of its part.
    # A Windows package is built in a short folder of its own, not under the destination.
    if not callable(fetch) and builds:
        # The one whose path is the longest is named: with it short enough, so are the rest.
        longest = max(builds, key=lambda build: len(Path(build.name).stem) + len(build.name))
        name = safe_file_name(longest.name)
        reason = too_long(root / Path(name).stem / name, longest.name)
        if reason:
            raise RuntimeError(reason)

    for index, build in enumerate(builds, start=1):
        start = (index - 1) / total
        end = index / total
        download_end = start + (end - start) * (0.85 if extract else 1.0)
        label = f"{build.name} [{build.platform}]"
        # Whose signature the installers of this build must carry: the vendor's for all its files, or the build's own.
        signer = (str(signer_for(build) or "") if callable(signer_for) else "") or vendor_signer
        entry: dict[str, Any] = {"vendor": provider.id, "name": build.name, "platform": build.platform}

        def settle(archive: Path, source: BuildSource | None, *, folder: Path) -> list[str]:
            """The file against what its source says of it, then unpacked: what is wrong with it, or nothing.

            Asked once for a build of one source and after every source of a build that has several.
            What a former source left in the entry goes first.
            """
            for key in ("source", "sha256", "signature", "extracted", "inf"):
                entry.pop(key, None)
            entry.update(folder=str(folder), archive=str(archive))
            if source is not None:
                entry["source"] = source.host
                wanted, said_by = source.sha256, "the catalog of the packages"
                if source.checksum is not None:
                    # The source was told to check: the sum is asked of it now that the file has a name,
                    # and a file it can name no sum for is trusted as little as one whose sum differs.
                    said_by = source.said_by or f"{source.host} itself"
                    try:
                        wanted = str(source.checksum(archive.name) or "")
                        why = "" if wanted else f"{said_by} names no sum for it"
                    except RuntimeError as exc:
                        if context.cancelled():
                            raise
                        wanted, why = "", str(exc)
                    if not wanted:
                        aside = set_aside(archive)
                        context.log(f"[WARN] {archive.name}: {why} - its sum was to be checked and there is none; it is kept as {aside.name}, do not run it")
                        entry.update(sha256="not named", archive=str(aside))
                        return [f"{archive.name}: {why}"]
            else:
                # Whose page the hash is from, when it is not the vendor's own: NVIDIA's files that come from TechPowerUp.
                wanted, said_by = (str(expected_sha256(build) or "") if callable(expected_sha256) else ""), str(getattr(provider, "sha256_from", "") or provider.name)
            if wanted:
                if file_sha256(archive) != wanted.upper():
                    aside = set_aside(archive)
                    context.log(f"[WARN] {archive.name}: SHA256 differs from the one {said_by} gives - the file is not the one; it is kept as {aside.name}, do not run it")
                    entry.update(sha256="differs", archive=str(aside))
                    return [f"{archive.name}: SHA256 differs from the one {said_by} gives"]
                context.log(f"[OK] {archive.name}: SHA256 matches {said_by}")
                entry["sha256"] = "matches"
            if source is not None and source.installer and archive.suffix.lower() == ".zip":
                try:
                    archive = take_installer(context, archive, folder, source.installer)
                except (RuntimeError, zipfile.BadZipFile) as exc:
                    aside = set_aside(archive)
                    context.log(f"[WARN] {exc}; the archive is kept as {aside.name}")
                    entry["archive"] = str(aside)
                    return [str(exc)]
                entry["archive"] = str(archive)
            suffix = archive.suffix.lower()
            if signer and suffix == ".cab":
                unpacked = unpack_driver_cab(context, archive, folder, signer)
                problem = str(unpacked.pop("problem", "") or "")
                entry.update(unpacked)
                return [problem] if problem else []
            if signer and suffix in {".zip", ".7z"}:
                # A package whose installers are checked for a signature is unpacked whatever the switches say - they are
                # checked as files - and apart: its files reach the folder of the build only once every installer is confirmed.
                checking = folder / CHECKING_FOLDER
                shutil.rmtree(checking, ignore_errors=True)
                try:
                    count = extract_zip(context, archive, checking) if suffix == ".zip" else extract_7z(context, archive, checking)
                except (RuntimeError, zipfile.BadZipFile) as exc:
                    if context.cancelled():
                        raise
                    count = 0
                    context.log(f"[WARN] {archive.name}: {exc}")
                if not count:
                    shutil.rmtree(checking, ignore_errors=True)
                    aside = set_aside(archive)
                    context.log(f"[WARN] {archive.name}: nothing could be unpacked from it; it is kept as {aside.name}")
                    entry["archive"] = str(aside)
                    return [f"{archive.name}: the package could not be unpacked"]
                unsigned = check_installers(context, checking, signer)
                archive.unlink()
                if unsigned:
                    untrusted = folder / UNTRUSTED_FOLDER
                    shutil.rmtree(untrusted, ignore_errors=True)
                    checking.replace(untrusted)
                    context.log(f"[WARN] {archive.name}: unpacked into {untrusted} and left there - do not run what is in it")
                    entry.update(signature="not confirmed", archive=str(untrusted))
                    return unsigned
                for item in checking.iterdir():
                    place = folder / item.name
                    if place.is_dir():
                        shutil.rmtree(place, ignore_errors=True)
                    else:
                        place.unlink(missing_ok=True)
                    item.replace(place)
                checking.rmdir()
                context.log(f"[CLEAN] {archive.name} removed")
                entry.update(extracted=count, signature="Valid", archive="")
                return []
            if signer:
                unsigned = check_installers(context, archive, signer)
                if unsigned:
                    aside = set_aside(archive)
                    context.log(f"[WARN] {archive.name}: kept as {aside.name}")
                    entry.update(signature="not confirmed", archive=str(aside))
                    return unsigned
                entry["signature"] = "Valid"
                return []
            if (extract or build_iso) and suffix == ".zip":
                entry["extracted"] = extract_zip(context, archive, folder)
                if delete_zip:
                    archive.unlink()
                    context.log(f"[CLEAN] {archive.name} removed")
                    entry["archive"] = ""
            elif extract and suffix == ".7z":
                # What tar could not read stays: an archive is removed only once its files are out.
                entry["extracted"] = extract_7z(context, archive, folder)
                if entry["extracted"] and delete_zip:
                    archive.unlink()
                    context.log(f"[CLEAN] {archive.name} removed")
                    entry["archive"] = ""
            return []

        if callable(fetch):
            file_name = build.name
            # Windows packages are unpacked and built in <drive>:\UUP, not under the destination.
            folder = uup_work_root(context) / Path(file_name).stem
            archive = folder / file_name
            context.log(f"[FETCH] {label}")
            fetch(build, archive)
            context.log(f"[OK] {archive} ({archive.stat().st_size} bytes)")
            _progress_between(context, start, download_end, 1.0)
            problems = settle(archive, None, folder=folder)
        elif callable(sources_of):
            found = sources_of(build)
            # The folder its vendor names for the build, else the name of its file.
            folder = safe_folder(root, found[0].folder) if found and found[0].folder else root / Path(safe_file_name(build.name)).stem
            folder.mkdir(parents=True, exist_ok=True)
            try:
                download_from_sources(context, found, folder, label, progress_start=start, progress_end=download_end,
                                      accept=lambda archive, source, folder=folder: settle(archive, source, folder=folder))
                problems = []
            except NotTrusted as exc:
                problems = exc.problems
                entry.setdefault("folder", str(folder))
        else:
            url = _resolve(context, provider, build)
            # A catalog whose own file names are a guid and a hash names its packages itself.
            file_name = safe_file_name(build.name if getattr(provider, "names_its_files", False) else _archive_name(url))
            folder = root / Path(file_name).stem
            folder.mkdir(parents=True, exist_ok=True)
            archive = folder / file_name
            download_with_resume(context, url, archive, label, progress_start=start, progress_end=download_end)
            problems = settle(archive, None, folder=folder)

        if problems:
            failed.append(problems)
        else:
            forget_untrusted(folder)
            if build_iso:
                iso = Path(run_uup_script(context, folder))
                verify_iso_build(context, iso, folder, build.version)
                entry["iso"] = hand_over_iso(context, iso, folder, root, clean_cache=clean_cache)
                if clean_cache:
                    entry["folder"] = ""
        context.progress(end)
        results.append(entry)

    if failed:
        raise RuntimeError(f"{len(failed)} of {len(results)} build(s) are not to be trusted: " + "; ".join(problem for problems in failed for problem in problems))
    context.log(f"[DONE] {len(results)} build(s) in {root}")
    return {"downloaded": len(results), "results": results, "output": str(root)}

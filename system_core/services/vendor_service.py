"""Vendor builds in the GUI: the version list for the form and the download jobs.

One form serves every vendor. A `vendor` switch picks the provider, each
vendor has its own platform and product toggles, and the shared versions
list comes from the provider. The download job resolves a link per ticked
version, fetches it with resume into its own folder under
`Vendors\\<Vendor>\\<archive name>` and unpacks a zip on request, leaving
exactly what a person would have made by hand.
"""

from __future__ import annotations

from pathlib import Path
import os
import re
import shutil
import subprocess
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

from system_core.core.jobs import JobContext
from system_core.vendors import VENDOR_IDS, RegistrationRequired, VendorBuild, VendorProvider, get_provider
from system_core.vendors.blackmagic import REGISTRATION_FIELDS


VENDORS_DIRECTORY_NAME = "Vendors"
USER_AGENT = "Audion-Get-Vendors"
CHUNK_BYTES = 1024 * 1024
WHOLE_CATALOG = "*"
DEFAULT_VENDOR = VENDOR_IDS[0]

# Which form fields carry each vendor's choices: the platform field (empty when
# the vendor has one platform), the product field, and extra switches handed to
# the provider by name.
VENDOR_FIELDS: dict[str, tuple[str, str, dict[str, str]]] = {
    "blackmagic": ("bmd_platform", "bmd_product", {}),
    "affinity": ("aff_platform", "aff_product", {}),
    "nvidia": ("", "nv_product", {"series": "nv_series", "form": "nv_form", "mine": "nv_my_generations"}),
    # TechPowerUp: no single product field; `kind` names which of the per-kind product fields is read.
    "techpowerup": ("", "", {"kind": "tpu_kind", "drivers": "tpu_driver", "tools": "tpu_tool", "monitor": "tpu_monitor", "bench": "tpu_bench", "bios": "tpu_bios"}),
    "uupdump": ("uup_platform", "uup_product", {"lang": "uup_lang", "edition": "uup_edition", "virtual": "uup_virtual"}),
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


def vendor_build_options(root: Path | str | None = None, values: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Versions of one product, or the whole catalog for the platform with the product in front."""
    del root
    vendor = selected_vendor(values)
    provider = get_provider(vendor)
    platform, product, extra = selection(values, vendor)
    if vendor == "nvidia":
        remember_my_generations(values or {})
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
    # A Windows card names its product: "Windows 11 25H2 26200.9278 ..." - 10, 11 and
    # whatever comes next must not read alike in a list of hundreds.
    if vendor == "uupdump":
        options = [_option(build.download_id, f"{build.product} {build.label}") for build in builds]
    else:
        options = [_option(build.portable_id if portable_only else build.download_id, build.label) for build in builds]
    # A card with two files says which id is which, so its two arrows and the
    # Portable switch can tell the installer from the portable build.
    for option, build in zip(options, builds):
        if build.portable_id:
            option["portable_id"] = build.portable_id
            option["installer_id"] = build.download_id if build.download_id != build.portable_id else ""
    # The newest final release is marked so its card stands out from the rest of the list.
    for option, build in zip(options, builds):
        if build.beta or re.search(r"\b(beta|preview|insider|rs_prerelease)\b", build.label, re.IGNORECASE):
            continue
        if not re.search(r"\blatest\b", build.label):
            option["label"] = option["label_ru"] = f"{option['label']} - latest"
        break
    if vendor == "uupdump":
        # A Windows generation (22H2, 24H2, 25H2...) gets new cumulative builds for years;
        # the newest build of each generation is gold, the rest of that generation is history.
        seen: set[str] = set()
        for option, build in zip(options, builds):
            generation = str(build.version or "").split(" ", 1)[0].strip().upper()
            if not generation or build.beta or re.search(r"\b(beta|preview|insider|rs_prerelease)\b", build.label, re.IGNORECASE):
                continue
            if generation in seen:
                continue
            seen.add(generation)
            for key in ("label", "label_ru"):
                option[key] = f"★ {option[key]}"
    return options


# ----- shared download with resume -----


def _human_size(size: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def _progress_between(context: JobContext, start: float, end: float, fraction: float) -> None:
    fraction = max(0.0, min(1.0, fraction))
    context.progress(start + (end - start) * fraction)


def _remote_size(url: str, open_url: UrlOpener) -> int:
    request = Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with open_url(request, timeout=60) as response:
            return int(response.headers.get("Content-Length") or 0)
    except (HTTPError, URLError, TimeoutError, OSError):
        return 0


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
) -> Path:
    """Fetch `url` into `target`, continuing a `.part` left by an earlier run.

    A finished file is trusted when its size matches the server's and is not
    fetched again. Cancelling keeps the `.part` so the next run resumes it.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    total = expected_size or _remote_size(url, open_url)
    if target.exists() and target.stat().st_size > 0 and (total <= 0 or target.stat().st_size == total):
        context.log(f"[CACHE] {label}: {target} ({target.stat().st_size} bytes)")
        _progress_between(context, progress_start, progress_end, 1.0)
        return target

    part = target.with_name(target.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if total > 0 and offset >= total:
        offset = 0
        part.unlink()
    headers = {"User-Agent": USER_AGENT}
    if offset > 0:
        headers["Range"] = f"bytes={offset}-"
        context.log(f"[RESUME] {label}: from {offset} bytes")
    else:
        context.log(f"[DOWNLOAD] {label}")
    context.log(f"[URL] {url.split('?')[0]}")

    request = Request(url, headers=headers)
    try:
        with open_url(request, timeout=120) as response:
            status = int(getattr(response, "status", 200) or 200)
            if offset > 0 and status != 206:
                # The server ignored the range: start over rather than glue a full body after a partial one.
                context.log("[RESUME] server does not resume, starting over")
                offset = 0
            if total <= 0:
                length = int(response.headers.get("Content-Length") or 0)
                total = length + offset if length > 0 else 0
            mode = "ab" if offset > 0 else "wb"
            downloaded = offset
            started = time.monotonic()
            last_log = -10
            with part.open(mode) as handle:
                while True:
                    if context.cancelled():
                        context.activity("")
                        raise RuntimeError(f"Cancelled: {label} (partial file kept for resume)")
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    elapsed = max(0.001, time.monotonic() - started)
                    speed = (downloaded - offset) / elapsed
                    if total > 0:
                        fraction = downloaded / total
                        _progress_between(context, progress_start, progress_end, fraction)
                        percent = int(fraction * 100)
                        context.activity(
                            f"{label}: {_human_size(downloaded)} / {_human_size(total)} ({percent}%), {_human_size(speed)}/s"
                        )
                        if percent >= last_log + 10:
                            context.log(f"[DOWNLOAD] {label}: {percent}% ({downloaded}/{total} bytes)")
                            last_log = percent
                    else:
                        context.activity(f"{label}: {_human_size(downloaded)}, {_human_size(speed)}/s")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Download failed: {label} ({exc})") from exc
    finally:
        context.activity("")

    size = part.stat().st_size
    if total > 0 and size != total:
        raise RuntimeError(f"Download incomplete: {label} ({size} of {total} bytes); run again to resume")
    part.replace(target)
    context.log(f"[OK] {target} ({size} bytes)")
    _progress_between(context, progress_start, progress_end, 1.0)
    return target


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


def extract_zip(context: JobContext, archive: Path, target_dir: Path) -> int:
    """Unpack a zip next to itself; returns the number of entries written.

    An archive whose entries all sit in one root folder is unpacked without
    that folder: the files land straight in the target.
    """
    context.log(f"[EXTRACT] {archive.name} -> {target_dir}")
    count = 0
    with zipfile.ZipFile(archive) as bundle:
        root = zip_single_root(bundle.namelist())
        if root:
            context.log(f"[EXTRACT] single root folder '{root}' dropped")
        for info in bundle.infolist():
            if context.cancelled():
                raise RuntimeError("Cancelled during extraction")
            relative = info.filename.replace("\\", "/").strip("/")
            if root:
                relative = relative[len(root):].lstrip("/")
                if not relative:
                    continue  # the root folder entry itself
            destination = target_dir / relative
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, destination.open("wb") as handle:
                shutil.copyfileobj(source, handle, CHUNK_BYTES)
            count += 1
    context.log(f"[OK] extracted {count} files")
    return count


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
    add_drivers = _param_bool(context, "uup_add_drivers", False)
    if add_drivers:
        source = _input_base(context)
        count = apply_converter_drivers(folder, source)
        context.log(f"[ISO] {count} driver package(s) found under {source} go into install.wim (Drivers\\OS, AddDrivers=1)")
    enable_converter_autoexit(folder, skip_edge=_param_bool(context, "uup_skip_edge", False), add_drivers=add_drivers)
    apps_mode = _param_text(context, "uup_apps_mode", "stock") or "stock"
    chosen = apply_converter_apps(folder, apps_mode, _param_list(context, "uup_apps"))
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
    context.log(f"[ISO] starting {UUP_SCRIPT} in a console window; it downloads from Microsoft with aria2 and builds the ISO")
    context.log("[ISO] the window asks for administrator rights; the build takes a while and shows its own progress")
    # One console, the converter's own: a `cmd /c start /wait` wrapper would sit
    # next to it as an empty black window for the whole build.
    process = subprocess.Popen(
        ["cmd.exe", "/c", str(script)],
        cwd=str(folder),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )
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
    DISM on this machine, so the button waits until the ADK is installed.
    """
    if selected_vendor(values) == "uupdump" and adk_dism_path() is None:
        return "gate_adk_missing"
    return ""


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


def apply_converter_drivers(folder: Path, source: Path) -> int:
    """Copy every INF driver package found under `source` into the build's `Drivers\\OS`; returns their count.

    The converter feeds that folder to DISM /Add-Driver /Recurse for
    install.wim (`OS`); `ALL` and `WinPE` would also touch the boot images and
    are left alone: a machine's full driver set (GPU, audio) has no place in
    WinPE. An .exe installer is not a driver package and is not accepted.
    """
    if not source.is_dir():
        raise RuntimeError(f"Input folder not found: {source}. Put the driver packages (folders with .inf, .sys, .cat) anywhere inside it, or export this machine's drivers first.")
    packages = find_driver_packages(source)
    if not packages:
        raise RuntimeError(f"No .inf driver packages anywhere under {source}. Only unpacked INF packages are embedded; a vendor's .exe installer must be unpacked, or the driver exported with 'This machine's drivers'.")
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
        version = values[guid_index + 1] if 0 <= guid_index < len(values) - 1 else ""
        drivers.append({"published": published, "original": original, "provider": provider, "class": class_name, "guid": guid, "version": version})
    return drivers


def list_machine_drivers() -> list[dict[str, str]]:
    pnputil = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "pnputil.exe"
    if not pnputil.exists():
        raise RuntimeError("pnputil.exe not found; driver export works on Windows only.")
    completed = subprocess.run([str(pnputil), "/enum-drivers"], capture_output=True, text=True, errors="replace", check=False)
    return parse_pnputil_drivers(completed.stdout or "")


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
    "{36fc9e60-c465-11cf-8056-444553540000}": ("USB", "USB"),
    "{e2f84ce7-8efa-411c-aa69-97454ca4cb57}": ("Extension", "Расширение"),
    "{5c4c3332-344d-483c-8739-259e934c9cc8}": ("Software component", "Программный компонент"),
    "{f2e7dd72-6468-4e36-b6f1-6488f42c1b52}": ("Firmware", "Прошивка"),
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
    """Cards for the 'This machine's drivers' window: one per third-party driver, network ones ticked by default.

    Sorted with the network classes first, then by class name, so the ticked
    ones sit on top. Each card names the class, the original inf and the
    provider; the tooltip adds the published name and the version.
    """
    del root, values
    drivers = list_machine_drivers()
    # The driver store keeps older versions of the same inf next to the current one;
    # only the newest of each network driver is ticked by default.
    newest: dict[str, dict[str, str]] = {}
    for driver in drivers:
        if driver["guid"] not in NETWORK_CLASS_GUIDS:
            continue
        family = driver["original"].lower()
        if family not in newest or driver_version_key(driver.get("version", "")) > driver_version_key(newest[family].get("version", "")):
            newest[family] = driver
    default_names = {driver["published"] for driver in newest.values()}

    def rank(driver: dict[str, str]) -> tuple[int, str, str]:
        network = 0 if driver["guid"] in NETWORK_CLASS_GUIDS else 1
        names = DRIVER_CLASS_NAMES.get(driver["guid"], (driver["class"] or "Other", driver["class"] or "Прочее"))
        return (network, names[0].lower(), driver["original"].lower())

    options: list[dict[str, Any]] = []
    for driver in sorted(drivers, key=rank):
        names = DRIVER_CLASS_NAMES.get(driver["guid"], (driver["class"] or "Other", driver["class"] or "Прочее"))
        provider = driver["provider"] or "?"
        version = driver.get("version", "")
        options.append({
            "value": driver["published"],
            "label": f"{names[0]}: {driver['original']} · {provider}" + (f" {version}" if version else ""),
            "label_ru": f"{names[1]}: {driver['original']} · {provider}" + (f" {version}" if version else ""),
            "default": driver["published"] in default_names,
            "tags": [names[0]],
        })
    if not options:
        options.append(_option("", "No third-party drivers found on this machine", "Сторонних драйверов на этой машине не найдено"))
    return options


def export_selected_drivers(context: JobContext) -> dict[str, object]:
    """`pnputil /export-driver` of the ticked drivers into <output>\\Drivers.

    Each driver lands in its own folder with the .inf, .sys and .cat Windows
    already has for it, ready for `Drivers\\OS` of a UUP dump build or for
    `pnputil /add-driver` on a fresh system. Nothing on the machine changes.
    """
    chosen = _param_list(context, "driver_packages")
    if not chosen:
        raise RuntimeError("Tick at least one driver package.")
    destination = _output_base(context) / DRIVERS_DIRECTORY_NAME
    destination.mkdir(parents=True, exist_ok=True)
    pnputil = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "pnputil.exe"
    if not pnputil.exists():
        raise RuntimeError("pnputil.exe not found; driver export works on Windows only.")
    known = {driver["published"].lower(): driver for driver in list_machine_drivers()}
    context.log(f"[DRIVERS] exporting {len(chosen)} driver package(s) -> {destination}")
    exported = 0
    for index, published in enumerate(chosen, start=1):
        driver = known.get(str(published).lower(), {"published": str(published), "original": str(published), "provider": "", "guid": "", "class": ""})
        context.activity(f"pnputil /export-driver {driver['published']} ({index}/{len(chosen)})")
        # one folder per package: pnputil drops a single driver's files flat into the target
        package_dir = destination / f"{Path(driver['original']).stem}_{Path(driver['published']).stem}"
        package_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run([str(pnputil), "/export-driver", driver["published"], str(package_dir)], capture_output=True, text=True, errors="replace", check=False)
        names = DRIVER_CLASS_NAMES.get(driver["guid"], (driver["class"] or "Other", ""))
        if completed.returncode in (0, 259):
            exported += 1
            context.log(f"[OK] {names[0]}: {driver['original']} ({driver['provider']})")
        else:
            context.log(f"[WARN] {driver['published']} ({driver['original']}) not exported: {(completed.stderr or completed.stdout).strip()[:160]}")
        context.progress(index / len(chosen))
    context.activity("")
    size = sum(path.stat().st_size for path in destination.rglob("*") if path.is_file())
    context.log(f"[DONE] {exported} driver package(s), {size / 1e6:.0f} MB in {destination}")
    context.log(f"[NEXT] move the Drivers folder into input ({_input_base(context)}), any subfolder name will do, and tick 'Embed drivers from input' in the Windows build")
    return {"packages": exported, "bytes": size, "folder": str(destination)}


def download_vendor_builds(context: JobContext) -> dict[str, object]:
    """Fetch the ticked versions into `Vendors\\<Vendor>\\<archive name>`."""
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
    total = len(builds)
    fetch = getattr(provider, "fetch", None)

    for index, build in enumerate(builds, start=1):
        start = (index - 1) / total
        end = index / total
        download_end = start + (end - start) * (0.85 if extract else 1.0)
        label = f"{build.name} [{build.platform}]"
        if callable(fetch):
            file_name = build.name
            # Windows packages are unpacked and built in <drive>:\UUP, not under the destination.
            folder = uup_work_root(context) / Path(file_name).stem
            archive = folder / file_name
            context.log(f"[FETCH] {label}")
            fetch(build, archive)
            context.log(f"[OK] {archive} ({archive.stat().st_size} bytes)")
            _progress_between(context, start, download_end, 1.0)
        else:
            url = _resolve(context, provider, build)
            file_name = _archive_name(url)
            folder = root / Path(file_name).stem
            folder.mkdir(parents=True, exist_ok=True)
            archive = folder / file_name
            download_with_resume(context, url, archive, label, progress_start=start, progress_end=download_end)
        entry: dict[str, Any] = {"vendor": provider.id, "name": build.name, "platform": build.platform, "folder": str(folder), "archive": str(archive)}
        if (extract or build_iso) and archive.suffix.lower() == ".zip":
            entry["extracted"] = extract_zip(context, archive, folder)
            if delete_zip:
                archive.unlink()
                context.log(f"[CLEAN] {archive.name} removed")
                entry["archive"] = ""
        if build_iso:
            iso = Path(run_uup_script(context, folder))
            entry["iso"] = hand_over_iso(context, iso, folder, root, clean_cache=clean_cache)
            if clean_cache:
                entry["folder"] = ""
        context.progress(end)
        results.append(entry)

    context.log(f"[DONE] {len(results)} build(s) in {root}")
    return {"downloaded": len(results), "results": results, "output": str(root)}

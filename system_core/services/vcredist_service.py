"""Visual C++ redistributable bundles from the two authoritative collectors.

WinGet installs the VC++ runtimes one package at a time, straight from
Microsoft, and it has no package at all for 2012 (v11). Two maintained
all-in-one bundles cover the whole family in one pass:

* TechPowerUp - `Visual C++ Runtimes All-in-One`, a ZIP with the plain vendor
  installers plus `install_all.bat`.
* abbodi1406/vcredist - `VisualCppRedist_AIO`, a single self-contained
  executable that also handles the ARM64 and x86-only cases.

Downloading and installing are separate actions. The install actions are
`dangerous`, so the GUI asks first, and they refuse to start without
Administrator rights instead of leaving the runtimes half-installed.
"""

from __future__ import annotations

from pathlib import Path
import ctypes
import os
import re
import winreg
import zipfile

from system_core.core.jobs import JobContext

# The project's process runner and WinGet reader: logging, cancellation and
# output decoding in one place. `.cmd` scripts stay on plain pipes by project
# convention.
from system_core.services.winget_service import _run_process, _run_winget_capture

# One implementation of "download with a live progress line, size and sha256"
# is enough; the portable service already owns it.
from system_core.services.portable_browser_service import (
    DownloadedAsset,
    _download,
    _github_asset,
    _github_latest_release,
    _safe_name,
)
from system_core.services.package_links import download_folder_name
from system_core.services.techpowerup_service import resolve_signed_url


TECHPOWERUP_MSVC_SLUG = "visual-c-redistributable-runtime-package-all-in-one"
ABBODI_REPO = "abbodi1406/vcredist"
ABBODI_ASSET_PATTERNS = {
    "x86_x64": r"VisualCppRedist_AIO_x86_x64\.exe",
    "x86only": r"VisualCppRedist_AIO_x86only\.exe",
    "arm64": r"VisualCppRedist_AIO-arm64\.exe",
}
INSTALL_DIRECTORY_NAME = "Install"
TECHPOWERUP_BUNDLE_NAME = "MSVC All-in-One (TechPowerUp)"
ABBODI_BUNDLE_NAME = "MSVC AIO (abbodi1406)"

# `VC\Runtimes\<arch>` is the only honest source for a redistributable version:
# the ARP display name has been renamed twice (2015-2019 → 2015-2022 → v14) and
# one bundle leaves up to three ARP rows per architecture.
# The key was introduced with VS2012, so 2005/2008/2010 are absent from it by
# design and are reported from ARP instead - never as "not installed".
VC_RUNTIME_REGISTRY_VERSIONS = (("2012", "11.0"), ("2013", "12.0"), ("v14", "14.0"))
VC_RUNTIME_KEY = r"SOFTWARE\Microsoft\VisualStudio\{version}\VC\Runtimes\{arch}"


def _read_runtime_version(version: str, arch: str) -> str:
    key_path = VC_RUNTIME_KEY.format(version=version, arch=arch)
    # Both registry views: the 2013 redistributable writes under WOW6432Node
    # for x64 as well, so a 64-bit-only probe silently reports it as missing.
    for access in (
        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
                installed, _ = winreg.QueryValueEx(key, "Installed")
                if not int(installed):
                    continue
                raw, _ = winreg.QueryValueEx(key, "Version")
                return normalize_runtime_version(str(raw))
        except (FileNotFoundError, OSError, ValueError):
            continue
    return ""


def normalize_runtime_version(raw: str) -> str:
    """`v14.51.36247.00` and `14.51.36247.0` are the same build; say so."""
    text = str(raw or "").strip().lstrip("vV")
    parts = [part for part in text.split(".") if part != ""]
    if not parts:
        return ""
    numbers: list[int] = []
    for part in parts:
        try:
            numbers.append(int(part))
        except ValueError:
            return text
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return ".".join(str(number) for number in numbers)


def runtime_state() -> dict[str, str]:
    """`{'v14 x64': '14.51.36247', ...}` for every runtime the registry knows."""
    state: dict[str, str] = {}
    for label, version in VC_RUNTIME_REGISTRY_VERSIONS:
        for arch in ("x86", "x64"):
            state[f"{label} {arch}"] = _read_runtime_version(version, arch)
    return state


def _is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


def _require_elevation(context: JobContext) -> None:
    if _is_elevated():
        return
    raise RuntimeError(
        "The runtime installers need Administrator rights. Restart Audion Get Tools elevated, "
        "or run the downloaded installer yourself."
    )


def _log_runtime_change(context: JobContext, before: dict[str, str], after: dict[str, str]) -> list[str]:
    changed: list[str] = []
    context.log("")
    context.log("[STATE] Visual C++ runtimes (registry):")
    for name in after:
        old = before.get(name, "")
        new = after.get(name, "")
        if old == new:
            context.log(f"  {name:<10} {new or '-'}")
            continue
        changed.append(f"{name}: {old or 'absent'} -> {new or 'absent'}")
        context.log(f"  {name:<10} {old or 'absent'} -> {new or 'absent'}")
    if not changed:
        context.log("[INFO] Nothing changed: every runtime was already at this build or newer.")
    return changed


def _target_dir(context: JobContext, bundle_name: str) -> Path:
    """A bundle keeps its own folder under `Install`, named as the button names it."""
    raw = str(context.operation.parameters.get("output_path") or "").strip()
    base = Path(raw) if raw else context.paths.output
    path = base / INSTALL_DIRECTORY_NAME / (download_folder_name(bundle_name) or "MSVC")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _extract_zip(context: JobContext, asset: DownloadedAsset) -> Path | None:
    """Unpack the bundle next to its archive; both stay under `Install`.

    What sits in `Install` is judged by what it does, not by its extension: this
    bundle installs runtimes into the system, so the zip it ships as is as
    welcome here as an exe or an msi would be.
    """
    if asset.path.suffix.lower() != ".zip":
        return None
    target = asset.path.with_suffix("")
    context.log(f"[INFO] Unpacking to {target}")
    with zipfile.ZipFile(asset.path) as archive:
        archive.extractall(target)
    return target


def _report_asset(context: JobContext, asset: DownloadedAsset) -> None:
    context.log(f"[FILE] {asset.path} ({asset.size:,} bytes)")
    context.log(f"[HASH] sha256 {asset.sha256}")


def download_msvc_all_in_one(context: JobContext) -> dict[str, object]:
    """TechPowerUp `Visual C++ Runtimes All-in-One`: download and unpack."""
    context.log("[INFO] Download only, nothing is installed.")
    url, name, file_id = resolve_signed_url(context, TECHPOWERUP_MSVC_SLUG)
    context.progress(0.05)

    asset = _download(
        context,
        url,
        _target_dir(context, TECHPOWERUP_BUNDLE_NAME) / name,
        "Visual C++ Runtimes All-in-One",
        progress_start=0.05,
        progress_end=0.9,
    )
    _report_asset(context, asset)

    extracted = _extract_zip(context, asset)
    installer = None
    if extracted is not None:
        installer = next(iter(sorted(extracted.rglob("install_all.bat"))), None)
        if installer is not None:
            context.log(f"[INFO] Installer script: {installer}")
            context.log("[INFO] Run it from an elevated console; it installs every runtime in one pass.")
    context.progress(1.0)

    return {
        "source": "techpowerup",
        "file_id": file_id,
        "url": url,
        "path": str(asset.path),
        "size": asset.size,
        "sha256": asset.sha256,
        "extracted_to": str(extracted) if extracted else "",
        "installer_script": str(installer) if installer else "",
    }


def download_msvc_aio_abbodi(context: JobContext) -> dict[str, object]:
    """abbodi1406 `VisualCppRedist_AIO`: latest GitHub release asset."""
    variant = str(context.operation.parameters.get("aio_variant") or "x86_x64").strip().lower()
    pattern = ABBODI_ASSET_PATTERNS.get(variant)
    if pattern is None:
        raise RuntimeError(
            f"Unknown AIO variant {variant!r}. Known variants: {', '.join(sorted(ABBODI_ASSET_PATTERNS))}."
        )

    context.log("[INFO] Download only, nothing is installed.")
    release = _github_latest_release(ABBODI_REPO)
    tag = str(release.get("tag_name") or release.get("name") or "latest")
    github_asset = _github_asset(release, pattern)
    name = str(github_asset.get("name") or "VisualCppRedist_AIO.exe")
    url = str(github_asset.get("browser_download_url") or "")
    if not url:
        raise RuntimeError(f"GitHub asset has no download URL: {ABBODI_REPO} {name}")
    context.log(f"[GITHUB] {ABBODI_REPO} latest={tag} asset={name}")
    context.progress(0.05)

    asset = _download(
        context,
        url,
        _target_dir(context, ABBODI_BUNDLE_NAME) / _safe_name(name),
        f"VisualCppRedist AIO {tag}",
        progress_start=0.05,
        progress_end=1.0,
    )
    _report_asset(context, asset)
    # `/ai` is the silent switch of the AIO package; it still needs elevation,
    # so Audion Get prints it instead of running it.
    context.log(f"[INFO] Silent install from an elevated console: \"{asset.path}\" /ai")
    context.progress(1.0)

    return {
        "source": "abbodi1406",
        "release": tag,
        "variant": variant,
        "url": url,
        "path": str(asset.path),
        "size": asset.size,
        "sha256": asset.sha256,
    }


def install_msvc_all_in_one(context: JobContext) -> dict[str, object]:
    """Download the TechPowerUp bundle and run its `install_all.bat`."""
    _require_elevation(context)
    result = download_msvc_all_in_one(context)
    script = str(result.get("installer_script") or "")
    if not script:
        raise RuntimeError("install_all.bat was not found in the downloaded archive.")

    before = runtime_state()
    installer = Path(script)
    context.log("")
    context.log(f"[RUN] {installer}")
    context.log("[INFO] Each runtime is installed with /passive /norestart; a progress window is normal.")
    context.progress(0.5)
    process = _run_process(
        context,
        [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(installer)],
        cwd=installer.parent,
        check=False,
        activity="Visual C++ Runtimes All-in-One",
    )
    # The script always prints "successfully" and swallows every exit code, so
    # the registry is the only real answer.
    changed = _log_runtime_change(context, before, runtime_state())
    context.progress(1.0)
    return {
        **result,
        "installed": True,
        "exit_code": process.exit_code,
        "runtime_changes": changed,
    }


def install_msvc_aio_abbodi(context: JobContext) -> dict[str, object]:
    """Download the abbodi1406 AIO and run it with the silent `/ai` switch."""
    _require_elevation(context)
    result = download_msvc_aio_abbodi(context)
    installer = Path(str(result.get("path") or ""))
    if not installer.is_file():
        raise RuntimeError("The downloaded AIO installer is missing.")

    before = runtime_state()
    context.log("")
    context.log(f"[RUN] {installer} /ai")
    context.progress(0.5)
    process = _run_process(
        context,
        [str(installer), "/ai"],
        cwd=installer.parent,
        check=False,
        activity="VisualCppRedist AIO",
    )
    changed = _log_runtime_change(context, before, runtime_state())
    context.progress(1.0)
    if process.exit_code != 0 and not changed:
        raise RuntimeError(
            f"VisualCppRedist_AIO finished with exit code {process.exit_code} and changed nothing. "
            f"The installer is kept at {installer}."
        )
    return {
        **result,
        "installed": True,
        "exit_code": process.exit_code,
        "runtime_changes": changed,
    }


# WinGet only carries the families below; 2012 has no package at all, and the
# pre-2015 ones cannot be version-compared because their manifests pin the last
# vendor build and never move again.
WINGET_RUNTIME_PACKAGES: tuple[tuple[str, str, str], ...] = (
    ("v14", "x86", "Microsoft.VCRedist.2015+.x86"),
    ("v14", "x64", "Microsoft.VCRedist.2015+.x64"),
    ("2013", "x86", "Microsoft.VCRedist.2013.x86"),
    ("2013", "x64", "Microsoft.VCRedist.2013.x64"),
)
COMPARABLE_FAMILIES = {"v14"}
LEGACY_ARP_FAMILIES = ("2005", "2008", "2010")
ARP_UNINSTALL_KEYS = (
    (winreg.KEY_WOW64_64KEY, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.KEY_WOW64_32KEY, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
)
VERSION_TOKEN = re.compile(r"\b\d+\.\d+\.\d+(?:\.\d+)?\b")


def _winget_available_version(package_id: str) -> str:
    """Version WinGet would install, read without trusting localized captions."""
    result = _run_winget_capture(
        ["show", "--id", package_id, "-e", "--source", "winget", "--accept-source-agreements", "--disable-interactivity"],
        timeout=45.0,
    )
    if result.exit_code != 0:
        return ""
    for line in result.lines[:6]:
        match = VERSION_TOKEN.search(line)
        if match:
            return normalize_runtime_version(match.group(0))
    return ""


def _arp_visual_cpp_entries() -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for access, path in ARP_UNINSTALL_KEYS:
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | access)
        except OSError:
            continue
        with root:
            for index in range(winreg.QueryInfoKey(root)[0]):
                try:
                    name = winreg.EnumKey(root, index)
                    with winreg.OpenKey(root, name, 0, winreg.KEY_READ | access) as item:
                        display = str(winreg.QueryValueEx(item, "DisplayName")[0])
                        if "Visual C++" not in display:
                            continue
                        try:
                            version = str(winreg.QueryValueEx(item, "DisplayVersion")[0])
                        except OSError:
                            version = ""
                        entries.append((display, version))
                except OSError:
                    continue
    return sorted(set(entries))


def check_msvc_runtimes(context: JobContext) -> dict[str, object]:
    """Report what is really installed, what WinGet offers, and the verdict.

    Only the 2015+ family (`v14`) is compared version to version. The older
    families are reported as present or absent on purpose: their WinGet
    manifests are frozen at the final vendor build, so a version comparison
    would invent a disagreement that does not exist.
    """
    installed = runtime_state()
    available = {
        f"{family} {arch}": _winget_available_version(package_id)
        for family, arch, package_id in WINGET_RUNTIME_PACKAGES
    }

    rows: list[dict[str, str]] = []
    context.log("[MSVC] Visual C++ runtimes, registry against WinGet")
    context.log(f"  {'Family':<8}{'Arch':<6}{'Installed':<18}{'WinGet':<18}Verdict")
    for label, _version in VC_RUNTIME_REGISTRY_VERSIONS:
        for arch in ("x86", "x64"):
            name = f"{label} {arch}"
            current = installed.get(name, "")
            offered = available.get(name, "")
            if label == "2012":
                verdict = "installed" if current else "missing: only the AIO bundles carry 2012"
            elif not current:
                verdict = "not installed" if offered else "not installed, no WinGet package"
            elif label not in COMPARABLE_FAMILIES:
                verdict = "installed (WinGet manifest is frozen, not comparable)"
            elif not offered:
                verdict = "installed, WinGet did not answer"
            elif current == offered:
                verdict = "up to date"
            else:
                verdict = f"differs from WinGet ({offered})"
            context.log(f"  {label:<8}{arch:<6}{current or '-':<18}{offered or '-':<18}{verdict}")
            rows.append({"family": label, "arch": arch, "installed": current, "winget": offered, "verdict": verdict})

    arp = _arp_visual_cpp_entries()
    context.log("")
    context.log("[MSVC] Older families, from Programs and Features only:")
    for family in LEGACY_ARP_FAMILIES:
        found = sorted({version for name, version in arp if f"Visual C++ {family}" in name and version})
        context.log(f"  {family:<8}{', '.join(found) if found else 'not installed'}")
        rows.append(
            {
                "family": family,
                "arch": "",
                "installed": ", ".join(found),
                "winget": "",
                "verdict": "present in ARP" if found else "not installed",
            }
        )
    context.log("[NOTE] VC\\Runtimes exists only since 2012, so these are reported by presence, not by version.")

    context.log("")
    context.log(f"[ARP] {len(arp)} Visual C++ entries in Programs and Features:")
    for display, version in arp:
        context.log(f"  {display}  [{version}]")
    context.log("")
    context.log(
        "[NOTE] One bundle writes several ARP rows (bundle plus Minimum/Additional Runtime), "
        "and the display name has changed from 2015-2019 to 2015-2022 to v14. "
        r"Only VC\Runtimes\<arch> is a reliable version source."
    )
    context.progress(1.0)

    return {
        "runtimes": rows,
        "arp_entries": [{"name": name, "version": version} for name, version in arp],
        "arp_count": len(arp),
    }

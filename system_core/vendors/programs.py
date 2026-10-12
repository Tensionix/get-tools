"""Programs taken as files: the installer or the portable build, the newest its maker publishes.

WinGet installs a program, and a vendor provider keeps every version of a product. This one
answers a third need - "give me the file": the installer of a launcher its maker hands out from a
fixed address, the portable build of a utility that lives on a release page, a tool WinGet does
not carry at all. The files of a fresh machine, kept on a stick.

The programs are data - `config\\programs.yaml`: shelves (a page of the window each), blocks of a
shelf, programs of a block, and for a program where its installer and its portable build come
from:

    {source: github, repo: owner/repo, asset: <regex of the file name in the latest release>}
    {source: url,    url: <fixed address>, name: <file name, when the address has none>}
    {source: winget, id: <package id>, type: zip | portable (for a portable build)}

A github source may also name `sums` - the regex of the checksum file in the same release
(`<64 hex digits>  <file name>` a line). Then the downloaded file is checked against the line of its
name, and a file with no line, or a release with no such file, is not trusted: the sum was asked for.
The file, its address and the checksum file are named by one answer about the release - a maker who
publishes daily may put out a new one between two readings, and the sum would be of another file.

A program may also say what it is - `about` and `about_ru`, a sentence each: the tooltip of its card.

The list asks nothing of the network: a release page is read and WinGet is called only when a
program is downloaded.

Ported from Audion Setup Tools (Manifests\\Downloads.psd1; Engine\\Downloads.ps1: Save-DownloadSource,
Resolve-GithubDownload, Save-WingetDownload).
"""
from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request

from system_core.core.config import load_yaml_or_json
from system_core.services.package_links import download_folder_name, extract_urls, resolve_package_page
from system_core.vendors import PLATFORM_WINDOWS, BuildSource, VendorBuild

PREFIX = "prg:"
INSTALLER = "installer"
PORTABLE = "portable"
WAYS = ("github", "url", "winget")
FILE_SUFFIXES = (".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle", ".zip", ".7z")

SUMS_SAID_BY = "the checksum file of the release"
SUM_LINE = re.compile(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")
SUMS_BYTES = 1 << 20  # a checksum file is a page of text; more than this is not one

ReleaseCall = Callable[[str], tuple[str, list[tuple[str, str]]]]
WingetCall = Callable[[Any, str, str, Path], Path]
TextCall = Callable[[str], str]


@dataclass(frozen=True)
class Program:
    key: str
    name: str
    shelf: str
    block: tuple[str, str]  # the heading it stands under, English and Russian
    installer: dict[str, str] | None = None
    portable: dict[str, str] | None = None
    about: tuple[str, str] = ("", "")  # what the program is, English and Russian: the tooltip of its card

    def source(self, kind: str) -> dict[str, str] | None:
        return self.portable if kind == PORTABLE else self.installer


def read_shelves(data: Any) -> dict[str, list[Program]]:
    """`config\\programs.yaml` taken apart; a program that names no file, or names it in a way nobody knows, is an error in words."""
    shelves: dict[str, list[Program]] = {}
    seen: set[str] = set()
    for shelf, body in ((data or {}).get("shelves") or {}).items():
        programs: list[Program] = []
        for block in (body or {}).get("blocks") or []:
            title = (str(block.get("title") or ""), str(block.get("title_ru") or block.get("title") or ""))
            for row in block.get("programs") or []:
                key, name = str(row.get("id") or "").strip(), str(row.get("name") or "").strip()
                if not key or not name:
                    raise ValueError(f"programs.yaml: a program of {shelf!r} has no id or no name: {row!r}")
                if key in seen or ":" in key:
                    raise ValueError(f"programs.yaml: the id {key!r} is taken or has a colon")
                seen.add(key)
                files: dict[str, dict[str, str] | None] = {}
                for kind in (INSTALLER, PORTABLE):
                    source = row.get(kind)
                    if source is None:
                        files[kind] = None
                        continue
                    source = {str(field): str(value) for field, value in dict(source).items()}
                    way = source.get("source", "")
                    needs = {"github": ("repo", "asset"), "url": ("url",), "winget": ("id",)}.get(way)
                    if needs is None or not all(source.get(field) for field in needs):
                        raise ValueError(f"programs.yaml: {key}.{kind} - a source is one of {', '.join(WAYS)} with its fields, got {source!r}")
                    if way == "github":
                        re.compile(source["asset"])
                    if "sums" in source:
                        # Only a release has a checksum file beside its files.
                        if way != "github" or not source["sums"]:
                            raise ValueError(f"programs.yaml: {key}.{kind} - `sums` is the checksum file of a github release, "
                                             f"a {way} source has none: {source!r}")
                        re.compile(source["sums"])
                    files[kind] = source
                if not files[INSTALLER] and not files[PORTABLE]:
                    raise ValueError(f"programs.yaml: {key} names neither an installer nor a portable build")
                about = str(row.get("about") or "").strip()
                programs.append(Program(key, name, str(shelf), title, files[INSTALLER], files[PORTABLE],
                                        (about, str(row.get("about_ru") or "").strip() or about)))
        shelves[str(shelf)] = programs
    return shelves


def _latest_release(repo: str) -> tuple[str, list[tuple[str, str]]]:
    from system_core.services.winget_service import _github_latest_assets

    return _github_latest_assets(repo)


def _read_text(url: str) -> str:
    """A small text file by its address: the checksum file of a release."""
    from urllib.request import urlopen

    try:
        with urlopen(Request(url, headers={"User-Agent": "Audion-Get"}), timeout=60) as response:
            return response.read(SUMS_BYTES).decode("utf-8", "replace")
    except OSError as exc:  # no answer, a refusal, a broken connection
        raise RuntimeError(f"the checksum file did not come from {url} ({exc})") from exc


def winget_file(context: Any, package_id: str, installer_type: str, folder: Path) -> Path:
    """The file WinGet would install from, by `winget download`: into a folder of its own first, so that it is
    known which file came; the one installer moves to the folder, what it depends on goes beside it."""
    from system_core.services import winget_service

    drop = folder / "_winget"
    shutil.rmtree(drop, ignore_errors=True)
    # The job counts its own progress: a download inside it is not the whole of it.
    own = vars(context).get("progress")
    context.progress = lambda value: None
    try:
        winget_service._download_package_to(context, package_id, drop, installer_type)
        main = [item for item in drop.iterdir() if item.is_file() and item.suffix.lower() not in winget_service.DOWNLOAD_ARTEFACT_SKIP_SUFFIXES]
        if len(main) != 1:
            names = ", ".join(item.name for item in main) or "nothing"
            raise RuntimeError(f"WinGet left {len(main)} installer files instead of one ({names}) - which is the installer is unclear; nothing was kept")
        target = folder / main[0].name
        target.unlink(missing_ok=True)
        main[0].replace(target)
        dependencies = drop / "Dependencies"
        if dependencies.is_dir():
            for item in dependencies.rglob("*"):
                if item.is_file():
                    into = folder / "Dependencies" / item.relative_to(dependencies)
                    into.parent.mkdir(parents=True, exist_ok=True)
                    item.replace(into)
            context.log(f"[INFO] dependencies: {folder / 'Dependencies'}")
        return target
    finally:
        if own is None:
            del context.progress
        else:
            context.progress = own
        shutil.rmtree(drop, ignore_errors=True)


class ProgramsProvider:
    id = "programs"
    name = "Programs"
    platforms = (PLATFORM_WINDOWS,)

    _instance: "ProgramsProvider | None" = None

    def __init__(self, path: Path | None = None, releases: ReleaseCall | None = None, winget: WingetCall | None = None,
                 show: Callable[[str], str] | None = None, text: TextCall | None = None) -> None:
        self._path = path
        self._releases: ReleaseCall = releases or _latest_release
        self._winget: WingetCall = winget or winget_file
        self._show = show
        self._text: TextCall = text or _read_text
        self._read: tuple[float, dict[str, list[Program]]] | None = None

    @classmethod
    def instance(cls) -> "ProgramsProvider":
        if cls._instance is None:
            cls._instance = cls(Path(__file__).resolve().parents[2] / "config" / "programs.yaml")
        return cls._instance

    # ----- the shelves -----

    def shelves(self) -> dict[str, list[Program]]:
        """The file as it is now: read again when it changes."""
        if not self._path or not self._path.is_file():
            return {}
        stamp = self._path.stat().st_mtime
        if self._read is None or self._read[0] != stamp:
            self._read = (stamp, read_shelves(load_yaml_or_json(self._path)))
        return self._read[1]

    def program(self, key: str) -> Program | None:
        return next((program for programs in self.shelves().values() for program in programs if program.key == key), None)

    def _build(self, program: Program, kind: str = "") -> VendorBuild:
        """A program as a card of the list; with `kind` - as the one file a run asks for."""
        installer = f"{PREFIX}{program.key}:{INSTALLER}" if program.installer else ""
        portable = f"{PREFIX}{program.key}:{PORTABLE}" if program.portable else ""
        own = f"{PREFIX}{program.key}:{kind}" if kind else (installer or portable)
        return VendorBuild(vendor=self.id, product=program.shelf, version=program.name, platform=PLATFORM_WINDOWS, date="",
                           download_id=own, name=program.name + (" (portable)" if own == portable else ""),
                           portable_id=portable, about=program.about[0], about_ru=program.about[1])

    def blocks(self, shelf: str) -> list[tuple[tuple[str, str], list[VendorBuild]]]:
        """A shelf as the window shows it: its blocks in the order of the file, each with its programs."""
        found: list[tuple[tuple[str, str], list[VendorBuild]]] = []
        for program in self.shelves().get(shelf, []):
            if not found or found[-1][0] != program.block:
                found.append((program.block, []))
            found[-1][1].append(self._build(program))
        return found

    # ----- a file of a program -----

    def release_file(self, repo: str, pattern: str) -> tuple[str, str, str]:
        """The file of the latest release its pattern names: its name, its address, the release."""
        tag, assets = self._releases(repo)
        return self.named(repo, tag, assets, pattern)

    @staticmethod
    def named(repo: str, tag: str, assets: list[tuple[str, str]], pattern: str) -> tuple[str, str, str]:
        """The file of a release, as its answer lists them, that the pattern names."""
        for name, url in assets:
            if re.search(pattern, name, re.IGNORECASE):
                return name, url, tag
        names = ", ".join(name for name, _url in assets) or "nothing"
        raise RuntimeError(f"The latest release of {repo} ({tag}) has no file matching {pattern}; there are: {names}")

    def release_sum(self, repo: str, tag: str, assets: list[tuple[str, str]], pattern: str, file_name: str) -> str:
        """The SHA-256 the checksum file of a release gives for one of its files; no such file or no such line is said in words."""
        found = next(((name, url) for name, url in assets if re.search(pattern, name, re.IGNORECASE)), None)
        if found is None:
            raise RuntimeError(f"the release of {repo} ({tag}) has no checksum file matching {pattern}")
        sums_name, sums_url = found
        for line in self._text(sums_url).splitlines():
            match = SUM_LINE.match(line)
            if match and match.group(2) == file_name:
                return match.group(1).upper()
        raise RuntimeError(f"{sums_name} of {repo} ({tag}) names no sum for {file_name}")

    def _parse(self, download_id: str) -> tuple[Program, str] | None:
        wanted = str(download_id or "").strip()
        if not wanted.startswith(PREFIX):
            return None
        key, _, kind = wanted[len(PREFIX):].rpartition(":")
        program = self.program(key)
        if program is None or kind not in (INSTALLER, PORTABLE) or program.source(kind) is None:
            return None
        return program, kind

    def sources(self, build: VendorBuild) -> list[BuildSource]:
        parsed = self._parse(build.download_id)
        if parsed is None:
            return []
        program, kind = parsed
        source = program.source(kind) or {}
        # A program keeps one folder; its portable build unpacks into Portable inside it.
        folder = download_folder_name(program.name, program.key) + ("/Portable" if kind == PORTABLE else "")
        way = source["source"]
        if way == "github":
            # The release is read once, when the file is asked for: the file, its address and its sum
            # are named by that one answer.
            release: dict[str, Any] = {}

            def link() -> tuple[str, str]:
                release["tag"], release["assets"] = self._releases(source["repo"])
                return self.named(source["repo"], release["tag"], release["assets"], source["asset"])[1], ""

            def checksum(file_name: str) -> str:
                if "assets" not in release:
                    release["tag"], release["assets"] = self._releases(source["repo"])
                return self.release_sum(source["repo"], release["tag"], release["assets"], source["sums"], file_name)

            checked = bool(source.get("sums"))
            return [BuildSource(host="github.com", name="", folder=folder, link=link,
                                checksum=checksum if checked else None, said_by=SUMS_SAID_BY if checked else "")]
        if way == "url":
            return [BuildSource(host=Request(source["url"]).host or "the maker's site", name=source.get("name", ""), folder=folder,
                                link=lambda: (source["url"], ""))]
        return [BuildSource(host="winget", name="", folder=folder, link=lambda: (self.resolve_link(build), ""),
                            fetch=lambda context, into: self._winget(context, source["id"], source.get("type", ""), into))]

    # ----- VendorProvider -----

    def products(self, platform: str) -> list[str]:
        return list(self.shelves()) if platform in self.platforms else []

    def builds(self, product: str, platform: str, **selection: Any) -> list[VendorBuild]:
        del selection
        if platform not in self.platforms:
            return []
        return [build for _block, builds in self.blocks(product) for build in builds]

    def build_by_id(self, download_id: str) -> VendorBuild | None:
        parsed = self._parse(download_id)
        return self._build(parsed[0], parsed[1]) if parsed else None

    def resolve_link(self, build: VendorBuild, registration: dict[str, str] | None = None) -> str:
        """The address of the file; of a WinGet package - the address WinGet itself names for its installer."""
        del registration
        parsed = self._parse(build.download_id)
        if parsed is None:
            raise RuntimeError(f"Unknown build id {build.download_id}; refresh the list.")
        program, kind = parsed
        source = program.source(kind) or {}
        if source["source"] == "github":
            return self.release_file(source["repo"], source["asset"])[1]
        if source["source"] == "url":
            return source["url"]
        if self._show is None:
            from system_core.services.winget_service import _winget_show_output

            self._show = _winget_show_output
        shown = self._show(source["id"])
        for url in extract_urls(shown):
            if url.split("?")[0].lower().endswith(FILE_SUFFIXES):
                return url
        page = resolve_package_page(source["id"], shown)
        if page is None:
            raise RuntimeError(f"WinGet names no address for {source['id']}; the file comes by `winget download`.")
        return page.url

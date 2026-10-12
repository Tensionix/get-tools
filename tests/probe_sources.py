"""A probe of the sources on the real network: does every place the program takes a file from still answer.

    runtime\\python.exe tests\\probe_sources.py

Run by hand before a release. The name has no `test_` on purpose: tests\\run_all.py does not start
it, and no test of the engine calls the network. It downloads nothing but the checksum files:

  - every program of config\\programs.yaml, every shelf: a `github` source - its pattern named a
    file in the latest release; a `url` source - the address answered and named a size; a `winget`
    source - the package exists;
  - every source with `sums` - the checksum file of the release has a line for the named file;
  - every entry of PACKAGE_GITHUB_BUILDS - its patterns named their files;
  - the sixteen packages 3.1.0 added to the catalogue - `winget show --id <ID> -e` finds each;
  - the apps the launcher of Craft installs - each is on the shelf `craft`, and the shelf carries no
    app the launcher has dropped. The launcher names its suite in its own source; a new app there is
    the sign to add it to config\\programs.yaml (the owner: new Craft packages are followed by the
    make-up of the launcher).

A line for a source - what was asked and what was named - and the sum of them. The exit code is 1
when a source did not answer. The releases are read from the pages of GitHub, as the program itself
reads them (`_github_latest_assets`): the quota of the API is not spent.
"""
from __future__ import annotations

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from system_core.services import vendor_service, winget_service  # noqa: E402
from system_core.services.package_links import PACKAGE_GITHUB_BUILDS  # noqa: E402
from system_core.vendors import programs  # noqa: E402
from system_core.vendors.programs import ProgramsProvider  # noqa: E402
from test_catalog import ADDED_3_1_0  # noqa: E402

Release = tuple[str, list[tuple[str, str]]]

# Where the launcher of Craft lists the apps it installs: `pub const SUITE: [CraftApp; N] = [ ... ];`.
LAUNCHER_CATALOGUE = "https://raw.githubusercontent.com/storytold/craft-launcher/HEAD/crates/core/src/catalog.rs"
# A program of the shelf `craft` that is no app of the launcher's suite, and why.
NOT_OF_THE_SUITE = {
    "craft-launcher": "the launcher itself",
    "artcraft": "the studio ArtCraft: the launcher does not install it",
}


def suite_repos(text: str) -> list[str]:
    """The repositories of the Craft App Suite as the source of the launcher names them, in its order.

    The list is `pub const SUITE: [CraftApp; N] = [ app(...), ... ];`, and of the strings of an
    entry only its repository has a slash. A file of another shape is an error in words: then the
    make-up of the launcher has to be looked at by hand.
    """
    found = re.search(r"pub const SUITE: \[CraftApp; (\d+)\] = \[(.*?)\n\];", text, re.DOTALL)
    if found is None:
        raise ValueError("the catalogue of the launcher has no `pub const SUITE: [CraftApp; N]` of the known shape")
    repos = re.findall(r'"([\w.-]+/[\w.-]+)"', found.group(2))
    if len(repos) != int(found.group(1)) or len(set(repos)) != len(repos):
        raise ValueError(f"the launcher declares {found.group(1)} apps and names {len(repos)} repositories ({len(set(repos))} different)")
    return repos


class Probe:
    def __init__(self) -> None:
        self.lines: list[tuple[bool, str]] = []
        self.releases: dict[str, Release | Exception] = {}
        self.shown: dict[str, bool] = {}

    def say(self, good: bool, kind: str, asked: str, named: str) -> None:
        self.lines.append((good, f"{'ok  ' if good else 'FAIL'} {kind:7} {asked}  ->  {named}"))
        print(self.lines[-1][1], flush=True)

    # ----- the three kinds of source -----

    def read_releases(self, repos: list[str]) -> None:
        """The latest release of every repository, read once: several sources share one."""
        def read(repo: str) -> tuple[str, Release | Exception]:
            try:
                return repo, winget_service._github_latest_assets(repo)
            except Exception as exc:  # noqa: BLE001 - a repository that did not answer is a line of the report
                return repo, exc

        with ThreadPoolExecutor(max_workers=6) as pool:
            self.releases.update(pool.map(read, sorted(set(repos))))

    def github(self, what: str, repo: str, pattern: str, whole: bool = False) -> str:
        """The file a pattern names in the latest release of a repository; "" when there is none."""
        release = self.releases.get(repo)
        if release is None or isinstance(release, Exception):
            self.say(False, "github", f"{what}: {repo} {pattern}", f"the release did not come ({release})")
            return ""
        tag, assets = release
        match = re.fullmatch if whole else re.search
        names = [name for name, _url in assets if match(pattern, name, re.IGNORECASE)]
        if not names:
            self.say(False, "github", f"{what}: {repo} {pattern}", f"no file of {tag} matches; there are {len(assets)} files")
            return ""
        self.say(True, "github", f"{what}: {repo} {pattern}", f"{names[0]} ({tag})" + (f" - and {len(names) - 1} more, the first is taken" if len(names) > 1 else ""))
        return names[0]

    def sums(self, what: str, repo: str, pattern: str, file_name: str, provider: ProgramsProvider) -> None:
        tag, assets = self.releases[repo]  # type: ignore[misc]
        try:
            digest = provider.release_sum(repo, tag, assets, pattern, file_name)
        except RuntimeError as exc:
            self.say(False, "sums", f"{what}: {repo} {pattern}", str(exc))
            return
        self.say(True, "sums", f"{what}: {repo} {pattern}", f"{digest[:12]}... for {file_name}")

    def url(self, what: str, address: str) -> None:
        size, _modified = vendor_service._remote_size(address, urlopen, {"User-Agent": vendor_service.SOURCE_AGENT})
        self.say(size > 0, "url", f"{what}: {address}", f"{size} bytes" if size else "no answer, or no size named")

    def winget(self, what: str, package: str) -> None:
        if package not in self.shown:
            self.shown[package] = f"[{package}]".lower() in winget_service._winget_show_output(package).lower()
        self.say(self.shown[package], "winget", f"{what}: {package}", "the package exists" if self.shown[package] else "winget show did not find it")


def main() -> int:
    started = time.monotonic()
    probe = Probe()
    provider = ProgramsProvider(ROOT / "config" / "programs.yaml")
    shelves = provider.shelves()
    sources = [(program, kind, program.source(kind)) for shelf in shelves.values() for program in shelf
               for kind in (programs.INSTALLER, programs.PORTABLE) if program.source(kind)]
    probe.read_releases([source["repo"] for _program, _kind, source in sources if source["source"] == "github"]
                        + [builds.repo for builds in PACKAGE_GITHUB_BUILDS.values()])

    print(f"== config\\programs.yaml: {sum(len(shelf) for shelf in shelves.values())} programs on {len(shelves)} shelves, {len(sources)} files")
    for program, kind, source in sources:
        what = f"{program.shelf}/{program.key}.{kind}"
        if source["source"] == "github":
            named = probe.github(what, source["repo"], source["asset"])
            if named and source.get("sums"):
                probe.sums(what, source["repo"], source["sums"], named, provider)
        elif source["source"] == "url":
            probe.url(what, source["url"])
        else:
            probe.winget(what, source["id"])

    print(f"== PACKAGE_GITHUB_BUILDS: {len(PACKAGE_GITHUB_BUILDS)} packages")
    for package, builds in PACKAGE_GITHUB_BUILDS.items():
        for kind, pattern in (("installer", builds.installer), ("archive", builds.archive)):
            if pattern:
                probe.github(f"{package}.{kind}", builds.repo, pattern, whole=True)

    added = [package for _block, _list, _section, rows in ADDED_3_1_0 for package, _label, _cli in rows]
    print(f"== the catalogue, added by 3.1.0: {len(added)} packages")
    for package in added:
        probe.winget("catalogue", package)

    print("== the suite of the launcher of Craft against the shelf `craft`")
    on_shelf = {str((program.installer or program.portable or {}).get("repo") or ""): program.key
                for program in shelves.get("craft", []) if program.key not in NOT_OF_THE_SUITE}
    try:
        suite = suite_repos(programs._read_text(LAUNCHER_CATALOGUE))
    except (RuntimeError, ValueError) as exc:
        suite = []
        probe.say(False, "suite", LAUNCHER_CATALOGUE, f"{exc} - look at the launcher by hand")
    for repo in suite:
        probe.say(repo in on_shelf, "suite", f"the launcher installs {repo}",
                  f"on the shelf as {on_shelf[repo]}" if repo in on_shelf else "NOT on the shelf: add it to config\\programs.yaml")
    for repo, key in on_shelf.items():
        if suite and repo not in suite:
            probe.say(False, "suite", f"the shelf carries {key} ({repo})", "the launcher no longer installs it")
    for key, why in NOT_OF_THE_SUITE.items():
        probe.say(provider.program(key) is not None, "suite", f"{key}: {why}", "on the shelf" if provider.program(key) else "not on the shelf any more")

    failed = [line for good, line in probe.lines if not good]
    print(f"{len(probe.lines)} sources asked, failed {len(failed)}, {time.monotonic() - started:.0f} s")
    for line in failed:
        print(line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

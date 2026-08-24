"""Resolving the manual download page for a WinGet package."""

from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from system_core.services.package_links import (  # noqa: E402
    PACKAGE_ARCHIVE_TYPES,
    PACKAGE_GITHUB_BUILDS,
    PACKAGE_PAGE_OVERRIDES,
    download_folder_name,
    github_releases_page,
    github_repo_from_url,
    package_archive_github,
    package_archive_type,
    package_installer_github,
    pick_windows_asset,
    resolve_package_page,
)


RU_SHOW_7ZIP = """
Найдено 7-Zip [7zip.7zip]
URL-адрес издателя: https://7-zip.org/
URL-адрес службы поддержки издателя: https://7-zip.org/support.html
Домашняя страница: https://7-zip.org/download.html
URL-адрес лицензии: https://7-zip.org/license.txt
"""

EN_SHOW_OBSIDIAN = """
Found Obsidian [Obsidian.Obsidian]
Publisher Url: https://obsidian.md/
Installer Url: https://github.com/obsidianmd/obsidian-releases/releases/download/v1.9.14/Obsidian.exe
"""


class PackageLinkTests(unittest.TestCase):
    def test_override_wins_without_touching_winget(self) -> None:
        page = resolve_package_page("TechPowerUp.GPU-Z")
        self.assertIsNotNone(page)
        assert page is not None
        self.assertEqual(page.origin, "override")
        self.assertIn("techpowerup.com", page.url)

    def test_overrides_are_lowercase_ids(self) -> None:
        """`resolve_package_page` looks up a casefolded id; keys must match."""
        for key in PACKAGE_PAGE_OVERRIDES:
            self.assertEqual(key, key.lower())

    def test_github_installer_becomes_latest_release_page(self) -> None:
        page = resolve_package_page("Obsidian.Obsidian", EN_SHOW_OBSIDIAN)
        assert page is not None
        self.assertEqual(page.origin, "github")
        self.assertEqual(page.url, "https://github.com/obsidianmd/obsidian-releases/releases/latest")

    def test_localized_output_still_picks_the_download_page(self) -> None:
        """Only links are read, so a Russian WinGet resolves like an English one."""
        page = resolve_package_page("7zip.7zip", RU_SHOW_7ZIP)
        assert page is not None
        self.assertEqual(page.origin, "vendor")
        self.assertEqual(page.url, "https://7-zip.org/download.html")

    def test_license_and_support_links_are_not_download_pages(self) -> None:
        page = resolve_package_page(
            "Some.Package",
            "Support: https://example.com/support\nLicense: https://example.com/license.txt\nHome: https://example.com/",
        )
        assert page is not None
        self.assertEqual(page.url, "https://example.com/")

    def test_no_links_means_no_page(self) -> None:
        self.assertIsNone(resolve_package_page("Some.Package", "no links here"))
        self.assertIsNone(resolve_package_page(""))

    def test_github_owner_pages_are_not_repositories(self) -> None:
        self.assertEqual(github_releases_page("https://github.com/orgs/microsoft/repositories"), "")
        self.assertEqual(
            github_releases_page("https://github.com/ip7z/7zip/blob/main/LICENSE.txt"),
            "https://github.com/ip7z/7zip/releases/latest",
        )


class ArchiveTypeTests(unittest.TestCase):
    def test_known_packages_report_their_installer_type(self) -> None:
        self.assertEqual(package_archive_type("Notepad++.Notepad++"), "zip")
        self.assertEqual(package_archive_type("notepad++.notepad++"), "zip")
        self.assertEqual(package_archive_type("Rufus.Rufus"), "portable")

    def test_rclone_ships_its_command_line_build_as_an_archive(self) -> None:
        self.assertEqual(package_archive_type("Rclone.Rclone"), "zip")

    def test_packages_without_an_archive_report_nothing(self) -> None:
        self.assertEqual(package_archive_type("Microsoft.PowerShell"), "")
        self.assertEqual(package_archive_type("CodeSector.TeraCopy"), "")
        self.assertEqual(package_archive_type(""), "")

    def test_table_holds_only_types_winget_download_accepts(self) -> None:
        self.assertEqual(set(PACKAGE_ARCHIVE_TYPES.values()), {"zip", "portable"})

    def test_table_keys_are_lowercase(self) -> None:
        for key in PACKAGE_ARCHIVE_TYPES:
            self.assertEqual(key, key.lower())

    def test_github_archive_keys_are_lowercase(self) -> None:
        for key in PACKAGE_GITHUB_BUILDS:
            self.assertEqual(key, key.lower())

    def test_installer_comes_from_the_same_release(self) -> None:
        """Both buttons off one release, so they never hand out two versions."""
        repo, pattern = package_installer_github("Eugeny.Tabby")
        self.assertEqual(repo, package_archive_github("Eugeny.Tabby")[0])
        self.assertIsNotNone(re.fullmatch(pattern, "tabby-1.0.235-setup-x64.exe"))

    def test_github_archive_counts_as_an_archive_build(self) -> None:
        """The button appears for these too, though WinGet knows nothing of them."""
        self.assertEqual(package_archive_type("Eugeny.Tabby"), "zip")
        self.assertEqual(package_archive_github("Eugeny.Tabby")[0], "Eugeny/tabby")

    def test_rclone_manager_takes_both_builds_from_one_release(self) -> None:
        """WinGet carries the msi alone, and an older one; the release has both."""
        repo, installer = package_installer_github("RClone-Manager.rclone-manager")
        archive_repo, archive = package_archive_github("RClone-Manager.rclone-manager")
        self.assertEqual(repo, "Zarestia-Dev/rclone-manager")
        self.assertEqual(repo, archive_repo)
        self.assertEqual(package_archive_type("RClone-Manager.rclone-manager"), "zip")
        self.assertIsNotNone(re.fullmatch(installer, "RClone.Manager_0.3.1_x64-setup.exe"))
        # The vendor spells the architecture one way in the installer and
        # another in the archive; both names come from the same release.
        self.assertIsNotNone(
            re.fullmatch(archive, "RClone.Manager_0.3.1_x86_64_windows_portable.zip")
        )

    def test_rclone_manager_patterns_refuse_the_neighbouring_assets(self) -> None:
        """One release also holds arm64, the msi, Linux, Android and signatures."""
        _, installer = package_installer_github("RClone-Manager.rclone-manager")
        _, archive = package_archive_github("RClone-Manager.rclone-manager")
        for name in (
            "RClone.Manager_0.3.1_arm64-setup.exe",
            "RClone.Manager_0.3.1_x64_en-US.msi",
            "RClone.Manager_0.3.1_x64-setup.exe.sig",
        ):
            self.assertIsNone(re.fullmatch(installer, name), name)
        for name in (
            "RClone.Manager_0.3.1_aarch64_windows_portable.zip",
            "RClone.Manager_0.3.1_x86_64_linux_portable.tar.gz",
        ):
            self.assertIsNone(re.fullmatch(archive, name), name)

    def test_github_archive_pattern_matches_the_released_name(self) -> None:
        repo, pattern = package_archive_github("eugeny.tabby")
        self.assertIsNotNone(re.fullmatch(pattern, "tabby-1.0.235-portable-x64.zip"))
        # Neither the arm64 build nor the installer must slip through.
        self.assertIsNone(re.fullmatch(pattern, "tabby-1.0.235-portable-arm64.zip"))
        self.assertIsNone(re.fullmatch(pattern, "tabby-1.0.235-setup-x64.exe"))


class ReleaseAssetTests(unittest.TestCase):
    """Picking the Windows build out of a mixed release page."""

    TABBY = (
        "tabby-1.0.235-linux-x64.AppImage",
        "tabby-1.0.235-linux-x64.tar.gz",
        "tabby-1.0.235-macos-arm64.zip",
        "tabby-1.0.235-portable-arm64.zip",
        "tabby-1.0.235-portable-x64.zip",
        "tabby-1.0.235-setup-arm64.exe",
        "tabby-1.0.235-setup-x64.exe.blockmap",
        "tabby-1.0.235-setup-x64.exe",
    )

    # Six platforms in one release: Windows, macOS, Linux, Android, plus the
    # signatures and the updater feed. This is what the fallback has to sort out
    # if the vendor ever renames the files the patterns pin.
    RCLONE_MANAGER = (
        "latest.json",
        "RClone.Manager-0.3.1-1.x86_64.rpm",
        "RClone.Manager_0.3.1_amd64.AppImage",
        "RClone.Manager_0.3.1_arm64-setup.exe",
        "RClone.Manager_0.3.1_arm64_en-US.msi",
        "RClone.Manager_0.3.1_x64-setup.exe",
        "RClone.Manager_0.3.1_x64-setup.exe.sig",
        "RClone.Manager_0.3.1_x64.dmg",
        "RClone.Manager_0.3.1_x64_en-US.msi",
        "RClone.Manager_0.3.1_x86_64.apk",
        "RClone.Manager_0.3.1_x86_64_linux_portable.tar.gz",
        "RClone.Manager_0.3.1_x86_64_windows_portable.zip",
    )

    def test_installer_and_archive_are_told_apart(self) -> None:
        self.assertEqual(pick_windows_asset(self.TABBY, "installer"), "tabby-1.0.235-setup-x64.exe")
        self.assertEqual(pick_windows_asset(self.TABBY, "archive"), "tabby-1.0.235-portable-x64.zip")

    def test_the_windows_build_is_found_in_a_six_platform_release(self) -> None:
        self.assertEqual(
            pick_windows_asset(self.RCLONE_MANAGER, "installer"),
            "RClone.Manager_0.3.1_x64-setup.exe",
        )
        self.assertEqual(
            pick_windows_asset(self.RCLONE_MANAGER, "archive"),
            "RClone.Manager_0.3.1_x86_64_windows_portable.zip",
        )

    def test_other_systems_and_architectures_are_refused(self) -> None:
        self.assertEqual(pick_windows_asset(["app-linux-x64.AppImage", "app-macos.dmg"], "installer"), "")
        self.assertEqual(pick_windows_asset(["app-arm64-setup.exe"], "installer"), "")
        self.assertEqual(pick_windows_asset(["app-win32.zip"], "archive"), "")

    def test_checksums_and_symbols_are_not_builds(self) -> None:
        names = ["app-x64.exe.sha256", "app-x64-pdb.zip", "app-x64.exe.blockmap"]
        self.assertEqual(pick_windows_asset(names, "installer"), "")
        self.assertEqual(pick_windows_asset(names, "archive"), "")

    def test_nothing_fits_means_nothing_returned(self) -> None:
        self.assertEqual(pick_windows_asset([], "installer"), "")
        self.assertEqual(pick_windows_asset(["README.md"], "archive"), "")

    def test_repository_is_read_out_of_any_github_link(self) -> None:
        self.assertEqual(github_repo_from_url("https://github.com/Eugeny/tabby/releases/latest"), "Eugeny/tabby")
        self.assertEqual(github_repo_from_url("https://github.com/orgs/microsoft"), "")
        self.assertEqual(github_repo_from_url("https://example.com/x/y"), "")


class FolderNameTests(unittest.TestCase):
    def test_the_product_keeps_its_own_spelling(self) -> None:
        self.assertEqual(download_folder_name("MPC-BE"), "MPC-BE")
        self.assertEqual(download_folder_name("Notepad++"), "Notepad++")
        self.assertEqual(
            download_folder_name("MSVC All-in-One (TechPowerUp)"),
            "MSVC All-in-One (TechPowerUp)",
        )

    def test_characters_windows_refuses_are_replaced(self) -> None:
        self.assertEqual(download_folder_name("yt-dlp / ffmpeg"), "yt-dlp ffmpeg")
        self.assertEqual(download_folder_name('Bad:Name?"'), "Bad Name")

    def test_empty_name_falls_back(self) -> None:
        self.assertEqual(download_folder_name("", "Everything"), "Everything")
        self.assertEqual(download_folder_name(""), "")


if __name__ == "__main__":
    unittest.main()

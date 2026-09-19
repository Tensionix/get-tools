from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from system_core.services import github_auth  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "DPAPI is a Windows service")
class GithubTokenStoreTests(unittest.TestCase):
    def test_token_round_trips_through_dpapi_and_never_sits_in_the_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(github_auth.load_token(root), "")
            self.assertNotIn("Authorization", github_auth.github_headers(root))
            path = github_auth.save_token("ghp_exampletoken123", root)
            self.assertEqual(path, root / "config" / "github_token.dpapi")
            self.assertNotIn("ghp_exampletoken123", path.read_text(encoding="ascii"))
            self.assertEqual(github_auth.load_token(root), "ghp_exampletoken123")
            headers = github_auth.github_headers(root, Accept="text/html")
            self.assertEqual(headers["Authorization"], "Bearer ghp_exampletoken123")
            self.assertEqual(headers["Accept"], "text/html")
            self.assertEqual(headers["User-Agent"], "Audion-Get")
            self.assertTrue(github_auth.clear_token(root))
            self.assertFalse(github_auth.clear_token(root))
            self.assertEqual(github_auth.load_token(root), "")

    def test_a_foreign_or_damaged_file_reads_as_no_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = github_auth.token_path(root)
            path.parent.mkdir(parents=True)
            path.write_text("bm90IGEgYmxvYg==\n", encoding="ascii")  # 'not a blob'
            self.assertEqual(github_auth.load_token(root), "")
            self.assertNotIn("Authorization", github_auth.github_headers(root))


if __name__ == "__main__":
    unittest.main()

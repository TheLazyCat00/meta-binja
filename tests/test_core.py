import unittest

from meta_binja.core import canonical_repo_url, is_repo_url, repo_name_from_url


class UrlTests(unittest.TestCase):
    def test_https_repo(self):
        self.assertTrue(is_repo_url("https://github.com/Vector35/community-plugins"))
        self.assertEqual(
            canonical_repo_url("https://github.com/Vector35/community-plugins.git/"),
            "https://github.com/vector35/community-plugins",
        )

    def test_ssh_repo(self):
        self.assertTrue(is_repo_url("git@github.com:owner/repo.git"))
        self.assertEqual(
            canonical_repo_url("git@github.com:owner/repo.git"),
            "https://github.com/owner/repo",
        )

    def test_rejects_plain_text(self):
        self.assertFalse(is_repo_url("hashdb"))
        self.assertFalse(is_repo_url("https://example.com"))

    def test_repo_name(self):
        self.assertEqual(repo_name_from_url("https://github.com/a/my-plugin.git"), "my-plugin")


if __name__ == "__main__":
    unittest.main()

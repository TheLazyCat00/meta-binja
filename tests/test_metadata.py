"""Tests for README retrieval, link rewriting, and the TTL cache."""

from __future__ import annotations

import base64
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.stubs import install_binaryninja
except ImportError:  # pragma: no cover - direct ``python tests/test_metadata.py`` run
    from stubs import install_binaryninja

install_binaryninja()

from meta_binja.metadata import (
    JsonCache,
    absolutize_markdown,
    fetch_readme,
    fetch_repo_details,
    guard_public_url,
    local_readme,
)


class AbsolutizeTests(unittest.TestCase):
    """Validate that relative README references are resolved before rendering."""

    RAW = "https://raw.githubusercontent.com/owner/repo/HEAD/README.md"
    HTML = "https://github.com/owner/repo/blob/HEAD/README.md"

    def test_relative_images_resolve_against_raw_content(self):
        """A relative screenshot path becomes a raw URL that actually loads."""
        out = absolutize_markdown("![shot](docs/shot.png)", self.RAW, self.HTML)
        self.assertEqual(out, "![shot](https://raw.githubusercontent.com/owner/repo/HEAD/docs/shot.png)")

    def test_relative_links_resolve_against_the_web_view(self):
        """A relative document link points at the repository's web view."""
        out = absolutize_markdown("[docs](docs/usage.md)", self.RAW, self.HTML)
        self.assertEqual(out, "[docs](https://github.com/owner/repo/blob/HEAD/docs/usage.md)")

    def test_absolute_and_anchor_targets_are_untouched(self):
        """Absolute URLs, anchors, and mail links stay exactly as written."""
        source = "[a](https://example.test/x) [b](#section) [c](mailto:x@example.test) ![d](//cdn.test/i.png)"
        self.assertEqual(absolutize_markdown(source, self.RAW, self.HTML), source)

    def test_html_image_tags_are_resolved(self):
        """Inline HTML images in READMEs are rewritten too."""
        out = absolutize_markdown('<img src="media/logo.png" width="60">', self.RAW, self.HTML)
        self.assertIn("https://raw.githubusercontent.com/owner/repo/HEAD/media/logo.png", out)

    def test_root_relative_targets_stay_inside_the_repository(self):
        """A leading slash means the repository root, not the host root."""
        out = absolutize_markdown("![shot](/docs/shot.png)", self.RAW, self.HTML)
        self.assertEqual(out, "![shot](https://raw.githubusercontent.com/owner/repo/HEAD/docs/shot.png)")
        link = absolutize_markdown("[docs](/docs/usage.md)", self.RAW, self.HTML)
        self.assertEqual(link, "[docs](https://github.com/owner/repo/blob/HEAD/docs/usage.md)")

    def test_root_relative_angle_targets_are_resolved_too(self):
        """Angle-bracket targets follow the same repository-root rule."""
        out = absolutize_markdown("[x](</docs/a b.md>)", self.RAW, self.HTML)
        self.assertEqual(out, "[x](<https://github.com/owner/repo/blob/HEAD/docs/a b.md>)")

    def test_titles_after_the_target_are_preserved(self):
        """A Markdown link title survives rewriting."""
        out = absolutize_markdown('[x](docs/a.md "Title")', self.RAW, self.HTML)
        self.assertEqual(out, '[x](https://github.com/owner/repo/blob/HEAD/docs/a.md "Title")')


class JsonCacheTests(unittest.TestCase):
    """Validate the small TTL cache used for catalogs, READMEs, and repo facts."""

    def test_values_round_trip(self):
        """A stored value is returned while it is fresh."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = JsonCache(Path(temp_dir) / "c.json", 60)
            cache.set("key", {"a": 1})
            self.assertEqual(cache.get("key"), {"a": 1})

    def test_expired_values_are_ignored(self):
        """A value older than the TTL is treated as absent."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = JsonCache(Path(temp_dir) / "c.json", 0.01)
            cache.set("key", "value")
            time.sleep(0.02)
            self.assertIsNone(cache.get("key"))

    def test_concurrent_writes_keep_every_record(self):
        """Overlapping writers share a lock, so no cached record is lost."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "c.json"
            writers = [JsonCache(path, 60) for _ in range(4)]
            barrier = threading.Barrier(len(writers))

            def _write(index):
                cache = writers[index]
                barrier.wait()
                for item in range(10):
                    cache.set(f"key-{index}-{item}", item)

            threads = [threading.Thread(target=_write, args=(i,)) for i in range(len(writers))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            reader = JsonCache(path, 60)
            for index in range(len(writers)):
                for item in range(10):
                    self.assertEqual(reader.get(f"key-{index}-{item}"), item)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_corrupt_cache_behaves_like_an_empty_one(self):
        """A damaged cache file never breaks plugin discovery."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "c.json"
            path.write_text("not json", encoding="utf-8")
            cache = JsonCache(path, 60)
            self.assertIsNone(cache.get("key"))
            cache.set("key", "value")
            self.assertEqual(cache.get("key"), "value")


class PublicUrlGuardTests(unittest.TestCase):
    """Validate the destination check applied to repository-supplied URLs."""

    @staticmethod
    def _resolving(address):
        """Return a getaddrinfo stub that resolves every host to *address*."""
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return lambda *_args, **_kwargs: [(family, socket.SOCK_STREAM, 6, "", (address, 443))]

    def test_public_addresses_are_allowed(self):
        """A host resolving to a routable address passes the guard."""
        with patch("meta_binja.metadata.socket.getaddrinfo", self._resolving("93.184.216.34")):
            guard_public_url("https://example.test/image.png")

    def test_private_and_loopback_destinations_are_refused(self):
        """Internal services are never fetched on a README's behalf."""
        for address in ("127.0.0.1", "10.1.2.3", "192.168.0.5", "169.254.169.254", "::1", "fd00::1"):
            with self.subTest(address=address):
                with patch("meta_binja.metadata.socket.getaddrinfo", self._resolving(address)):
                    with self.assertRaises(ValueError):
                        guard_public_url("https://internal.test/image.png")

    def test_unresolvable_hosts_are_refused(self):
        """A host that does not resolve is refused rather than attempted."""
        with patch("meta_binja.metadata.socket.getaddrinfo", side_effect=socket.gaierror("nope")):
            with self.assertRaises(ValueError):
                guard_public_url("https://nowhere.test/image.png")

    def test_cleartext_and_credential_urls_are_refused(self):
        """Non-HTTPS URLs and embedded credentials never reach the network."""
        with patch("meta_binja.metadata.socket.getaddrinfo", self._resolving("93.184.216.34")):
            with self.assertRaises(ValueError):
                guard_public_url("http://example.test/image.png")
            with self.assertRaises(ValueError):
                guard_public_url("https://user:token@example.test/image.png")


class ReadmeTests(unittest.TestCase):
    """Validate README discovery from local checkouts and from GitHub."""

    def test_local_checkout_readme_wins(self):
        """An installed plugin's README is read from disk, not the network."""
        with tempfile.TemporaryDirectory() as temp_dir:
            checkout = Path(temp_dir)
            (checkout / "README.md").write_text("# Local\n\n![s](img/s.png)", encoding="utf-8")
            document = fetch_readme("https://github.com/owner/repo", checkout)
            self.assertIsNotNone(document)
            self.assertTrue(document.is_markdown)
            self.assertIn("# Local", document.text)
            self.assertIn("https://raw.githubusercontent.com/owner/repo/HEAD/img/s.png", document.text)

    def test_local_readme_lookup_is_case_insensitive(self):
        """A lower-case ``readme.md`` is found like the conventional spelling."""
        with tempfile.TemporaryDirectory() as temp_dir:
            (Path(temp_dir) / "readme.md").write_text("hello", encoding="utf-8")
            found = local_readme(Path(temp_dir))
            self.assertIsNotNone(found)
            self.assertEqual(found[1], "hello")

    def test_github_readme_is_fetched_and_cached(self):
        """A GitHub README is fetched once and then served from the cache."""
        payload = {
            "name": "README.md",
            "content": base64.b64encode(b"# Remote\n\n[docs](docs/a.md)").decode(),
            "download_url": "https://raw.githubusercontent.com/owner/repo/main/README.md",
            "html_url": "https://github.com/owner/repo/blob/main/README.md",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = JsonCache(Path(temp_dir) / "readme.json", 60)
            with patch("meta_binja.metadata.github_json", return_value=payload) as api:
                first = fetch_readme("https://github.com/owner/repo", None, cache)
                second = fetch_readme("https://github.com/owner/repo", None, cache)
            self.assertEqual(api.call_count, 1)
            self.assertIn("# Remote", first.text)
            self.assertIn("https://github.com/owner/repo/blob/main/docs/a.md", first.text)
            self.assertEqual(second.text, first.text)

    def test_network_failure_returns_none(self):
        """A README fetch failure degrades to ``None`` rather than raising."""
        with patch("meta_binja.metadata.github_json", side_effect=OSError("offline")):
            self.assertIsNone(fetch_readme("https://github.com/owner/repo"))


class RepoDetailsTests(unittest.TestCase):
    """Validate the optional repository fact lookup."""

    def test_details_are_normalized(self):
        """GitHub's payload is reduced to the fields the UI displays."""
        payload = {
            "description": "A plugin",
            "stargazers_count": 128,
            "pushed_at": "2026-01-02T03:04:05Z",
            "license": {"spdx_id": "MIT"},
            "topics": ["binaryninja", 5],
            "archived": False,
        }
        with patch("meta_binja.metadata.github_json", return_value=payload):
            details = fetch_repo_details("https://github.com/owner/repo")
        self.assertEqual(details["stars"], 128)
        self.assertEqual(details["license"], "MIT")
        self.assertEqual(details["topics"], ["binaryninja"])

    def test_non_github_hosts_yield_no_details(self):
        """Hosts without a supported API contribute nothing instead of failing."""
        self.assertEqual(fetch_repo_details("https://gitlab.com/owner/repo"), {})
        self.assertEqual(fetch_repo_details(None), {})


if __name__ == "__main__":
    unittest.main()

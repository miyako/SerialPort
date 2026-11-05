import io
from pathlib import Path
import sys
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci_config


class VendorRedirectTests(unittest.TestCase):
    def test_exact_vendor_cdn_https_redirect_is_accepted(self):
        handler = ci_config.Tool4DRedirectHandler()
        request = urllib.request.Request("https://resources-download.4d.com/fixed.tar.xz")
        redirected = handler.redirect_request(
            request, io.BytesIO(), 302, "Found", {},
            "https://builds2.4d.com/fixed.tar.xz?signature=public-download")
        self.assertEqual(redirected.host, "builds2.4d.com")
        self.assertEqual(handler.max_redirections, 5)
        self.assertEqual(handler.max_repeats, 2)

    def test_every_hop_rejects_untrusted_or_insecure_destinations(self):
        handler = ci_config.Tool4DRedirectHandler()
        trusted = "https://resources-download.4d.com/fixed.tar.xz"
        invalid = (
            "http://builds2.4d.com/file", "https://evil.example/file",
            "https://builds2.4d.com.evil.example/file", "https://other.4d.com/file",
            "https://user:password@builds2.4d.com/file",
            "https://builds2.4d.com:444/file", "https://builds2.4d.com/file#fragment",
        )
        for url in invalid:
            for source, destination in ((trusted, url), (url, trusted)):
                with self.subTest(source=source, destination=destination), self.assertRaises(ValueError):
                    handler.redirect_request(
                        urllib.request.Request(source), io.BytesIO(), 302, "Found", {}, destination)

    def test_restricted_opener_is_installed(self):
        with patch.object(ci_config.urllib.request, "build_opener") as build:
            ci_config.open_tool4d("https://resources-download.4d.com/fixed.tar.xz")
            self.assertIsInstance(build.call_args.args[0], ci_config.Tool4DRedirectHandler)
            build.return_value.open.assert_called_once_with(
                "https://resources-download.4d.com/fixed.tar.xz", timeout=120)

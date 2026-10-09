"""Opt-in macOS Keychain round-trip, including long OAuth credential bundles."""
from __future__ import annotations

import os
import sys
import unittest

from portal import secret_store


@unittest.skipUnless(sys.platform == "darwin" and os.getenv("PORTAL_TEST_KEYCHAIN") == "1",
                     "run with PORTAL_TEST_KEYCHAIN=1 in the signed-in macOS session")
class KeychainIntegrationTests(unittest.TestCase):
    def test_long_credential_bundle_round_trips_without_truncation(self) -> None:
        reference = secret_store.new_reference()
        expected = {"refresh_token": "x" * 2000, "client_secret": "y" * 300}
        try:
            secret_store.put(reference, expected)
            self.assertEqual(secret_store.get(reference), expected)
        finally:
            secret_store.delete(reference)


if __name__ == "__main__":
    unittest.main()

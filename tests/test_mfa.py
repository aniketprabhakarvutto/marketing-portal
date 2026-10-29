import tempfile
import time
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from portal import auth, db
from portal.config import APP


class MfaTests(unittest.TestCase):
    def setUp(self):
        self.old_path, self.old_url, self.old_key = APP.db_path, APP.database_url, APP.mfa_encryption_key
        self.temp = tempfile.TemporaryDirectory()
        APP.db_path = str(Path(self.temp.name) / "portal.db")
        APP.database_url = ""
        APP.mfa_encryption_key = Fernet.generate_key().decode("ascii")
        db.close_connection()
        db.init_db()
        self.password = "a correct horse battery staple"
        self.admin = auth.create_first_admin("admin@example.com", "Admin", self.password)

    def tearDown(self):
        db.close_connection()
        APP.db_path, APP.database_url, APP.mfa_encryption_key = self.old_path, self.old_url, self.old_key
        auth._mfa_challenges.clear()
        self.temp.cleanup()

    def test_enrollment_encrypts_seed_and_requires_second_factor(self):
        enrolled = auth.begin_mfa_enrollment(self.admin["user"]["id"], self.password)
        row = db.query("SELECT mfa_secret_encrypted FROM portal_users WHERE id=?", (self.admin["user"]["id"],))[0]
        self.assertNotIn(enrolled["secret"], row["mfa_secret_encrypted"])
        result = auth.complete_mfa_enrollment(enrolled["challenge"], auth._totp(enrolled["secret"]))
        self.assertEqual(10, len(result["recovery_codes"]))
        login = auth.login("admin@example.com", self.password, "127.0.0.1")
        self.assertTrue(login["mfa_required"])
        verified = auth.verify_mfa(login["challenge"], auth._totp(enrolled["secret"]))
        self.assertEqual("admin@example.com", verified["user"]["email"])

    def test_recovery_code_is_single_use(self):
        enrolled = auth.begin_mfa_enrollment(self.admin["user"]["id"], self.password)
        result = auth.complete_mfa_enrollment(enrolled["challenge"], auth._totp(enrolled["secret"]))
        recovery = result["recovery_codes"][0]
        first = auth.login("admin@example.com", self.password, "127.0.0.1")
        auth.verify_mfa(first["challenge"], recovery)
        second = auth.login("admin@example.com", self.password, "127.0.0.1")
        with self.assertRaisesRegex(ValueError, "incorrect"):
            auth.verify_mfa(second["challenge"], recovery)

    def test_mfa_challenge_rate_limit_and_expiry(self):
        enrolled = auth.begin_mfa_enrollment(self.admin["user"]["id"], self.password)
        auth.complete_mfa_enrollment(enrolled["challenge"], auth._totp(enrolled["secret"]))
        login = auth.login("admin@example.com", self.password, "127.0.0.1")
        for _ in range(auth.MFA_MAX_FAILURES):
            with self.assertRaises(ValueError):
                auth.verify_mfa(login["challenge"], "000000")
        with self.assertRaisesRegex(ValueError, "expired"):
            auth.verify_mfa(login["challenge"], auth._totp(enrolled["secret"]))


if __name__ == "__main__":
    unittest.main()

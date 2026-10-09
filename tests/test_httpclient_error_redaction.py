from __future__ import annotations

import io
import traceback
import urllib.error
import unittest
from unittest.mock import patch

from portal import httpclient


URL = "https://user:USER-SECRET@example.invalid/path?api_key=QUERY-SECRET"
RESPONSE_SECRET = "RESPONSE-SECRET"


class Response:
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return f"not json {RESPONSE_SECRET}".encode()


class HttpErrorRedactionTests(unittest.TestCase):
    def assert_safe(self, message: str) -> None:
        self.assertIn("example.invalid", message)
        for secret in ("USER-SECRET", "QUERY-SECRET", RESPONSE_SECRET):
            self.assertNotIn(secret, message)

    def assert_safe_traceback(self, error: Exception) -> None:
        self.assert_safe("".join(traceback.format_exception(type(error), error, error.__traceback__)))

    def test_non_json_error_does_not_echo_url_or_response(self) -> None:
        with patch("portal.httpclient._open", return_value=Response()):
            with self.assertRaises(RuntimeError) as raised:
                httpclient.get(URL)
        self.assert_safe(str(raised.exception))
        self.assert_safe_traceback(raised.exception)

    def test_http_error_keeps_body_for_provider_logic_but_not_message(self) -> None:
        error = urllib.error.HTTPError(URL, 403, "Forbidden", {}, io.BytesIO(RESPONSE_SECRET.encode()))
        with patch("portal.httpclient._open", side_effect=error):
            with self.assertRaises(httpclient.HttpError) as raised:
                httpclient.get(URL)
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(raised.exception.body, RESPONSE_SECRET)
        self.assert_safe(str(raised.exception))
        self.assert_safe_traceback(raised.exception)

    def test_network_error_does_not_echo_url_from_reason(self) -> None:
        error = urllib.error.URLError(f"Could not resolve {URL} {RESPONSE_SECRET}")
        with patch("portal.httpclient._open", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                httpclient.get(URL)
        self.assert_safe(str(raised.exception))
        self.assert_safe_traceback(raised.exception)

    def test_csv_network_error_does_not_echo_url_from_reason(self) -> None:
        error = urllib.error.URLError(f"Could not resolve {URL} {RESPONSE_SECRET}")
        with patch("portal.httpclient._open", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                httpclient.get_text(URL)
        self.assert_safe(str(raised.exception))
        self.assert_safe_traceback(raised.exception)


if __name__ == "__main__":
    unittest.main()
